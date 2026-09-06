"""
Mapy registrů všech zařízení v závodě.

Tohle je jediné místo, kde je popsané, co který registr znamená. U reálné
zakázky sem přepíšeš adresy z dokumentace výrobce (Sauter, Domat, Regin,
Carrier, De Dietrich, ...) a zbytek kódu zůstane stejný.

scale     = kolikrát je hodnota v registru zvětšená proti fyzikální
            (teplota 21.5 °C se posílá jako 215, scale = 10)
words     = kolik 16bitových registrů hodnota zabírá. Provozní hodiny, počty
            startů a stavy počítadel energie se u reálných regulátorů posílají
            jako 32bit (2 registry, big-endian word order), protože 65535 h je
            jen 7,5 roku provozu a do 65535 kWh se závod nevejde ani za měsíc.

POČÍTADLA ENERGIE (*_energy) jsou stavy podružného měření — elektroměru,
            plynoměru, kalorimetru. Narůstají a nikdy se nenulují, stejně jako
            na skutečném měřidle. Spotřeba za období se z nich počítá rozdílem
            dvou odečtů, ne tím, že by se počítadlo vynulovalo.
valid_min / valid_max = mez pro rozpoznání ROZBITÉHO čidla, ne provozní rozsah.
            Kontrola má chytit přerušený nebo zkratovaný obvod (-120 °C,
            přepálené stovky stupňů), NE neobvyklou, ale reálnou hodnotu.

Adresy se přidělují automaticky v pořadí, v jakém jsou registry napsané —
u1 hodnota zabere jednu adresu, u32 dvě.
"""


def R(key, name, unit="", scale=1, vmin=None, vmax=None, words=1):
    """Popis jednoho registru. Adresa se doplní až v _map()."""
    return {"key": key, "name": name, "unit": unit, "scale": scale,
            "valid_min": vmin, "valid_max": vmax, "words": words}


def H(key, name, unit="", scale=1, default=0.0, lo=0.0, hi=100.0):
    """Popis jednoho nastavitelného (holding) registru."""
    return {"key": key, "name": name, "unit": unit, "scale": scale,
            "default": default, "min": lo, "max": hi, "words": 1}


def _map(specs):
    """Přidělí registrům adresy za sebou podle jejich šířky."""
    addr = 0
    for s in specs:
        s["addr"] = addr
        addr += s["words"]
    return specs


def by_key(regs):
    return {r["key"]: r for r in regs}


def span(regs):
    """Kolik 16bitových adres celá mapa zabírá."""
    return sum(r["words"] for r in regs)


# =============================================================================
# VZT jednotka (přívod + odtah, rekuperace, vodní ohřívač, vodní chladič)
# =============================================================================
AHU_INPUT = _map([
    R("t_outdoor",      "Teplota venkovní (sání)",        "°C", 10, -50, 120),
    R("t_after_recup",  "Teplota za rekuperátorem",       "°C", 10, -50, 120),
    R("t_after_heater", "Teplota za ohřívačem",           "°C", 10, -50, 120),
    R("t_supply",       "Teplota přívodu (za chladičem)", "°C", 10, -50, 120),
    R("t_extract",      "Teplota odtahu (místnost)",      "°C", 10, -50, 120),
    R("t_exhaust",      "Teplota odpadního vzduchu",      "°C", 10, -50, 120),
    R("rh_extract",     "Vlhkost odtahu",                 "%",  10,   0, 100),
    R("heat_cmd",       "Povel na topný ventil",          "%",  10,   0, 100),
    R("cool_cmd",       "Povel na chladicí ventil",       "%",  10,   0, 100),
    R("recup_cmd",      "Povel na rekuperaci (obtok)",    "%",  10,   0, 100),
    R("damper",         "Poloha klapek",                  "%",  10,   0, 100),
    R("fan_supply",     "Otáčky přívodního ventilátoru",  "%",  10,   0, 100),
    R("fan_extract",    "Otáčky odtahového ventilátoru",  "%",  10,   0, 100),
    # průtok se u velkých jednotek nevejde do 16 bitů, proto 32bitová hodnota
    R("flow_supply",    "Průtok přívodu",              "m³/h",  1,    0, 200000, words=2),
    R("filter_dp_sup",  "Tlaková ztráta filtru přívod",   "Pa",  1,    0, 2000),
    R("filter_dp_ext",  "Tlaková ztráta filtru odtah",    "Pa",  1,    0, 2000),
    R("current",        "Proud motorů",                   "A",  100,   0, 50),
    R("power",          "Elektrický příkon",              "kW", 10,    0, 200),
    R("chw_power",      "Chladicí výkon",                 "kW", 10,    0, 1000),
    R("hw_power",       "Topný výkon",                    "kW", 10,    0, 1000),
    R("state",          "Stav jednotky",                  "",    1,    0, 10),
    R("alarms",         "Slovo alarmů",                   "",    1,    0, 65535),
    R("run_hours",      "Provozní hodiny",                "h",   1,    0, 4000000, words=2),
    R("uptime",         "Hodiny od zapnutí",              "h",   10,   0, 400000, words=2),
    R("el_energy",       "Elektřina ventilátorů",             "kWh", 10, 0, 40000000, words=2),
    R("heat_energy",     "Odebrané teplo",                    "kWh", 10, 0, 40000000, words=2),
    R("cool_energy",     "Odebraný chlad",                    "kWh", 10, 0, 40000000, words=2),
    R("recup_energy",    "Energie získaná rekuperací",        "kWh", 10, 0, 40000000, words=2),
    R("waste_energy",    "Zmařené teplo",                     "kWh", 10, 0, 40000000, words=2),
])

AHU_HOLDING = _map([
    H("mode",       "Režim (0 stop, 1 auto, 2 ručně)",  "",   1,  1.0,  0.0,   2.0),
    H("sp_room",    "Žádaná teplota místnosti",         "°C", 10, 22.0, 16.0,  28.0),
    H("sp_fan",     "Žádané otáčky ventilátorů",        "%",  10, 78.0, 30.0, 100.0),
    H("sp_supply_min", "Minimální teplota přívodu",     "°C", 10, 16.0, 10.0,  22.0),
    H("sp_supply_max", "Maximální teplota přívodu",     "°C", 10, 32.0, 24.0,  45.0),
    H("dp_limit",   "Mez tlakové ztráty filtru",        "Pa", 1, 250.0, 150.0, 400.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",      "Kvitování poruchy",                "",   1,  0.0,  0.0,   1.0),
])

AHU_STATES = {0: "Stop", 1: "Náběh", 2: "Provoz", 3: "Doběh", 4: "Porucha"}
# Zpoždění alarmů [s]. Porucha stroje nebo čidla se hlásí hned, ale odchylka
# od žádané teploty se v provozu mění pořád — kdyby se hlásila okamžitě,
# záznamník by se zaplnil zákmity a to podstatné by se v nich ztratilo.
# Bity, které tady nejsou, mají výchozí krátké zpoždění.
AHU_ALARM_DELAYS = {0: 60, 1: 60, 6: 60}
AHU_ALARMS = {
    0: "Zanesený filtr přívod",
    1: "Zanesený filtr odtah",
    2: "Porucha čidla",
    3: "Zaseklý topný ventil",
    4: "Porucha ventilátoru",
    5: "Nebezpečí zámrazu ohřívače",
    6: "Nedosažena žádaná teplota",
}

# =============================================================================
# Chladicí jednotka (chiller) — 2 kompresory, vzduchem/vodou chlazený kondenzátor
# =============================================================================
CHILLER_INPUT = _map([
    R("t_chw_in",     "Teplota chlazené vody vstup",   "°C", 10, -30, 100),
    R("t_chw_out",    "Teplota chlazené vody výstup",  "°C", 10, -30, 100),
    R("t_cw_in",      "Teplota kondenz. vody vstup",   "°C", 10, -30, 100),
    R("t_cw_out",     "Teplota kondenz. vody výstup",  "°C", 10, -30, 100),
    R("p_suction",    "Tlak sání (nízký tlak)",       "bar", 100,  0, 40),
    R("p_discharge",  "Tlak výtlaku (vysoký tlak)",   "bar", 100,  0, 40),
    R("t_evap",       "Vypařovací teplota",            "°C", 10, -60, 100),
    R("t_cond",       "Kondenzační teplota",           "°C", 10, -60, 120),
    R("superheat",    "Přehřátí sání",                  "K", 10, -20, 60),
    R("subcool",      "Podchlazení",                    "K", 10, -20, 60),
    R("eev",          "Otevření expanzního ventilu",    "%", 10,   0, 100),
    R("capacity",     "Výkon jednotky",                 "%", 10,   0, 100),
    R("flow_chw",     "Průtok chlazené vody",       "m³/h",  10,   0, 2000),
    R("power",        "Elektrický příkon",             "kW", 10,   0, 1000),
    R("cool_power",   "Chladicí výkon",                "kW", 10,   0, 3000),
    R("cop",          "COP (chladicí faktor)",          "",  100,  0, 15),
    R("current",      "Proud",                          "A", 10,   0, 800),
    R("c1_state",     "Kompresor 1 stav",               "",   1,   0, 4),
    R("c2_state",     "Kompresor 2 stav",               "",   1,   0, 4),
    R("state",        "Stav jednotky",                  "",   1,   0, 10),
    R("alarms",       "Slovo alarmů",                   "",   1,   0, 65535),
    R("c1_hours",     "Motohodiny kompresoru 1",        "h",  1,   0, 4000000, words=2),
    R("c2_hours",     "Motohodiny kompresoru 2",        "h",  1,   0, 4000000, words=2),
    R("c1_starts",    "Počet startů kompresoru 1",      "",   1,   0, 4000000, words=2),
    R("c2_starts",    "Počet startů kompresoru 2",      "",   1,   0, 4000000, words=2),
    R("uptime",       "Hodiny od zapnutí",              "h",  10,  0, 400000, words=2),
    R("el_energy",       "Spotřebovaná elektřina",            "kWh", 10, 0, 40000000, words=2),
    R("cool_energy",     "Vyrobený chlad",                    "kWh", 10, 0, 40000000, words=2),
])

CHILLER_HOLDING = _map([
    H("enable",     "Povolení chodu",              "",   1,  1.0,  0.0,   1.0),
    H("sp_chw_out", "Žádaná teplota chlazené vody","°C", 10,  6.0,  4.0,  14.0),
    H("cap_limit",  "Omezení výkonu",              "%",  10,100.0, 20.0, 100.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",      "Kvitování poruchy",           "",   1,  0.0,  0.0,   1.0),
])

COMP_STATES = {0: "Stop", 1: "Náběh", 2: "Provoz", 3: "Blokace (min. pauza)", 4: "Porucha"}
CHILLER_STATES = {0: "Stop", 1: "Připraven", 2: "Chlazení", 3: "Odstávka", 4: "Porucha"}
CHILLER_ALARM_DELAYS = {6: 60}
CHILLER_ALARMS = {
    0: "Vysoký tlak (HP)",
    1: "Nízký tlak (LP)",
    2: "Nízký průtok výparníku",
    3: "Nízké přehřátí",
    4: "Porucha kompresoru 1",
    5: "Porucha kompresoru 2",
    6: "Nedosažena žádaná teplota",
}

# =============================================================================
# Chladicí věž — 2 ventilátory, dopouštění, odluh
# =============================================================================
TOWER_INPUT = _map([
    R("t_water_in",   "Teplota vody na věž",        "°C", 10, -30, 100),
    R("t_water_out",  "Teplota vody z věže",        "°C", 10, -30, 100),
    R("t_ambient",    "Teplota venkovní",           "°C", 10, -50, 120),
    R("t_wetbulb",    "Teplota mokrého teploměru",  "°C", 10, -50, 120),
    R("approach",     "Approach (výstup − mokrý)",   "K", 10, -20, 40),
    R("f1_speed",     "Otáčky ventilátoru 1",        "%", 10,   0, 100),
    R("f2_speed",     "Otáčky ventilátoru 2",        "%", 10,   0, 100),
    R("f1_state",     "Ventilátor 1 stav",           "",   1,   0, 4),
    R("f2_state",     "Ventilátor 2 stav",           "",   1,   0, 4),
    R("flow",         "Průtok věží",             "m³/h",  10,   0, 2000),
    R("basin_level",  "Hladina v bazénu",            "%", 10,   0, 100),
    R("makeup_valve", "Dopouštěcí ventil",           "%", 10,   0, 100),
    R("makeup_total", "Spotřeba dopouštěné vody",   "m³",  10,   0, 4000000, words=2),
    R("conductivity", "Vodivost vody",           "µS/cm",  1,   0, 10000),
    R("blowdown",     "Odluh otevřen",               "",   1,   0, 1),
    R("reject_power", "Odvedený tepelný výkon",     "kW", 10,   0, 5000),
    R("state",        "Stav věže",                   "",   1,   0, 10),
    R("alarms",       "Slovo alarmů",                "",   1,   0, 65535),
    R("f1_hours",     "Motohodiny ventilátoru 1",    "h",  1,   0, 4000000, words=2),
    R("f2_hours",     "Motohodiny ventilátoru 2",    "h",  1,   0, 4000000, words=2),
    R("power",        "Příkon ventilátorů",          "kW", 10,  0, 500),
    R("uptime",       "Hodiny od zapnutí",           "h",  10,  0, 400000, words=2),
    R("el_energy",       "Spotřebovaná elektřina",            "kWh", 10, 0, 40000000, words=2),
    R("reject_energy",   "Odvedené teplo",                    "kWh", 10, 0, 40000000, words=2),
])

TOWER_HOLDING = _map([
    H("enable",       "Povolení chodu",            "",   1,  1.0,  0.0,  1.0),
    H("sp_water_out", "Žádaná teplota vody z věže","°C", 10, 27.0, 18.0, 35.0),
    H("sp_cond_max",  "Vodivost pro odluh",     "µS/cm",  1,2000.0,500.0,5000.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",      "Kvitování poruchy",         "",   1,  0.0,  0.0,   1.0),
])

TOWER_ALARM_DELAYS = {3: 60, 4: 60}
TOWER_ALARMS = {
    0: "Nízká hladina v bazénu",
    1: "Porucha ventilátoru 1",
    2: "Porucha ventilátoru 2",
    3: "Vysoká vodivost vody",
    4: "Nedosažena žádaná teplota",
    5: "Nebezpečí zámrazu bazénu",
}

# =============================================================================
# Okruh chlazené vody — 2 primární a 2 sekundární oběhová čerpadla, průtoky
# =============================================================================
def _pump_regs(prefix, label):
    return [
        R(f"{prefix}_state", f"{label} stav",          "",   1,  0, 4),
        R(f"{prefix}_speed", f"{label} otáčky",        "%", 10,  0, 100),
        R(f"{prefix}_flow",  f"{label} průtok",    "m³/h",  10,  0, 2000),
        R(f"{prefix}_head",  f"{label} dopravní výška","kPa", 10, 0, 1000),
        R(f"{prefix}_power", f"{label} příkon",       "kW", 10,  0, 200),
        R(f"{prefix}_hours", f"{label} motohodiny",    "h",  1,  0, 4000000, words=2),
        R(f"{prefix}_starts",f"{label} počet startů",  "",   1,  0, 4000000, words=2),
    ]


CHW_INPUT = _map([
    R("t_supply",   "Teplota chlazené vody přívod",  "°C", 10, -30, 100),
    R("t_return",   "Teplota chlazené vody zpátečka","°C", 10, -30, 100),
    R("flow_prim",  "Průtok primárního okruhu",   "m³/h",  10,   0, 5000),
    R("flow_sec",   "Průtok sekundárního okruhu", "m³/h",  10,   0, 5000),
    R("p_supply",   "Tlak přívod",                 "bar", 100,   0, 25),
    R("p_return",   "Tlak zpátečka",               "bar", 100,   0, 25),
    R("dp",         "Tlaková diference",           "bar", 100,   0, 25),
    R("load_power", "Odebíraný chladicí výkon",     "kW", 10,    0, 5000),
    R("prim_lead",  "Vedoucí primární čerpadlo",     "",   1,    1, 2),
    R("sec_lead",   "Vedoucí sekundární čerpadlo",   "",   1,    1, 2),
    R("alarms",     "Slovo alarmů",                  "",   1,    0, 65535),
    *_pump_regs("p1", "Primární čerpadlo 1"),
    *_pump_regs("p2", "Primární čerpadlo 2"),
    *_pump_regs("s1", "Sekundární čerpadlo 1"),
    *_pump_regs("s2", "Sekundární čerpadlo 2"),
    R("uptime",     "Hodiny od zapnutí",             "h",  10,   0, 400000, words=2),
    R("el_energy",       "Elektřina čerpadel",                "kWh", 10, 0, 40000000, words=2),
])

CHW_HOLDING = _map([
    H("enable",       "Povolení okruhu",               "",   1,  1.0,  0.0,   1.0),
    H("sp_dp",        "Žádaná tlaková diference",    "bar", 100, 1.20, 0.40,  3.00),
    H("changeover_h", "Interval střídání čerpadel",    "h",  1, 24.0,  1.0, 500.0),
    H("sp_supply",    "Žádaná teplota přívodu",       "°C", 10,  6.0,  4.0,  14.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",        "Kvitování poruchy",           "",   1,  0.0,  0.0,   1.0),
])

PUMP_STATES = {0: "Stop", 1: "Náběh", 2: "Provoz", 3: "Záloha", 4: "Porucha"}
CHW_ALARM_DELAYS = {6: 60}
CHW_ALARMS = {
    0: "Porucha primárního čerpadla 1",
    1: "Porucha primárního čerpadla 2",
    2: "Porucha sekundárního čerpadla 1",
    3: "Porucha sekundárního čerpadla 2",
    4: "Nízký průtok okruhu",
    5: "Nízký tlak v okruhu",
    6: "Vysoká teplota přívodu",
}

# =============================================================================
# Kotel (2 shodné kusy) — modulovaný hořák
# =============================================================================
BOILER_INPUT = _map([
    R("t_flow",     "Teplota výstupní vody",     "°C", 10, -30, 150),
    R("t_return",   "Teplota zpátečky",          "°C", 10, -30, 150),
    R("t_flue",     "Teplota spalin",            "°C", 10, -30, 400),
    R("modulation", "Modulace hořáku",            "%", 10,   0, 100),
    R("burner",     "Stav hořáku",                "",   1,   0, 4),
    R("flame",      "Signál plamene",             "%", 10,   0, 100),
    R("pressure",   "Tlak vody v kotli",        "bar", 100,  0, 10),
    R("flow",       "Průtok kotlem",          "m³/h",  10,   0, 500),
    R("power",      "Tepelný výkon",             "kW", 10,   0, 3000),
    R("gas_flow",   "Spotřeba plynu",         "m³/h",  10,   0, 500),
    R("gas_total",  "Spotřeba plynu celkem",    "m³",  10,   0, 4000000, words=2),
    R("efficiency", "Účinnost",                   "%", 10,   0, 110),
    R("state",      "Stav kotle",                 "",   1,   0, 10),
    R("alarms",     "Slovo alarmů",               "",   1,   0, 65535),
    R("run_hours",  "Motohodiny hořáku",          "h",  1,   0, 4000000, words=2),
    R("starts",     "Počet startů hořáku",        "",   1,   0, 4000000, words=2),
    R("uptime",     "Hodiny od zapnutí",          "h",  10,  0, 400000, words=2),
    R("gas_energy",      "Energie ve spáleném plynu",         "kWh", 10, 0, 40000000, words=2),
    R("heat_energy",     "Vyrobené teplo",                    "kWh", 10, 0, 40000000, words=2),
])

BOILER_HOLDING = _map([
    H("enable",  "Povolení chodu",         "",   1,  1.0,  0.0,   1.0),
    H("sp_flow", "Nejvyšší dovolená výstupní teplota", "°C", 10, 80.0, 45.0, 90.0),
    H("mod_min", "Minimální modulace",     "%",  10, 25.0, 10.0,  60.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",   "Kvitování poruchy",      "",   1,  0.0,  0.0,   1.0),
])

BURNER_STATES = {0: "Stop", 1: "Předvětrání", 2: "Zapalování", 3: "Hoří", 4: "Porucha"}
BOILER_ALARM_DELAYS = {4: 60, 5: 60}
BOILER_ALARMS = {
    0: "Porucha hořáku (bez plamene)",
    1: "Havarijní termostat",
    2: "Nízký tlak vody",
    3: "Nízký průtok kotlem",
    4: "Vysoká teplota spalin",
    5: "Nedosažena žádaná teplota",
}

# =============================================================================
# Kotelna — rozdělovač/sběrač, 2 oběhová čerpadla, ekvitermní křivka
# =============================================================================
HW_INPUT = _map([
    R("t_header_flow",   "Teplota rozdělovače",     "°C", 10, -30, 150),
    R("t_header_return", "Teplota sběrače",         "°C", 10, -30, 150),
    R("t_outdoor",       "Teplota venkovní",        "°C", 10, -50, 120),
    R("sp_calc",         "Vypočtená žádaná teplota","°C", 10,   0, 150),
    R("flow",            "Průtok topné vody",    "m³/h",  10,   0, 1000),
    R("p_system",        "Tlak systému",           "bar", 100,  0, 10),
    R("p_expansion",     "Tlak expanzní nádoby",   "bar", 100,  0, 10),
    R("makeup_valve",    "Dopouštění systému",       "%", 10,   0, 100),
    R("heat_power",      "Dodávaný tepelný výkon",  "kW", 10,   0, 5000),
    R("load_power",      "Odebíraný tepelný výkon", "kW", 10,   0, 5000),
    R("lead",            "Vedoucí kotel",            "",   1,   1, 2),
    R("pump_lead",       "Vedoucí čerpadlo",         "",   1,   1, 2),
    R("alarms",          "Slovo alarmů",             "",   1,   0, 65535),
    *_pump_regs("hp1", "Oběhové čerpadlo 1"),
    *_pump_regs("hp2", "Oběhové čerpadlo 2"),
    R("uptime",          "Hodiny od zapnutí",       "h",  10,   0, 400000, words=2),
    R("el_energy",       "Elektřina čerpadel",                "kWh", 10, 0, 40000000, words=2),
    R("heat_energy",     "Teplo dodané spotřebičům",          "kWh", 10, 0, 40000000, words=2),
])

HW_HOLDING = _map([
    H("enable",       "Povolení kotelny",           "",   1,  1.0,  0.0,   1.0),
    H("curve_slope",  "Sklon ekvitermní křivky",    "",  100, 3.40, 1.00,  6.00),
    H("curve_shift",  "Posun ekvitermní křivky",   "K",  10,  0.0,-15.0,  15.0),
    H("sp_min",       "Minimální teplota rozdělovače","°C",10, 40.0, 25.0,  60.0),
    H("sp_max",       "Maximální teplota rozdělovače","°C",10, 80.0, 60.0,  95.0),
    H("changeover_h", "Interval střídání kotlů",    "h",   1, 48.0,  1.0, 500.0),
    H("summer_limit", "Venkovní teplota pro letní odstávku", "°C", 10, 18.0, 10.0, 30.0),
    # Kvitování poruchy: dispečink zapíše 1, zařízení poruchu zapomene
    # a registr si samo vynuluje. Když příčina trvá, porucha naskočí znovu.
    H("reset",        "Kvitování poruchy",          "",   1,  0.0,  0.0,   1.0),
])

HW_ALARM_DELAYS = {4: 60, 5: 60}
HW_ALARMS = {
    0: "Porucha oběhového čerpadla 1",
    1: "Porucha oběhového čerpadla 2",
    2: "Nízký tlak systému",
    3: "Nízký průtok",
    4: "Nedosažena žádaná teplota rozdělovače",
    5: "Nutné dopuštění systému",
}

# =============================================================================
# Katalog typů zařízení
# =============================================================================
DEVICE_TYPES = {
    "ahu":     {"label": "VZT jednotka",      "input": AHU_INPUT,     "holding": AHU_HOLDING,
                "states": AHU_STATES,     "alarms": AHU_ALARMS,
                "delays": AHU_ALARM_DELAYS},
    "chiller": {"label": "Chladicí jednotka", "input": CHILLER_INPUT, "holding": CHILLER_HOLDING,
                "states": CHILLER_STATES, "alarms": CHILLER_ALARMS,
                "delays": CHILLER_ALARM_DELAYS},
    "tower":   {"label": "Chladicí věž",      "input": TOWER_INPUT,   "holding": TOWER_HOLDING,
                "states": {}, "alarms": TOWER_ALARMS,
                "delays": TOWER_ALARM_DELAYS},
    "chw":     {"label": "Okruh chlazené vody","input": CHW_INPUT,    "holding": CHW_HOLDING,
                "states": {}, "alarms": CHW_ALARMS,
                "delays": CHW_ALARM_DELAYS},
    "boiler":  {"label": "Kotel",             "input": BOILER_INPUT,  "holding": BOILER_HOLDING,
                "states": BURNER_STATES,  "alarms": BOILER_ALARMS,
                "delays": BOILER_ALARM_DELAYS},
    "hw":      {"label": "Kotelna",           "input": HW_INPUT,      "holding": HW_HOLDING,
                "states": {}, "alarms": HW_ALARMS,
                "delays": HW_ALARM_DELAYS},
}


# =============================================================================
# Převod fyzikální hodnota <-> registry
# =============================================================================
def encode(value, reg):
    """Fyzikální hodnota -> seznam 16bitových slov (u32 big-endian word order)."""
    raw = int(round(value * reg["scale"]))
    if reg["words"] == 2:
        raw = max(0, min(0xFFFFFFFF, raw))
        return [(raw >> 16) & 0xFFFF, raw & 0xFFFF]
    raw = max(-32768, min(32767, raw))
    return [raw & 0xFFFF]


def decode(words, reg):
    """Seznam 16bitových slov -> fyzikální hodnota, se znaménkem u 16bit."""
    if reg["words"] == 2:
        raw = (words[0] << 16) | words[1]
        return raw / reg["scale"]
    raw = words[0]
    if raw > 32767:
        raw -= 65536
    return raw / reg["scale"]


def encode_all(data, regs):
    """dict {key: hodnota} -> souvislý blok registrů podle mapy."""
    out = []
    for reg in regs:
        out.extend(encode(data.get(reg["key"], 0.0), reg))
    return out


def decode_all(words, regs):
    """Souvislý blok registrů -> dict {key: hodnota}."""
    out, i = {}, 0
    for reg in regs:
        out[reg["key"]] = decode(words[i:i + reg["words"]], reg)
        i += reg["words"]
    return out


def active_bits(word, names):
    """Rozloží bitové slovo alarmů na {číslo bitu: text} aktivních alarmů."""
    w = int(word)
    return {bit: txt for bit, txt in sorted(names.items()) if w & (1 << bit)}


def bits(word, names):
    """Seznam textů aktivních alarmů."""
    return list(active_bits(word, names).values())
