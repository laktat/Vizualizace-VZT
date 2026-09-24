"""
Playwright: kritické cesty uživatele přes rozhraní.

Testuje se to, co uživatel opravdu dělá, a kontroluje se VÝSLEDEK, ne jen
že se něco vykreslilo. Prázdná obrazovka bez výjimky je pořád rozbitá
obrazovka, a číslo zobrazené jako "—" je pořád chybějící hodnota.
"""

import re

import pytest
from playwright.sync_api import expect

from conftest import get, post

BASE = "http://127.0.0.1:8000"
OBRAZOVKY = ["prehled", "vzt1", "vzt2", "vzt3", "chlazeni", "kotelna",
             "energie", "alarmy", "poruchy", "rezimy"]


@pytest.fixture
def app(page):
    """Načte dispečink a počká, až se spojí a naplní daty."""
    page.goto(BASE, wait_until="domcontentloaded")
    expect(page.locator("#link-state.up")).to_be_visible(timeout=20_000)
    expect(page.locator("#nav a[data-view]").first).to_be_visible()
    return page


def cisla_na_obrazovce(page):
    """
    Vrátí hodnoty vypsané z vazby na data — bez těch, co zůstaly prázdné.

    Čte se text_content, ne inner_text: většina hodnot sedí v SVG, a na
    SVG prvcích inner_text neexistuje.
    """
    texts = page.locator("#screen [data-v]").all_text_contents()
    return [t for t in texts if t and t.strip() and t.strip() != "—"]


# --- načtení a navigace ------------------------------------------------------
def test_prehled_se_naplni_zivymi_hodnotami(app):
    expect(app.locator("#view-title")).to_have_text("Přehled závodu")
    expect(app.locator("svg.plan")).to_be_visible()

    vyplnene = cisla_na_obrazovce(app)
    prazdne = app.locator("#screen [data-v]").count() - len(vyplnene)
    assert len(vyplnene) > 40, f"jen {len(vyplnene)} vyplněných hodnot"
    assert prazdne == 0, f"{prazdne} hodnot zůstalo prázdných (—)"


def test_zahlavi_ukazuje_pocet_zarizeni(app):
    expect(app.locator("#online")).to_contain_text("11")


def test_vsechny_obrazovky_se_vykresli(app):
    for view in OBRAZOVKY:
        app.goto(f"{BASE}/#{view}", wait_until="domcontentloaded")
        expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)
        nadpis = app.locator("#view-title").inner_text()
        assert nadpis and nadpis != "—", f"{view}: obrazovka bez nadpisu"
        obsah = app.locator("#screen").inner_text()
        assert len(obsah) > 120, f"{view}: obrazovka je prázdná"


def test_proklik_z_prehledu_do_detailu(app):
    app.locator('#screen [data-goto="vzt1"]').first.click()
    expect(app.locator("#view-title")).to_contain_text("VZT 1")
    expect(app.locator("svg.plan")).to_be_visible()


def test_zivy_provoz_hodnoty_se_meni(app):
    """Bez tohohle by prošla i zamrzlá obrazovka s jednou nactenou hodnotou."""
    app.goto(f"{BASE}/#vzt1", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)
    # teplota v hale je na obrazovce dvakrát (panel i čidlo u odtahu)
    cidlo = app.locator('#screen [data-v="vzt1.t_extract"]').first
    prvni = cidlo.text_content()
    app.wait_for_timeout(12_000)
    assert cidlo.text_content() != prvni, \
        "hodnota se za 12 s nezměnila — chodí vůbec data?"


# --- ovládání ----------------------------------------------------------------
def test_posuvnik_zapise_zadanou_hodnotu(app, restore_setpoints):
    restore_setpoints("vzt2", "sp_room")
    app.goto(f"{BASE}/#vzt2", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)

    slider = app.locator("#sp-vzt2-sp_room")
    expect(slider).to_be_visible()
    slider.fill("23.5")
    slider.dispatch_event("change")

    for _ in range(20):
        _, state = get("/api/state")
        if state["devices"]["vzt2"]["setpoints"]["sp_room"] == 23.5:
            return
        app.wait_for_timeout(1000)
    pytest.fail("posuvník žádanou hodnotu do zařízení nezapsal")


def test_zkusebni_porucha_se_nasadi_a_zrusi(app):
    app.goto(f"{BASE}/#poruchy", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)

    karta = app.locator('.fault-dev[data-dev="chl3"]')
    expect(karta).to_be_visible()
    karta.locator('.opt[data-index="1"]').click()

    expect(karta).to_have_class(re.compile("armed"), timeout=20_000)
    expect(app.locator("#healbar, #testbar").first).to_be_visible()
    _, state = get("/api/state")
    assert state["devices"]["chl3"]["setpoints"]["fault_sim"] == 1.0

    karta.locator('.opt[data-index="0"]').click()
    expect(karta).not_to_have_class(re.compile("armed"), timeout=20_000)
    post("/api/write", {"device": "chl3", "key": "fault_sim", "value": 0})


def alarm_bitu(dev_id, bit):
    """Najde aktivní alarm daného bitu, nebo None."""
    _, alarms = get("/api/alarms?scope=active")
    for a in alarms["rows"]:
        if a["device"] == dev_id and a["bit"] == bit:
            return a
    return None


def cekej(podminka, kroku=40):
    """Počká na podmínku; zdržení hlídá filtrace zákmitů, proto tak dlouho."""
    import time
    for _ in range(kroku):
        vysledek = podminka()
        if vysledek:
            return vysledek
        time.sleep(2)
    return None


def test_kvitovani_zapise_jmeno_obsluhy(app):
    """
    Vyrobí ČERSTVÝ nekvitovaný alarm, kvituje ho v rozhraní a ověří podpis.

    Alarm si test musí založit sám a hlídat ho podle konkrétního bitu:
    kvitovaný alarm má tlačítko zakázané (správně) a věž má vedle poruchy
    ventilátoru i alarm zámrazu bazénu, který nezmizí. Sledovat "jakýkoli
    alarm věže" tedy nestačí.
    """
    FAN1_BIT = 1
    # Odstranit příčinu nestačí: porucha se v zařízení zapamatuje a drží,
    # dokud ji někdo nekvituje. Tak je to navržené, takže úklid před testem
    # musí projít stejnou cestou jako obsluha v poli.
    post("/api/write", {"device": "vez", "key": "fault_sim", "value": 0})
    post("/api/alarms/reset", {"device": "vez", "by": "QA úklid"})
    assert cekej(lambda: alarm_bitu("vez", FAN1_BIT) is None), \
        "alarm poruchy ventilátoru věže nezmizel ani po kvitování"
    post("/api/write", {"device": "vez", "key": "fault_sim", "value": 1})
    alarm = cekej(lambda: alarm_bitu("vez", FAN1_BIT))
    assert alarm, "porucha věže nevyvolala alarm"
    assert not alarm["acked_at"], "čerstvý alarm už je kvitovaný"

    try:
        app.goto(f"{BASE}/#alarmy", wait_until="domcontentloaded")
        expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)
        app.locator("#op").fill("QA tester")
        app.locator("#op").dispatch_event("change")

        radek = app.locator(f'#active-list li[data-id="{alarm["id"]}"]')
        expect(radek).to_be_visible(timeout=30_000)
        radek.locator("button.ack").click()

        podepsany = cekej(lambda: (alarm_bitu("vez", FAN1_BIT) or {}).get("acked_by"))
        assert podepsany == "QA tester", \
            f"v knize je {podepsany!r} místo jména obsluhy"
        expect(radek).to_have_class(re.compile("acked"), timeout=20_000)
    finally:
        post("/api/write", {"device": "vez", "key": "fault_sim", "value": 0})
        post("/api/alarms/reset", {"device": "vez", "by": "QA úklid"})


def test_prepnuti_rezimu_prenastavi_zavod(app):
    app.goto(f"{BASE}/#rezimy", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)

    _, before = get("/api/modes")
    cil = "leto" if before.get("active") != "leto" else "zima"
    app.locator(f'button[data-apply="{cil}"]').click()

    expect(app.locator("#mode-active")).to_contain_text(
        "Letní" if cil == "leto" else "Zimní", timeout=30_000)
    _, after = get("/api/modes")
    assert after["active"] == cil

    ocekavane = after["profiles"][cil]["vzt1"]["sp_room"]
    for _ in range(20):
        _, state = get("/api/state")
        if state["devices"]["vzt1"]["setpoints"]["sp_room"] == ocekavane:
            return
        app.wait_for_timeout(1000)
    pytest.fail("přepnutí režimu se do zařízení nepropsalo")


def test_vypinac_korekci_funguje(app):
    app.goto(f"{BASE}/#alarmy", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)

    _, before = get("/api/healing")
    app.locator("#heal-toggle").click()
    app.wait_for_timeout(2500)
    _, after = get("/api/healing")
    assert after["enabled"] != before["enabled"], "vypínač nic neudělal"

    app.locator("#heal-toggle").click()
    app.wait_for_timeout(2500)
    _, restored = get("/api/healing")
    assert restored["enabled"] == before["enabled"]


# --- obsah obrazovek ---------------------------------------------------------
def test_energetika_ukazuje_naklady_a_rozpad(app):
    app.goto(f"{BASE}/#energie", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)

    polozky = app.locator("#usage-el li")
    expect(polozky.first).to_be_visible(timeout=20_000)
    assert polozky.count() >= 9, "rozpad spotřeby nemá všechna zařízení"
    assert app.locator("#kpi-list li").count() >= 5, "chybí měrné ukazatele"
    text = app.locator("#screen").inner_text()
    assert "Kč" in text, "náklady se nezobrazují"


def test_kniha_alarmu_ma_zaznamy_i_korekce(app):
    app.goto(f"{BASE}/#alarmy", wait_until="domcontentloaded")
    expect(app.locator("#link-state.up")).to_be_visible(timeout=20_000)
    radky = app.locator("#log-body tr")
    expect(radky.first).to_be_visible(timeout=20_000)
    assert radky.count() > 1, "kniha alarmů je prázdná"


def test_v_konzoli_prohlizece_nejsou_chyby(page):
    """Rozhraní nesmí tiše shazovat skript — na tom už jednou stála celá aplikace."""
    chyby = []
    page.on("pageerror", lambda e: chyby.append(str(e)))
    page.on("console", lambda m: chyby.append(m.text) if m.type == "error" else None)

    page.goto(BASE, wait_until="domcontentloaded")
    expect(page.locator("#link-state.up")).to_be_visible(timeout=20_000)
    for view in OBRAZOVKY:
        page.goto(f"{BASE}/#{view}", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

    assert not chyby, "chyby v konzoli prohlížeče:\n" + "\n".join(chyby[:8])
