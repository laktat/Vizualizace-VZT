"""
Edge gateway: čte zařízení závodu a publikuje je na sběrnici zpráv (MQTT).
Tohle je ta část, která by na reálné zakázce běžela pořád — systemd služba
na průmyslovém PC v rozvaděči, u technologie.

Gateway je JEDINÝ, kdo na sběrnici (Modbus, BACnet) sahá kvůli čtení.
Nadřazené vrstvy se zařízení neptají, odebírají si jeho zprávy. Díky tomu
provoz na sběrnici neroste s počtem konzumentů: přibude dispečink, historizace,
reporty — a zařízení o tom neví.

Publikuje se jen ZMĚNA, stejné pásmo necitlivosti jako u archivu. Škrtí to
zároveň síť i databázi, protože obojí trpí stejnou nemocí: hodnota, která se
nepohnula, nemá cenu ani poslat, ani uložit.

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

Dostupnost zařízení se posílá zvlášť a v každém kole — mlčení kvůli pásmu
necitlivosti se nesmí plést s nedostupným regulátorem. Gateway má u brokeru
nastavenou poslední vůli, takže i jeho vlastní pád je pro odběratele událost.

Spuštění:
    python poller.py                 čte donekonečna
    python poller.py --once          jeden odečet přes všechna zařízení
    python poller.py --device vzt1   jen jedno zařízení
    python poller.py --interval 2    jak často číst [s]
    python poller.py --retention-days 30   jak dlouho se drží historie
    python poller.py --prune --vacuum      uklidit archiv a skončit
    python poller.py --archive none        jen publikovat, nearchivovat
    python poller.py --no-mqtt             jen archivovat, nepublikovat
"""

import argparse
import asyncio
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import drivers
import mqtt_bus
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

# Jak často se místo změn pošle ÚPLNÝ snímek, a to s příznakem retained.
# Pásmo necitlivosti totiž posílá jen to, co se pohnulo — kdo se připojí
# později, má obraz plný děr, dokud se každá hodnota jednou nezmění.
# Retained snímek dostane nový odběratel od brokeru okamžitě při přihlášení.
SNAPSHOT_EVERY = 6        # kol


def init_db():
    # busy_timeout: když je databáze chvíli zamčená jiným zápisem, počká se
    # místo okamžité chyby. Bez toho stačí jeden souběh a zápis selže.
    con = sqlite3.connect(DB, timeout=10.0)
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


class Publisher:
    """
    Publikuje hodnoty na sběrnici zpráv.

    Drží se stranou od čtení: když broker neběží nebo spadne, sběr dat jede
    dál a jen se to jednou oznámí. Gateway u technologie nesmí přestat číst
    proto, že má nadřazený systém výpadek.
    """

    def __init__(self, host=mqtt_bus.HOST, port=mqtt_bus.PORT):
        self.client = None
        self.online = None
        try:
            self.client = mqtt_bus.connect(
                "edge-gateway", will_topic=mqtt_bus.gateway_topic(),
                will_payload={"online": False}, host=host, port=port)
            self.client.publish(mqtt_bus.gateway_topic(),
                                mqtt_bus.encode({"online": True}),
                                qos=mqtt_bus.QOS_STATE, retain=True)
            self.online = True
            print(f"Publikuji na MQTT {host}:{port}, téma "
                  f"{mqtt_bus.topic('<zařízení>', mqtt_bus.SENSORS)}", flush=True)
        except Exception as exc:
            print(f"  ! broker nedostupný ({exc}) — jede se dál bez publikování",
                  flush=True)
            self.online = False

    def send(self, device_id, kind, payload, qos=mqtt_bus.QOS_DATA, retain=False):
        if self.client is None or not payload:
            return 0
        try:
            self.client.publish(mqtt_bus.topic(device_id, kind),
                                mqtt_bus.encode(payload), qos=qos, retain=retain)
            if self.online is False:
                print("  + broker zase odpovídá", flush=True)
                self.online = True
            return len(payload)
        except Exception:
            if self.online is not False:
                print("  ! broker neodpovídá — publikování vynecháno", flush=True)
                self.online = False
            return 0

    def close(self):
        if self.client is None:
            return
        try:
            self.client.publish(mqtt_bus.gateway_topic(),
                                mqtt_bus.encode({"online": False}),
                                qos=mqtt_bus.QOS_STATE, retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


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
        """Vrátí (měřené, žádané), nebo (None, None) když zařízení neodpovídá."""
        values, setpoints = await self.driver.read_points()
        if values is None:
            if self.online is not False:
                print(f"  ! {self.dev.name} neodpovídá "
                      f"({self.driver.protocol})", flush=True)
            self.online = False
            return None, None
        if self.online is False:
            print(f"  + {self.dev.name} zase odpovídá", flush=True)
        self.online = True
        return values, setpoints


def archive(con, device_id, values):
    """
    Uloží hodnoty do archivu, ale nikdy kvůli tomu nespadne.

    Gateway u technologie je jediný, kdo čte sběrnici. Kdyby ho shodil
    zamčený soubor nebo plný disk, přestane číst celý závod — a to je
    nepoměrně horší než díra v historii. Archiv je tu ten postradatelný.
    """
    try:
        store(con, device_id, values)
        return len(values)
    except Exception as exc:
        global _archive_warned
        if not _archive_warned:
            print(f"  ! archiv nepíše ({exc}) — sběr a publikování jedou dál",
                  flush=True)
            _archive_warned = True
        return 0


_archive_warned = False


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
                    help="posílat a zapisovat každý odečet, i bez změny")
    ap.add_argument("--archive", choices=["sqlite", "none"], default="sqlite",
                    help="kam archivovat historii")
    ap.add_argument("--no-mqtt", action="store_true",
                    help="nepublikovat na sběrnici zpráv")
    ap.add_argument("--mqtt-host", default=mqtt_bus.HOST)
    ap.add_argument("--mqtt-port", type=int, default=mqtt_bus.PORT)
    args = ap.parse_args()

    con = init_db() if args.archive == "sqlite" or args.prune else None

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
    if con is not None:
        con.close()


async def collect(con, devices, args):
    """
    Smyčka gatewaye: přečíst zařízení, poslat změny na sběrnici, archivovat.

    Zařízení se čtou najednou, ne jedno po druhém — jedno pomalé nebo mlčící
    pak nezdrží ostatní.
    """
    readers = [DeviceReader(d) for d in devices]
    changes = None if args.no_deadband else ChangeFilter()
    publisher = None if args.no_mqtt else Publisher(args.mqtt_host, args.mqtt_port)
    archiving = args.archive == "sqlite"

    by_protocol = {}
    for r in readers:
        by_protocol.setdefault(r.driver.protocol, []).append(r.dev.id)
    popis = " · ".join(f"{p}: {len(ids)}" for p, ids in by_protocol.items())
    print(f"Gateway čte {len(readers)} zařízení ({popis}) každých "
          f"{args.interval:.0f} s", flush=True)
    print(f"  archiv: {'data.sqlite' if archiving else 'vypnutý'}"
          f" · pásmo necitlivosti: {'vypnuté' if changes is None else 'zapnuté'}",
          flush=True)
    if args.retention_days and archiving:
        print(f"  historie se drží {args.retention_days:g} dní, pak se maže",
              flush=True)

    last_prune, tick = 0.0, 0
    try:
        while True:
            now = time.monotonic()
            snapshot = tick % SNAPSHOT_EVERY == 0
            tick += 1
            results = await asyncio.gather(*(r.read() for r in readers))

            data, stored, sent = {}, 0, 0
            for reader, (values, setpoints) in zip(readers, results):
                dev_id = reader.dev.id
                # dostupnost jde v každém kole, i když se data nezměnila
                if publisher:
                    sent += publisher.send(
                        dev_id, mqtt_bus.STATUS,
                        {"online": values is not None,
                         "protocol": reader.driver.protocol},
                        qos=mqtt_bus.QOS_STATE, retain=True)
                if values is None:
                    continue

                data[dev_id] = values
                changed = (changes.changed(dev_id, values, now)
                           if changes else values)
                if publisher:
                    if snapshot:
                        # úplný obraz pro toho, kdo se připojí později
                        sent += publisher.send(dev_id, mqtt_bus.SENSORS, values,
                                               qos=mqtt_bus.QOS_STATE, retain=True)
                    else:
                        sent += publisher.send(dev_id, mqtt_bus.SENSORS, changed)

                    # Žádané hodnoty se posílají CELÉ, ne jen změněné.
                    # Zapamatovaná (retained) zpráva je vždycky jen ta
                    # poslední: kdyby nesla jen změnu, dostal by nově
                    # připojený dispečink jedinou hodnotu a zbytek by se
                    # nedozvěděl, dokud se náhodou nezmění. Je jich pár
                    # a mění se zřídka, takže se tím nic neušetří.
                    sent += publisher.send(dev_id, mqtt_bus.SETPOINTS, setpoints,
                                           qos=mqtt_bus.QOS_STATE, retain=True)
                if archiving and changed:
                    stored += archive(con, dev_id, changed)
            if archiving:
                try:
                    con.commit()
                except Exception:
                    pass

            if archiving and args.retention_days and now - last_prune > PRUNE_EVERY:
                n = prune(con, args.retention_days)
                last_prune = now
                if n:
                    print(f"  archiv: smazáno {n:,} starých vzorků"
                          .replace(",", " "), flush=True)

            print(f"[{datetime.now():%H:%M:%S}] {len(data)}/{len(readers)} online · "
                  f"posláno {sent}{' (úplný snímek)' if snapshot else ''} · "
                  f"zapsáno {stored} · {summary(data)}", flush=True)

            if args.once:
                break
            await asyncio.sleep(args.interval)
    finally:
        if publisher:
            publisher.close()
        for r in readers:
            await r.driver.close()


if __name__ == "__main__":
    main()
