"""
Dispečink závodu — webový server.

Čte všechna zařízení přes Modbus TCP a rozesílá jejich stav připojeným
prohlížečům přes WebSocket. Zapsané žádané hodnoty posílá opačným směrem
do zařízení. Vizualizace v prohlížeči tak drží živý obraz provozu, aniž by
se stránka překreslovala.

    ┌──────────┐  Modbus TCP   ┌────────────┐  WebSocket  ┌───────────┐
    │ zařízení │ ◄───────────► │   server   │ ◄─────────► │ prohlížeč │
    └──────────┘   čtení 1 s   └────────────┘   stav 1 s  └───────────┘

Spuštění:
    python -m web.server                  # http://127.0.0.1:8000
    python -m web.server --port 9000
"""

import argparse
import asyncio
import json
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope
import alarmlog
import drivers
import diagnostics
import energy
import ml_anomaly
import modes
import mqtt_bus
import self_healing
import plant
import registers as regs

STATIC = Path(__file__).parent / "static"


def build_version():
    """
    Označení verze rozhraní — čas poslední změny souborů vizualizace.

    Ukazuje se v rohu obrazovky. Když se po úpravě nic nezmění, je hned
    poznat, že prohlížeč drží starou stránku, a nemusí se to hádat.
    """
    files = list(STATIC.glob("*.js")) + list(STATIC.glob("*.css")) \
        + list(STATIC.glob("*.html"))
    newest = max((f.stat().st_mtime for f in files), default=0)
    return datetime.fromtimestamp(newest).strftime("%d.%m. %H:%M")


class NoCacheStatic(StaticFiles):
    """
    Statické soubory, které si prohlížeč nesmí nechat bez zeptání.

    Dispečink je jednostránková aplikace: bez tohohle si prohlížeč po úpravě
    klidně nechá starý JavaScript a operátor kouká na verzi, která už
    neplatí. S "no-cache" se pokaždé zeptá a dostane 304, pokud se nic
    nezměnilo — stojí to jeden dotaz a ušetří to hodinu hledání.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return super().is_not_modified(response_headers, request_headers)

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp
TICK_INTERVAL = 1.0        # jak často se skládá stav a počítají alarmy [s]

# Jak dlouho se věří datům ze sběrnice zpráv. Když od zařízení nic nepřijde
# déle, sáhne dispečink na sběrnici sám — gateway může spadnout a konzole
# kvůli tomu nesmí oslepnout.
MQTT_STALE_AFTER = 25.0
MIN_BROADCAST_GAP = 0.2    # nejkratší rozestup mezi zprávami do prohlížeče [s]
HISTORY_LEN = 900          # kolik vzorků se drží pro trendy (15 min)
DIAG_INTERVAL = 5.0        # jak často se přepočítává vyhodnocení provozu [s]
ARCHIVE = Path(__file__).parent.parent / "data.sqlite"
SEED_MAX_AGE = 3600       # jak staré vzorky se ještě načtou z archivu [s]
ALARM_DB = Path(__file__).parent.parent / "alarms.sqlite"


class Dispatcher:
    """Sběr dat ze všech zařízení a rozesílání stavu do prohlížečů."""

    def __init__(self):
        self.links = {d.id: drivers.for_device(d) for d in plant.DEVICES}
        self.state = {}
        self.history = {d.id: deque(maxlen=HISTORY_LEN) for d in plant.DEVICES}
        self.diagnostics = {}
        self.diag_at = 0.0
        self.clients = set()
        self.alarms = alarmlog.AlarmLog(str(ALARM_DB))
        # co přišlo ze sběrnice zpráv; hodnoty se hromadí, protože gateway
        # posílá jen změny
        self.feed = {d.id: {"values": {}, "setpoints": {}, "online": False,
                            "at": 0.0} for d in plant.DEVICES}
        self.mqtt = None
        self.gateway_online = False
        self.source = {"bus": 0, "direct": 0}
        # základ pro automatické korekce bere z aktivního provozního režimu —
        # to je hodnota, kterou nastavil operátor, a k té se korekce vrací
        self.mode_profile = {}
        self.reload_mode_profile()
        self.healing = self_healing.SelfHealing(baselines=self.mode_baseline)
        # model chodu ventilátoru se učí z toho samého proudu dat, který
        # dispečink dostává ze sběrnice zpráv
        self.anomaly = ml_anomaly.AnomalyDetector(
            [d.id for d in plant.DEVICES if d.type == "ahu"])
        self.loop = None
        self.wake = asyncio.Event()
        self.seed_history()

    def seed_history(self):
        """
        Načte nedávnou historii z archivu, který sbírá poller.

        Bez toho by po každém restartu serveru vyhodnocení provozu 15 minut
        mlčelo, protože trendy se počítají z průběhu, ne z jedné hodnoty.

        Berou se jen vzorky mladší než SEED_MAX_AGE — starší patří jiné
        session, kde zařízení mohla mít jiný stav počítadel, a v trendu by
        dělaly nesmysly. Když archiv neexistuje nebo je starý, začne se od nuly.
        """
        if not ARCHIVE.exists():
            return
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=SEED_MAX_AGE)).isoformat(timespec="seconds")
        try:
            con = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True)
            for dev_id in self.history:
                rows = con.execute("""
                    SELECT ts, key, value FROM samples
                    WHERE device = ? AND ts >= ? ORDER BY ts
                """, (dev_id, cutoff)).fetchall()
                samples = {}
                for ts, key, value in rows:
                    samples.setdefault(ts, {"ts": ts})[key] = value
                for ts in sorted(samples)[-HISTORY_LEN:]:
                    self.history[dev_id].append(samples[ts])
            con.close()
            total = sum(len(v) for v in self.history.values())
            if total:
                print(f"Z archivu načteno {total} vzorků nedávné historie")
        except Exception as exc:
            print(f"Archiv se nepodařilo načíst ({exc}) — začínám s prázdnou historií")

    def reload_mode_profile(self):
        """Načte profil aktivního provozního režimu. Volá se i po přepnutí."""
        try:
            data = modes.load()
            active = data.get("active")
            self.mode_profile = data["profiles"].get(active, {}) if active else {}
        except Exception:
            self.mode_profile = {}

    def mode_baseline(self, device_id, key):
        return (self.mode_profile.get(device_id) or {}).get(key)

    async def run_healing(self, state):
        """
        Nechá automatické korekce rozhodnout a zásahy provede.

        Zapisuje se stejnou cestou jako posuvník — přes driver. Každý zásah
        jde do knihy alarmů jako samostatná událost, aby operátor na jednom
        místě viděl nejen co se pokazilo, ale i co s tím systém sám udělal.
        """
        now = time.monotonic()
        for link in self.links.values():
            dev = link.dev
            d = state["devices"].get(dev.id, {})
            if not d.get("online"):
                continue
            findings = self.diagnostics.get(dev.id)
            if not findings:
                continue

            for action in self.healing.consider(dev, findings, d["values"],
                                                d["setpoints"], now):
                try:
                    written = await link.write_point(action.key, action.value)
                except Exception as exc:
                    self.alarms.log_action(
                        dev.id, f"Korekce se nepovedla — {action.key}",
                        f"{action.text}: zápis neprošel ({exc})")
                    continue
                self.healing.note_applied(dev.id, now)
                self.alarms.log_action(dev.id, action.text, action.detail)
                print(f"  korekce: {dev.name} — {action.text} "
                      f"(zapsáno {written})", flush=True)

    def update_diagnostics(self, state):
        """
        Přepočítá vyhodnocení provozu. Nedělá se každou sekundu — počítá se
        z celého sledovaného úseku a stejně se mění po minutách, ne po vzorcích.
        """
        now = time.monotonic()
        if self.diagnostics and now - self.diag_at < DIAG_INTERVAL:
            return
        self.diag_at = now
        for link in self.links.values():
            dev = link.dev
            d = state["devices"].get(dev.id, {})
            if not d.get("online"):
                continue
            score = (self.anomaly.score(dev.id, d["values"])
                     if dev.type == "ahu" else None)
            self.diagnostics[dev.id] = diagnostics.diagnose(
                dev, list(self.history[dev.id]), d["values"], d["setpoints"],
                anomaly=score)

    # -- zdroj dat: sběrnice zpráv, nebo přímé čtení jako záskok -------------
    def on_mqtt(self, client, userdata, message):
        """
        Zpráva ze sběrnice. Běží na vlákně paho, takže tady jen rychle uložit
        a probudit smyčku — dlouhý výpočet by zdržel příjem dalších zpráv.
        """
        device_id, kind = mqtt_bus.parse(message.topic)
        if device_id is None or device_id == mqtt_bus.GATEWAY:
            if device_id == mqtt_bus.GATEWAY:
                data = mqtt_bus.decode(message.payload) or {}
                self.gateway_online = bool(data.get("online"))
            return
        data = mqtt_bus.decode(message.payload)
        if data is None or device_id not in self.feed:
            return

        feed = self.feed[device_id]
        if kind == mqtt_bus.SENSORS:
            feed["values"].update(data)
            feed["at"] = time.monotonic()
        elif kind == mqtt_bus.SETPOINTS:
            feed["setpoints"].update(data)
            feed["at"] = time.monotonic()
        elif kind == mqtt_bus.STATUS:
            feed["online"] = bool(data.get("online"))
            feed["at"] = time.monotonic()

        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.wake.set)

    def start_mqtt(self):
        """Přihlásí se ke sběrnici. Když broker neběží, jede se bez něj."""
        try:
            self.mqtt = mqtt_bus.connect("dispecink", on_message=self.on_mqtt)
            self.mqtt.subscribe(mqtt_bus.subscription())
            print(f"Odebírám {mqtt_bus.subscription()} z "
                  f"{mqtt_bus.HOST}:{mqtt_bus.PORT}")
        except Exception as exc:
            print(f"Broker nedostupný ({exc}) — čtu zařízení přímo")

    def feed_usable(self, dev, now):
        """
        Jsou data ze sběrnice použitelná?

        Nestačí, že přišla nedávno. Měřené hodnoty se posílají jen při změně,
        takže po připojení může být obraz děravý — a s dírami by diagnostika
        i energetika počítaly nesmysly.

        Kontrolují se i ŽÁDANÉ hodnoty, ne jen měřené. Původně se hlídaly jen
        měřené a QA našlo, co to způsobí: jednomu zařízení chyběla ve stavu
        žádaná hodnota fault_sim, takže zkušební panel neukázal nasazenou
        poruchu a posuvník neměl co zobrazit. Dokud není obraz úplný, přečte
        si dispečink zařízení sám.
        """
        feed = self.feed[dev.id]
        if now - feed["at"] > MQTT_STALE_AFTER:
            return False
        spec = regs.DEVICE_TYPES[dev.type]
        measured = {r["key"] for r in spec["input"]}
        wanted = {h["key"] for h in spec["holding"]}
        return (measured.issubset(feed["values"].keys())
                and wanted.issubset(feed["setpoints"].keys()))

    async def gather_points(self):
        """
        Posbírá hodnoty ze všech zařízení.

        Přednost má sběrnice zpráv — gateway čte technologii za všechny, takže
        provoz na Modbusu a BACnetu neroste s počtem konzumentů. Zařízení,
        o kterém sběrnice mlčí, si dispečink přečte sám. Díky tomu funguje
        konzole i bez gatewaye, jen za cenu vlastního provozu na sběrnici.
        """
        now = time.monotonic()
        from_bus, to_read = {}, []
        for link in self.links.values():
            dev = link.dev
            if self.feed_usable(dev, now):
                feed = self.feed[dev.id]
                from_bus[dev.id] = (
                    (dict(feed["values"]), dict(feed["setpoints"]))
                    if feed["online"] else (None, None))
            else:
                to_read.append(link)

        results = await asyncio.gather(*(l.read_points() for l in to_read)) \
            if to_read else []
        direct = {l.dev.id: r for l, r in zip(to_read, results)}
        self.source = {"bus": len(from_bus), "direct": len(direct)}
        return {link.dev.id: from_bus.get(link.dev.id) or direct[link.dev.id]
                for link in self.links.values()}

    async def poll_once(self):
        points = await self.gather_points()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = {"ts": ts, "devices": {}}

        for link in self.links.values():
            dev = link.dev
            values, setpoints = points[dev.id]
            if values is None:
                state["devices"][dev.id] = {"online": False}
                # nedostupný regulátor své alarmy poslat nemůže, tak si je
                # necháme rozpracované a přidáme alarm nedostupnosti
                self.alarms.set_offline(dev.id, True, ts)
                continue
            self.alarms.set_offline(dev.id, False, ts)
            alarm_names = regs.DEVICE_TYPES[dev.type]["alarms"]
            active = regs.active_bits(values.get("alarms", 0), alarm_names)
            self.alarms.update(dev.id, active, ts,
                               regs.DEVICE_TYPES[dev.type]["delays"])
            state["devices"][dev.id] = {
                "online": True,
                "values": {k: round(v, 3) for k, v in values.items()},
                "setpoints": {k: round(v, 3) for k, v in setpoints.items()},
                "alarms": list(active.values()),
            }
            self.history[dev.id].append({"ts": ts, **values})
            # model dostává celý proud dat, ne jen vzorky z přepočtu
            # diagnostiky — učí se z toho, co v závodě opravdu teklo
            if dev.type == "ahu":
                self.anomaly.observe(dev.id, values)

        # energetická bilance: součty a měrné ukazatele z počítadel zařízení
        online = {d: v["values"] for d, v in state["devices"].items()
                  if v.get("online")}
        if online:
            state["energy"] = energy.summary(online)

        state["alarm_counts"] = self.alarms.counts()
        state["alarms_active"] = self.alarms.active()
        state["source"] = {**self.source, "gateway": self.gateway_online}

        self.update_diagnostics(state)
        state["healing"] = self.healing.status()
        for dev_id, findings in self.diagnostics.items():
            if dev_id in state["devices"] and state["devices"][dev_id]["online"]:
                state["devices"][dev_id]["diagnostics"] = findings

        self.state = state
        return state

    async def broadcast(self, message):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run(self):
        """
        Hlavní smyčka.

        Stav se skládá v pravidelném taktu, protože alarmy, diagnostika i
        energetika pracují s časem — zpoždění alarmu se nedá odvodit z toho,
        že zrovna přišla zpráva. Rozeslání do prohlížeče je ale UDÁLOSTNÍ:
        smyčka čeká, dokud nedorazí zpráva ze sběrnice nebo neuplyne takt.
        Když se v závodě něco pohne, je to na obrazovce hned, a ne až za
        sekundu.
        """
        self.loop = asyncio.get_running_loop()
        self.start_mqtt()
        last = 0.0
        while True:
            try:
                state = await self.poll_once()
                await self.run_healing(state)
                if self.clients:
                    await self.broadcast(json.dumps(state))
                last = time.monotonic()
            except Exception as exc:
                print(f"chyba sběru: {exc}")

            # čeká se na zprávu, nejdéle jeden takt
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=TICK_INTERVAL)
                # po události se chvíli počká, ať se zprávy z jednoho kola
                # gatewaye slijí do jednoho rozeslání
                gap = MIN_BROADCAST_GAP - (time.monotonic() - last)
                if gap > 0:
                    await asyncio.sleep(gap)
            except asyncio.TimeoutError:
                pass


def check_setpoint(device_id, key, value):
    """
    Zkontroluje žádanou hodnotu, než se pustí do zařízení.

    Driver hodnotu ještě jednou omezí do mezí registru — to je poslední
    pojistka, aby se do technologie nikdy nedostal nesmysl. Jenže omezení
    samo o sobě nestačí: požadavek na poruchu číslo 99 se utne na nejvyšší
    platnou, což je JINÁ skutečná porucha, a volající dostane 200, jako by
    se stalo to, co chtěl. Proto se mimo meze odmítá tady, s vysvětlením.

    Vrací (hodnota, None) nebo (None, chyba).
    """
    dev = plant.DEVICES_BY_ID.get(device_id)
    if dev is None:
        return None, "neznámé zařízení"
    reg = regs.by_key(regs.DEVICE_TYPES[dev.type]["holding"]).get(key)
    if reg is None:
        return None, f"{device_id} nemá žádanou hodnotu {key}"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{key}: {value!r} není číslo"
    if number != number or number in (float("inf"), float("-inf")):
        return None, f"{key}: hodnota musí být číslo, ne {value!r}"
    if not (reg["min"] <= number <= reg["max"]):
        return None, (f"{key}: {number:g} je mimo rozsah "
                      f"{reg['min']:g} až {reg['max']:g} {reg['unit']}".strip())
    return number, None


def build_meta():
    """
    Popis závodu pro prohlížeč: co kde stojí, co která veličina znamená,
    jaké má jednotky a jak se jmenují stavy a alarmy. Vizualizace si tak
    umí popsat hodnoty sama a nemusí je mít natvrdo v HTML.
    """
    types = {}
    for name, spec in regs.DEVICE_TYPES.items():
        types[name] = {
            "label": spec["label"],
            "values": {r["key"]: {"name": r["name"], "unit": r["unit"]}
                       for r in spec["input"]},
            "setpoints": [{"key": h["key"], "name": h["name"], "unit": h["unit"],
                           "min": h["min"], "max": h["max"],
                           "step": 1 if h["scale"] == 1 else 0.5}
                          for h in spec["holding"]],
            "states": spec["states"],
            "alarms": spec["alarms"],
            # katalog zkušebních poruch: pořadí = hodnota registru fault_sim
            "faults": [{"index": i + 1, "code": code, "label": label}
                       for i, (code, label) in enumerate(spec["faults"])],
        }
    return {
        "version": build_version(),
        "areas": plant.AREAS,
        "tariffs": plant.TARIFFS,
        "devices": [{"id": d.id, "name": d.name, "type": d.type,
                     "area": d.area, "port": d.port,
                     "protocol": dispatcher.links[d.id].protocol}
                    for d in plant.DEVICES],
        "types": types,
        "pumpStates": regs.PUMP_STATES,
        "compStates": regs.COMP_STATES,
        "burnerStates": regs.BURNER_STATES,
    }


dispatcher = Dispatcher()


@asynccontextmanager
async def lifespan(app):
    """Po startu serveru se rozběhne sběr dat, při vypnutí se zastaví."""
    task = asyncio.create_task(dispatcher.run())
    yield
    task.cancel()


app = FastAPI(title="Dispečink závodu", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/api/meta")
async def meta():
    return JSONResponse(build_meta())


@app.get("/api/state")
async def state():
    return JSONResponse(dispatcher.state)


@app.get("/api/history/{device_id}")
async def history(device_id: str, keys: str = ""):
    """Průběh vybraných veličin jednoho zařízení pro trendy."""
    if device_id not in dispatcher.history:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
    wanted = [k for k in keys.split(",") if k]
    rows = list(dispatcher.history[device_id])
    out = {"ts": [r["ts"] for r in rows]}
    for key in wanted:
        out[key] = [r.get(key) for r in rows]
    return JSONResponse(out)


@app.get("/api/energy")
async def energy_summary():
    """Energetická bilance závodu — spotřeba, náklady a měrné ukazatele."""
    return JSONResponse(dispatcher.state.get("energy", {}))


@app.get("/api/diagnostics/{device_id}")
async def device_diagnostics(device_id: str):
    """Vyhodnocení provozu jednoho zařízení."""
    if device_id not in dispatcher.links:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
    return JSONResponse({"device": device_id,
                         "findings": dispatcher.diagnostics.get(device_id, [])})


@app.get("/api/modes")
async def get_modes():
    """Uložené provozní režimy a co se v nich dá nastavovat."""
    data = modes.load()
    return JSONResponse({**data, "labels": modes.MODES,
                         "points": modes.describe()})


@app.post("/api/modes/save")
async def save_mode(payload: dict):
    """Uloží upravený profil jednoho režimu."""
    mode = payload.get("mode")
    if mode not in modes.MODES:
        return JSONResponse({"error": "neznámý režim"}, status_code=400)
    values = payload.get("values")
    if not isinstance(values, dict) or not values:
        # Bez téhle kontroly smazal malformovaný požadavek celý uložený
        # profil: chybějící "values" se bralo jako prázdný profil.
        return JSONResponse({"error": "chybí values s hodnotami profilu"},
                            status_code=400)

    data = modes.load()
    profile, unknown = {}, []
    for dev_id, dev_values in values.items():
        dev = plant.DEVICES_BY_ID.get(dev_id)
        if dev is None or not isinstance(dev_values, dict):
            unknown.append(dev_id)
            continue
        allowed = {h["key"] for h in modes.editable(dev.type)}
        clean = {}
        for key, value in dev_values.items():
            if key not in allowed:
                unknown.append(f"{dev_id}.{key}")
                continue
            checked, problem = check_setpoint(dev_id, key, value)
            if problem:
                return JSONResponse({"error": problem}, status_code=400)
            clean[key] = checked
        profile[dev_id] = clean

    if not profile:
        return JSONResponse({"error": "profil neobsahuje žádné známé zařízení"},
                            status_code=400)
    data["profiles"][mode] = profile
    modes.save(data["profiles"], data["active"])
    return {"mode": mode, "saved": True, "devices": len(profile),
            "ignored": unknown}


@app.post("/api/modes/apply")
async def apply_mode(payload: dict):
    """
    Přepne závod do zvoleného režimu.

    Projde uložený profil a zapíše každou žádanou hodnotu do jejího zařízení
    přes driver — stejnou cestou jako posuvník na obrazovce. Zařízení, které
    zrovna neodpovídá, se přeskočí a řekne se to; zbytek se přepne.
    """
    mode = payload.get("mode")
    if mode not in modes.MODES:
        return JSONResponse({"error": "neznámý režim"}, status_code=400)

    data = modes.load()
    profile = data["profiles"][mode]
    written, failed = 0, []
    for dev_id, values in profile.items():
        link = dispatcher.links.get(dev_id)
        if link is None:
            continue
        for key, value in values.items():
            try:
                await link.write_point(key, value)
                written += 1
            except Exception:
                failed.append(f"{dev_id}.{key}")
                break        # zařízení neodpovídá, zbytek nemá cenu zkoušet
    modes.save(data["profiles"], mode)
    dispatcher.reload_mode_profile()
    return {"mode": mode, "written": written, "failed": failed}


@app.get("/api/anomaly")
async def anomaly_status():
    """Stav modelů chodu ventilátorů — kolik mají nasbíráno a jestli už učí."""
    return JSONResponse({"models": dispatcher.anomaly.status()})


@app.get("/api/healing")
async def healing_status():
    """Stav automatických korekcí a posledních zásahů."""
    return JSONResponse({**dispatcher.healing.status(),
                         "actions": dispatcher.alarms.actions(limit=50),
                         "devices": {d.id: d.name for d in plant.DEVICES}})


@app.post("/api/healing")
async def healing_switch(payload: dict):
    """
    Zapne nebo vypne automatické korekce — globálně, nebo pro jedno zařízení.

    Vypínač je podmínka, ne ozdoba: systém, který zasahuje do technologie a
    nedá se zastavit, je závazek, ne pomoc.
    """
    heal = dispatcher.healing
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        # Dřív se chybějící "enabled" bralo jako False, takže malformovaný
        # požadavek tiše vypnul automatiku. Vypnutí musí být vždycky
        # výslovné.
        return JSONResponse({"error": "enabled musí být true nebo false"},
                            status_code=400)
    device_id = payload.get("device")
    if device_id:
        if device_id not in dispatcher.links:
            return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
        (heal.disabled_devices.discard if enabled
         else heal.disabled_devices.add)(device_id)
    else:
        heal.enabled = enabled
    dispatcher.alarms.log_action(
        device_id or "závod",
        f"Automatické korekce {'zapnuty' if enabled else 'vypnuty'}",
        "změnila obsluha z dispečinku")
    return heal.status()


@app.get("/api/alarms")
async def alarms(scope: str = "active", limit: int = 200, device: str = None,
                 unacked: int = 0):
    """
    Záznamník alarmů. scope=active vrací trvající, scope=history i ukončené.
    """
    if scope not in ("active", "history"):
        # Překlep ve scope dřív tiše vrátil historii místo aktivních alarmů.
        return JSONResponse({"error": "scope musí být active nebo history"},
                            status_code=400)
    if not 1 <= limit <= 1000:
        # SQLite bere negativní LIMIT jako "bez omezení", takže limit=-5
        # vracel celou knihu.
        return JSONResponse({"error": "limit musí být 1 až 1000"},
                            status_code=400)
    if device is not None and device not in dispatcher.links:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)

    log = dispatcher.alarms
    rows = (log.active() if scope == "active"
            else log.history(limit=limit, device=device, only_unacked=bool(unacked)))
    return JSONResponse({"counts": log.counts(), "rows": rows,
                         "devices": {d.id: d.name for d in plant.DEVICES}})


@app.post("/api/alarms/ack")
async def ack_alarms(payload: dict):
    """
    Kvitování alarmu — potvrzení, že o něm operátor ví.

    Nezasahuje do zařízení: alarm zůstane, dokud trvá jeho příčina. Zapíše se
    jen kdo a kdy ho vzal na vědomí, aby se to dalo dohledat.
    Přijímá {ids: [...]} nebo {device: "vzt1"}, plus {by: "jméno"}.
    """
    by = (payload.get("by") or "dispečink")[:60]
    if payload.get("device"):
        n = dispatcher.alarms.ack_device(payload["device"], by)
    else:
        n = dispatcher.alarms.ack(payload.get("ids") or [], by)
    return {"acked": n, "by": by}


@app.post("/api/alarms/reset")
async def reset_device(payload: dict):
    """
    Odblokování poruchy — zápis do kvitovacího registru zařízení.

    Tohle už na zařízení sahá: zapamatovaná porucha se zapomene a stroj smí
    zkusit naběhnout. Když příčina trvá, porucha naskočí okamžitě znovu.
    """
    device_id = payload.get("device")
    link = dispatcher.links.get(device_id)
    if link is None:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
    try:
        await link.write_point("reset", 1.0)
    except Exception as exc:
        return JSONResponse({"error": f"kvitování neprošlo: {exc}"},
                            status_code=502)
    by = (payload.get("by") or "dispečink")[:60]
    dispatcher.alarms.ack_device(device_id, by)
    return {"device": device_id, "reset": True, "by": by}


@app.post("/api/write")
async def write(payload: dict):
    """Zápis žádané hodnoty — {device, key, value}."""
    device_id, key = payload.get("device"), payload.get("key")
    value, problem = check_setpoint(device_id, key, payload.get("value"))
    if problem:
        code = 404 if problem == "neznámé zařízení" else 400
        return JSONResponse({"error": problem}, status_code=code)
    try:
        written = await dispatcher.links[device_id].write_point(key, value)
    except Exception as exc:
        return JSONResponse({"error": f"zápis neprošel: {exc}"}, status_code=502)
    return {"device": device_id, "key": key, "value": written}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    dispatcher.clients.add(ws)
    try:
        if dispatcher.state:
            await ws.send_text(json.dumps(dispatcher.state))
        while True:
            await ws.receive_text()      # klient nic neposílá, jen drží spojení
    except WebSocketDisconnect:
        pass
    finally:
        dispatcher.clients.discard(ws)


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser(description="Webový dispečink závodu")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"Dispečink poběží na http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
