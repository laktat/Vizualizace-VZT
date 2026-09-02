"""
Simulátor celého závodu na Modbus TCP.

Spustí jeden Modbus TCP server pro každé zařízení ze seznamu v plant.py —
tak, jak by v provozu stál v každém rozvaděči vlastní regulátor s vlastní
IP adresou. Nad nimi běží jedna společná fyzikální simulace (sim/factory.py),
takže zařízení na sebe navzájem reagují.

Spuštění:
    python simulator.py                        zdravý závod, jarní počasí
    python simulator.py --season leto          horký den — chillery a věž na doraz
    python simulator.py --speed 120            dvakrát rychlejší běh času
    python simulator.py --fault vzt2:stuck-valve --fault chl1:comp1

Dostupné poruchy:
    VZT:      stuck-valve, sensor-fail, fan-fault
    Chiller:  comp1, comp2
    Věž:      fan1, fan2
    Okruh:    p1, p2, s1, s2        (čerpadla chlazené vody)
    Kotelna:  hp1, hp2              (oběhová čerpadla)
    Kotel:    burner-fault
"""

import argparse
import asyncio
import logging

from pymodbus.datastore import (
    ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

import plant
import registers as regs
from sim.factory import Factory
from sim import common

STEP = 1.0          # jak často se počítá krok simulace [reálné sekundy]


def build_context(dev):
    """Datový prostor jednoho zařízení: input registry + holding registry."""
    spec = regs.DEVICE_TYPES[dev.type]
    ir = ModbusSequentialDataBlock(0, [0] * (regs.span(spec["input"]) + 8))
    hr_init = regs.encode_all({h["key"]: h["default"] for h in spec["holding"]},
                              spec["holding"])
    hr = ModbusSequentialDataBlock(0, hr_init + [0] * 8)
    slave = ModbusSlaveContext(ir=ir, hr=hr, zero_mode=True)
    return ModbusServerContext(slaves={dev.unit_id: slave}, single=False)


def read_holdings(dev, context):
    """Přečte, co do zařízení zapsal dispečink; nesmysly nahradí výchozími."""
    spec = regs.DEVICE_TYPES[dev.type]["holding"]
    raw = context[dev.unit_id].getValues(3, 0, regs.span(spec))
    values = regs.decode_all(raw, spec)
    out = {}
    for h in spec:
        v = values[h["key"]]
        out[h["key"]] = v if h["min"] <= v <= h["max"] else h["default"]
    return out


async def run_simulation(contexts, factory, speed):
    """Hlavní smyčka: přečti žádané hodnoty, spočítej krok, zapiš měření."""
    tick = 0
    while True:
        holdings = {d.id: read_holdings(d, contexts[d.id]) for d in plant.DEVICES}
        data = factory.step(STEP * speed, holdings)

        for d in plant.DEVICES:
            spec = regs.DEVICE_TYPES[d.type]["input"]
            words = regs.encode_all(data[d.id], spec)
            contexts[d.id][d.unit_id].setValues(4, 0, words)

        tick += 1
        if tick % 30 == 0:
            amb = data["_ambient"]
            chw, hw = data["chw"], data["kotelna"]
            print(f"{amb['hour']:5.2f} h | venku {amb['t_out']:5.1f} °C | "
                  f"chlaz. voda {chw['t_supply']:4.1f}/{chw['t_return']:4.1f} °C "
                  f"({chw['load_power']:5.0f} kW) | "
                  f"topná {hw['t_header_flow']:4.1f} °C "
                  f"({hw['load_power']:5.0f} kW) | "
                  f"věž {data['vez']['t_water_out']:4.1f} °C")
        await asyncio.sleep(STEP)


async def main(args):
    logging.getLogger("pymodbus").setLevel(logging.ERROR)

    factory = Factory(plant.DEVICES, season=args.season)
    for spec in args.fault:
        dev_id, _, name = spec.partition(":")
        if dev_id not in plant.DEVICES_BY_ID:
            raise SystemExit(f"Neznámé zařízení: {dev_id}")
        factory.set_fault(dev_id, name)
        print(f"  porucha: {plant.DEVICES_BY_ID[dev_id].name} — {name}")

    contexts = {d.id: build_context(d) for d in plant.DEVICES}

    print(f"\nSimulace závodu běží ({args.season}, čas {args.speed}× zrychlený)")
    for area, label in plant.AREAS.items():
        names = [f"{d.name} :{d.port}" for d in plant.DEVICES if d.area == area]
        print(f"  {label}: " + ", ".join(names))
    print()

    servers = [
        StartAsyncTcpServer(context=contexts[d.id], address=(plant.HOST, d.port))
        for d in plant.DEVICES
    ]
    await asyncio.gather(run_simulation(contexts, factory, args.speed), *servers)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulátor závodu na Modbus TCP")
    ap.add_argument("--season", choices=["zima", "jaro", "leto", "podzim"],
                    default="jaro", help="počasí, ve kterém závod jede")
    ap.add_argument("--speed", type=float, default=common.SIM_SPEED,
                    help="zrychlení času (60 = jedna sekunda je minuta provozu)")
    ap.add_argument("--fault", action="append", default=[],
                    metavar="ZAŘÍZENÍ:PORUCHA",
                    help="vyrob poruchu, např. vzt2:stuck-valve")
    asyncio.run(main(ap.parse_args()))
