"""
Rozhraní driveru zařízení.

Driver umí dvě věci:

    read_points()        přečte zařízení; vrátí (měřené hodnoty, žádané hodnoty)
                         jako slovníky {klíč: číslo}, nebo (None, None),
                         když zařízení neodpovídá
    write_point(k, v)    zapíše jednu žádanou hodnotu

Klíče jsou stejné bez ohledu na protokol — jsou to klíče z mapy veličin daného
typu zařízení (registers.DEVICE_TYPES). Co je pod tím, jestli Modbus registr
nebo BACnet objekt, řeší konkrétní driver a nikoho nad ním to nezajímá.

Proto se taky sémantika (názvy veličin, jednotky, meze žádaných hodnot, texty
alarmů) drží dál v registers.py a nekopíruje se do map protokolů: ty popisují
jen adresaci.
"""


class Driver:
    """Společný základ. Každý protokol si doplní čtení a zápis."""

    #: jak se protokol jmenuje ve výpisech a ve vizualizaci
    protocol = "?"

    def __init__(self, dev):
        self.dev = dev
        self.online = False

    async def read_points(self):
        """-> (měřené, žádané) nebo (None, None) při výpadku spojení."""
        raise NotImplementedError

    async def write_point(self, key, value):
        """Zapíše žádanou hodnotu. Vrací hodnotu, která se opravdu zapsala."""
        raise NotImplementedError

    async def close(self):
        """Uklidí spojení. Volá se při ukončení."""

    def __repr__(self):
        return f"<{self.protocol} {self.dev.id}>"


def for_device(dev):
    """
    Vyrobí driver podle toho, jaký protokol má zařízení v plant.py.

    Import se dělá až tady, aby se BAC0 natahovalo jen tehdy, když v závodě
    opravdu je nějaké BACnet zařízení.
    """
    protocol = getattr(dev, "protocol", "modbus")
    if protocol == "modbus":
        from .modbus import ModbusDriver
        return ModbusDriver(dev)
    if protocol == "bacnet":
        from .bacnet import BacnetDriver
        return BacnetDriver(dev)
    raise ValueError(f"{dev.id}: neznámý protokol {protocol!r}")
