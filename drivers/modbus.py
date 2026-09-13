"""
Driver pro Modbus TCP.

Logika je stejná, jakou dispečink používal od začátku — měřené hodnoty se
čtou jako input registry (funkce 4), žádané jako holding registry (funkce 3),
zápis jde do holding registru. Nově je jen schovaná za společné rozhraní.

Klient se vytváří až při prvním čtení: pymodbus si při vzniku sahá po
běžícím event loopu, takže ho nejde postavit dřív, než smyčka běží.
"""

from pymodbus.client import AsyncModbusTcpClient

import plant
import registers as regs

from .base import Driver


class ModbusDriver(Driver):
    protocol = "Modbus TCP"

    def __init__(self, dev):
        super().__init__(dev)
        spec = regs.DEVICE_TYPES[dev.type]
        self.input = spec["input"]
        self.holding = spec["holding"]
        self.count = regs.span(self.input)
        self.hold_count = regs.span(self.holding)
        self.client = None

    def _client(self):
        if self.client is None:
            self.client = AsyncModbusTcpClient(
                plant.HOST, port=self.dev.port, timeout=2)
        return self.client

    async def read_points(self):
        client = self._client()
        try:
            if not client.connected:
                await client.connect()
            rr = await client.read_input_registers(
                address=0, count=self.count, slave=self.dev.unit_id)
            hr = await client.read_holding_registers(
                address=0, count=self.hold_count, slave=self.dev.unit_id)
            if rr.isError() or hr.isError():
                raise IOError("chyba čtení")
            values = regs.decode_all(rr.registers, self.input)
            setpoints = regs.decode_all(hr.registers, self.holding)
        except Exception:
            self.online = False
            client.close()
            return None, None
        self.online = True
        return values, setpoints

    async def write_point(self, key, value):
        reg = regs.by_key(self.holding).get(key)
        if reg is None:
            raise KeyError(f"{self.dev.id} nemá registr {key}")
        value = max(reg["min"], min(reg["max"], float(value)))
        client = self._client()
        if not client.connected:
            await client.connect()
        rr = await client.write_register(
            reg["addr"], regs.encode(value, reg)[0], slave=self.dev.unit_id)
        if rr.isError():
            raise IOError(str(rr))
        return value

    async def close(self):
        if self.client is not None:
            self.client.close()
