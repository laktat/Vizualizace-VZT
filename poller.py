"""
Poller: čte registry ze všech zařízení závodu přes Modbus TCP a ukládá je
do SQLite. Tohle je ta část, která by na reálné zakázce běžela pořád
(systemd služba na průmyslovém PC v rozvaděči).

Každé zařízení má vlastní spojení. Když jedno neodpovídá (výpadek sítě,
vypnutý rozvaděč), ostatní se čtou dál a k nedostupnému se poller
v dalším kole vrátí — přesně to se od sběru dat čeká.

ARCHIV SE NESMÍ ROZRŮST DO NEKONEČNA. Dvě věci, které to drží na uzdě:

  1) Zapisuje se jen ZMĚNA. Když se hodnota od posledního zápisu nepohnula
     víc než o pásmo necitlivosti, nemá smysl ji ukládat znovu — mezi dvěma
     záznamy se prostě nic nedělo. Tohle dělá každý slušný historizační
     software a ušetří to většinu místa, protože půlka veličin stojí.
     Pojistkou je HEARTBEAT: i nehybná hodnota se zapíše jednou za čas, aby
     v trendu nevznikaly díry a bylo poznat, že sběr běžel.
  2) Stará data se mažou. Kolik se drží, říká --retention-days.

Spuštění:
    python poller.py                 čte donekonečna
    python poller.py --once          jeden odečet přes všechna zařízení
    python poller.py --device vzt1   jen jedno zařízení
    python poller.py --interval 2    jak často číst [s]
    python poller.py --retention-days 30   jak dlouho se drží historie
    python poller.py --prune --vacuum      uklidit archiv a skončit
"""

import argparse
import asyncio
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import drivers
import plant

DB = "data.sqlite"
INTERVAL = 10
RETENTION_DAYS = 7        # jak dlouho se drží historie
PRUNE_EVERY = 3600        # jak často se maže staré [s]

# Pásmo necitlivosti: hodnota se zapíše, až když se pohne víc než o tohle.
# Relativní díl pokrývá velké veličiny (počítadla energie, průtoky),
# absolutní podlaha ty malé (teploty, tlaky) a zároveň odfiltruje šum čidel.
DEADBAND_REL = 0.002
DEADBAND_ABS = 0.1
HEARTBEAT = 600           # i nehybná hodnota se zapíše jednou za tolik [s]


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


class ChangeFilter:
    """
    Rozhodne, které hodnoty má cenu zapsat.

    Drží si poslední zapsanou hodnotu každé veličiny a její čas. Zapisuje se,
    když se hodnota pohnula za pásmo necitlivosti, nebo když od posledního
    zápisu uplynul heartbeat.
    """

    def __init__(self):
        self.last = {}        # (zařízení, klíč) -> (hodnota, čas zápisu)

    def changed(self, device_id, values, now):
        out = {}
        for key, value in values.items():
            prev = self.last.get((device_id, key))
            if prev is not None:
                old, when = prev
                band = max(DEADBAND_ABS, abs(old) * DEADBAND_REL)
                if abs(value - old) <= band and now - when < HEARTBEAT:
                    continue
            self.last[(device_id, key)] = (value, now)
            out[key] = value
        return out


def prune(con, days):
    """Smaže vzorky starší než zadaný počet dní. Vrací, kolik jich zmizelo."""
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    cur = con.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
    con.commit()
    return cur.rowcount


def thin(con, older_than_h, minutes=1):
    """
    Proředí starší data na jeden vzorek za minutu.

    Pásmo necitlivosti hlídá nově zapisovaná data, ale co se nasbíralo dřív,
    zůstává husté. Pro trend starý několik dní je vteřinové rozlišení k
    ničemu — stačí jeden vzorek za minutu a soubor se scvrkne na desetinu.
    Nechává se vždy první vzorek v každé minutě, takže průběh zůstane čitelný.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_h)).isoformat(
        timespec="seconds")
    # přihrádka = zařízení + veličina + minuta ("2026-09-13T16:24")
    cur = con.execute("""
        DELETE FROM samples
        WHERE ts < :cut AND rowid NOT IN (
            SELECT MIN(rowid) FROM samples WHERE ts < :cut
            GROUP BY device, key, substr(ts, 1, 16)
        )
    """, {"cut": cutoff})
    con.commit()
    return cur.rowcount


def vacuum(con):
    """
    Uvolní místo po smazaných řádcích.

    SQLite po DELETE soubor nezmenší, jen si stránky označí jako volné.
    VACUUM ho přepíše — chvíli to trvá a potřebuje to místo na disku, proto
    se to dělá jen na vyžádání.
    """
    con.execute("VACUUM")
    con.commit()


class DeviceReader:
    """
    Čtení jednoho zařízení přes driver, s hlášením výpadku a návratu.

    Driver řeší protokol, tahle třída jen to, co je pro sběr společné:
    aby jedno mlčící zařízení nezastavilo ostatní a aby se o výpadku
    napsalo do logu jednou, ne při každém odečtu.
    """

    def __init__(self, dev):
        self.dev = dev
        self.driver = drivers.for_device(dev)
        self.online = None

    async def read(self):
        """Vrátí dict {klíč: hodnota}, nebo None když zařízení neodpovídá."""
        values, _ = await self.driver.read_points()
        if values is None:
            if self.online is not False:
                print(f"  ! {self.dev.name} neodpovídá "
                      f"({self.driver.protocol})", flush=True)
            self.online = False
            return None
        if self.online is False:
            print(f"  + {self.dev.name} zase odpovídá", flush=True)
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
    ap.add_argument("--retention-days", type=float, default=RETENTION_DAYS,
                    help="jak dlouho se drží historie (0 = neomezeně)")
    ap.add_argument("--prune", action="store_true",
                    help="uklidit archiv a skončit (bez sběru)")
    ap.add_argument("--thin-older-than", type=float, default=0, metavar="HODIN",
                    help="proředit data starší než X hodin na vzorek za minutu")
    ap.add_argument("--vacuum", action="store_true",
                    help="po úklidu uvolnit místo na disku")
    ap.add_argument("--no-deadband", action="store_true",
                    help="zapisovat každý odečet, i když se nic nezměnilo")
    args = ap.parse_args()

    con = init_db()

    if args.prune:
        before = Path(DB).stat().st_size
        n = prune(con, args.retention_days)
        print(f"Smazáno {n:,} vzorků starších než {args.retention_days:g} dní"
              .replace(",", " "), flush=True)
        if args.thin_older_than:
            t = thin(con, args.thin_older_than)
            print(f"Proředěno {t:,} vzorků starších než "
                  f"{args.thin_older_than:g} h".replace(",", " "), flush=True)
        if args.vacuum:
            print("Uvolňuji místo (VACUUM), chvíli to potrvá…", flush=True)
            vacuum(con)
            after = Path(DB).stat().st_size
            print(f"Archiv: {before / 1e9:.2f} GB -> {after / 1e9:.2f} GB", flush=True)
        con.close()
        return

    devices = [d for d in plant.DEVICES
               if not args.device or d.id in args.device]
    if not devices:
        raise SystemExit("Žádné takové zařízení — viz plant.py")

    asyncio.run(collect(con, devices, args))
    con.close()


async def collect(con, devices, args):
    """
    Smyčka sběru. Zařízení se čtou najednou, ne jedno po druhém — jedno
    pomalé nebo mlčící pak nezdrží ostatní.
    """
    readers = [DeviceReader(d) for d in devices]
    changes = None if args.no_deadband else ChangeFilter()
    by_protocol = {}
    for r in readers:
        by_protocol.setdefault(r.driver.protocol, []).append(r.dev.id)
    popis = " · ".join(f"{p}: {len(ids)}" for p, ids in by_protocol.items())
    print(f"Sběr dat z {len(readers)} zařízení ({popis}), "
          f"každých {args.interval:.0f} s do {DB}", flush=True)
    if args.retention_days:
        print(f"Historie se drží {args.retention_days:g} dní, pak se maže",
              flush=True)

    last_prune = 0.0
    try:
        while True:
            now = time.monotonic()
            results = await asyncio.gather(*(r.read() for r in readers))

            data, stored = {}, 0
            for reader, values in zip(readers, results):
                if values is None:
                    continue
                data[reader.dev.id] = values
                to_store = (changes.changed(reader.dev.id, values, now)
                            if changes else values)
                if to_store:
                    store(con, reader.dev.id, to_store)
                    stored += len(to_store)
            con.commit()

            if args.retention_days and now - last_prune > PRUNE_EVERY:
                n = prune(con, args.retention_days)
                last_prune = now
                if n:
                    print(f"  archiv: smazáno {n:,} starých vzorků"
                          .replace(",", " "), flush=True)

            print(f"[{datetime.now():%H:%M:%S}] {len(data)}/{len(readers)} online · "
                  f"zapsáno {stored} hodnot · {summary(data)}", flush=True)

            if args.once:
                break
            await asyncio.sleep(args.interval)
    finally:
        for r in readers:
            await r.driver.close()


if __name__ == "__main__":
    main()
