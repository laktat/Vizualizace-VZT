"""
Energetická bilance závodu — kolik se spotřebuje, kde a co to stojí.

Počítá se ze stavů podružného měření, které hlásí zařízení ve svých
registrech (elektroměry ventilátorů a kompresorů, plynoměr kotlů,
kalorimetry tepla a chladu). Modul z nich udělá tři věci, které chce
provozní vidět:

  1) KDE se energie spotřebovává — rozpad po zařízeních, ne jedno číslo
     za celý závod. Bez rozpadu se nedá nic zlepšit.
  2) MĚRNÉ UKAZATELE — chladicí faktor strojovny, účinnost kotelny,
     a hlavně cena vyrobené kilowatthodiny tepla a chladu. Tohle je to
     číslo, se kterým se dá porovnávat měsíc proti měsíci.
  3) CO ZBYTEČNĚ UTÍKÁ — kolik ušetřila rekuperace a kolik se naopak
     zmařilo, když jednotka topila a chladila proti sobě.

Ceny energií jsou v plant.TARIFFS.
"""

import plant

CHILLERS = ("chl1", "chl2", "chl3")
BOILERS = ("kotel1", "kotel2")
AHUS = ("vzt1", "vzt2", "vzt3")


def _v(values, dev_id, key, default=0.0):
    d = values.get(dev_id)
    return default if not d else d.get(key, default)


def _sum(values, devices, key):
    return sum(_v(values, d, key) for d in devices)


def _div(a, b):
    return a / b if b > 1e-9 else None


def _installed(devices, key):
    """Instalovaný výkon dané skupiny zařízení [kW]."""
    return sum(plant.DEVICES_BY_ID[d].params.get(key, 0.0) for d in devices
               if d in plant.DEVICES_BY_ID)


def _item(name, area, power_kw, energy_kwh, price):
    return {"name": name, "area": area, "power_kw": power_kw,
            "energy_kwh": energy_kwh, "cost": energy_kwh * price, "share": 0.0}


def _shares(items):
    total = sum(i["energy_kwh"] for i in items)
    for i in items:
        i["share"] = (i["energy_kwh"] / total * 100.0) if total > 1e-9 else 0.0
    return sorted(items, key=lambda i: -i["energy_kwh"])


def summary(values):
    """
    values = {device_id: {klíč: hodnota}} — poslední odečet ze všech zařízení.
    Vrací strukturu pro energetickou obrazovku dispečinku.
    """
    t = plant.TARIFFS
    p_el, p_gas, p_water = (t["electricity"]["price"], t["gas"]["price"],
                            t["water"]["price"])
    names = {d.id: d.name for d in plant.DEVICES}
    uptime = max((_v(values, d.id, "uptime") for d in plant.DEVICES), default=0.0)

    # --- kde se spotřebovává elektřina -------------------------------------
    el_items = []
    for dev_id in AHUS:
        el_items.append(_item(names.get(dev_id, dev_id), "Vzduchotechnika",
                              _v(values, dev_id, "power"),
                              _v(values, dev_id, "el_energy"), p_el))
    for dev_id in CHILLERS:
        el_items.append(_item(names.get(dev_id, dev_id), "Výroba chladu",
                              _v(values, dev_id, "power"),
                              _v(values, dev_id, "el_energy"), p_el))
    el_items.append(_item("Chladicí věž", "Výroba chladu",
                          _v(values, "vez", "power"),
                          _v(values, "vez", "el_energy"), p_el))
    el_items.append(_item("Čerpadla chlazené vody", "Výroba chladu",
                          sum(_v(values, "chw", f"{p}_power")
                              for p in ("p1", "p2", "s1", "s2")),
                          _v(values, "chw", "el_energy"), p_el))
    el_items.append(_item("Oběhová čerpadla kotelny", "Kotelna",
                          sum(_v(values, "kotelna", f"{p}_power")
                              for p in ("hp1", "hp2")),
                          _v(values, "kotelna", "el_energy"), p_el))
    el_items = _shares(el_items)

    el_energy = sum(i["energy_kwh"] for i in el_items)
    el_power = sum(i["power_kw"] for i in el_items)

    # --- plyn ----------------------------------------------------------------
    gas_items = _shares([
        _item(names.get(b, b), "Kotelna",
              _v(values, b, "gas_flow") * t["gas_lhv"],
              _v(values, b, "gas_energy"), p_gas)
        for b in BOILERS
    ])
    gas_energy = sum(i["energy_kwh"] for i in gas_items)
    gas_power = sum(i["power_kw"] for i in gas_items)
    gas_m3 = _sum(values, BOILERS, "gas_total")

    # --- voda do chladicí věže ----------------------------------------------
    water_m3 = _v(values, "vez", "makeup_total")

    # --- co se z toho vyrobilo ----------------------------------------------
    cool_made = _sum(values, CHILLERS, "cool_energy")
    heat_made = _sum(values, BOILERS, "heat_energy")
    cool_plant_el = (_sum(values, CHILLERS, "el_energy")
                     + _v(values, "vez", "el_energy")
                     + _v(values, "chw", "el_energy"))
    heat_plant_cost = (gas_energy * p_gas
                       + _v(values, "kotelna", "el_energy") * p_el)

    # Měrný ukazatel má smysl teprve tehdy, když je z čeho ho počítat.
    # Podíl spočítaný z pár kilowatthodin je sice aritmeticky správně, ale
    # svádí ke špatnému závěru: v zimě, kdy se chlad skoro nevyrábí, vyjde
    # chladicí faktor strojovny hluboko pod jedničku jen kvůli tomu, že
    # čerpadla chvíli po startu běžela. Prahem je desetina hodiny při plném
    # instalovaném výkonu — pod ním se místo čísla ukáže vysvětlení.
    min_cool = 0.1 * _installed(CHILLERS, "capacity_kw")
    min_heat = 0.1 * _installed(BOILERS, "power_kw")
    cool_ready, heat_ready = cool_made >= min_cool, heat_made >= min_heat

    cop_chillers = (_div(cool_made, _sum(values, CHILLERS, "el_energy"))
                    if cool_ready else None)
    cop_plant = _div(cool_made, cool_plant_el) if cool_ready else None
    boiler_eff = _div(heat_made, gas_energy) if heat_ready else None
    cool_price = _div(cool_plant_el * p_el, cool_made) if cool_ready else None
    heat_price = _div(heat_plant_cost, heat_made) if heat_ready else None

    too_little_cool = (f"zatím se vyrobilo jen {cool_made:.0f} kWh chladu — "
                       f"na měrný ukazatel je to málo")
    too_little_heat = (f"zatím se dodalo jen {heat_made:.0f} kWh tepla — "
                       f"na měrný ukazatel je to málo")

    # --- rekuperace a zmařená energie ---------------------------------------
    recup = _sum(values, AHUS, "recup_energy")
    ahu_heat = _sum(values, AHUS, "heat_energy")
    ahu_cool = _sum(values, AHUS, "cool_energy")
    waste = _sum(values, AHUS, "waste_energy")

    # Rekuperované teplo by jinak musel dodat ohřívač, proto se cení cenou
    # tepla z kotelny. Když ještě není z čeho cenu spočítat, sáhne se po
    # ceně plynu a po elektřině při běžném chladicím faktoru.
    unit_heat = heat_price if heat_price else p_gas
    unit_cool = cool_price if cool_price else p_el / 3.0

    savings = {
        "energy_kwh": recup,
        "cost": recup * unit_heat,
        "share": (recup / (recup + ahu_heat + ahu_cool) * 100.0
                  if recup + ahu_heat + ahu_cool > 1e-9 else 0.0),
    }
    # Zmařené teplo se platí dvakrát: nejdřív se vyrobí, pak ho musí
    # chladič odebrat.
    wasted = {
        "energy_kwh": waste,
        "cost": waste * (unit_heat + unit_cool),
        "per_day_cost": (waste * (unit_heat + unit_cool) / uptime * 24.0
                         if uptime > 0.1 else 0.0),
    }

    # --- měrný příkon ventilátorů (SFP) --------------------------------------
    sfp = []
    for dev_id in AHUS:
        flow = _v(values, dev_id, "flow_supply")
        power = _v(values, dev_id, "power")
        if flow > 100:
            sfp.append({"device": dev_id, "name": names.get(dev_id, dev_id),
                        "value": power / (flow / 3600.0)})

    total_cost = el_energy * p_el + gas_energy * p_gas + water_m3 * p_water
    per_day = (lambda x: x / uptime * 24.0 if uptime > 0.1 else 0.0)

    return {
        "uptime_h": uptime,
        "currency": t["currency"],
        "electricity": {
            "power_kw": el_power, "energy_kwh": el_energy,
            "cost": el_energy * p_el, "price": p_el,
            "per_day_kwh": per_day(el_energy), "items": el_items,
        },
        "gas": {
            "power_kw": gas_power, "energy_kwh": gas_energy, "volume_m3": gas_m3,
            "cost": gas_energy * p_gas, "price": p_gas,
            "per_day_kwh": per_day(gas_energy), "items": gas_items,
        },
        "water": {
            "volume_m3": water_m3, "cost": water_m3 * p_water, "price": p_water,
            "per_day_m3": per_day(water_m3),
        },
        "total": {"cost": total_cost, "per_day_cost": per_day(total_cost)},
        "produced": {
            "cool_kwh": cool_made, "heat_kwh": heat_made,
            "cool_price": cool_price, "heat_price": heat_price,
        },
        "kpi": [
            {"key": "cop_chillers", "name": "Chladicí faktor chillerů",
             "value": cop_chillers, "dec": 2, "unit": "",
             "note": ("vyrobený chlad na kilowatthodinu elektřiny kompresorů"
                      if cool_ready else too_little_cool)},
            {"key": "cop_plant", "name": "Chladicí faktor strojovny",
             "value": cop_plant, "dec": 2, "unit": "",
             "note": ("včetně chladicí věže a oběhových čerpadel — tohle platíte"
                      if cool_ready else too_little_cool)},
            {"key": "boiler_eff", "name": "Účinnost kotelny",
             "value": boiler_eff * 100.0 if boiler_eff else None, "dec": 1,
             "unit": "%",
             "note": ("vyrobené teplo z energie ve spáleném plynu"
                      if heat_ready else too_little_heat)},
            {"key": "cool_price", "name": "Cena chladu",
             "value": cool_price, "dec": 2, "unit": f"{t['currency']}/kWh",
             "note": ("elektřina celé strojovny chlazení na kWh chladu"
                      if cool_ready else too_little_cool)},
            {"key": "heat_price", "name": "Cena tepla",
             "value": heat_price, "dec": 2, "unit": f"{t['currency']}/kWh",
             "note": ("plyn a elektřina čerpadel na kWh dodaného tepla"
                      if heat_ready else too_little_heat)},
            {"key": "recup_share", "name": "Podíl rekuperace",
             "value": savings["share"], "dec": 0, "unit": "%",
             "note": "kolik z práce výměníků zastal rekuperátor zdarma"},
        ],
        "savings": savings,
        "waste": wasted,
        "sfp": sfp,
    }
