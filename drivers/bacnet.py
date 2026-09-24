"""
Driver pro BACnet/IP (klient přes knihovnu BAC0).

Čte se PŘÍMOU ADRESOU, tedy "127.0.0.1:47809 analogInput 3 presentValue".
Who-Is / discovery se schválně nepoužívá: na loopbacku s nestandardními
porty se broadcast chová nespolehlivě a stejně by jen zjistil to, co už
stojí v plant.py.

Tři věci, které BACnet dělá jinak než Modbus a driver je schová:

  * presentValue se vrací jako float32, takže 18,4 přijde jako
    18.399999618530273. Zaokrouhluje se, ať v archivu a v trendech nejsou
    ohavná čísla.
  * Binární hodnoty chodí jako výčtový typ, ne 0/1. Porovnává se přes str().
  * Slovo alarmů se z jednotlivých Binary Values skládá zpátky na číslo,
    protože zbytek dispečinku (alarmlog, vizualizace) čeká bitovou masku.

Klient je sám o sobě BACnet zařízení a potřebuje vlastní UDP port. Procesů,
které čtou závod, je ale víc — dispečink a poller běží vedle sebe — a na
jednom portu se dvě BACnet aplikace nepotkají. Každý proces si proto bere
první volný port od plant.BACNET_CLIENT_PORT výš a podle něj si odvodí i
číslo svého zařízení, aby se dvě instance nehlásily stejným ID.
"""

import asyncio
import logging

import bacnet_points as points
import plant

from .base import Driver

_client = None          # jeden klient pro celý proces, sdílený všemi drivery
_client_lock = asyncio.Lock()


def _quiet():
    for name in ("BAC0", "bacpypes3", "BAC0.core", "BAC0.tasks"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


PORT_TRIES = 10           # kolik portů se zkusí, než to vzdáme


async def _get_client():
    """
    Vytvoří klienta při prvním použití. Jeden na proces.

    Port se hledá: první volný od plant.BACNET_CLIENT_PORT výš. Dispečink
    a poller tak můžou běžet vedle sebe, aniž by si musely porty rozdělovat
    ručně v konfiguraci.
    """
    global _client
    async with _client_lock:
        if _client is not None:
            return _client
        _quiet()
        import BAC0
        from BAC0.core.io.IOExceptions import InitializationError

        last = None
        for offset in range(PORT_TRIES):
            port = plant.BACNET_CLIENT_PORT + offset
            try:
                _client = BAC0.lite(ip=f"{plant.HOST}/32", port=port,
                                    deviceId=plant.BACNET_CLIENT_ID + offset,
                                    localObjName=f"dispecink{offset or ''}")
            except (InitializationError, OSError) as exc:
                last = exc
                continue
            await asyncio.sleep(1.0)      # ať se stack rozběhne
            return _client
        raise RuntimeError(
            f"BACnet klient se nemá kam připojit — porty "
            f"{plant.BACNET_CLIENT_PORT}–{plant.BACNET_CLIENT_PORT + PORT_TRIES - 1} "
            f"jsou obsazené ({last})")


class BacnetDriver(Driver):
    protocol = "BACnet/IP"

    def __init__(self, dev):
        super().__init__(dev)
        self.address = f"{plant.HOST}:{dev.port}"

    async def _read(self, client, kind, instance):
        return await client.read(f"{self.address} {kind} {instance} presentValue")

    async def read_points(self):
        client = await _get_client()
        try:
            values = {}
            for p in points.ANALOG_INPUTS:
                raw = await self._read(client, "analogInput", p["instance"])
                values[p["key"]] = round(float(raw), 3)

            # slovo alarmů se poskládá z jednotlivých binárních bodů
            word = 0
            for p in points.BINARY_VALUES:
                raw = await self._read(client, "binaryValue", p["instance"])
                if str(raw) == "active":
                    word |= 1 << p["bit"]
            values[points.ALARM_KEY] = float(word)

            setpoints = {}
            for p in points.ANALOG_VALUES:
                raw = await self._read(client, "analogValue", p["instance"])
                setpoints[p["key"]] = round(float(raw), 3)
        except Exception:
            self.online = False
            return None, None
        self.online = True
        return values, setpoints

    async def write_point(self, key, value):
        point = points.ANALOG_VALUE_BY_KEY.get(key)
        if point is None:
            raise ValueError(f"{self.dev.id} nemá bod {key}")
        import registers as regs
        reg = regs.by_key(regs.DEVICE_TYPES[self.dev.type]["holding"])[key]
        value = max(reg["min"], min(reg["max"], float(value)))

        client = await _get_client()
        # Veřejné write() je "vystřel a zapomeň" — chybu jen zaloguje a volající
        # se nedozví nic. Dispečink ale musí operátorovi umět říct, že zápis
        # neprošel, proto se volá vnitřní korutina, která vrací výsledek.
        await client._write(
            f"{self.address} analogValue {point['instance']} presentValue {value}")
        return value

    async def close(self):
        global _client
        if _client is not None:
            try:
                _client.disconnect()
            except Exception:
                pass
            _client = None
