"""
Společné vybavení pro testy.

Testy běží proti ŽIVÉ aplikaci na 127.0.0.1:8000, ne proti podvrženým datům.
Důvod: většina chyb, které se v tomhle projektu našly, se neprojevila v
jednotce, ale ve složení — driver vrátil jiný tvar, než čekala diagnostika;
malformovaný požadavek smazal profil. Test proti běžícímu závodu to chytí.

Když aplikace neběží, testy se přeskočí s jasnou zprávou místo toho, aby
padaly na spojení.
"""

import json
import urllib.error
import urllib.request

import pytest

BASE = "http://127.0.0.1:8000"
TIMEOUT = 20


def request(method, path, payload=None):
    """Vrátí (stav, tělo). Tělo je rozparsované JSON, nebo text."""
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=TIMEOUT) as response:
            raw = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read().decode(), exc.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def get(path):
    return request("GET", path)


def post(path, payload):
    return request("POST", path, payload)


@pytest.fixture(scope="session", autouse=True)
def app_running():
    try:
        status, _ = get("/api/state")
    except Exception as exc:
        pytest.skip(f"aplikace neběží na {BASE} ({exc}) — "
                    f"spusť simulator.py, poller.py a web.server")
    if status != 200:
        pytest.skip(f"aplikace odpovídá {status}")


@pytest.fixture
def state():
    status, body = get("/api/state")
    assert status == 200, f"/api/state vrátilo {status}"
    return body


@pytest.fixture
def meta():
    status, body = get("/api/meta")
    assert status == 200
    return body


@pytest.fixture
def restore_setpoints():
    """
    Vrátí po testu žádané hodnoty tam, kde byly.

    Testy zapisují do živého závodu; bez úklidu by po sobě zanechaly
    přenastavenou technologii a další test by měřil něco jiného.
    """
    saved = []

    def remember(device, key):
        _, body = get("/api/state")
        saved.append((device, key, body["devices"][device]["setpoints"][key]))

    yield remember
    for device, key, value in reversed(saved):
        post("/api/write", {"device": device, "key": key, "value": value})
