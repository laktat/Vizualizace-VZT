"""
Poller: čte registry ze všech zařízení závodu přes Modbus TCP a ukládá je
do SQLite. Tohle je ta část, která by na reálné zakázce běžela pořád
(systemd služba na průmyslovém PC v rozvaděči).

Každé zařízení má vlastní spojení. Když jedno neodpovídá (výpadek sítě,
vypnutý rozvaděč), ostatní se čtou dál a k nedostupnému se poller
v dalším kole vrátí — přesně to se od sběru dat čeká.

Spuštění:
    python poller.py                 čte donekonečna
    python poller.py --once          jeden odečet přes všechna zařízení
    python poller.py --device vzt1   jen jedno zařízení
    python poller.py --interval 2    jak často číst [s]
"""

import argparse
import sqlite3
import time
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

import plant
import registers as regs

DB = "data.sqlite"
INTERVAL = 5


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts     TEXT NOT NULL,
            device TEXT NOT NULL,
            key    TEXT NOT NULL,
            value  REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_samples ON samples (device, key, ts)")
    con.commit()
    return con


class DeviceReader:
    """Jedno spojení na jedno zařízení, které se samo obnovuje po výpadku."""

    def __init__(self, dev):
        self.dev = dev
        self.spec = regs.DEVICE_TYPES[dev.type]["input"]
        self.count = regs.span(self.spec)
        self.client = ModbusTcpClient(plant.HOST, port=dev.port, timeout=2)
        self.online = None

    def read(self):
        """Vrátí dict {klíč: hodnota}, nebo None když zařízení neodpovídá."""
        try:
            if not self.client.connected:
                self.client.connect()
            rr = self.client.read_input_registers(
                address=0, count=self.count, slave=self.dev.unit_id)
            if rr.isError():
                raise IOError(str(rr))
            values = regs.decode_all(rr.registers, self.spec)
        except Exception as exc:
            if self.online is not False:
                print(f"  ! {self.dev.name} neodpovídá ({exc})")
            self.online = False
            self.client.close()
            return None

        if self.online is False:
            print(f"  + {self.dev.name} zase odpovídá")
        self.online = True
        return values


def store(con, device_id, values):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.executemany(
        "INSERT INTO samples (ts, device, key, value) VALUES (?, ?, ?, ?)",
        [(ts, device_id, k, v) for k, v in values.items()],
    )


def summary(data):
    """Jeden řádek do konzole, ať je vidět, že sběr běží."""
    parts = []
    if "vzt1" in data:
        parts.append(f"hala A {data['vzt1']['t_extract']:.1f} °C")
    if "chw" in data:
        parts.append(f"chlazená {data['chw']['t_supply']:.1f} °C")
    if "kotelna" in data:
        parts.append(f"topná {data['kotelna']['t_header_flow']:.1f} °C")
    alarms = sum(1 for d in data.values() if d.get("alarms"))
    if alarms:
        parts.append(f"{alarms} zařízení s alarmem")
    return " · ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Sběr dat ze závodu do SQLite")
    ap.add_argument("--once", action="store_true", help="jeden odečet a konec")
    ap.add_argument("--device", action="append", help="číst jen vybraná zařízení")
    ap.add_argument("--interval", type=float, default=INTERVAL, help="perioda čtení [s]")
    args = ap.parse_args()

    devices = [d for d in plant.DEVICES
               if not args.device or d.id in args.device]
    if not devices:
        raise SystemExit("Žádné takové zařízení — viz plant.py")

    con = init_db()
    readers = [DeviceReader(d) for d in devices]
    print(f"Sběr dat z {len(readers)} zařízení, každých {args.interval:.0f} s "
          f"do {DB}")

    while True:
        data = {}
        for r in readers:
            values = r.read()
            if values is not None:
                data[r.dev.id] = values
                store(con, r.dev.id, values)
        con.commit()

        online = f"{len(data)}/{len(readers)}"
        print(f"[{datetime.now():%H:%M:%S}] {online} online · {summary(data)}")

        if args.once:
            break
        time.sleep(args.interval)

    for r in readers:
        r.client.close()


if __name__ == "__main__":
    main()
