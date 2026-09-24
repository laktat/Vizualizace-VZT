"""
Release gate: smí to jít do provozu?

Není to další sada jednotlivých kontrol, ale rozhodnutí. Ptá se na věci,
které musí platit VŽDY, jinak se nevydává — a to i tehdy, když všechny
ostatní testy prošly. Většina z nich vznikla z chyby, která se v tomhle
projektu opravdu stala.

Spouští se jako součást celé sady:  pytest tests/
"""

import subprocess
import time

import pytest

from conftest import get, post

AHUS = ("vzt1", "vzt2", "vzt3")


# --- běží všechny součásti? ---------------------------------------------------
def test_vsechny_soucasti_bezi():
    vystup = subprocess.run(["ps", "ax"], capture_output=True, text=True).stdout
    for popis, vzorek in (("simulátor závodu", "simulator.py"),
                          ("edge gateway", "poller.py"),
                          ("dispečink", "web.server"),
                          ("broker zpráv", "mosquitto")):
        assert vzorek in vystup, f"neběží {popis} ({vzorek})"


def test_vsechna_zarizeni_odpovidaji(state):
    offline = [k for k, v in state["devices"].items() if not v["online"]]
    assert not offline, f"neodpovídají: {offline}"


def test_obe_sbernice_dodavaji_data(state, meta):
    """Ukázka integrace dvou protokolů je smysl projektu; musí jet obě."""
    protocols = {d["protocol"] for d in meta["devices"]}
    assert protocols == {"Modbus TCP", "BACnet/IP"}
    bacnet = [d["id"] for d in meta["devices"] if d["protocol"] == "BACnet/IP"]
    for dev_id in bacnet:
        assert state["devices"][dev_id]["online"], f"{dev_id} po BACnetu nejede"
        assert state["devices"][dev_id]["values"], f"{dev_id} nedodává hodnoty"


# --- úplnost dat --------------------------------------------------------------
def test_zadna_hodnota_nechybi(state, meta):
    """
    NAŠEL QA: jednomu zařízení chyběla ve stavu žádaná hodnota, protože se
    posílaly jen změny a zapamatovaná zpráva nesla jen tu poslední. Zkušební
    panel pak neukázal nasazenou poruchu.
    """
    chybi = []
    for dev in meta["devices"]:
        device = state["devices"][dev["id"]]
        if not device["online"]:
            continue
        spec = meta["types"][dev["type"]]
        for key in spec["values"]:
            if key not in device["values"]:
                chybi.append(f"{dev['id']}.{key} (měřená)")
        for point in spec["setpoints"]:
            if point["key"] not in device["setpoints"]:
                chybi.append(f"{dev['id']}.{point['key']} (žádaná)")
    assert not chybi, f"chybějící hodnoty: {chybi}"


def test_zadna_hodnota_neni_nesmysl(state):
    """Hlídá i to, co projde jako číslo: přetečený registr, NaN, prázdno."""
    for dev_id, device in state["devices"].items():
        if not device["online"]:
            continue
        for key, value in device["values"].items():
            assert isinstance(value, (int, float)), f"{dev_id}.{key} není číslo"
            assert value == value, f"{dev_id}.{key} je NaN"
            assert abs(value) < 1e9, f"{dev_id}.{key} = {value} je nesmysl"


# --- ovládání funguje ---------------------------------------------------------
def test_zapis_projde_az_do_zarizeni(restore_setpoints):
    restore_setpoints("vzt3", "sp_room")
    status, _ = post("/api/write",
                     {"device": "vzt3", "key": "sp_room", "value": 22.5})
    assert status == 200
    for _ in range(25):
        _, state = get("/api/state")
        if state["devices"]["vzt3"]["setpoints"]["sp_room"] == 22.5:
            return
        time.sleep(1)
    pytest.fail("zápis se do zařízení nedostal — ovládání je rozbité")


def test_bezpecnostni_zabrany_korekci_drzi(state):
    """
    Automatika zasahuje do technologie. Když se tyhle dvě věci rozejdou,
    nevydává se: musí být vypínatelná a nesmí jí rozhodovat model.
    """
    healing = state["healing"]
    assert "enabled" in healing, "korekce nemají vypínač"

    status, _ = post("/api/healing", {"device": "vzt1"})
    assert status == 400, "malformovaný požadavek smí vypnout automatiku"

    for dev_id in AHUS:
        _, body = get(f"/api/diagnostics/{dev_id}")
        for finding in body["findings"]:
            if finding["key"] == "anomalie":
                assert finding["level"] != "bad", \
                    "model dává úroveň k řešení a mohl by řídit korekce"


# --- nic tiše nespadlo --------------------------------------------------------
def test_rozhrani_odpovida_na_vsech_cestach():
    cesty = ["/", "/api/meta", "/api/state", "/api/energy", "/api/modes",
             "/api/healing", "/api/anomaly", "/api/alarms",
             "/api/history/vzt1?keys=t_extract", "/api/diagnostics/vzt1"]
    for cesta in cesty:
        status, _ = get(cesta)
        assert status == 200, f"{cesta} vrací {status}"


def test_archiv_i_kniha_alarmu_pisi():
    """Bez záznamu není co dohledat, a to je u dispečinku podmínka."""
    status, alarms = get("/api/alarms?scope=history&limit=5")
    assert status == 200 and alarms["rows"], "kniha alarmů je prázdná"

    _, first = get("/api/history/vzt1?keys=t_extract")
    time.sleep(12)
    _, second = get("/api/history/vzt1?keys=t_extract")
    assert len(second["ts"]) >= len(first["ts"]), "historie se neplní"
