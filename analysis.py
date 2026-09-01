"""
Vyhodnocení dat z jednotky.

Tři nezávislé kontroly:
  1) filter_forecast     — kdy dojde k zanesení filtru (predikce výměny)
  2) valve_fault_check   — topí se, i když regulace posílá na ventil 0 %?
  3) sensor_plausibility — hlásí některé čidlo nesmysl mimo fyzikální rozsah?

Princip je záměrně jednoduchý a čitelný. Pointa není ve složitém modelu,
ale v tom, že kontroly odpovídají tomu, jak se jednotka chová fyzicky.
"""

import sqlite3

import numpy as np
import pandas as pd

from registers import INPUT_REGISTERS

DB = "data.sqlite"
REG = {r["key"]: r for r in INPUT_REGISTERS}


def load(key, db=DB):
    con = sqlite3.connect(db)
    df = pd.read_sql_query(
        "SELECT ts, value FROM samples WHERE key = ? ORDER BY ts", con, params=(key,)
    )
    con.close()
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def latest(db=DB):
    """Poslední naměřená hodnota od každé veličiny -> dict."""
    con = sqlite3.connect(db)
    rows = con.execute("""
        SELECT s.key, s.value FROM samples s
        JOIN (SELECT key, MAX(ts) AS mx FROM samples GROUP BY key) m
          ON s.key = m.key AND s.ts = m.mx
    """).fetchall()
    con.close()
    return dict(rows)


# --- 1) zanášení filtru ------------------------------------------------------
def filter_forecast(df, limit_pa=250.0, min_points=10):
    if len(df) < min_points:
        return None

    t0 = df["ts"].iloc[0]
    x = (df["ts"] - t0).dt.total_seconds().to_numpy()
    y = df["value"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    current = y[-1]

    if slope <= 0:
        return {"slope_pa_per_day": slope * 86400, "current_pa": current,
                "limit_pa": limit_pa, "days_left": None,
                "msg": "Tlaková ztráta neroste — filtr je v pořádku."}

    days_left = (limit_pa - current) / slope / 86400
    if days_left < 0:
        msg = "Filtr už je za mezí — vyměnit hned."
    elif days_left < 1:
        msg = f"Filtr dosáhne meze zhruba za {days_left * 24:.1f} h — naplánovat výměnu."
    elif days_left < 14:
        msg = f"Filtr dosáhne meze zhruba za {days_left:.1f} dne — naplánovat výměnu."
    else:
        msg = f"Filtr vydrží ještě zhruba {days_left:.0f} dní."

    return {"slope_pa_per_day": slope * 86400, "current_pa": current,
            "limit_pa": limit_pa, "days_left": days_left, "msg": msg,
            "fit": (slope, intercept, t0)}


# --- 2) zaseklý / vadný topný ventil ----------------------------------------
def valve_fault_check(window=20, cmd_closed=5.0, setpoint_room=22.0,
                      room_tol=1.5, coil_tol=3.0, min_closed=5):
    """
    Regulace řídí na teplotu MÍSTNOSTI. Když místnost dosáhne žádané teploty,
    pošle na ventil 0 % — a od té chvíle se místnost nesmí dál přehřívat.
    Když se přesto drží nad žádanou teplotou, ventil topí, i když nemá.

    Vyhodnocují se dvě nezávislé evidence:
      room_evidence — při zavřeném ventilu se místnost drží nad žádanou teplotou
                      (hlavní signál, odpovídá regulační logice)
      coil_evidence — přívod je i tak teplejší než vzduch za rekuperátorem,
                      tzn. ohřívač reálně topí (rychlé potvrzení přímo na ventilu)

    window     = kolik posledních vzorků beru
    cmd_closed = povel pod touto hodnotou [%] považuji za "zavřeno"
    room_tol   = o kolik smí být místnost nad žádanou, než to beru jako přetápění
    coil_tol   = dovolený rozdíl přívod - rekuperátor při zavřeném ventilu [°C]
    min_closed = kolik vzorků se zavřeným ventilem musí být, aby mělo smysl soudit
    """
    cmd = load("valve_cmd").tail(window).reset_index(drop=True)
    room = load("t_extract").tail(window).reset_index(drop=True)
    sup = load("t_supply").tail(window).reset_index(drop=True)
    rec = load("t_after_recup").tail(window).reset_index(drop=True)
    n = min(len(cmd), len(room), len(sup), len(rec))
    if n < min_closed:
        return None

    cmd = cmd["value"].to_numpy()[-n:]
    room = room["value"].to_numpy()[-n:]
    heating = sup["value"].to_numpy()[-n:] - rec["value"].to_numpy()[-n:]

    closed = cmd < cmd_closed
    n_closed = int(closed.sum())
    if n_closed < min_closed:
        # Ventil skoro pořád moduluje = aktivně reaguje. Když k tomu místnost
        # drží žádanou teplotu, je to zdravý stav, ne "nevím".
        room_ok = abs(room.mean() - setpoint_room) <= room_tol
        if room_ok:
            msg = ("Ventil moduluje a místnost drží žádanou teplotu "
                   f"({room.mean():.1f} °C) — reaguje normálně.")
        else:
            msg = "Ventil zatím nebyl dost dlouho zavřený na plné vyhodnocení."
        return {"fault": False, "room_evidence": False, "coil_evidence": False,
                "n_closed": n_closed, "evaluated": bool(room_ok), "msg": msg}

    room_closed = room[closed]
    heat_closed = heating[closed]

    room_evidence = room_closed.mean() > setpoint_room + room_tol
    coil_evidence = heat_closed.mean() > coil_tol
    fault = room_evidence or coil_evidence

    if fault:
        msg = ("Ventil hlásí zavřeno, ale topí se dál. "
               f"Místnost drží {room_closed.mean():.1f} °C "
               f"(žádaná {setpoint_room:.0f} °C) a přívod je o "
               f"{heat_closed.mean():.1f} °C nad rekuperátorem. "
               "Zkontrolovat pohon a topný ventil.")
    else:
        msg = "Při zavřeném ventilu se místnost nepřehřívá — ventil reaguje normálně."

    return {"fault": fault, "room_evidence": bool(room_evidence),
            "coil_evidence": bool(coil_evidence), "n_closed": n_closed,
            "room_mean": float(room_closed.mean()),
            "heat_mean": float(heat_closed.mean()), "msg": msg}


# --- 3) věrohodnost čidel ----------------------------------------------------
def sensor_plausibility(values=None, window=15):
    """
    Označí čidla, která hlásí hodnotu mimo fyzikálně smysluplný rozsah
    (viz valid_min/valid_max v registers.py). Takovému čidlu regulace
    nesmí věřit — typicky -120 °C = přerušený nebo zkratovaný obvod.

    DŮLEŽITÉ: nekouká jen na poslední hodnotu, ale na posledních `window`
    vzorků každého čidla. Vadné čidlo často "bliká" — chvíli měří správně,
    chvíli hlásí nesmysl. Kdyby se hlídala jen poslední hodnota, přerušované
    poruchy by se minuly. Stačí jeden nevěrohodný vzorek a čidlu se nevěří.
    """
    bad = []
    for reg in INPUT_REGISTERS:
        if "valid_min" not in reg:
            continue
        df = load(reg["key"]).tail(window)
        if df.empty:
            continue
        out = df[(df["value"] < reg["valid_min"]) | (df["value"] > reg["valid_max"])]
        if not out.empty:
            worst = out.loc[out["value"].abs().idxmax(), "value"]
            bad.append({"key": reg["key"], "name": reg["name"], "value": worst,
                        "unit": reg["unit"], "count": len(out), "of": len(df),
                        "range": (reg["valid_min"], reg["valid_max"])})

    if not bad:
        return {"ok": True, "bad": [], "msg": "Všechna čidla hlásí věrohodné hodnoty."}

    parts = [f"{b['name']} = {b['value']:.0f} {b['unit']} "
             f"(mimo {b['range'][0]}–{b['range'][1]}, {b['count']}/{b['of']} vzorků)"
             for b in bad]
    return {"ok": False, "bad": bad,
            "msg": "Čidlo mimo rozsah, nedůvěřovat: " + "; ".join(parts)}


def room_check(setpoint_room=22.0, tolerance=1.5):
    """Hlídá, jestli jednotka drží žádanou teplotu místnosti (odtah)."""
    df = load("t_extract")
    if df.empty:
        return None
    recent = df["value"].tail(20)
    recent = recent[(recent > -50) & (recent < 120)]   # ignoruj jen rozbité čidlo
    if recent.empty:
        return None
    deviation = recent.mean() - setpoint_room
    ok = abs(deviation) <= tolerance
    return {"ok": ok, "deviation": deviation,
            "msg": ("Místnost drží žádanou teplotu."
                    if ok else
                    f"Teplota místnosti se odchyluje o {deviation:+.1f} °C od žádané.")}

if __name__ == "__main__":
    print("=== Filtr ===")
    fc = filter_forecast(load("filter_dp"))
    print(fc["msg"] if fc else "málo dat")

    print("\n=== Topný ventil ===")
    vf = valve_fault_check()
    print(vf["msg"] if vf else "málo dat")

    print("\n=== Místnost ===")
    rc = room_check()
    print(rc["msg"] if rc else "málo dat")

    print("\n=== Čidla ===")
    print(sensor_plausibility()["msg"])
