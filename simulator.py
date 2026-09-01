"""
Simulátor vzduchotechnické jednotky (AHU) na Modbus TCP.

Model teplotního řetězce jednotky:
    venku -> rekuperátor -> topný ohřívač (ventil) -> přívod do místnosti -> odtah

REGULACE ŘÍDÍ NA TEPLOTU MÍSTNOSTI (kaskáda na prostor):
porovná žádanou teplotu místnosti se skutečnou (čidlo v odtahu, kde se z
místnosti odsává) a podle toho otevírá topný ventil. Když je v místnosti dost
teplo, pošle na ventil 0 % — a od té chvíle by se teplota místnosti a odtahu
neměla dál zvedat. Pokud se zvedá, ventil topí, i když nemá.

ŽIVÉ OVLÁDÁNÍ: žádaná teplota místnosti a otáčky ventilátorů se čtou z holding
registrů. Dashboard do nich zapisuje přes Modbus, takže jednotka reaguje za běhu.
Vyšší otáčky = víc protlačeného vzduchu = rychleji se zanáší filtr.

Poruchy pro test vyhodnocení:
    --fault stuck-valve   topný ventil/pohon se zasekne otevřený (topí při 0 %)
    --fault sensor-fail   čidlo přívodu hlásí nesmysl (-120 °C)

Spuštění:  python simulator.py                    # zdravá jednotka
           python simulator.py --fault stuck-valve
           python simulator.py --fault sensor-fail
Poslouchá na 127.0.0.1:5020
"""

import argparse
import asyncio
import math
import random
import time

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

from registers import INPUT_REGISTERS, HOLDING_REGISTERS, encode, decode

# --- parametry jednotky a regulace ------------------------------------------
KP_ROOM = 40.0             # zesílení P-regulátoru [% ventilu na °C odchylky]
RECUP_EFFICIENCY = 0.70    # účinnost rekuperace [-]
MAX_HEAT_DELTA = 25.0      # o kolik ohřívač ohřeje při 100 % ventilu [°C]
STUCK_VALVE_POS = 32.0     # na kolika % se ventil zasekne při poruše [%]
ROOM_COUPLING = 0.05       # jak rychle přívod ohřívá místnost [1/krok]
ROOM_LOSS = 0.01           # jak rychle místnost ztrácí teplo ven [1/krok]
FAN_NOMINAL = 78.0         # jmenovité otáčky, k nim se vztahuje zanášení filtru
BASE_FILTER_WEAR = 0.35    # rychlost zanášení při jmenovitých otáčkách [Pa/krok]


class AHU:
    """Jednoduchý, ale fyzikálně smysluplný model chování jednotky."""

    def __init__(self, fault="none"):
        self.t_start = time.time()
        self.fault = fault
        self.filter_dp = 45.0
        self.room = 20.0               # počáteční teplota místnosti [°C]

    def outdoor_temp(self, t):
        # denní sinusoida kolem 12 °C, perioda 5 min = "den"
        return 12.0 + 8.0 * math.sin(2 * math.pi * t / 300.0) + random.gauss(0, 0.15)

    def step(self, setpoint_room, fan):
        t = time.time() - self.t_start
        t_out = self.outdoor_temp(t)

        # 1) rekuperace: předehřeje sání teplem z odtahu (z místnosti)
        t_after_recup = t_out + RECUP_EFFICIENCY * (self.room - t_out)

        # 2) REGULACE NA MÍSTNOST: čím je místnost chladnější než žádaná,
        #    tím víc otevře ventil. Když je místnost dost teplá, povel = 0.
        error_room = setpoint_room - self.room
        valve_cmd = max(0.0, min(100.0, KP_ROOM * error_room))

        # 3) skutečná poloha ventilu — při poruše se rozejde s povelem
        actual_valve = STUCK_VALVE_POS if self.fault == "stuck-valve" else valve_cmd

        # 4) ohřívač přidá teplo podle SKUTEČNÉ polohy ventilu.
        #    Vyšší otáčky = víc vzduchu, ohřev na stejný výkon je o něco menší.
        heat = actual_valve / 100.0 * MAX_HEAT_DELTA * (FAN_NOMINAL / max(fan, 1))
        t_supply = t_after_recup + heat + random.gauss(0, 0.1)

        # 5) dynamika místnosti: ohřívá ji přívodní vzduch (tím víc, čím větší
        #    otáčky), ochlazují ztráty ven.
        gain = ROOM_COUPLING * (fan / FAN_NOMINAL)
        self.room += gain * (t_supply - self.room) + ROOM_LOSS * (t_out - self.room)
        t_extract = self.room + random.gauss(0, 0.08)

        # 6) zanášení filtru: rychlost roste s otáčkami (víc protlačeného vzduchu)
        self.filter_dp += BASE_FILTER_WEAR * (fan / FAN_NOMINAL) + random.gauss(0, 0.05)
        current = 4.2 * (fan / FAN_NOMINAL) + (self.filter_dp - 45) * 0.004 + random.gauss(0, 0.05)

        data = {
            "t_outdoor": t_out,
            "t_after_recup": t_after_recup,
            "t_supply": t_supply,
            "t_extract": t_extract,
            "valve_cmd": valve_cmd,        # do registru jde POVEL, ne skutečnost
            "filter_dp": self.filter_dp,
            "fan_supply": fan,
            "current": current,
            "run_hours": t / 60.0,
        }

        # 7) porucha čidla: přepíšeme jednu naměřenou hodnotu nesmyslem
        if self.fault == "sensor-fail":
            if int(t) % 20 < 12:          # čidlo "bliká" — část času vadné
                data["t_supply"] = -120.0

        return data


async def updater(context, ahu):
    hold_defaults = {r["key"]: r["default"] for r in HOLDING_REGISTERS}
    while True:
        # přečti žádané hodnoty z holding registrů (dashboard je mohl přepsat)
        raw = context[1].getValues(3, 0, len(HOLDING_REGISTERS))
        hold = {}
        for i, reg in enumerate(HOLDING_REGISTERS):
            val = decode(raw[i], reg)
            # ochrana proti nesmyslům / nenastaveno
            lo, hi = reg.get("min", -1e9), reg.get("max", 1e9)
            hold[reg["key"]] = val if lo <= val <= hi else hold_defaults[reg["key"]]

        data = ahu.step(setpoint_room=hold["sp_room"], fan=hold["sp_fan"])
        values = [encode(data[reg["key"]], reg) for reg in INPUT_REGISTERS]
        context[1].setValues(4, 0, values)   # fc=4 input registers, adresa 0
        await asyncio.sleep(1.0)


async def main(fault):
    ahu = AHU(fault=fault)

    input_block = ModbusSequentialDataBlock(0, [0] * len(INPUT_REGISTERS))
    holding_block = ModbusSequentialDataBlock(
        0, [encode(r["default"], r) for r in HOLDING_REGISTERS]
    )
    device = ModbusSlaveContext(ir=input_block, hr=holding_block, zero_mode=True)
    context = ModbusServerContext(slaves={1: device}, single=False)

    asyncio.create_task(updater(context, ahu))
    stav = {"none": "zdravá jednotka",
            "stuck-valve": "PORUCHA: zaseklý topný ventil",
            "sensor-fail": "PORUCHA: vadné čidlo přívodu"}[fault]
    print(f"Simulátor VZT jednotky běží na 127.0.0.1:5020 (device id 1) — {stav}")
    await StartAsyncTcpServer(context=context, address=("127.0.0.1", 5020))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", choices=["none", "stuck-valve", "sensor-fail"],
                    default="none", help="vyrob poruchu pro test vyhodnocení")
    args = ap.parse_args()
    asyncio.run(main(args.fault))
