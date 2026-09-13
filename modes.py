"""
Provozní režimy — zimní a letní nastavení celého závodu na jedno kliknutí.

Přepnout závod mezi zimou a létem znamená přenastavit desítky žádaných
hodnot: teploty v halách, chlazenou vodu, povolení chillerů, ekvitermní
křivku, věž. Obcházet kvůli tomu jedenáct obrazovek je práce pro nic a
snadno se na něco zapomene — typicky zůstane povolený chiller přes zimu.

Režim je proto uložená sada žádaných hodnot. Uživatel si obě sady jednou
nastaví a pak už jen překlikává.

CO JE V REŽIMU: všechny žádané hodnoty kromě povelů. Kvitování poruchy a
zkušební porucha do režimu nepatří — to nejsou nastavení, ale úkony.

Profily se skládají z výchozích hodnot v registers.py a sezónních odchylek
níže. Celá sada se tak nemusí vypisovat dvakrát a když se do mapy registrů
přidá nová žádaná hodnota, objeví se v obou režimech sama.

Ruční úpravy se ukládají do modes.json vedle kódu.
"""

import json
from pathlib import Path

import plant
import registers as regs

STORE = Path(__file__).parent / "modes.json"

#: klíče, které do režimu nepatří — jsou to povely, ne nastavení
COMMANDS = {"reset", "fault_sim"}

MODES = {"zima": "Zimní provoz", "leto": "Letní provoz"}

# Čím se zima liší od léta. Klíč je typ zařízení, uvnitř jen to, co se
# od výchozí hodnoty v registers.py mění.
OVERRIDES = {
    "zima": {
        "ahu": {"sp_room": 21.5, "sp_supply_min": 18.0, "sp_supply_max": 35.0},
        "chiller": {"enable": 0.0},
        "tower": {"enable": 0.0},
        "chw": {"enable": 0.0, "sp_supply": 10.0},
        "boiler": {"enable": 1.0},
        "hw": {"enable": 1.0, "curve_shift": 0.0, "summer_limit": 16.0},
    },
    "leto": {
        "ahu": {"sp_room": 24.0, "sp_supply_min": 16.0, "sp_supply_max": 28.0},
        "chiller": {"enable": 1.0},
        "tower": {"enable": 1.0},
        "chw": {"enable": 1.0, "sp_supply": 6.0},
        "boiler": {"enable": 0.0},
        "hw": {"enable": 0.0, "summer_limit": 18.0},
    },
}


def editable(device_type):
    """Žádané hodnoty daného typu, které se dají v režimu nastavit."""
    return [h for h in regs.DEVICE_TYPES[device_type]["holding"]
            if h["key"] not in COMMANDS]


def default_profile(mode):
    """Sestaví profil z výchozích hodnot registrů a sezónních odchylek."""
    over = OVERRIDES.get(mode, {})
    profile = {}
    for dev in plant.DEVICES:
        values = {h["key"]: float(h["default"]) for h in editable(dev.type)}
        values.update({k: float(v) for k, v in over.get(dev.type, {}).items()
                       if k in values})
        profile[dev.id] = values
    return profile


def load():
    """
    Načte uložené režimy. Co v souboru chybí, doplní se z výchozích —
    přidaná žádaná hodnota se tak objeví i ve starém uloženém profilu.
    """
    stored = {}
    if STORE.exists():
        try:
            stored = json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            stored = {}

    profiles = {}
    for mode in MODES:
        base = default_profile(mode)
        saved = (stored.get("profiles") or {}).get(mode, {})
        for dev_id, values in base.items():
            values.update({k: float(v) for k, v in (saved.get(dev_id) or {}).items()
                           if k in values})
        profiles[mode] = base
    return {"profiles": profiles, "active": stored.get("active")}


def save(profiles, active=None):
    STORE.write_text(json.dumps({"profiles": profiles, "active": active},
                                ensure_ascii=False, indent=2), encoding="utf-8")


def describe():
    """Popis režimů pro vizualizaci: co se dá nastavovat a v jakých mezích."""
    out = {}
    for dev in plant.DEVICES:
        out[dev.id] = [{"key": h["key"], "name": h["name"], "unit": h["unit"],
                        "min": h["min"], "max": h["max"],
                        "step": 1 if h["scale"] == 1 else 0.5}
                       for h in editable(dev.type)]
    return out


if __name__ == "__main__":
    data = load()
    for mode, label in MODES.items():
        n = sum(len(v) for v in data["profiles"][mode].values())
        print(f"{label}: {n} žádaných hodnot")
    print(f"\naktivní režim: {data['active'] or '(zatím nepřepnuto)'}")
    print("\nrozdíly mezi zimou a létem:")
    z, l = data["profiles"]["zima"], data["profiles"]["leto"]
    for dev in plant.DEVICES:
        for key in z[dev.id]:
            if z[dev.id][key] != l[dev.id][key]:
                print(f"  {dev.id:8} {key:14} zima {z[dev.id][key]:7.1f}"
                      f"   léto {l[dev.id][key]:7.1f}")
