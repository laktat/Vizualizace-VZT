"""
Dashboard nad daty z VZT jednotky — s živým nákresem a ovládáním.

Spuštění:  streamlit run app.py

- nahoře posuvníky žádané teploty a otáček → zapisují se přes Modbus do jednotky,
  která na ně za běhu reaguje (vyšší otáčky = rychlejší zanášení filtru)
- realistický nákres jednotky s živými teplotami na čidlech a stavem filtru
- diagnostika (čidla, topný ventil, filtr) a grafy
"""

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pymodbus.client import ModbusTcpClient

from analysis import (
    load, latest, filter_forecast, room_check,
    valve_fault_check, sensor_plausibility,
)
import plant
from registers import AHU_HOLDING, by_key, encode

HOLDING_BY_KEY = by_key(AHU_HOLDING)
from schematic import render

st.set_page_config(page_title="Monitoring VZT jednotky", layout="wide")

# --- auto-obnovování každé 3 s (volitelná závislost) -------------------------
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=3000, key="auto")
    _autorefresh = True
except Exception:
    _autorefresh = False

DEVICE = plant.DEVICES_BY_ID["vzt1"]
HOST, PORT, SLAVE = plant.HOST, DEVICE.port, DEVICE.unit_id


def write_setpoints(sp_room, sp_fan):
    """Zapíše žádané hodnoty do holding registrů jednotky přes Modbus."""
    try:
        c = ModbusTcpClient(HOST, port=PORT, timeout=1.5)
        if not c.connect():
            return False
        for key, value in (("sp_room", sp_room), ("sp_fan", sp_fan)):
            reg = HOLDING_BY_KEY[key]
            c.write_register(reg["addr"], encode(value, reg)[0], slave=SLAVE)
        c.close()
        return True
    except Exception:
        return False


st.title(f"Monitoring — {DEVICE.name}")
cap = "Data přes Modbus TCP, žádané hodnoty zapisovány zpět do jednotky."
st.caption(cap + ("  Obnovuje se automaticky každé 3 s." if _autorefresh
                  else "  (Auto-obnovování vypnuté — obnov klávesou R.)"))

# --- ovládání ----------------------------------------------------------------
r = HOLDING_BY_KEY["sp_room"]; f = HOLDING_BY_KEY["sp_fan"]
c1, c2 = st.columns(2)
sp_room = c1.slider("Žádaná teplota místnosti (°C)",
                    r["min"], r["max"], r["default"], 0.5)
sp_fan = c2.slider("Otáčky ventilátorů (%)",
                   int(f["min"]), int(f["max"]), int(f["default"]), 1)

connected = write_setpoints(sp_room, sp_fan)
if not connected:
    st.warning("Simulátor neběží — spusť `python simulator.py`. "
               "Posuvníky se projeví, jakmile poběží.")

vals = latest()
if not vals or "t_supply" not in vals:
    st.info("Zatím žádná data. Spusť `python simulator.py` a `python poller.py`, "
            "chvíli počkej a obnov.")
    st.stop()

# --- NÁKRES JEDNOTKY ---------------------------------------------------------
svg = render(vals, sp_room, sp_fan, dp_limit=HOLDING_BY_KEY["dp_limit"]["default"])
components.html(
    f'<div style="max-width:940px;margin:0 auto">{svg}</div>',
    height=470, scrolling=False,
)

# --- diagnostika -------------------------------------------------------------
st.subheader("Diagnostika")
d1, d2 = st.columns(2)

with d1:
    sp = sensor_plausibility()
    (st.error if not sp["ok"] else st.success)(f"**Čidla:** {sp['msg']}")

    vf = valve_fault_check()
    if vf:
        (st.error if vf["fault"] else st.success)(f"**Topný ventil:** {vf['msg']}")

with d2:
    rc = room_check(setpoint_room=sp_room)
    if rc:
        (st.success if rc["ok"] else st.warning)(f"**Místnost:** {rc['msg']}")

    fc = filter_forecast(load("filter_dp_sup"),
                         limit_pa=HOLDING_BY_KEY["dp_limit"]["default"])
    if fc:
        crit = fc["days_left"] is not None and fc["days_left"] < 14
        (st.error if crit else st.success)(
            f"**Filtr:** {fc['msg']}"
            + (f"  \nTrend: {fc['slope_pa_per_day']:.1f} Pa/den" if crit else ""))

# --- grafy -------------------------------------------------------------------
st.subheader("Teploty v řetězci jednotky")
dfs = {k: load(k) for k in ["t_outdoor", "t_after_recup", "t_supply", "t_extract",
                            "heat_cmd", "filter_dp_sup"]}
temps = pd.concat([
    dfs["t_outdoor"].assign(veličina="venkovní"),
    dfs["t_after_recup"].assign(veličina="za rekuperátorem"),
    dfs["t_supply"].assign(veličina="přívod"),
    dfs["t_extract"].assign(veličina="odtah"),
])
temps = temps[(temps["value"] > -50) & (temps["value"] < 130)]
st.line_chart(temps, x="ts", y="value", color="veličina", height=280)

st.subheader("Povel na ventil vs. skutečný ohřev")
sup = dfs["t_supply"]; rec = dfs["t_after_recup"]
merged = pd.merge(sup, rec, on="ts", suffixes=("_sup", "_rec"))
merged = merged[merged["value_sup"] > -50]
merged["ohřev (°C)"] = merged["value_sup"] - merged["value_rec"]
cmd = dfs["heat_cmd"].rename(columns={"value": "povel ventil (%)"})
chart = pd.merge(merged[["ts", "ohřev (°C)"]], cmd[["ts", "povel ventil (%)"]], on="ts")
if not chart.empty:
    st.line_chart(chart, x="ts", y=["ohřev (°C)", "povel ventil (%)"], height=240)
st.caption("Zdravá jednotka: povel klesne k nule → ohřev taky. "
           "Zaseklý ventil: povel je nula, ohřev drží vysoko.")

st.subheader("Tlaková ztráta filtru a predikce výměny")
dp = dfs["filter_dp_sup"].copy()
limit = HOLDING_BY_KEY["dp_limit"]["default"]
if fc and "fit" in fc:
    slope, intercept, t0 = fc["fit"]
    horizon = pd.date_range(dp["ts"].iloc[0], periods=len(dp) * 2, freq="5s")
    x = (horizon - t0).total_seconds()
    trend = pd.DataFrame({"ts": horizon, "value": slope * x + intercept, "řada": "trend"})
    dp["řada"] = "měření"
    lim = pd.DataFrame({"ts": horizon, "value": limit, "řada": "mez výměny"})
    chart2 = pd.concat([dp, trend, lim])
else:
    chart2 = dp.assign(**{"řada": "měření"})
st.line_chart(chart2, x="ts", y="value", color="řada", height=240)
