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
from pymodbus.client import AsyncModbusTcpClient

import diagnostics
import plant
import registers as regs

STATIC = Path(__file__).parent / "static"
POLL_INTERVAL = 1.0        # jak často se čtou zařízení [s]
HISTORY_LEN = 900          # kolik vzorků se drží pro trendy (15 min)
DIAG_INTERVAL = 5.0        # jak často se přepočítává vyhodnocení provozu [s]
ARCHIVE = Path(__file__).parent.parent / "data.sqlite"
SEED_MAX_AGE = 3600       # jak staré vzorky se ještě načtou z archivu [s]


class DeviceLink:
    """Spojení na jedno zařízení — čtení měření a zápis žádaných hodnot."""

    def __init__(self, dev):
        self.dev = dev
        spec = regs.DEVICE_TYPES[dev.type]
        self.input = spec["input"]
        self.holding = spec["holding"]
        self.count = regs.span(self.input)
        self.hold_count = regs.span(self.holding)
        # klient se vytvoří až v běžící smyčce — pymodbus si při vzniku
        # sahá po aktuálním event loopu
        self.client = None
        self.online = False

    def _connect_obj(self):
        if self.client is None:
            self.client = AsyncModbusTcpClient(plant.HOST, port=self.dev.port, timeout=2)
        return self.client

    async def read(self):
        """Vrátí (měření, žádané hodnoty) nebo (None, None) při výpadku."""
        client = self._connect_obj()
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

    async def write(self, key, value):
        """Zapíše jednu žádanou hodnotu do zařízení."""
        reg = regs.by_key(self.holding).get(key)
        if reg is None:
            raise KeyError(f"{self.dev.id} nemá registr {key}")
        value = max(reg["min"], min(reg["max"], float(value)))
        client = self._connect_obj()
        if not client.connected:
            await client.connect()
        rr = await client.write_register(
            reg["addr"], regs.encode(value, reg)[0], slave=self.dev.unit_id)
        if rr.isError():
            raise IOError(str(rr))
        return value


class Dispatcher:
    """Sběr dat ze všech zařízení a rozesílání stavu do prohlížečů."""

    def __init__(self):
        self.links = {d.id: DeviceLink(d) for d in plant.DEVICES}
        self.state = {}
        self.history = {d.id: deque(maxlen=HISTORY_LEN) for d in plant.DEVICES}
        self.diagnostics = {}
        self.diag_at = 0.0
        self.clients = set()
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
            self.diagnostics[dev.id] = diagnostics.diagnose(
                dev, list(self.history[dev.id]), d["values"], d["setpoints"])

    async def poll_once(self):
        results = await asyncio.gather(*(l.read() for l in self.links.values()))
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = {"ts": ts, "devices": {}}

        for link, (values, setpoints) in zip(self.links.values(), results):
            dev = link.dev
            if values is None:
                state["devices"][dev.id] = {"online": False}
                continue
            alarm_names = regs.DEVICE_TYPES[dev.type]["alarms"]
            state["devices"][dev.id] = {
                "online": True,
                "values": {k: round(v, 3) for k, v in values.items()},
                "setpoints": {k: round(v, 3) for k, v in setpoints.items()},
                "alarms": regs.bits(values.get("alarms", 0), alarm_names),
            }
            self.history[dev.id].append({"ts": ts, **values})

        self.update_diagnostics(state)
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
        """Hlavní smyčka — čte zařízení a posílá stav všem prohlížečům."""
        while True:
            try:
                state = await self.poll_once()
                if self.clients:
                    await self.broadcast(json.dumps(state))
            except Exception as exc:
                print(f"chyba sběru: {exc}")
            await asyncio.sleep(POLL_INTERVAL)


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
        }
    return {
        "areas": plant.AREAS,
        "devices": [{"id": d.id, "name": d.name, "type": d.type,
                     "area": d.area, "port": d.port} for d in plant.DEVICES],
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
    return FileResponse(STATIC / "index.html")


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


@app.get("/api/diagnostics/{device_id}")
async def device_diagnostics(device_id: str):
    """Vyhodnocení provozu jednoho zařízení."""
    if device_id not in dispatcher.links:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
    return JSONResponse({"device": device_id,
                         "findings": dispatcher.diagnostics.get(device_id, [])})


@app.post("/api/write")
async def write(payload: dict):
    """Zápis žádané hodnoty do zařízení — {device, key, value}."""
    device_id, key = payload.get("device"), payload.get("key")
    link = dispatcher.links.get(device_id)
    if link is None:
        return JSONResponse({"error": "neznámé zařízení"}, status_code=404)
    try:
        value = await link.write(key, payload.get("value"))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"device": device_id, "key": key, "value": value}


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


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser(description="Webový dispečink závodu")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"Dispečink poběží na http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
