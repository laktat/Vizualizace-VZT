"""
Záznamník alarmů — kdy porucha vznikla, kdy zmizela a kdo ji kvitoval.

Alarmy, které hlásí zařízení ve svém bitovém slově, jsou okamžitý stav:
řeknou, co je špatně teď. Provozu to nestačí. Když v noci naskočila porucha
čerpadla a do rána zmizela, ráno na displeji není nic — a přesto se to stalo
a mělo by se to vědět.

Záznamník proto sleduje ZMĚNY: každý přechod bitu z nuly na jedničku založí
záznam, přechod zpět mu doplní čas ukončení. Kvitování je třetí, samostatný
údaj — porucha může být kvitovaná a přitom pořád trvat, i naopak.

Data leží ve vlastní databázi (alarms.sqlite), ne v archivu měření, který
plní poller. Jeden soubor = jeden zapisovatel, ať se procesy nehádají o zámek.

BIT_OFFLINE je alarm, který nehlásí zařízení, ale dispečink sám: zařízení
neodpovídá. Nedostupný regulátor totiž žádné své alarmy poslat nemůže.

FILTRACE ZÁKMITŮ. Alarm se zapíše, teprve když vydrží nastavenou dobu, a
ukončí se, až je nastavenou dobu pryč. Bez toho by se záznamník zaplnil
vteřinovými zákmity na hranici regulace — třeba "nedosažena žádaná teplota"
se u kotle na kraji pásma objeví a zmizí desetkrát za minutu — a to podstatné
by se v tom ztratilo. Jak dlouho který alarm musí vydržet, je u jeho popisu
v registers.py.
"""

import sqlite3
import time
from datetime import datetime, timezone

BIT_OFFLINE = -1
OFFLINE_TEXT = "Zařízení neodpovídá"
ON_DELAY = 5.0            # výchozí doba, kterou alarm musí vydržet [s]
OFF_DELAY = 10.0          # a jak dlouho musí být pryč, než se ukončí [s]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AlarmLog:
    def __init__(self, path):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS alarm_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                device     TEXT NOT NULL,
                bit        INTEGER NOT NULL,
                text       TEXT NOT NULL,
                raised_at  TEXT NOT NULL,
                cleared_at TEXT,
                acked_at   TEXT,
                acked_by   TEXT
            )
        """)
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_alarm_open "
                         "ON alarm_log (device, bit, cleared_at)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_alarm_time "
                         "ON alarm_log (raised_at DESC)")
        self.con.commit()
        # rozpracované záznamy si držíme v paměti, ať se nedotazujeme každou sekundu
        self.open = {(r["device"], r["bit"]): r["id"] for r in self.con.execute(
            "SELECT id, device, bit FROM alarm_log WHERE cleared_at IS NULL")}
        # rozjeté odpočty filtrace: co se objevilo a co zmizelo, ale ještě
        # nevydrželo dost dlouho, aby se to zapsalo
        self.pending_on = {}
        self.pending_off = {}

    # -- zápis změn -----------------------------------------------------------
    def update(self, device_id, active, now=None, delays=None):
        """
        active = {číslo bitu: text} alarmů, které na zařízení právě platí.
        delays = {číslo bitu: doba v sekundách}, jak dlouho musí alarm vydržet.
        Vrací seznam nově zapsaných alarmů.
        """
        now = now or _now()
        delays = delays or {}
        mono = time.monotonic()
        raised = []

        # 1) alarmy, které platí: buď už jsou zapsané, nebo jim běží odpočet
        for bit, text in active.items():
            key = (device_id, bit)
            self.pending_off.pop(key, None)
            if key in self.open:
                continue
            since = self.pending_on.setdefault(key, mono)
            if mono - since >= delays.get(bit, ON_DELAY):
                cur = self.con.execute(
                    "INSERT INTO alarm_log (device, bit, text, raised_at) "
                    "VALUES (?, ?, ?, ?)", (device_id, bit, text, now))
                self.open[key] = cur.lastrowid
                self.pending_on.pop(key, None)
                raised.append({"device": device_id, "bit": bit, "text": text})

        # 2) co zmizelo dřív, než stačilo vydržet, se zahodí bez záznamu
        for key in [k for k in self.pending_on
                    if k[0] == device_id and k[1] not in active]:
            del self.pending_on[key]

        # 3) zapsané alarmy se ukončí, až jsou dost dlouho pryč
        for key, row_id in list(self.open.items()):
            dev, bit = key
            if dev != device_id or bit == BIT_OFFLINE or bit in active:
                continue
            gone = self.pending_off.setdefault(key, mono)
            if mono - gone >= OFF_DELAY:
                self.con.execute("UPDATE alarm_log SET cleared_at = ? WHERE id = ?",
                                 (now, row_id))
                del self.open[key]
                del self.pending_off[key]

        self.con.commit()
        return raised

    def set_offline(self, device_id, offline, now=None):
        """
        Alarm nedostupnosti si vede dispečink sám.

        Taky s odpočtem: jeden vynechaný odečet je běžná věc a nemá cenu ji
        zapisovat. Teprve když zařízení mlčí delší dobu, jde o poruchu.
        """
        now = now or _now()
        key = (device_id, BIT_OFFLINE)
        mono = time.monotonic()

        if offline and key not in self.open:
            since = self.pending_on.setdefault(key, mono)
            if mono - since < ON_DELAY:
                return []
            self.pending_on.pop(key, None)
            cur = self.con.execute(
                "INSERT INTO alarm_log (device, bit, text, raised_at) "
                "VALUES (?, ?, ?, ?)", (device_id, BIT_OFFLINE, OFFLINE_TEXT, now))
            self.open[key] = cur.lastrowid
            self.con.commit()
            return [{"device": device_id, "bit": BIT_OFFLINE, "text": OFFLINE_TEXT}]
        if not offline:
            self.pending_on.pop(key, None)
        if not offline and key in self.open:
            self.con.execute("UPDATE alarm_log SET cleared_at = ? WHERE id = ?",
                             (now, self.open.pop(key)))
            self.con.commit()
        return []

    # -- kvitování ------------------------------------------------------------
    def ack(self, ids, by, now=None):
        """Kvituje konkrétní záznamy. Vrací, kolika se to dotklo."""
        if not ids:
            return 0
        now = now or _now()
        marks = ",".join("?" * len(ids))
        cur = self.con.execute(
            f"UPDATE alarm_log SET acked_at = ?, acked_by = ? "
            f"WHERE id IN ({marks}) AND acked_at IS NULL", (now, by, *ids))
        self.con.commit()
        return cur.rowcount

    def ack_device(self, device_id, by, now=None):
        """Kvituje všechny nekvitované alarmy jednoho zařízení."""
        now = now or _now()
        cur = self.con.execute(
            "UPDATE alarm_log SET acked_at = ?, acked_by = ? "
            "WHERE device = ? AND acked_at IS NULL", (now, by, device_id))
        self.con.commit()
        return cur.rowcount

    # -- čtení ----------------------------------------------------------------
    def active(self):
        """Alarmy, které právě trvají — nejnovější první."""
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM alarm_log WHERE cleared_at IS NULL "
            "ORDER BY raised_at DESC, id DESC")]

    def history(self, limit=200, device=None, only_unacked=False):
        """Záznamy včetně ukončených — nejnovější první."""
        sql = "SELECT * FROM alarm_log"
        where, args = [], []
        if device:
            where.append("device = ?")
            args.append(device)
        if only_unacked:
            where.append("acked_at IS NULL")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY raised_at DESC, id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.con.execute(sql, args)]

    def counts(self):
        """Počty pro ukazatel v záhlaví."""
        row = self.con.execute("""
            SELECT
              SUM(cleared_at IS NULL) AS active,
              SUM(cleared_at IS NULL AND acked_at IS NULL) AS unacked,
              SUM(acked_at IS NULL) AS unacked_total
            FROM alarm_log
        """).fetchone()
        return {"active": row["active"] or 0,
                "unacked": row["unacked"] or 0,
                "unacked_total": row["unacked_total"] or 0}
