"""
Mapa registrů jednotky.

Tohle je jediné místo, kde je popsané, co který registr znamená.
U reálné jednotky sem přepíšeš adresy z dokumentace výrobce
(Sauter, Domat, Regin, ...) a zbytek kódu zůstane stejný.

scale     = kolikrát je hodnota v registru zvětšená proti fyzikální
            (teplota 21.5 °C se v registru posílá jako 215, scale = 10)
valid_min / valid_max = mez pro rozpoznání ROZBITÉHO čidla, ne provozní rozsah.
            Kontrola má chytit jen přerušený nebo zkratovaný obvod (typicky
            -120 °C nebo přepálené stovky stupňů), NE neobvyklou, ale reálnou
            hodnotu. Uvnitř jednotky (za ohřívačem) klidně bývá 80 °C — to je
            normální provoz. Proto jsou meze u teplot široké a spustí se až na
            nesmyslech (pod -50 °C nebo nad 120 °C).
"""

# Input registers (funkce 4) — jen ke čtení, měřené hodnoty.
# Poradi sleduje cestu vzduchu jednotkou:
# venku -> za rekuperatorem -> za topnym ventilem (privod) -> odtah
INPUT_REGISTERS = [
    {"addr": 0, "key": "t_outdoor",     "name": "Teplota venkovní (sání)",      "unit": "°C", "scale": 10, "valid_min": -50, "valid_max": 120},
    {"addr": 1, "key": "t_after_recup", "name": "Teplota za rekuperátorem",     "unit": "°C", "scale": 10, "valid_min": -50, "valid_max": 120},
    {"addr": 2, "key": "t_supply",      "name": "Teplota za ohřívačem (přívod)","unit": "°C", "scale": 10, "valid_min": -50, "valid_max": 120},
    {"addr": 3, "key": "t_extract",     "name": "Teplota odtahu (místnost)",    "unit": "°C", "scale": 10, "valid_min": -50, "valid_max": 120},
    {"addr": 4, "key": "valve_cmd",     "name": "Povel na topný ventil",        "unit": "%",  "scale": 10, "valid_min": 0,   "valid_max": 100},
    {"addr": 5, "key": "filter_dp",     "name": "Tlaková ztráta filtru",        "unit": "Pa", "scale": 1,  "valid_min": 0,   "valid_max": 2000},
    {"addr": 6, "key": "fan_supply",    "name": "Otáčky přívodního ventil.",    "unit": "%",  "scale": 10, "valid_min": 0,   "valid_max": 100},
    {"addr": 7, "key": "current",       "name": "Proud motoru",                 "unit": "A",  "scale": 100,"valid_min": 0,   "valid_max": 50},
    {"addr": 8, "key": "run_hours",     "name": "Provozní hodiny",              "unit": "h",  "scale": 1,  "valid_min": 0,   "valid_max": 200000},
]

# Holding registers (funkce 3) — nastaveni, da se cist i zapisovat.
# Dashboard do nich zapisuje pres Modbus, simulator je cte kazdy krok.
HOLDING_REGISTERS = [
    {"addr": 0, "key": "sp_room",   "name": "Žádaná teplota místnosti", "unit": "°C", "scale": 10, "default": 22.0, "min": 18.0, "max": 26.0},
    {"addr": 1, "key": "sp_fan",    "name": "Žádané otáčky ventilátorů","unit": "%",  "scale": 10, "default": 78.0, "min": 40.0, "max": 100.0},
    {"addr": 2, "key": "dp_limit",  "name": "Mez tlak. ztráty filtru",  "unit": "Pa", "scale": 1,  "default": 250.0,"min": 150.0,"max": 400.0},
]

HOLDING_BY_KEY = {r["key"]: r for r in HOLDING_REGISTERS}


def encode(value, reg):
    """Fyzikalni hodnota -> registr (uint16), podle scale."""
    raw = int(round(value * reg["scale"]))
    return max(-32768, min(32767, raw)) & 0xFFFF


def decode(raw, reg):
    """Registr (uint16) -> fyzikalni hodnota, se znamenkem."""
    if raw > 32767:
        raw -= 65536
    return raw / reg["scale"]
