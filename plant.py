"""
Soupis zařízení smyšleného závodu — jediné místo, kde je popsané,
co v závodě stojí a na jaké Modbus adrese to poslouchá.

Každé zařízení má v reálu vlastní regulátor s vlastní IP adresou, proto tady
každé dostane vlastní TCP port. Poller i vizualizace jen procházejí tenhle
seznam; přidat zařízení = přidat řádek sem a modul do sim/.

Rozvržení portů:
    502x  vzduchotechnika
    503x  chlazení (chillery, věž, okruh chlazené vody)
    505x  kotelna (kotle, okruh topné vody)
"""

HOST = "127.0.0.1"


class Device:
    def __init__(self, id, name, type, port, area, params=None, unit_id=1):
        self.id = id            # krátký identifikátor, klíč v databázi
        self.name = name        # jak se jmenuje ve vizualizaci
        self.type = type        # typ z registers.DEVICE_TYPES
        self.port = port        # Modbus TCP port
        self.unit_id = unit_id  # Modbus device/slave id
        self.area = area        # provozní celek pro přehledovou obrazovku
        self.params = params or {}   # parametry pro simulaci (velikost, zátěž)

    def __repr__(self):
        return f"<{self.id} {self.type} :{self.port}>"


DEVICES = [
    # --- vzduchotechnika -----------------------------------------------------
    Device("vzt1", "VZT 1 — Výrobní hala A", "ahu", 5021, "vzt", {
        "flow_nom": 45000,      # jmenovitý průtok [m³/h]
        "room_volume": 42000,   # objem obsluhovaného prostoru [m³]
        "internal_gain": 110,   # vnitřní tepelné zisky (stroje, lidé) [kW]
        "heater_kw": 320,       # výkon vodního ohřívače [kW]
        "cooler_kw": 200,       # výkon vodního chladiče [kW]
        "recup_eff": 0.72,
        "filter_wear": 0.30,    # zanášení filtru [Pa/h při jmenovitém průtoku]
    }),
    Device("vzt2", "VZT 2 — Lakovna", "ahu", 5022, "vzt", {
        "flow_nom": 16000,
        "room_volume": 18000,
        "internal_gain": 25,
        "heater_kw": 180,       # lakovna jede na 100% čerstvém vzduchu -> velký ohřívač
        "cooler_kw": 80,
        "recup_eff": 0.45,      # nízká rekuperace, odtah je znečištěný
        "filter_wear": 0.85,    # filtry se zanášejí rychle (přestřik barvy)
    }),
    Device("vzt3", "VZT 3 — Sklad a administrativa", "ahu", 5023, "vzt", {
        "flow_nom": 12000,
        "room_volume": 16000,
        "internal_gain": 20,
        "heater_kw": 110,
        "cooler_kw": 60,
        "recup_eff": 0.78,
        "filter_wear": 0.18,
    }),

    # --- výroba chladu -------------------------------------------------------
    Device("chl1", "Chiller 1", "chiller", 5031, "chlazeni", {
        "capacity_kw": 250, "flow_nom": 43, "comp_kw": 35,
    }),
    Device("chl2", "Chiller 2", "chiller", 5032, "chlazeni", {
        "capacity_kw": 250, "flow_nom": 43, "comp_kw": 35,
    }),
    Device("chl3", "Chiller 3", "chiller", 5033, "chlazeni", {
        "capacity_kw": 150, "flow_nom": 26, "comp_kw": 22,
    }),
    Device("vez", "Chladicí věž", "tower", 5034, "chlazeni", {
        "reject_kw": 900,       # jmenovitý odvedený výkon [kW]
        "flow_nom": 140,        # jmenovitý průtok kondenzátorové vody [m³/h]
        "basin_m3": 12,
    }),
    Device("chw", "Okruh chlazené vody", "chw", 5035, "chlazeni", {
        "prim_flow_nom": 112,   # součet jmenovitých průtoků chillerů [m³/h]
        "sec_flow_nom": 60,     # průtok při plné zátěži spotřebičů
        "volume_m3": 18,        # objem vody v okruhu (setrvačnost) [m³]
    }),

    # --- kotelna -------------------------------------------------------------
    Device("kotel1", "Kotel 1", "boiler", 5051, "kotelna", {
        "power_kw": 400, "min_mod": 0.25, "flow_nom": 17,
    }),
    Device("kotel2", "Kotel 2", "boiler", 5052, "kotelna", {
        "power_kw": 400, "min_mod": 0.25, "flow_nom": 17,
    }),
    Device("kotelna", "Okruh topné vody", "hw", 5053, "kotelna", {
        "flow_nom": 30,
        "volume_m3": 9,
    }),
]

DEVICES_BY_ID = {d.id: d for d in DEVICES}

AREAS = {
    "vzt":      "Vzduchotechnika",
    "chlazeni": "Výroba chladu",
    "kotelna":  "Kotelna",
}


def by_type(type):
    return [d for d in DEVICES if d.type == type]


if __name__ == "__main__":
    for area, label in AREAS.items():
        print(f"\n{label}")
        for d in DEVICES:
            if d.area == area:
                print(f"  {d.id:8} {d.name:32} {d.type:8} {HOST}:{d.port}")
