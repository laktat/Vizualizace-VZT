"""
Automatické korekce — co systém udělá sám, než přijde člověk.

Diagnostika v diagnostics.py umí říct, co je špatně. Tenhle modul z části
těch zjištění dělá zásah: dopočítá novou žádanou hodnotu a nechá ji zapsat
do zařízení přes driver, stejnou cestou jako posuvník.

CO TENHLE MODUL NENÍ: není to AI ani nic učícího se. Je to deterministická
pravidlová logika se zábranami. Kdyby se jednou rozhodovalo z modelu, pořád
musí projít stejnými zábranami — ty jsou na zásahu do technologie důležitější
než samo rozhodnutí.

PROČ SE NĚKTERÉ VĚCI NEOPRAVUJÍ SAMY

Zanesený filtr regulace neopraví, ten potřebuje člověka s novým filtrem.
Zvyšování otáček navíc zanášení zrychluje — opotřebení filtru roste s druhou
mocninou otáček — takže korekce by urychlovala příčinu, kterou má léčit.
Co je legitimní, je držet PRŮTOK: přiškrcená cesta znamená méně vzduchu při
stejných otáčkách a to se dorovnat dá. Skutečné jednotky tuhle funkci mají
(regulace na konstantní průtok). Nad mezí výměny se ale korekce vzdá — od
toho místa je to práce pro údržbu, ne pro regulaci.

Požární ochrany a výpadku komunikace se modul nedotkne vůbec. První je
bezpečnostní blokace, druhý se nedá zapsat do zařízení, které mlčí.

ZÁBRANY, které platí pro každý zásah:

    vypínatelné     globálně i po zařízení
    omezené         krok, strop a nejvyšší odchylka od základu
    nespěchající    jedna korekce za COOLDOWN, ať nekmitá proti regulátoru
    vratné          když příčina zmizí, korekce se odvolá
    dohledatelné    každý zásah do knihy alarmů se starou a novou hodnotou

ZÁKLAD, ke kterému se korekce vrací, je hodnota z aktivního provozního
režimu — to je to, co operátor chtěl. Když žádný režim aktivní není, bere se
výchozí hodnota z mapy registrů. Díky tomu se základ neztratí ani restartem
dispečinku; v paměti by se ztratil a korekce by zůstala navěky.
"""

import registers as regs

# --- zábrany -----------------------------------------------------------------
STEP_PCT = 3.0            # o kolik se otáčky hýbou na jeden zásah [%]
CEILING_PCT = 92.0        # strop otáček; zbytek je rezerva pro regulaci [%]
MAX_DEVIATION = 12.0      # nejvyšší odchylka od základu [%]
COOLDOWN = 120.0          # nejkratší doba mezi zásahy na jednom zařízení [s]

#: konzervativní pásmo teploty přívodu, když je čidlo vadné [°C]
SAFE_SUPPLY_MIN = 20.0
SAFE_SUPPLY_MAX = 26.0

FIRE_BIT = 7              # požární ochrana ve slově alarmů VZT
FAN_FAULT_BIT = 4


class Action:
    """Jeden zásah: co, kam, jakou hodnotu a proč."""

    def __init__(self, device_id, key, value, text, detail, revert=False):
        self.device_id = device_id
        self.key = key
        self.value = value
        self.text = text
        self.detail = detail
        self.revert = revert

    def __repr__(self):
        return f"<{self.device_id}.{self.key}={self.value:.1f}>"


class SelfHealing:
    def __init__(self, enabled=True, baselines=None):
        self.enabled = enabled
        self.disabled_devices = set()
        self.last_action = {}      # zařízení -> čas posledního zásahu
        self.corrections = {}      # (zařízení, klíč) -> aktuální korigovaná hodnota
        self.given_up = set()      # kde se korekce vzdala (je to na údržbu)
        self._baselines = baselines or (lambda dev_id, key: None)

    # -- stav pro vizualizaci -------------------------------------------------
    def status(self):
        return {
            "enabled": self.enabled,
            "disabled_devices": sorted(self.disabled_devices),
            "corrections": [
                {"device": dev, "key": key, "value": value}
                for (dev, key), value in sorted(self.corrections.items())
            ],
            "given_up": sorted(self.given_up),
        }

    def active_for(self, dev_id):
        return self.enabled and dev_id not in self.disabled_devices

    # -- pomocné --------------------------------------------------------------
    def baseline(self, dev, key):
        """Hodnota, ke které se korekce vrací: co nastavil operátor."""
        value = self._baselines(dev.id, key)
        if value is not None:
            return float(value)
        reg = regs.by_key(regs.DEVICE_TYPES[dev.type]["holding"]).get(key)
        return float(reg["default"]) if reg else 0.0

    @staticmethod
    def _limits(dev, key):
        reg = regs.by_key(regs.DEVICE_TYPES[dev.type]["holding"])[key]
        return reg["min"], reg["max"]

    def _cooling_down(self, dev_id, now):
        return now - self.last_action.get(dev_id, 0.0) < COOLDOWN

    @staticmethod
    def _finding(findings, key):
        for f in findings:
            if f.get("key") == key:
                return f
        return None

    # -- rozhodování ----------------------------------------------------------
    def consider(self, dev, findings, values, setpoints, now):
        """
        Rozhodne, co udělat. Vrací seznam zásahů; sám nic nezapisuje, aby se
        rozhodnutí dalo vyzkoušet bez technologie.
        """
        if not self.active_for(dev.id) or dev.type != "ahu" or not findings:
            return []
        if self._cooling_down(dev.id, now):
            return []

        alarms = int(values.get("alarms", 0))
        # bezpečnostní blokace se neobchází a s vyhozeným ventilátorem
        # nemá cenu hýbat otáčkami
        if alarms & (1 << FIRE_BIT) or alarms & (1 << FAN_FAULT_BIT):
            return []
        if values.get("fan_supply", 0.0) < 5.0:
            return []

        actions = []
        actions += self._airflow(dev, findings, values, setpoints)
        actions += self._sensor_fallback(dev, findings, setpoints)
        return actions[:1]      # jeden zásah za kolo, ať se dá sledovat

    def _airflow(self, dev, findings, values, setpoints):
        """Kompenzace na konstantní průtok, když se přiškrcuje cesta."""
        finding = self._finding(findings, "prutok")
        if finding is None:
            return []

        key = "sp_fan"
        base = self.baseline(dev, key)
        current = float(setpoints.get(key, base))
        lo, hi = self._limits(dev, key)
        dp = values.get("filter_dp_sup", 0.0)
        limit = setpoints.get("dp_limit", 250.0)

        # Nad mezí výměny se korekce vzdává: filtr je za životností a další
        # otáčky by ho jen dorazily. Tohle je práce pro údržbu.
        if dp >= limit:
            if dev.id not in self.given_up:
                self.given_up.add(dev.id)
                return [Action(dev.id, key, current,
                               "Korekce průtoku ukončena — filtr je za mezí výměny",
                               f"tlaková ztráta {dp:.0f} Pa z {limit:.0f} Pa; "
                               f"otáčky nechávám na {current:.0f} %, "
                               f"dorovnávat průtok už nemá smysl")]
            return []
        self.given_up.discard(dev.id)

        if finding["level"] in ("warn", "bad"):
            ceiling = min(CEILING_PCT, base + MAX_DEVIATION, hi)
            target = min(current + STEP_PCT, ceiling)
            if target <= current + 0.1:
                return []
            self.corrections[(dev.id, key)] = target
            return [Action(dev.id, key, target,
                           f"Otáčky ventilátoru zvýšeny na {target:.0f} %",
                           f"{finding['msg']} Kompenzace na konstantní průtok, "
                           f"základ {base:.0f} %, strop {ceiling:.0f} %")]

        # příčina zmizela (vyměněný filtr, otevřená klapka) — korekce se vrací
        if finding["level"] == "ok" and current > base + 0.1:
            target = max(current - STEP_PCT, base)
            if target >= base - 0.1 and abs(target - base) < 0.1:
                self.corrections.pop((dev.id, key), None)
            else:
                self.corrections[(dev.id, key)] = target
            return [Action(dev.id, key, target,
                           f"Otáčky ventilátoru sníženy na {target:.0f} %",
                           f"průtok odpovídá otáčkám, korekce se vrací "
                           f"k základu {base:.0f} %", revert=True)]
        return []

    def _sensor_fallback(self, dev, findings, setpoints):
        """
        Vadné čidlo: zúží se pásmo teploty přívodu.

        Regulátor si při vadném čidle přívodu sáhne po náhradní veličině a
        jede dál — jenže naslepo. Zúžené pásmo omezí, jak moc může přívod
        ujet, než si toho někdo všimne.
        """
        finding = self._finding(findings, "cidla")
        if finding is None:
            return []

        pairs = (("sp_supply_min", SAFE_SUPPLY_MIN, max),
                 ("sp_supply_max", SAFE_SUPPLY_MAX, min))
        if finding["level"] == "bad":
            for key, safe, pick in pairs:
                base = self.baseline(dev, key)
                current = float(setpoints.get(key, base))
                target = pick(base, safe)
                if abs(current - target) < 0.1:
                    continue
                self.corrections[(dev.id, key)] = target
                return [Action(dev.id, key, target,
                               f"Pásmo přívodu zúženo — {key} na {target:.1f} °C",
                               f"{finding['msg']} Jednotka jede na náhradní "
                               f"čidlo, zúžené pásmo omezí, jak daleko může "
                               f"teplota přívodu ujet")]
            return []

        # čidlo se vrátilo — pásmo zpátky
        for key, _, _ in pairs:
            if (dev.id, key) not in self.corrections:
                continue
            base = self.baseline(dev, key)
            current = float(setpoints.get(key, base))
            if abs(current - base) < 0.1:
                self.corrections.pop((dev.id, key), None)
                continue
            self.corrections.pop((dev.id, key), None)
            return [Action(dev.id, key, base,
                           f"Pásmo přívodu obnoveno — {key} na {base:.1f} °C",
                           "čidlo hlásí věrohodné hodnoty, korekce se vrací",
                           revert=True)]
        return []

    def note_applied(self, dev_id, now):
        self.last_action[dev_id] = now
