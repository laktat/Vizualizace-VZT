"""
Realistický nákres vzduchotechnické jednotky (pohled shora) jako SVG.

Protiproudá jednotka s potrubím:
  vlevo dole  SÁNÍ (venku) ─► filtr ─► rekuperátor ─► ohřívač ─► přívodní vent. ─► PŘÍVOD (vpravo dole)
  vpravo horní ODTAH (z místnosti) ─► rekuperátor ─► odtahový vent. ─► ODPAD (vlevo nahoře)

Do nákresu se doplní živé teploty na čidlech, stav filtru (barva podle zanesení),
poloha topného ventilu a otáčky ventilátorů.
"""

from registers import INPUT_REGISTERS

REG = {r["key"]: r for r in INPUT_REGISTERS}

# barvy
CASING = "#eef1f4"; CASING_ST = "#aeb9c4"
DUCT = "#cbd5df"; DUCT_ST = "#8fa0b0"
INK = "#16283a"; MUTE = "#5b6b7a"
BLUE = "#123a5e"; RED = "#d64545"; GREEN = "#2f9e5f"; AMBER = "#e0a72f"


def _bad(key, val):
    r = REG.get(key)
    return r is not None and "valid_min" in r and not (r["valid_min"] <= val <= r["valid_max"])


def _temp(val, bad):
    return "CHYBA" if bad else f"{val:.1f} °C"


def _filter_color(frac):
    if frac < 0.5:  return "#dcf3e5", GREEN
    if frac < 0.85: return "#fdf1cf", AMBER
    return "#fbe0df", RED


def _sensor(cx, cy, label, val, bad, above=True):
    """Čidlo = tečka na potrubí + bublina s hodnotou."""
    col = RED if bad else BLUE
    txtcol = RED if bad else INK
    by = cy - 62 if above else cy + 14
    line_end = (cy - 14) if above else (cy + 14)   # kam vede spojnice k bublině
    return f'''
      <line x1="{cx}" y1="{cy}" x2="{cx}" y2="{line_end}" stroke="{col}" stroke-width="1.5" stroke-dasharray="2 2"/>
      <circle cx="{cx}" cy="{cy}" r="4.5" fill="{col}"/>
      <g transform="translate({cx-46},{by})">
        <rect width="92" height="48" rx="7" fill="#ffffff" stroke="{col}" stroke-width="1.5"/>
        <text x="46" y="18" text-anchor="middle" font-size="10.5" fill="{MUTE}">{label}</text>
        <text x="46" y="37" text-anchor="middle" font-size="16" font-weight="700" fill="{txtcol}">{_temp(val, bad)}</text>
      </g>'''


def _arrow(x, y, d="r", col=DUCT_ST):
    if d == "r": pts = f"{x},{y-5} {x+9},{y} {x},{y+5}"
    elif d == "l": pts = f"{x},{y-5} {x-9},{y} {x},{y+5}"
    return f'<polygon points="{pts}" fill="{col}"/>'


def render(values, setpoint_room, fan, dp_limit=250.0):
    v = values
    def g(k, d=0.0): return v.get(k, d)

    # stavy čidel
    bad = {k: _bad(k, g(k)) for k in ["t_outdoor", "t_after_recup", "t_supply", "t_extract"]}

    dp = g("filter_dp", 45)
    frac = max(0.0, min(1.0, (dp - 45) / max(dp_limit - 45, 1)))
    f_fill, f_st = _filter_color(frac)
    valve = g("valve_cmd", 0)
    fanpct = g("fan_supply", fan)

    svg = f'''<svg viewBox="0 0 980 470" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="'Segoe UI',system-ui,sans-serif">
  <rect x="0" y="0" width="980" height="470" fill="transparent"/>

  <!-- ================= POTRUBÍ ================= -->
  <!-- sání zleva dole -->
  <rect x="8" y="300" width="150" height="46" fill="{DUCT}" stroke="{DUCT_ST}"/>
  <rect x="8" y="300" width="14" height="46" fill="none" stroke="{DUCT_ST}"/>
  <line x1="16" y1="300" x2="16" y2="346" stroke="{DUCT_ST}"/>
  {_arrow(120, 323, "r")}
  <text x="20" y="366" font-size="11" fill="{MUTE}">SÁNÍ (venku)</text>

  <!-- přívod vpravo dole -->
  <rect x="822" y="300" width="150" height="46" fill="{DUCT}" stroke="{DUCT_ST}"/>
  {_arrow(905, 323, "r")}
  <text x="826" y="366" font-size="11" fill="{MUTE}">PŘÍVOD (do místnosti)</text>

  <!-- odtah vpravo nahoře -->
  <rect x="822" y="120" width="150" height="46" fill="{DUCT}" stroke="{DUCT_ST}"/>
  {_arrow(858, 143, "l")}
  <text x="826" y="112" font-size="11" fill="{MUTE}">ODTAH (z místnosti)</text>

  <!-- odpad vlevo nahoře -->
  <rect x="8" y="120" width="150" height="46" fill="{DUCT}" stroke="{DUCT_ST}"/>
  {_arrow(30, 143, "l")}
  <text x="20" y="112" font-size="11" fill="{MUTE}">ODPAD (ven)</text>

  <!-- ================= SKŘÍŇ JEDNOTKY ================= -->
  <rect x="150" y="86" width="680" height="300" rx="12" fill="{CASING}" stroke="{CASING_ST}" stroke-width="2"/>
  <text x="166" y="106" font-size="12" font-weight="700" fill="{BLUE}">VZT JEDNOTKA — pohled shora</text>

  <!-- vodicí proudnice (dráhy vzduchu) -->
  <text x="166" y="300" font-size="10" fill="{MUTE}">přívodní vzduch ►</text>
  <text x="736" y="176" font-size="10" fill="{MUTE}" text-anchor="end">◄ odtahový vzduch</text>

  <!-- ===== FILTR (přívod) ===== -->
  <g>
    <rect x="196" y="286" width="54" height="74" rx="4" fill="{f_fill}" stroke="{f_st}" stroke-width="2"/>
    <line x1="196" y1="300" x2="250" y2="300" stroke="{f_st}" stroke-dasharray="3 3"/>
    <line x1="196" y1="316" x2="250" y2="316" stroke="{f_st}" stroke-dasharray="3 3"/>
    <line x1="196" y1="332" x2="250" y2="332" stroke="{f_st}" stroke-dasharray="3 3"/>
    <line x1="196" y1="348" x2="250" y2="348" stroke="{f_st}" stroke-dasharray="3 3"/>
    <text x="223" y="378" text-anchor="middle" font-size="10" fill="{MUTE}">FILTR</text>
    <!-- ukazatel zanesení -->
    <rect x="196" y="266" width="54" height="9" rx="4" fill="#e5e9ee" stroke="{DUCT_ST}"/>
    <rect x="196" y="266" width="{54*frac:.0f}" height="9" rx="4" fill="{f_st}"/>
    <text x="223" y="258" text-anchor="middle" font-size="11" font-weight="700" fill="{f_st}">{dp:.0f} Pa</text>
  </g>

  <!-- ===== REKUPERÁTOR (protiproudý, obě dráhy) ===== -->
  <g>
    <rect x="360" y="150" width="150" height="210" rx="6" fill="#e9eff6" stroke="#9db2c6" stroke-width="2"/>
    <line x1="360" y1="150" x2="510" y2="360" stroke="#9db2c6" stroke-width="1.5"/>
    <line x1="510" y1="150" x2="360" y2="360" stroke="#9db2c6" stroke-width="1.5"/>
    <text x="435" y="256" text-anchor="middle" font-size="11" font-weight="700" fill="{BLUE}">REKUPERÁTOR</text>
    <text x="435" y="272" text-anchor="middle" font-size="9.5" fill="{MUTE}">zpětné získávání tepla</text>
  </g>

  <!-- ===== TOPNÝ OHŘÍVAČ + ventil ===== -->
  <g>
    <rect x="560" y="286" width="60" height="74" rx="4" fill="#ffe6d2" stroke="#e08b4a" stroke-width="2"/>
    <path d="M566 300 q8 -8 16 0 q8 8 16 0 q8 -8 16 0" fill="none" stroke="#d9772e" stroke-width="2"/>
    <path d="M566 318 q8 -8 16 0 q8 8 16 0 q8 -8 16 0" fill="none" stroke="#d9772e" stroke-width="2"/>
    <path d="M566 336 q8 -8 16 0 q8 8 16 0 q8 -8 16 0" fill="none" stroke="#d9772e" stroke-width="2"/>
    <text x="590" y="378" text-anchor="middle" font-size="10" fill="{MUTE}">OHŘÍVAČ</text>
    <!-- ventil -->
    <circle cx="590" cy="266" r="13" fill="#fff" stroke="#d9772e" stroke-width="2"/>
    <text x="590" y="270" text-anchor="middle" font-size="10" font-weight="700" fill="#b45f1e">{valve:.0f}%</text>
    <text x="590" y="246" text-anchor="middle" font-size="9.5" fill="{MUTE}">ventil</text>
  </g>

  <!-- ===== PŘÍVODNÍ VENTILÁTOR ===== -->
  <g>
    <circle cx="720" cy="323" r="26" fill="#e9eff6" stroke="#9db2c6" stroke-width="2"/>
    <g stroke="#5b7288" stroke-width="2.2" fill="none">
      <path d="M720 323 q -14 -10 -22 2"/>
      <path d="M720 323 q 14 -10 22 2"/>
      <path d="M720 323 q 0 17 -18 12"/>
    </g>
    <circle cx="720" cy="323" r="3.5" fill="#5b7288"/>
    <text x="720" y="368" text-anchor="middle" font-size="10" fill="{MUTE}">PŘÍVOD. VENT.</text>
    <text x="720" y="300" text-anchor="middle" font-size="11" font-weight="700" fill="{BLUE}">{fanpct:.0f}%</text>
  </g>

  <!-- ===== ODTAHOVÝ VENTILÁTOR ===== -->
  <g>
    <circle cx="300" cy="143" r="24" fill="#e9eff6" stroke="#9db2c6" stroke-width="2"/>
    <g stroke="#5b7288" stroke-width="2.2" fill="none">
      <path d="M300 143 q -13 -9 -20 2"/>
      <path d="M300 143 q 13 -9 20 2"/>
      <path d="M300 143 q 0 16 -16 11"/>
    </g>
    <circle cx="300" cy="143" r="3.2" fill="#5b7288"/>
    <text x="300" y="184" text-anchor="middle" font-size="10" fill="{MUTE}">ODTAH. VENT.</text>
  </g>

  <!-- ================= ČIDLA ================= -->
  {_sensor(120, 323, "venkovní", g("t_outdoor"), bad["t_outdoor"], above=True)}
  {_sensor(535, 323, "za rekuperátorem", g("t_after_recup"), bad["t_after_recup"], above=True)}
  {_sensor(788, 323, "přívod", g("t_supply"), bad["t_supply"], above=False)}
  {_sensor(788, 143, "odtah / místnost", g("t_extract"), bad["t_extract"], above=False)}

  <!-- žádaná teplota -->
  <g transform="translate(360,96)">
    <rect width="150" height="30" rx="6" fill="#e6eef6" stroke="{BLUE}"/>
    <text x="10" y="19" font-size="11" fill="{MUTE}">žádaná v místnosti:</text>
    <text x="140" y="20" text-anchor="end" font-size="14" font-weight="700" fill="{BLUE}">{setpoint_room:.1f} °C</text>
  </g>
</svg>'''
    return svg


if __name__ == "__main__":
    demo = {"t_outdoor": 6.2, "t_after_recup": 16.8, "t_supply": 21.4,
            "t_extract": 21.9, "valve_cmd": 34, "filter_dp": 120, "fan_supply": 78}
    open("demo.svg", "w").write(render(demo, 22.0, 78.0))
    print("demo.svg zapsano")
