"""
Mapa bodů VZT3 pro BACnet/IP.

Stejná filozofie jako registers.py: jedno místo, kde je popsané, kde co leží.
Rozdíl je jen v tom, co je "adresa" — u Modbusu číslo registru, tady typ
objektu a číslo instance.

CO TADY NENÍ: názvy veličin, jednotky pro vizualizaci, meze žádaných hodnot
ani texty alarmů. To všechno zůstává v registers.py, protože to popisuje
ZAŘÍZENÍ, ne protokol. VZT3 je pořád typ "ahu" a dispečink o ní ví přesně
totéž co o VZT1 a VZT2 — jen si pro to chodí jinam. Kdyby se metadata
kopírovala sem, dřív nebo později by se obě kopie rozešly.

Body se odvozují ze stejných map jako Modbus, takže se klíče nemůžou rozejít:
přidat veličinu do registers.AHU_INPUT znamená, že ji BACnet dostane taky.

OBJEKTOVÝ MODEL
    Analog Input   měřené hodnoty (jen ke čtení)
    Analog Value   žádané hodnoty (dispečink do nich zapisuje)
    Binary Value   jednotlivé bity slova alarmů

Slovo alarmů se po BACnetu neposílá jako číslo — to by byl Modbus zabalený do
BACnetu. Každý bit je samostatný Binary Value, jak je v BACnetu zvykem, a
driver z nich zpátky složí to bitové slovo, které čeká zbytek dispečinku.
"""

import registers as regs

# Jednotky: z popisu v registers.py na výčet, kterému rozumí BACnet.
UNITS = {
    "°C": "degreesCelsius",
    "%": "percent",
    "Pa": "pascals",
    "kW": "kilowatts",
    "kWh": "kilowattHours",
    "m³/h": "cubicMetersPerHour",
    "A": "amperes",
    "h": "hours",
    "": "noUnits",
}

#: klíč, pod kterým se posílá slovo alarmů (skládá se z Binary Values)
ALARM_KEY = "alarms"


def _analog(specs, start=1):
    """Přidělí objektům čísla instancí v pořadí, v jakém jsou v mapě veličin."""
    out = []
    for i, reg in enumerate(specs, start=start):
        out.append({
            "key": reg["key"],
            "instance": i,
            "name": reg["key"],
            "description": reg["name"],
            "units": UNITS.get(reg["unit"], "noUnits"),
        })
    return out


# --- měřené hodnoty: Analog Input -------------------------------------------
# slovo alarmů se přeskakuje, to jde jako Binary Value po bitech
ANALOG_INPUTS = _analog([r for r in regs.AHU_INPUT if r["key"] != ALARM_KEY])

# --- žádané hodnoty: Analog Value -------------------------------------------
ANALOG_VALUES = _analog(regs.AHU_HOLDING)

# --- alarmy: Binary Value, jeden na bit -------------------------------------
BINARY_VALUES = [
    {"key": f"alarm{bit}", "bit": bit, "instance": i, "name": f"alarm{bit}",
     "description": text}
    for i, (bit, text) in enumerate(sorted(regs.AHU_ALARMS.items()), start=1)
]

ANALOG_INPUT_BY_KEY = {p["key"]: p for p in ANALOG_INPUTS}
ANALOG_VALUE_BY_KEY = {p["key"]: p for p in ANALOG_VALUES}


def count():
    """Kolik bodů se musí přečíst na jeden odečet."""
    return len(ANALOG_INPUTS) + len(ANALOG_VALUES) + len(BINARY_VALUES)


if __name__ == "__main__":
    print(f"VZT3 přes BACnet/IP — {count()} bodů\n")
    for label, points, kind in (("Analog Input (měřené)", ANALOG_INPUTS, "AI"),
                                ("Analog Value (žádané)", ANALOG_VALUES, "AV"),
                                ("Binary Value (alarmy)", BINARY_VALUES, "BV")):
        print(f"{label}:")
        for p in points:
            unit = p.get("units", "")
            print(f"  {kind}{p['instance']:<3} {p['key']:16} {p['description'][:40]:42}"
                  f" {unit}")
        print()
