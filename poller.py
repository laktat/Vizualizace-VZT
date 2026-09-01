"""
Poller: čte registry z jednotky přes Modbus TCP a ukládá je do SQLite.

Tohle je ta část, která by na reálné zakázce běžela pořád (systemd služba).

Spuštění:  python poller.py            # čte donekonečna
           python poller.py --once     # jeden odečet, na test
"""

import argparse
import sqlite3
import time
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

from registers import INPUT_REGISTERS, decode

DB = "data.sqlite"
HOST, PORT, DEVICE_ID = "127.0.0.1", 5020, 1
INTERVAL = 5  # sekund


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts    TEXT NOT NULL,
            key   TEXT NOT NULL,
            value REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_samples ON samples (key, ts)")
    con.commit()
    return con


def read_once(client):
    """Vrátí dict {key: hodnota} nebo None, když se čtení nepovede."""
    count = len(INPUT_REGISTERS)
    try:
        rr = client.read_input_registers(address=0, count=count, slave=DEVICE_ID)
    except Exception as exc:                       # jednotka nedostupná, výpadek sítě
        print(f"[{datetime.now():%H:%M:%S}] jednotka neodpovídá: {exc}")
        return None
    if rr.isError():
        print(f"[{datetime.now():%H:%M:%S}] chyba čtení: {rr}")
        return None
    return {reg["key"]: decode(rr.registers[i], reg)
            for i, reg in enumerate(INPUT_REGISTERS)}


def store(con, values):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.executemany(
        "INSERT INTO samples (ts, key, value) VALUES (?, ?, ?)",
        [(ts, k, v) for k, v in values.items()],
    )
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="jeden odečet a konec")
    args = ap.parse_args()

    con = init_db()
    client = ModbusTcpClient(HOST, port=PORT, timeout=3)

    while True:
        if not client.connected:
            client.connect()

        values = read_once(client)
        if values:
            store(con, values)
            print(f"[{datetime.now():%H:%M:%S}] "
                  f"přívod {values['t_supply']:.1f} °C · "
                  f"filtr {values['filter_dp']:.0f} Pa · "
                  f"ventilátor {values['fan_supply']:.0f} %")

        if args.once:
            break
        time.sleep(INTERVAL)

    client.close()


if __name__ == "__main__":
    main()
