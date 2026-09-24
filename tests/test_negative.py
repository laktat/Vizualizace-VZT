"""
Negativní testy: uživatel, který se snaží aplikaci rozbít.

Každý test tady vznikl ze skutečného nálezu při QA, ne z fantazie. Komentář
u něj říká, co se dělo předtím, aby bylo poznat, proč na tom záleží.
"""

import pytest

from conftest import get, post


# --- zápis žádané hodnoty ----------------------------------------------------
@pytest.mark.parametrize("payload,status,duvod", [
    ({"device": "neexistuje", "key": "sp_room", "value": 22}, 404, "cizí zařízení"),
    ({"key": "sp_room", "value": 22}, 404, "chybí zařízení"),
    ({}, 404, "prázdný požadavek"),
    ({"device": "vzt1", "key": "nesmysl", "value": 22}, 400, "cizí klíč"),
    ({"device": "vzt1", "key": "t_supply", "value": 99}, 400, "zápis do měření"),
    ({"device": "vzt1", "key": "sp_room", "value": "teplo"}, 400, "text"),
    ({"device": "vzt1", "key": "sp_room", "value": None}, 400, "null"),
    ({"device": "vzt1", "key": "sp_room", "value": 9999}, 400, "nad rozsahem"),
    ({"device": "vzt1", "key": "sp_room", "value": -500}, 400, "pod rozsahem"),
    ({"device": "vzt1'; DROP TABLE samples;--", "key": "sp_room", "value": 22},
     404, "SQL v názvu zařízení"),
])
def test_zapis_odmita_nesmysly(payload, status, duvod):
    got, body = post("/api/write", payload)
    assert got == status, f"{duvod}: čekal {status}, přišlo {got} ({body})"
    assert "error" in body, f"{duvod}: odpověď bez vysvětlení"


def test_zapis_odmita_nan():
    """
    NAŠEL QA: NaN prošel a tiše se stal NEJVYŠŠÍ žádanou hodnotou.
    Porovnání s NaN je vždy nepravdivé, takže omezení do mezí vrátilo mez.
    Poslat regulaci topení NaN by znamenalo nastavit maximum.
    """
    status, body = post("/api/write",
                        {"device": "vzt1", "key": "sp_room", "value": "NaN"})
    assert status == 400, f"NaN prošel jako {body}"


def test_nesmyslny_index_poruchy_nenasadi_jinou():
    """
    NAŠEL QA: požadavek na poruchu 99 se utnul na nejvyšší platnou, což je
    JINÁ skutečná porucha (výpadek komunikace), a volající dostal 200.
    """
    status, body = post("/api/write",
                        {"device": "vzt1", "key": "fault_sim", "value": 99})
    assert status == 400, f"index 99 prošel jako {body}"
    _, state = get("/api/state")
    assert state["devices"]["vzt1"]["setpoints"]["fault_sim"] == 0.0, \
        "na zařízení se nasadila porucha, kterou nikdo nechtěl"


def test_zapis_v_mezich_se_opravdu_zapise(restore_setpoints):
    """Kontroluje se výsledek, ne jen že to nespadlo."""
    restore_setpoints("vzt1", "sp_room")
    status, body = post("/api/write",
                        {"device": "vzt1", "key": "sp_room", "value": 23.5})
    assert status == 200
    assert body["value"] == 23.5

    import time
    for _ in range(20):
        _, state = get("/api/state")
        if state["devices"]["vzt1"]["setpoints"]["sp_room"] == 23.5:
            return
        time.sleep(1)
    pytest.fail("zapsaná hodnota se do zařízení nedostala")


# --- provozní režimy ---------------------------------------------------------
@pytest.mark.parametrize("payload", [
    {"mode": "podzim"}, {}, {"mode": ""}, {"mode": 5},
])
def test_neznamy_rezim_se_odmitne(payload):
    status, body = post("/api/modes/apply", payload)
    assert status == 400 and "error" in body


def test_save_bez_hodnot_nesmaze_profil():
    """
    NAŠEL QA: save bez "values" bral chybějící pole jako prázdný profil
    a SMAZAL uložené nastavení režimu.
    """
    _, before = get("/api/modes")
    original = dict(before["profiles"]["zima"]["vzt1"])

    status, _ = post("/api/modes/save", {"mode": "zima"})
    assert status == 400, "prázdný save prošel"

    _, after = get("/api/modes")
    assert after["profiles"]["zima"]["vzt1"] == original, "profil se změnil"


def test_save_neprijme_cizi_zarizeni_ani_klic():
    status, body = post("/api/modes/save",
                        {"mode": "zima", "values": {"neexistuje": {"x": 1}}})
    assert status == 400, f"cizí zařízení prošlo: {body}"

    status, body = post("/api/modes/save",
                        {"mode": "zima", "values": {"vzt1": {"hacked": 999}}})
    assert status == 400 or body.get("ignored"), "cizí klíč se uložil bez poznámky"


def test_save_odmitne_hodnotu_mimo_rozsah():
    status, _ = post("/api/modes/save",
                     {"mode": "zima", "values": {"vzt1": {"sp_room": 999}}})
    assert status == 400


# --- automatické korekce -----------------------------------------------------
def test_chybejici_enabled_nic_nevypne():
    """
    NAŠEL QA: chybějící "enabled" se bralo jako False, takže malformovaný
    požadavek tiše vypnul automatiku zasahující do technologie.
    """
    _, before = get("/api/healing")
    status, _ = post("/api/healing", {"device": "vzt1"})
    assert status == 400, "požadavek bez enabled prošel"
    _, after = get("/api/healing")
    assert after["disabled_devices"] == before["disabled_devices"]
    assert after["enabled"] == before["enabled"]


def test_text_v_enabled_se_odmitne():
    status, _ = post("/api/healing", {"enabled": "ano"})
    assert status == 400


def test_korekce_se_da_vypnout_a_zapnout():
    status, body = post("/api/healing", {"enabled": False})
    assert status == 200 and body["enabled"] is False
    status, body = post("/api/healing", {"enabled": True})
    assert status == 200 and body["enabled"] is True


# --- alarmy ------------------------------------------------------------------
def test_preklep_ve_scope_se_odmitne():
    """NAŠEL QA: neznámý scope tiše vrátil historii místo aktivních alarmů."""
    status, _ = get("/api/alarms?scope=nesmysl")
    assert status == 400


def test_negativni_limit_nevrati_celou_knihu():
    """NAŠEL QA: SQLite bere negativní LIMIT jako bez omezení."""
    status, _ = get("/api/alarms?scope=history&limit=-5")
    assert status == 400


def test_limit_se_dodrzuje():
    status, body = get("/api/alarms?scope=history&limit=3")
    assert status == 200
    assert len(body["rows"]) <= 3, "limit se nedodržel"


def test_alarmy_pro_cizi_zarizeni():
    status, _ = get("/api/alarms?scope=history&device=neexistuje")
    assert status == 404


def test_kvitovani_dlouheho_jmena_se_zkrati():
    status, body = post("/api/alarms/ack", {"device": "vzt1", "by": "X" * 5000})
    assert status == 200
    assert len(body["by"]) <= 60, "jméno se neomezilo"


def test_reset_cizi_zarizeni():
    status, _ = post("/api/alarms/reset", {"device": "neexistuje"})
    assert status == 404


# --- historie a diagnostika --------------------------------------------------
def test_historie_cizi_zarizeni():
    status, _ = get("/api/history/neexistuje?keys=t_supply")
    assert status == 404


def test_diagnostika_cizi_zarizeni():
    status, _ = get("/api/diagnostics/neexistuje")
    assert status == 404


def test_cesta_mimo_aplikaci_nevrati_soubor():
    status, body = get("/api/history/..%2F..%2Fetc%2Fpasswd")
    assert status in (404, 400)
    assert "root:" not in str(body)
