"""
Kontrola rozhraní: odpovídá, a odpovídá SPRÁVNĚ.

Nestačí, že požadavek nespadl. U každého se kontroluje i obsah — že stav
obsahuje všechna zařízení, že zapsaná hodnota se opravdu zapsala, že
diagnostika vrací zjištění se známými klíči.
"""

from conftest import get, post

AHUS = ("vzt1", "vzt2", "vzt3")


# --- stav závodu -------------------------------------------------------------
def test_stav_obsahuje_vsechna_zarizeni(state, meta):
    assert len(state["devices"]) == len(meta["devices"]) == 11
    for dev in meta["devices"]:
        assert dev["id"] in state["devices"]


def test_online_zarizeni_maji_hodnoty(state):
    online = [k for k, v in state["devices"].items() if v["online"]]
    assert online, "žádné zařízení neodpovídá"
    for dev_id in online:
        device = state["devices"][dev_id]
        assert device["values"], f"{dev_id} je online, ale bez hodnot"
        assert device["setpoints"], f"{dev_id} je online, ale bez žádaných hodnot"


def test_vzt_maji_vsechny_ocekavane_veliciny(state, meta):
    """Klíče musí být stejné bez ohledu na protokol — Modbus i BACnet."""
    expected = set(meta["types"]["ahu"]["values"].keys())
    for dev_id in AHUS:
        device = state["devices"][dev_id]
        if not device["online"]:
            continue
        assert set(device["values"]) == expected, f"{dev_id} má jiné veličiny"


def test_teploty_jsou_v_fyzikalnim_rozsahu(state):
    for dev_id in AHUS:
        device = state["devices"][dev_id]
        if not device["online"]:
            continue
        for key in ("t_outdoor", "t_extract", "t_supply"):
            value = device["values"][key]
            assert -60 < value < 130, f"{dev_id}.{key} = {value} je nesmysl"


def test_stav_nese_zdroj_dat_a_pocty_alarmu(state):
    assert set(state["source"]) >= {"bus", "direct", "gateway"}
    assert state["source"]["bus"] + state["source"]["direct"] == 11
    assert set(state["alarm_counts"]) == {"active", "unacked", "unacked_total"}


# --- soupis a popis ----------------------------------------------------------
def test_meta_popisuje_oba_protokoly(meta):
    protocols = {d["protocol"] for d in meta["devices"]}
    assert "Modbus TCP" in protocols
    assert "BACnet/IP" in protocols, "VZT3 má jet po BACnetu"


def test_meta_nese_katalog_poruch_a_alarmu(meta):
    for name, spec in meta["types"].items():
        assert spec["alarms"], f"typ {name} nemá texty alarmů"
        assert spec["faults"], f"typ {name} nemá katalog poruch"
        assert spec["setpoints"], f"typ {name} nemá žádané hodnoty"


# --- energetika --------------------------------------------------------------
def test_energetika_scita_spravne(state):
    energy = state["energy"]
    items = energy["electricity"]["items"]
    total = sum(i["energy_kwh"] for i in items)
    assert abs(total - energy["electricity"]["energy_kwh"]) < 1.0, \
        "součet položek nesouhlasí s celkem"
    shares = sum(i["share"] for i in items)
    assert 99.0 < shares < 101.0, f"podíly dávají {shares} %, ne 100"


def test_energetika_nevydava_ukazatele_bez_dat(state):
    """Měrný ukazatel spočítaný z pár kilowatthodin svádí ke špatnému závěru."""
    produced = state["energy"]["produced"]
    for key in ("cool_price", "heat_price"):
        value = produced[key]
        assert value is None or 0.1 < value < 50, \
            f"{key} = {value} je mimo smysluplný rozsah"


# --- diagnostika -------------------------------------------------------------
def test_diagnostika_vraci_znama_zjisteni():
    known = {"cidla", "topny_ventil", "filter_dp_sup", "filter_dp_ext",
             "prutok", "rekuperace", "hala", "anomalie"}
    for dev_id in AHUS:
        status, body = get(f"/api/diagnostics/{dev_id}")
        assert status == 200
        findings = body["findings"]
        assert findings, f"{dev_id} nemá žádná zjištění"
        for finding in findings:
            assert finding["key"] in known, f"neznámé zjištění {finding['key']}"
            assert finding["level"] in ("ok", "warn", "bad", "wait")
            assert finding["msg"], "zjištění bez textu"


def test_model_nikdy_nehlasi_k_reseni():
    """Model není diagnóza — vážnost pojmenuje pravidlo nebo člověk."""
    for dev_id in AHUS:
        _, body = get(f"/api/diagnostics/{dev_id}")
        for finding in body["findings"]:
            if finding["key"] == "anomalie":
                assert finding["level"] != "bad", \
                    "model chodu ventilátoru nesmí dát úroveň k řešení"


# --- historie ----------------------------------------------------------------
def test_historie_vraci_radu_stejne_dlouhou_jako_casy():
    status, body = get("/api/history/vzt1?keys=t_extract,t_supply")
    assert status == 200
    assert len(body["ts"]) > 1, "historie je prázdná"
    for key in ("t_extract", "t_supply"):
        assert len(body[key]) == len(body["ts"]), f"{key} má jinou délku než ts"


def test_historie_je_setrizena_v_case():
    _, body = get("/api/history/vzt1?keys=t_extract")
    assert body["ts"] == sorted(body["ts"]), "historie není v čase vzestupně"
