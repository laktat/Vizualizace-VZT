"""
Vyhodnocení provozu zařízení — to, co dělá z monitoringu užitečný nástroj.

Kontroly nestojí na prahových hodnotách jedné naměřené hodnoty (to už umí sám
regulátor přes alarmy), ale na PRŮBĚHU několika veličin za sebou. Proto poznají
věci, které jedna hodnota neprozradí:

  * kdy se filtr zanese na mez výměny — z trendu tlakové ztráty
  * že topný ventil netěsní — topí se, i když regulace posílá zavřít
  * že se rekuperátor zanesl — měřená účinnost je pod projektovou
  * že je vzduchová cesta přiškrcená — průtok neodpovídá otáčkám
  * že jednotka nedosahuje žádané teploty a proč (došel výkon?)

ČASOVÁ OSA: predikce se počítá proti PROVOZNÍM HODINÁM zařízení, ne proti
kalendářnímu času. Filtr se zanáší chodem ventilátoru, ne tím, že plyne čas —
u jednotky, která jede jednu směnu, by kalendářní trend lhal dvojnásobně.
Provozní hodiny hlásí zařízení samo ve svém registru.
"""

import registers as regs

OK, WARN, BAD, WAIT = "ok", "warn", "bad", "wait"

MIN_SAMPLES = 30         # kolik vzorků musí být, aby mělo smysl něco počítat
MIN_HOURS_SPAN = 0.5     # a jak dlouhý úsek provozu musí pokrývat [h]


def _n(value, dec=1):
    """Číslo do textu po česku — s desetinnou čárkou."""
    return f"{value:.{dec}f}".replace(".", ",")


def _fit(xs, ys):
    """Přímka metodou nejmenších kvadrátů. Vrací (směrnice, konstanta) nebo None."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-9:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def _hours(value):
    """Provozní hodiny jako čitelný text — hodiny do dvou dnů, pak dny."""
    return f"{_n(value, 0)} h" if value < 48 else f"{_n(value / 24, 0)} dní"


def _continuous(hist):
    """
    Nejnovější úsek historie bez skoku v počítadle provozních hodin.

    Provozní hodiny mohou skočit zpět — po výměně regulátoru, po restartu
    zařízení nebo když se do historie připletou data z jiné session. Trend
    se pak musí počítat jen od toho skoku dál, jinak by proložená přímka
    neznamenala nic.
    """
    if not hist:
        return []
    cut = 0
    for i in range(len(hist) - 1, 0, -1):
        if hist[i].get("run_hours", 0.0) < hist[i - 1].get("run_hours", 0.0) - 0.01:
            cut = i
            break
    return hist[cut:]


def _running(hist, min_fan=5.0):
    """
    Vzorky, ve kterých jednotka opravdu jela — a jen z posledního
    souvislého úseku, aby se do vyhodnocení nepletl provoz zpřed skoku
    počítadla provozních hodin.
    """
    return [s for s in _continuous(hist) if s.get("fan_supply", 0) >= min_fan]


def _utilization(hist):
    """Jak velkou část sledovaného úseku jednotka běžela (0–1)."""
    hist = _continuous(hist)
    if not hist:
        return 0.0
    return len(_running(hist)) / len(hist)


# =============================================================================
# Jednotlivé kontroly VZT jednotky
# =============================================================================
def _sensors(values):
    """Hlásí některé čidlo hodnotu mimo fyzikální rozsah? (přerušený obvod, zkrat)"""
    bad = []
    for reg in regs.AHU_INPUT:
        lo, hi = reg["valid_min"], reg["valid_max"]
        if lo is None:
            continue
        v = values.get(reg["key"])
        if v is not None and not (lo <= v <= hi):
            bad.append((reg["name"], v, reg["unit"]))

    if not bad:
        return {"key": "cidla", "title": "Čidla", "level": OK,
                "msg": "Všechna čidla hlásí věrohodné hodnoty."}
    parts = ", ".join(f"{n} hlásí {_n(v, 0)} {u}" for n, v, u in bad)
    return {"key": "cidla", "title": "Čidla", "level": BAD,
            "msg": f"Vadné čidlo: {parts}.",
            "detail": "Hodnota je mimo fyzikální rozsah — typicky přerušený "
                      "nebo zkratovaný obvod. Regulace přešla na náhradní čidlo."}


def _filter(hist, values, setpoints, key, title):
    """
    Predikce zanesení filtru z trendu tlakové ztráty proti provozním hodinám.

    Kromě zbývajících provozních hodin dopočítá i kalendářní odhad podle toho,
    jak velkou část času jednotka v posledním úseku skutečně běžela — o to
    provozní technik nakonec stojí, protože podle toho plánuje výjezd.
    """
    limit = setpoints.get("dp_limit", 250.0)
    current = values.get(key)
    if current is None:
        return None

    run = _running(hist)
    xs = [s.get("run_hours", 0.0) for s in run]
    ys = [s.get(key, 0.0) for s in run]
    span = (max(xs) - min(xs)) if xs else 0.0

    base = {"key": key, "title": title,
            "chart": {"x": "run_hours", "y": key, "limit": limit}}

    if len(run) < MIN_SAMPLES or span < MIN_HOURS_SPAN:
        return {**base, "level": WAIT,
                "msg": f"{_n(current, 0)} Pa z {_n(limit, 0)} Pa · trend se ještě sbírá.",
                "detail": "Pro predikci je potřeba delší úsek provozu jednotky."}

    fit = _fit(xs, ys)
    if fit is None:
        return {**base, "level": WAIT, "msg": f"{_n(current, 0)} Pa z {_n(limit, 0)} Pa."}
    slope, intercept = fit
    base["chart"].update({"slope": slope, "intercept": intercept})

    if current >= limit:
        return {**base, "level": BAD,
                "msg": f"{_n(current, 0)} Pa — mez {_n(limit, 0)} Pa je překročená, vyměnit.",
                "detail": f"Zanáší se o {_n(slope, 2)} Pa na provozní hodinu."}

    if slope <= 0.005:
        return {**base, "level": OK,
                "msg": f"{_n(current, 0)} Pa z {_n(limit, 0)} Pa · tlaková ztráta neroste.",
                "detail": "Filtr se nezanáší — nejspíš je po výměně."}

    hours_left = (limit - current) / slope
    base["chart"]["hours_left"] = hours_left
    util = _utilization(hist)
    detail = (f"Zanáší se o {_n(slope, 2)} Pa na provozní hodinu. "
              f"Jednotka běžela {_n(util * 100, 0)} % sledovaného úseku")
    if util > 0.05:
        days = hours_left / (24.0 * util)
        detail += f", při stejném provozu to je asi {_n(days, 0)} kalendářních dní."
    else:
        detail += "."

    level = BAD if hours_left < 100 else (WARN if hours_left < 500 else OK)
    return {**base, "level": level,
            "msg": f"{_n(current, 0)} Pa z {_n(limit, 0)} Pa · mez za {_hours(hours_left)} provozu.",
            "detail": detail}


def _valve(hist, params):
    """
    Netěsnící nebo zaseklý topný ventil.

    Kontrola nestojí na tom, že by regulace musela poslat zavřít — porovnává
    ODEBRANÝ TOPNÝ VÝKON s tím, kolik ho poloha ventilu vůbec dovolí:

        topný výkon ≤ výkon ohřívače × povel na ventil

    Nerovnost platí vždy, protože výkon ohřívače je jeho horní mez a teplota
    topné vody ho může jen srazit, nikdy zvednout. Když jednotka topí víc,
    než povel dovoluje, teče přes ohřívač voda, která tam nemá co dělat —
    zaseklý pohon nebo netěsná klapka ventilu.
    """
    kw = params["heater_kw"]
    margin = max(0.06 * kw, 5.0)          # rezerva na dobíhající teplo a šum
    run = _running(hist)

    if len(run) < MIN_SAMPLES:
        return {"key": "topny_ventil", "title": "Topný ventil", "level": WAIT,
                "msg": "Jednotka neběží dost dlouho na vyhodnocení."}

    excess = [s for s in run
              if s.get("hw_power", 0) > kw * s.get("heat_cmd", 0) / 100.0 + margin]
    share = len(excess) / len(run)

    if share < 0.15:
        return {"key": "topny_ventil", "title": "Topný ventil", "level": OK,
                "msg": "Topný výkon odpovídá povelu na ventil — pohon reaguje správně."}

    extra = sum(s["hw_power"] - kw * s["heat_cmd"] / 100.0 for s in excess) / len(excess)
    cmd_avg = sum(s["heat_cmd"] for s in excess) / len(excess)
    implied = sum(s["hw_power"] for s in excess) / len(excess) / kw * 100.0
    closed = sum(1 for s in excess if s["heat_cmd"] < 1.0)

    detail = (f"Povel na ventil byl v průměru {_n(cmd_avg, 0)} %, topný výkon ale "
              f"odpovídá otevření aspoň {_n(implied, 0)} %. ")
    if closed:
        detail += (f"V {_n(closed / len(run) * 100, 0)} % času byl povel zavřít "
                   f"a jednotka přesto topila. ")
    detail += ("Ukazuje na zaseklý pohon nebo netěsnou klapku ventilu — jednotka "
               "pak topí a chladí proti sobě a platí se to dvakrát.")

    return {"key": "topny_ventil", "title": "Topný ventil",
            "level": BAD if share > 0.4 else WARN,
            "msg": f"Topí o {_n(extra, 0)} kW víc, než povel na ventil dovoluje — "
                   f"v {_n(share * 100, 0)} % sledovaného provozu.",
            "detail": detail}


def _recuperator(hist, params):
    """
    Účinnost rekuperace spočítaná z měřených teplot:

        účinnost = (za rekuperátorem − venkovní) / (odtah − venkovní)

    Počítá se jen ve vzorcích, kde je rekuperace naplno, jednotka jede a mezi
    halou a venkem je aspoň 6 K rozdílu — jinak se ve jmenovateli dělí šumem.
    Propad proti projektové účinnosti znamená zanesený výměník.
    """
    nominal = params["recup_eff"]
    usable = [s for s in _running(hist, 40.0)
              if s.get("recup_cmd", 0) > 90.0
              and abs(s.get("t_extract", 0) - s.get("t_outdoor", 0)) > 6.0]

    if len(usable) < MIN_SAMPLES:
        return {"key": "rekuperace", "title": "Rekuperace", "level": WAIT,
                "msg": "Účinnost nelze teď spočítat.",
                "detail": "Je potřeba provoz s plnou rekuperací a aspoň 6 K "
                          "rozdílu mezi halou a venkovní teplotou."}

    effs = [(s["t_after_recup"] - s["t_outdoor"]) / (s["t_extract"] - s["t_outdoor"])
            for s in usable]
    eff = sum(effs) / len(effs)
    drop = nominal - eff

    if drop < 0.06:
        return {"key": "rekuperace", "title": "Rekuperace", "level": OK,
                "msg": f"Účinnost {_n(eff * 100, 0)} % odpovídá projektu "
                       f"({_n(nominal * 100, 0)} %).",
                "detail": "Spočítáno z teplot venku, za rekuperátorem a v odtahu."}
    if drop < 0.15:
        level, msg = WARN, f"Účinnost {_n(eff * 100, 0)} % je pod projektovou " \
                           f"({_n(nominal * 100, 0)} %)."
    else:
        level, msg = BAD, f"Účinnost {_n(eff * 100, 0)} % proti projektovým " \
                          f"{_n(nominal * 100, 0)} % — výrazný propad."
    return {"key": "rekuperace", "title": "Rekuperace", "level": level, "msg": msg,
            "detail": "Spočítáno z teplot venku, za rekuperátorem a v odtahu. "
                      "Propad ukazuje na zanesený výměník nebo netěsný obtok — "
                      "chybějící teplo musí dodat ohřívač."}


def _airflow(hist, params):
    """
    Odpovídá průtok otáčkám ventilátoru?

    Přiškrcená cesta (zanesený filtr, přivřená klapka, ucpaná mřížka) protlačí
    při stejných otáčkách méně vzduchu. Porovnává se s projektovým průtokem.
    """
    nom = params["flow_nom"]
    usable = [s for s in _running(hist, 30.0) if s.get("flow_supply", 0) > 0]
    if len(usable) < MIN_SAMPLES:
        return None

    ratios = [s["flow_supply"] / (nom * s["fan_supply"] / 100.0) for s in usable]
    ratio = sum(ratios) / len(ratios)
    last = usable[-1]

    if ratio > 0.93:
        return {"key": "prutok", "title": "Vzduchová cesta", "level": OK,
                "msg": f"Průtok {_n(last['flow_supply'], 0)} m³/h odpovídá "
                       f"otáčkám {_n(last['fan_supply'], 0)} %."}
    return {"key": "prutok", "title": "Vzduchová cesta",
            "level": BAD if ratio < 0.85 else WARN,
            "msg": f"Průtok je o {_n((1 - ratio) * 100, 0)} % nižší, než otáčkám "
                   f"{_n(last['fan_supply'], 0)} % odpovídá.",
            "detail": f"Při {_n(last['fan_supply'], 0)} % otáček by mělo jít "
                      f"{_n(nom * last['fan_supply'] / 100, 0)} m³/h, jde "
                      f"{_n(last['flow_supply'], 0)} m³/h. Cesta je přiškrcená — "
                      f"nejčastěji zanesený filtr nebo přivřená klapka."}


def _room(hist, setpoints):
    """Drží jednotka žádanou teplotu, a když ne, došel jí výkon?"""
    sp = setpoints.get("sp_room")
    run = _running(hist)
    if sp is None or len(run) < MIN_SAMPLES:
        return {"key": "hala", "title": "Teplota v hale", "level": WAIT,
                "msg": "Jednotka neběží dost dlouho na vyhodnocení."}

    devs = [s["t_extract"] - sp for s in run]
    avg = sum(devs) / len(devs)
    last = run[-1]

    if abs(avg) <= 1.0:
        return {"key": "hala", "title": "Teplota v hale", "level": OK,
                "msg": f"Drží žádanou teplotu — {_n(last['t_extract'])} °C "
                       f"proti žádaným {_n(sp)} °C."}

    heat_sat = sum(1 for s in run if s.get("heat_cmd", 0) > 95) / len(run)
    cool_sat = sum(1 for s in run if s.get("cool_cmd", 0) > 95) / len(run)
    warm = avg > 0
    detail = ""
    if warm and cool_sat > 0.5:
        detail = (f"Chladicí ventil byl {_n(cool_sat * 100, 0)} % času na plno — "
                  f"jednotce došel chladicí výkon, ne regulace. Na návrhový den "
                  f"je to u výrobní haly běžné.")
    elif not warm and heat_sat > 0.5:
        detail = (f"Topný ventil byl {_n(heat_sat * 100, 0)} % času na plno — "
                  f"chybí topný výkon nebo je nízká teplota topné vody.")
    else:
        detail = ("Ventily nejsou na mezi, takže výkon je k dispozici — jde "
                  "spíš o nastavení regulace nebo pomalou reakci soustavy.")

    level = BAD if abs(avg) > 3.0 else WARN
    return {"key": "hala", "title": "Teplota v hale", "level": level,
            "msg": f"{'Teplejší' if warm else 'Chladnější'} o {_n(abs(avg))} K, "
                   f"než je žádaná hodnota ({_n(last['t_extract'])} proti {_n(sp)} °C).",
            "detail": detail}


# =============================================================================
def diagnose(dev, hist, values, setpoints):
    """
    Vyhodnotí jedno zařízení. Vrací seznam zjištění, nejzávažnější první.

    hist      = seznam vzorků {klíč: hodnota} v čase, nejstarší první
    values    = poslední měření
    setpoints = žádané hodnoty přečtené ze zařízení
    """
    if dev.type != "ahu":
        return []

    checks = [
        _sensors(values),
        _valve(hist, dev.params),
        _filter(hist, values, setpoints, "filter_dp_sup", "Filtr přívodu"),
        _filter(hist, values, setpoints, "filter_dp_ext", "Filtr odtahu"),
        _airflow(hist, dev.params),
        _recuperator(hist, dev.params),
        _room(hist, setpoints),
    ]
    order = {BAD: 0, WARN: 1, WAIT: 2, OK: 3}
    return sorted([c for c in checks if c], key=lambda c: order[c["level"]])
