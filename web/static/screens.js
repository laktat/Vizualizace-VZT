/*
 * Technologická schémata závodu.
 *
 * Každá obrazovka se poskládá jednou při přepnutí a pak už se jen doplňují
 * hodnoty — proto se ve schématu nic nepřekresluje a animace běží plynule.
 * Vazba na data se dělá atributy:
 *
 *   data-v="vzt1.t_supply"    text se nahradí hodnotou veličiny
 *   data-map="pumpStates"     hodnota se přeloží na název stavu
 *   data-run="vez.f1_speed"   prvku se přidá třída "on"/"run", když stroj běží
 *   data-bar="vzt1.heat_cmd"  šířka sloupce podle hodnoty
 *   data-led="chl1.state"     barva kontrolky podle stavu
 */

// --- stavební kameny ---------------------------------------------------------
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");

function T(x, y, txt, cls = "lbl", anchor = "start") {
  return `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${esc(txt)}</text>`;
}

function V(x, y, ref, o = {}) {
  const { dec = 1, unit = "", cls = "val", anchor = "start", map = "" } = o;
  return `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}"`
    + ` data-v="${ref}" data-dec="${dec}" data-unit="${unit}"`
    + `${map ? ` data-map="${map}"` : ""}>—</text>`;
}

/** Popiska a pod ní hodnota — nejčastější dvojice ve schématu. */
function field(x, y, label, ref, o = {}) {
  return T(x, y, label, o.small ? "lbl-sm" : "lbl", o.anchor || "start")
    + V(x, y + (o.small ? 15 : 19), ref, { cls: o.big ? "val-lg" : (o.small ? "val-sm" : "val"), ...o });
}

/** Vodorovný sloupec 0–100 % (poloha ventilu, otáčky, modulace). */
function bar(x, y, w, ref, o = {}) {
  const { max = 100, cls = "", h = 7 } = o;
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}" class="bar-bg"/>`
    + `<rect x="${x}" y="${y}" height="${h}" rx="${h / 2}" class="bar-fill ${cls}"`
    + ` data-bar="${ref}" data-bar-max="${max}" data-bar-w="${w}"/>`;
}

/** Kontrolka stavu stroje. */
function led(x, y, ref, map = "motor") {
  return `<circle cx="${x}" cy="${y}" class="led" data-led="${ref}" data-led-map="${map}"/>`;
}

/** Potrubí: podklad + animovaná náplň, která teče, jen když je průtok. */
function pipe(d, kind, flowRef, o = {}) {
  const min = o.min ?? 1;
  return `<path d="${d}" class="pipe ${kind}"/>`
    + `<path d="${d}" class="flow ${kind}" data-run="${flowRef}" data-run-min="${min}"`
    + `${o.reverse ? ' data-flow-reverse="1"' : ""}/>`;
}

/** Ventilátor — kolo se otáčí tím rychleji, čím vyšší jsou otáčky. */
function fan(cx, cy, r, ref) {
  const blades = [0, 60, 120, 180, 240, 300].map(a =>
    `<ellipse cx="${cx}" cy="${cy - r * 0.55}" rx="${r * 0.22}" ry="${r * 0.42}"
        transform="rotate(${a} ${cx} ${cy})" fill="#43607f"/>`).join("");
  return `<g class="rotor" data-run="${ref}" data-run-min="2" data-speed="${ref}">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="#16202e" stroke="#3b5170" stroke-width="1.5"/>
      ${blades}<circle cx="${cx}" cy="${cy}" r="${r * 0.18}" fill="#7fb4d8"/></g>`;
}

/** Čerpadlo — kolečko s rotorem a kontrolkou stavu. */
function pump(x, y, ref, label, o = {}) {
  const r = o.r || 17;
  return `<g>
    <circle cx="${x}" cy="${y}" r="${r}" fill="#16202e" stroke="#3b5170" stroke-width="1.5"/>
    <g class="rotor" data-run="${ref}_speed" data-run-min="2" data-speed="${ref}_speed">
      <path d="M ${x - r * .55} ${y} A ${r * .55} ${r * .55} 0 0 1 ${x + r * .55} ${y}"
            fill="none" stroke="#7fb4d8" stroke-width="3.4" stroke-linecap="round"/>
      <circle cx="${x}" cy="${y}" r="2.6" fill="#7fb4d8"/>
    </g>
    ${led(x + r - 2, y - r + 2, `${ref}_state`, "pump")}
    ${T(x, y + r + 15, label, "lbl-sm", "middle")}
  </g>`;
}

/** Čidlo na potrubí: tečka, spojnice a bublina s hodnotou. */
function sensor(x, y, label, ref, o = {}) {
  const up = o.down ? -1 : 1, by = y - up * 62, dec = o.dec ?? 1;
  return `<g>
    <line x1="${x}" y1="${y}" x2="${x}" y2="${y - up * 16}" stroke="#56b6e0"
          stroke-width="1.4" stroke-dasharray="2 2"/>
    <circle cx="${x}" cy="${y}" r="4" fill="#56b6e0"/>
    <rect x="${x - 46}" y="${by - 2}" width="92" height="46" rx="7"
          fill="#16202e" stroke="#3b5170" stroke-width="1.4"/>
    ${T(x, by + 16, label, "lbl-sm", "middle")}
    ${V(x, by + 35, ref, { dec, unit: o.unit ?? " °C", cls: "val", anchor: "middle" })}
  </g>`;
}

/** Rám technologického celku s nadpisem. */
function frame(x, y, w, h, title, o = {}) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" class="case"/>`
    + T(x + 18, y + 26, title, "title")
    + (o.goto ? `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12"
         class="hit" data-goto="${o.goto}"><title>Otevřít detail</title></rect>` : "");
}

const SCREENS = {};

// =============================================================================
// Přehled závodu
// =============================================================================
SCREENS.prehled = () => {
  const ahus = [
    { id: "vzt1", name: "VZT 1 — Výrobní hala A", x: 40 },
    { id: "vzt2", name: "VZT 2 — Lakovna", x: 500 },
    { id: "vzt3", name: "VZT 3 — Sklad a administrativa", x: 960 },
  ];

  const cards = ahus.map(a => `<g class="card" data-goto="${a.id}">
    ${frame(a.x, 40, 440, 200, a.name)}
    ${led(a.x + 420, 58, `${a.id}.state`, "ahu")}
    ${field(a.x + 20, 96, "teplota v hale", `${a.id}.t_extract`, { unit: " °C", big: true })}
    ${field(a.x + 140, 96, "přívod", `${a.id}.t_supply`, { unit: " °C", big: true })}
    ${field(a.x + 250, 96, "venku", `${a.id}.t_outdoor`, { unit: " °C", big: true })}
    ${field(a.x + 350, 96, "žádaná", `${a.id}.sp:sp_room`, { unit: " °C", big: true })}

    ${T(a.x + 20, 152, "ventilátory", "lbl-sm")}
    ${bar(a.x + 20, 159, 110, `${a.id}.fan_supply`)}
    ${V(a.x + 138, 166, `${a.id}.fan_supply`, { dec: 0, unit: " %", cls: "val-sm" })}
    ${T(a.x + 210, 152, "ohřev", "lbl-sm")}
    ${bar(a.x + 210, 159, 70, `${a.id}.heat_cmd`, { cls: "warm" })}
    ${T(a.x + 310, 152, "chlazení", "lbl-sm")}
    ${bar(a.x + 310, 159, 70, `${a.id}.cool_cmd`, { cls: "cold" })}

    ${T(a.x + 20, 196, "filtr přívodu", "lbl-sm")}
    ${bar(a.x + 20, 203, 110, `${a.id}.filter_dp_sup`, { max: 250, cls: "ok" })}
    ${V(a.x + 138, 210, `${a.id}.filter_dp_sup`, { dec: 0, unit: " Pa", cls: "val-sm" })}
    ${T(a.x + 210, 196, "průtok vzduchu", "lbl-sm")}
    ${V(a.x + 210, 212, `${a.id}.flow_supply`, { dec: 0, unit: " m³/h", cls: "val-sm" })}
    ${T(a.x + 350, 196, "odběr chladu", "lbl-sm")}
    ${V(a.x + 350, 212, `${a.id}.chw_power`, { dec: 0, unit: " kW", cls: "val-sm" })}
  </g>`).join("");

  // svislé přípojky jednotek na oba rozvody
  const drops = ahus.map(a => {
    const xh = a.x + 150, xc = a.x + 300;
    return pipe(`M ${xh} 240 V 322`, "warm", `${a.id}.hw_power`, { min: 2 })
      + pipe(`M ${xc} 240 V 392`, "cold", `${a.id}.chw_power`, { min: 2 });
  }).join("");

  return `<svg class="plan" viewBox="0 0 1440 900">
    ${cards}
    ${drops}

    <!-- rozvody topné a chlazené vody -->
    ${pipe("M 80 322 H 1340", "warm", "kotelna.flow", { min: 1 })}
    ${T(700, 312, "topná voda", "lbl-sm")}
    ${V(770, 313, "kotelna.t_header_flow", { unit: " °C", cls: "val-sm" })}
    ${pipe("M 120 392 H 1380", "cold", "chw.flow_sec", { min: 1 })}
    ${T(700, 382, "chlazená voda", "lbl-sm")}
    ${V(786, 383, "chw.t_supply", { unit: " °C", cls: "val-sm" })}

    <!-- přípojky zdrojů vedou po krajích, aby nekřížily druhý rozvod -->
    ${pipe("M 80 322 V 470", "warm", "kotelna.flow", { min: 1 })}
    ${pipe("M 1380 392 V 470", "cold", "chw.flow_sec", { min: 1 })}

    <!-- KOTELNA -->
    <g class="card" data-goto="kotelna">
      ${frame(40, 470, 560, 380, "Kotelna")}
      ${field(60, 512, "rozdělovač", "kotelna.t_header_flow", { unit: " °C", big: true })}
      ${field(190, 512, "sběrač", "kotelna.t_header_return", { unit: " °C", big: true })}
      ${field(310, 512, "žádaná (ekvitermní)", "kotelna.sp_calc", { unit: " °C", big: true })}
      ${field(470, 512, "výkon", "kotelna.heat_power", { dec: 0, unit: " kW", big: true })}

      ${[1, 2].map((n, i) => `<g>
        ${frame(60 + i * 270, 570, 250, 150, `Kotel ${n}`, {})}
        ${led(290 + i * 270, 588, `kotel${n}.burner`, "burner")}
        ${V(80 + i * 270, 618, `kotel${n}.burner`, { map: "burnerStates", cls: "val-sm" })}
        ${field(80 + i * 270, 636, "výstupní voda", `kotel${n}.t_flow`, { unit: " °C", small: true })}
        ${field(180 + i * 270, 636, "výkon", `kotel${n}.power`, { dec: 0, unit: " kW", small: true })}
        ${T(80 + i * 270, 690, "modulace hořáku", "lbl-sm")}
        ${bar(80 + i * 270, 697, 150, `kotel${n}.modulation`, { cls: "warm" })}
        ${V(240 + i * 270, 704, `kotel${n}.modulation`, { dec: 0, unit: " %", cls: "val-sm" })}
      </g>`).join("")}

      ${pump(110, 780, "kotelna.hp1", "Čerpadlo 1")}
      ${pump(190, 780, "kotelna.hp2", "Čerpadlo 2")}
      ${field(260, 768, "průtok", "kotelna.flow", { unit: " m³/h", small: true })}
      ${field(360, 768, "tlak systému", "kotelna.p_system", { dec: 2, unit: " bar", small: true })}
      ${field(470, 768, "venku", "kotelna.t_outdoor", { unit: " °C", small: true })}
    </g>

    <!-- VÝROBA CHLADU -->
    <g class="card" data-goto="chlazeni">
      ${frame(640, 470, 760, 380, "Výroba chladu")}
      ${field(660, 512, "chlazená voda", "chw.t_supply", { unit: " °C", big: true })}
      ${field(790, 512, "zpátečka", "chw.t_return", { unit: " °C", big: true })}
      ${field(900, 512, "odběr", "chw.load_power", { dec: 0, unit: " kW", big: true })}
      ${field(1010, 512, "voda z věže", "vez.t_water_out", { unit: " °C", big: true })}
      ${field(1140, 512, "mokrý teploměr", "vez.t_wetbulb", { unit: " °C", big: true })}
      ${field(1270, 512, "approach", "vez.approach", { unit: " K", big: true })}

      ${[1, 2, 3].map((n, i) => `<g>
        ${frame(660 + i * 190, 570, 175, 150, `Chiller ${n}`)}
        ${led(815 + i * 190, 588, `chl${n}.state`, "chiller")}
        ${field(678 + i * 190, 616, "výstup", `chl${n}.t_chw_out`, { unit: " °C", small: true })}
        ${field(760 + i * 190, 616, "výkon", `chl${n}.cool_power`, { dec: 0, unit: " kW", small: true })}
        ${field(678 + i * 190, 660, "sání", `chl${n}.p_suction`, { dec: 2, unit: " bar", small: true })}
        ${field(760 + i * 190, 660, "výtlak", `chl${n}.p_discharge`, { dec: 2, unit: " bar", small: true })}
        ${T(678 + i * 190, 706, "kompresory", "lbl-sm")}
        ${led(752 + i * 190, 702, `chl${n}.c1_state`, "comp")}
        ${led(768 + i * 190, 702, `chl${n}.c2_state`, "comp")}
      </g>`).join("")}

      <!-- chladicí věž -->
      ${frame(1230, 570, 150, 150, "Chladicí věž")}
      ${fan(1272, 622, 22, "vez.f1_speed")}
      ${fan(1338, 622, 22, "vez.f2_speed")}
      ${T(1248, 668, "hladina", "lbl-sm")}
      ${bar(1248, 675, 114, "vez.basin_level", { cls: "cold" })}
      ${field(1248, 700, "odvod tepla", "vez.reject_power", { dec: 0, unit: " kW", small: true })}

      ${pump(690, 780, "chw.p1", "Primár 1")}
      ${pump(770, 780, "chw.p2", "Primár 2")}
      ${pump(880, 780, "chw.s1", "Sekundár 1")}
      ${pump(960, 780, "chw.s2", "Sekundár 2")}
      ${field(1040, 768, "průtok primár", "chw.flow_prim", { unit: " m³/h", small: true })}
      ${field(1160, 768, "průtok sekundár", "chw.flow_sec", { unit: " m³/h", small: true })}
      ${field(1290, 768, "tlaková dif.", "chw.dp", { dec: 2, unit: " bar", small: true })}
    </g>
  </svg>`;
};

/** Čidlo s bublinou v samostatném pásu nad/pod schématem a svodem k místu měření. */
function probe(x, yPoint, byTop, label, ref, o = {}) {
  const down = byTop > yPoint;
  const yEnd = down ? byTop : byTop + 46;
  return `<g>
    <line x1="${x}" y1="${yPoint}" x2="${x}" y2="${yEnd}" stroke="#3b5170"
          stroke-width="1.4" stroke-dasharray="3 3"/>
    <circle cx="${x}" cy="${yPoint}" r="4" fill="#56b6e0"/>
    <rect x="${x - 46}" y="${byTop}" width="92" height="46" rx="7"
          fill="#16202e" stroke="#3b5170" stroke-width="1.4"/>
    ${T(x, byTop + 18, label, "lbl-sm", "middle")}
    ${V(x, byTop + 36, ref, { dec: o.dec ?? 1, unit: o.unit ?? " °C",
                              cls: "val", anchor: "middle" })}
  </g>`;
}

/** Výměník — teplosměnná plocha jako vlnovka v rámečku. */
function coil(x, y, w, h, color) {
  const n = 5, step = w / n;
  let d = `M ${x + 4} ${y + h - 6}`;
  for (let i = 0; i < n; i++) {
    const x0 = x + 4 + i * step;
    d += ` C ${x0 + step * .3} ${y - 4}, ${x0 + step * .7} ${y + h + 2}, ${x0 + step} ${y + h - 6}`;
  }
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4"
            fill="#16202e" stroke="#3b5170" stroke-width="1.4"/>
          <path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" opacity=".9"/>`;
}

/** Filtr — šrafovaná vložka, barva podle zanesení (dopočítá se v app.js). */
function filterBox(x, y, w, h, ref) {
  return `<g>
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="3" fill="#16202e"
          stroke="#3b5170" stroke-width="1.4" data-filter="${ref}"/>
    ${[0.25, 0.5, 0.75].map(f =>
      `<line x1="${x + w * f}" y1="${y + 3}" x2="${x + w * f - 8}" y2="${y + h - 3}"
             stroke="#4f6478" stroke-width="1.2"/>`).join("")}
  </g>`;
}

// =============================================================================
// Detail VZT jednotky
// =============================================================================
SCREENS.ahu = id => `<svg class="plan" viewBox="0 0 1440 700">
  <!-- skříň jednotky -->
  <rect x="150" y="210" width="750" height="310" rx="10" class="case"/>
  ${T(168, 200, "vzduchotechnická jednotka", "lbl-sm")}

  <!-- potrubí přívodu a odtahu -->
  ${pipe("M 60 270 H 1000", "air", `${id}.fan_supply`, { min: 2 })}
  ${pipe("M 1000 450 H 60", "air", `${id}.fan_extract`, { min: 2 })}
  ${T(62, 250, "sání vzduchu", "lbl-sm")}
  ${T(62, 486, "odpadní vzduch", "lbl-sm")}

  <!-- obsluhovaný prostor -->
  ${frame(1000, 210, 380, 310, "Obsluhovaný prostor")}
  ${field(1024, 268, "teplota v hale", `${id}.t_extract`, { unit: " °C", big: true })}
  ${field(1180, 268, "žádaná", `${id}.sp:sp_room`, { unit: " °C", big: true })}
  ${field(1024, 336, "vlhkost", `${id}.rh_extract`, { dec: 0, unit: " %", big: true })}
  ${field(1180, 336, "provozní hodiny", `${id}.run_hours`, { dec: 0, unit: " h", big: true })}
  ${T(1024, 404, "stav jednotky", "lbl-sm")}
  ${led(1030, 424, `${id}.state`, "ahu")}
  ${V(1046, 429, `${id}.state`, { map: "ahuStates", cls: "val-sm" })}
  ${T(1024, 464, "odběr tepla", "lbl-sm")}
  ${V(1024, 482, `${id}.hw_power`, { dec: 0, unit: " kW", cls: "val-sm" })}
  ${T(1180, 464, "odběr chladu", "lbl-sm")}
  ${V(1180, 482, `${id}.chw_power`, { dec: 0, unit: " kW", cls: "val-sm" })}

  <!-- filtr přívodu -->
  ${filterBox(180, 240, 40, 60, `${id}.filter_dp_sup`)}
  ${T(200, 320, "filtr", "lbl-sm", "middle")}
  ${V(200, 336, `${id}.filter_dp_sup`, { dec: 0, unit: " Pa", cls: "val-sm", anchor: "middle" })}

  <!-- rekuperátor -->
  <rect x="270" y="240" width="120" height="240" rx="6" fill="#16202e"
        stroke="#3b5170" stroke-width="1.5"/>
  <path d="M 270 240 L 390 480 M 390 240 L 270 480" stroke="#3b5170" stroke-width="1.4"/>
  ${T(330, 500, "rekuperátor", "lbl-sm", "middle")}
  ${T(330, 516, "účinnost 72 %", "lbl-sm", "middle")}
  ${T(330, 224, "obtok", "lbl-sm", "middle")}
  ${bar(292, 228, 76, `${id}.recup_cmd`, { cls: "cold", h: 6 })}

  <!-- ohřívač -->
  ${coil(430, 240, 45, 60, "#e07a4a")}
  ${T(452, 320, "ohřívač", "lbl-sm", "middle")}
  ${bar(418, 330, 70, `${id}.heat_cmd`, { cls: "warm", h: 6 })}
  ${V(452, 356, `${id}.heat_cmd`, { dec: 0, unit: " %", cls: "val-sm", anchor: "middle" })}

  <!-- chladič -->
  ${coil(515, 240, 45, 60, "#3fa3d8")}
  ${T(537, 320, "chladič", "lbl-sm", "middle")}
  ${bar(503, 330, 70, `${id}.cool_cmd`, { cls: "cold", h: 6 })}
  ${V(537, 356, `${id}.cool_cmd`, { dec: 0, unit: " %", cls: "val-sm", anchor: "middle" })}

  <!-- ventilátory -->
  ${fan(640, 270, 26, `${id}.fan_supply`)}
  ${T(640, 320, "přívodní ventilátor", "lbl-sm", "middle")}
  ${V(640, 336, `${id}.fan_supply`, { dec: 0, unit: " %", cls: "val-sm", anchor: "middle" })}
  ${fan(640, 450, 26, `${id}.fan_extract`)}
  ${T(640, 400, "odtahový ventilátor", "lbl-sm", "middle")}
  ${V(640, 416, `${id}.fan_extract`, { dec: 0, unit: " %", cls: "val-sm", anchor: "middle" })}

  <!-- filtr odtahu -->
  ${filterBox(800, 420, 40, 60, `${id}.filter_dp_ext`)}
  ${T(820, 508, "filtr odtahu", "lbl-sm", "middle")}
  ${V(820, 412, `${id}.filter_dp_ext`, { dec: 0, unit: " Pa", cls: "val-sm", anchor: "middle" })}

  <!-- čidla nad přívodem a pod odtahem -->
  ${probe(105, 270, 60, "venkovní", `${id}.t_outdoor`)}
  ${probe(410, 270, 60, "za rekuperátorem", `${id}.t_after_recup`)}
  ${probe(505, 270, 120, "za ohřívačem", `${id}.t_after_heater`)}
  ${probe(940, 270, 60, "přívod", `${id}.t_supply`)}
  ${probe(940, 450, 570, "odtah", `${id}.t_extract`)}
  ${probe(105, 450, 570, "odpadní vzduch", `${id}.t_exhaust`)}

  <!-- průtok a příkon -->
  ${field(700, 228, "průtok vzduchu", `${id}.flow_supply`, { dec: 0, unit: " m³/h", small: true })}
  ${field(810, 228, "příkon motorů", `${id}.power`, { dec: 1, unit: " kW", small: true })}
</svg>`;

// =============================================================================
// Výroba chladu — chladivový okruh, věž a hydraulika
// =============================================================================
/** Jeden chiller: výparník, dva kompresory, kondenzátor, expanzní ventil. */
function chillerBlock(n, x, y) {
  const id = `chl${n}`, w = 380, h = 300, rc = x + 250;
  return `<g>
    ${frame(x, y, w, h, `Chiller ${n}`)}
    ${led(x + w - 20, y + 18, `${id}.state`, "chiller")}
    ${V(x + w - 34, y + 23, `${id}.state`, { map: "chillerStates", cls: "val-sm", anchor: "end" })}

    <!-- hodnoty chladivového okruhu -->
    ${field(x + 18, y + 58, "tlak sání", `${id}.p_suction`, { dec: 2, unit: " bar", small: true })}
    ${field(x + 106, y + 58, "tlak výtlaku", `${id}.p_discharge`, { dec: 2, unit: " bar", small: true })}
    ${field(x + 200, y + 58, "přehřátí", `${id}.superheat`, { unit: " K", small: true })}
    ${field(x + 280, y + 58, "podchlazení", `${id}.subcool`, { unit: " K", small: true })}

    <!-- chladivový okruh: kondenzátor nahoře, výparník dole -->
    ${T(x + 18, y + 106, "kondenzátor", "lbl-sm")}
    ${coil(x + 18, y + 112, 100, 34, "#e07a4a")}
    ${T(x + 18, y + 226, "výparník", "lbl-sm")}
    ${coil(x + 18, y + 232, 100, 34, "#3fa3d8")}

    <!-- strana vysokého tlaku (výtlak) a nízkého tlaku (sání) -->
    <path d="M ${x + 118} ${y + 129} H ${x + 150}" stroke="#5a3a2c" stroke-width="4.5"
          fill="none" stroke-linecap="round"/>
    <path d="M ${x + 118} ${y + 249} H ${x + 150}" stroke="#22485f" stroke-width="4.5"
          fill="none" stroke-linecap="round"/>
    <path d="M ${x + 18} ${y + 129} H ${x + 8} V ${y + 249} H ${x + 18}" stroke="#22485f"
          stroke-width="4.5" fill="none" stroke-linecap="round"/>

    <!-- kompresory mezi sáním a výtlakem -->
    ${[1, 2].map((c, i) => `<g>
      <circle cx="${x + 168}" cy="${y + 152 + i * 74}" r="16" fill="#16202e"
              stroke="#3b5170" stroke-width="1.5"/>
      <g class="rotor" data-run="${id}.c${c}_state" data-run-min="1.5" data-speed="fixed">
        <path d="M ${x + 159} ${y + 152 + i * 74} A 9 9 0 0 1 ${x + 177} ${y + 152 + i * 74}"
              fill="none" stroke="#7fb4d8" stroke-width="3" stroke-linecap="round"/>
      </g>
      ${led(x + 181, y + 140 + i * 74, `${id}.c${c}_state`, "comp")}
    </g>`).join("")}
    <path d="M ${x + 150} ${y + 129} V ${y + 249}" stroke="#3b5170" stroke-width="1.2"
          stroke-dasharray="3 3" fill="none"/>

    <!-- expanzní ventil -->
    <path d="M ${x + 2} ${y + 182} l 9 -8 v 16 z M ${x + 12} ${y + 182} l -9 -8 v 16 z"
          fill="#7fb4d8"/>
    ${T(x + 18, y + 176, "exp. ventil", "lbl-sm")}
    ${V(x + 18, y + 192, `${id}.eev`, { dec: 0, unit: " %", cls: "val-sm" })}

    <!-- pravý sloupec: co jednotka odvádí -->
    ${field(rc, y + 104, "chlazená voda", `${id}.t_chw_out`, { unit: " °C", small: true })}
    ${field(rc, y + 146, "chladicí výkon", `${id}.cool_power`, { dec: 0, unit: " kW", small: true })}
    ${field(rc, y + 188, "COP", `${id}.cop`, { dec: 2, small: true })}
    ${T(rc, y + 230, "výkon jednotky", "lbl-sm")}
    ${bar(rc, y + 237, 90, `${id}.capacity`, { cls: "cold" })}
    ${V(rc + 98, y + 244, `${id}.capacity`, { dec: 0, unit: " %", cls: "val-sm" })}

    <!-- provozní hodiny kompresorů -->
    ${[1, 2].map((c, i) => `
      ${T(x + 18 + i * 180, y + 278, `Kompresor ${c}`, "lbl-sm")}
      ${V(x + 18 + i * 180, y + 294, `${id}.c${c}_hours`, { dec: 0, unit: " h", cls: "val-sm" })}
      ${V(x + 70 + i * 180, y + 294, `${id}.c${c}_starts`, { dec: 0, unit: " startů", cls: "val-sm" })}
    `).join("")}
  </g>`;
}

SCREENS.chlazeni = () => `<svg class="plan" viewBox="0 0 1440 1000">
  <!-- CHLADICÍ VĚŽ -->
  ${frame(40, 30, 480, 250, "Chladicí věž")}
  <path d="M 90 240 L 120 110 H 260 L 290 240 Z" fill="#16202e"
        stroke="#3b5170" stroke-width="1.5"/>
  <rect x="82" y="240" width="216" height="24" rx="4" fill="#1b3348" stroke="#3b5170"/>
  ${T(190, 258, "bazén", "lbl-sm", "middle")}
  ${fan(155, 96, 24, "vez.f1_speed")}
  ${fan(225, 96, 24, "vez.f2_speed")}
  ${led(133, 78, "vez.f1_state", "motor")}
  ${led(247, 78, "vez.f2_state", "motor")}
  ${field(320, 74, "voda na věž", "vez.t_water_in", { unit: " °C", small: true })}
  ${field(420, 74, "voda z věže", "vez.t_water_out", { unit: " °C", small: true })}
  ${field(320, 118, "mokrý teploměr", "vez.t_wetbulb", { unit: " °C", small: true })}
  ${field(420, 118, "approach", "vez.approach", { unit: " K", small: true })}
  ${field(320, 162, "odvedený výkon", "vez.reject_power", { dec: 0, unit: " kW", small: true })}
  ${field(420, 162, "průtok", "vez.flow", { dec: 0, unit: " m³/h", small: true })}
  ${T(320, 206, "hladina v bazénu", "lbl-sm")}
  ${bar(320, 213, 130, "vez.basin_level", { cls: "cold" })}
  ${field(320, 238, "vodivost", "vez.conductivity", { dec: 0, unit: " µS/cm", small: true })}
  ${field(420, 238, "dopouštění", "vez.makeup_valve", { dec: 0, unit: " %", small: true })}
  ${T(60, 74, "motohodiny", "lbl-sm")}
  ${V(60, 90, "vez.f1_hours", { dec: 0, unit: " h", cls: "val-sm" })}
  ${V(60, 108, "vez.f2_hours", { dec: 0, unit: " h", cls: "val-sm" })}

  <!-- kondenzátorová voda: teplá z chillerů na věž, ochlazená zpátky -->
  ${pipe("M 150 280 V 322 H 530 V 150 H 560", "warm", "vez.flow", { min: 1 })}
  ${pipe("M 560 470 H 548 V 350 H 240 V 280", "cold", "vez.flow", { min: 1 })}
  ${T(180, 344, "kondenzátorová voda — do kondenzátorů chillerů", "lbl-sm")}

  <!-- CHILLERY -->
  ${chillerBlock(1, 560, 30)}
  ${chillerBlock(2, 960, 30)}
  ${chillerBlock(3, 560, 350)}

  <!-- OKRUH CHLAZENÉ VODY -->
  ${frame(960, 350, 380, 300, "Okruh chlazené vody")}
  ${field(980, 396, "přívod", "chw.t_supply", { unit: " °C", big: true })}
  ${field(1090, 396, "zpátečka", "chw.t_return", { unit: " °C", big: true })}
  ${field(1210, 396, "odběr", "chw.load_power", { dec: 0, unit: " kW", big: true })}
  ${field(980, 462, "tlak přívod", "chw.p_supply", { dec: 2, unit: " bar", small: true })}
  ${field(1090, 462, "tlak zpátečka", "chw.p_return", { dec: 2, unit: " bar", small: true })}
  ${field(1210, 462, "tlaková diference", "chw.dp", { dec: 2, unit: " bar", small: true })}
  ${field(980, 512, "průtok primár", "chw.flow_prim", { unit: " m³/h", small: true })}
  ${field(1090, 512, "průtok sekundár", "chw.flow_sec", { unit: " m³/h", small: true })}
  ${field(1210, 512, "žádaná dif.", "chw.sp:sp_dp", { dec: 2, unit: " bar", small: true })}
  ${T(980, 566, "vedoucí čerpadla", "lbl-sm")}
  ${V(980, 584, "chw.prim_lead", { dec: 0, cls: "val-sm" })}
  ${T(996, 584, "primár", "lbl-sm")}
  ${V(1090, 584, "chw.sec_lead", { dec: 0, cls: "val-sm" })}
  ${T(1106, 584, "sekundár", "lbl-sm")}
  ${T(980, 620, "střídání po", "lbl-sm")}
  ${V(1060, 620, "chw.sp:changeover_h", { dec: 0, unit: " h", cls: "val-sm" })}

  <!-- HYDRAULIKA: primár, anuloid, sekundár -->
  ${frame(40, 700, 1300, 270, "Hydraulika okruhu chlazené vody")}
  ${T(80, 760, "primární okruh (chillery)", "lbl-sm")}
  ${pump(120, 810, "chw.p1", "Primár 1")}
  ${pump(210, 810, "chw.p2", "Primár 2")}
  ${pipe("M 250 810 H 420", "cold", "chw.flow_prim", { min: 1 })}
  ${field(120, 880, "průtok", "chw.flow_prim", { unit: " m³/h", small: true })}
  ${field(220, 880, "motohodiny P1", "chw.p1_hours", { dec: 0, unit: " h", small: true })}
  ${field(330, 880, "motohodiny P2", "chw.p2_hours", { dec: 0, unit: " h", small: true })}

  <!-- anuloid -->
  <rect x="420" y="740" width="56" height="180" rx="8" fill="#16202e"
        stroke="#3b5170" stroke-width="1.5"/>
  ${T(448, 730, "anuloid", "lbl-sm", "middle")}
  ${T(448, 936, "vyrovnává rozdíl průtoků", "lbl-sm", "middle")}

  ${T(520, 760, "sekundární okruh (spotřebiče)", "lbl-sm")}
  ${pipe("M 476 810 H 560", "cold", "chw.flow_sec", { min: 1 })}
  ${pump(600, 810, "chw.s1", "Sekundár 1")}
  ${pump(690, 810, "chw.s2", "Sekundár 2")}
  ${pipe("M 730 810 H 1300", "cold", "chw.flow_sec", { min: 1 })}
  ${T(1160, 790, "do chladičů VZT jednotek", "lbl-sm")}
  ${field(600, 880, "průtok", "chw.flow_sec", { unit: " m³/h", small: true })}
  ${field(700, 880, "otáčky S1", "chw.s1_speed", { dec: 0, unit: " %", small: true })}
  ${field(800, 880, "otáčky S2", "chw.s2_speed", { dec: 0, unit: " %", small: true })}
  ${field(910, 880, "motohodiny S1", "chw.s1_hours", { dec: 0, unit: " h", small: true })}
  ${field(1030, 880, "motohodiny S2", "chw.s2_hours", { dec: 0, unit: " h", small: true })}
  ${field(1150, 880, "dopravní výška S1", "chw.s1_head", { dec: 0, unit: " kPa", small: true })}
</svg>`;

// =============================================================================
// Kotelna
// =============================================================================
SCREENS.kotelna = () => `<svg class="plan" viewBox="0 0 1440 720">
  ${[1, 2].map((n, i) => {
    const x = 40, y = 30 + i * 330, id = `kotel${n}`;
    return `<g>
      ${frame(x, y, 620, 300, `Kotel ${n} — 400 kW`)}
      ${led(x + 600, y + 18, `${id}.burner`, "burner")}
      ${V(x + 586, y + 23, `${id}.burner`, { map: "burnerStates", cls: "val-sm", anchor: "end" })}

      <!-- těleso kotle s výměníkem a hořákem -->
      <rect x="${x + 30}" y="${y + 60}" width="200" height="180" rx="10" fill="#16202e"
            stroke="#3b5170" stroke-width="1.5"/>
      ${[0, 1, 2].map(k => `<path d="M ${x + 50} ${y + 92 + k * 26} h 160" stroke="#33506e"
            stroke-width="3" stroke-linecap="round" opacity=".8"/>`).join("")}
      ${T(x + 130, y + 84, "výměník", "lbl-sm", "middle")}
      <g data-flame="${id}.modulation">
        <path d="M ${x + 130} ${y + 210} c -26 -22 -30 -52 -6 -78 c -4 22 14 26 12 44
                 c 10 -12 8 -30 2 -44 c 26 20 32 56 -8 78 z" fill="#e07a4a" opacity="0"/>
      </g>
      ${T(x + 130, y + 232, "hořák", "lbl-sm", "middle")}
      <!-- komín -->
      <rect x="${x + 100}" y="${y + 38}" width="26" height="24" fill="#16202e"
            stroke="#3b5170" stroke-width="1.4"/>
      ${T(x + 136, y + 46, "spaliny", "lbl-sm")}
      ${V(x + 182, y + 47, `${id}.t_flue`, { unit: " °C", cls: "val-sm" })}

      ${field(x + 260, y + 76, "výstupní voda", `${id}.t_flow`, { unit: " °C", big: true })}
      ${field(x + 400, y + 76, "zpátečka", `${id}.t_return`, { unit: " °C", big: true })}
      ${field(x + 520, y + 76, "výkon", `${id}.power`, { dec: 0, unit: " kW", big: true })}

      ${T(x + 260, y + 140, "modulace hořáku", "lbl-sm")}
      ${bar(x + 260, y + 147, 200, `${id}.modulation`, { cls: "warm" })}
      ${V(x + 470, y + 154, `${id}.modulation`, { dec: 0, unit: " %", cls: "val-sm" })}

      ${field(x + 260, y + 190, "účinnost", `${id}.efficiency`, { unit: " %", small: true })}
      ${field(x + 360, y + 190, "tlak vody", `${id}.pressure`, { dec: 2, unit: " bar", small: true })}
      ${field(x + 470, y + 190, "průtok", `${id}.flow`, { unit: " m³/h", small: true })}
      ${field(x + 260, y + 240, "motohodiny hořáku", `${id}.run_hours`, { dec: 0, unit: " h", small: true })}
      ${field(x + 400, y + 240, "počet startů", `${id}.starts`, { dec: 0, small: true })}
      ${field(x + 520, y + 240, "spotřeba plynu", `${id}.gas_total`, { dec: 0, unit: " m³", small: true })}
    </g>`;
  }).join("")}

  <!-- rozdělovač a sběrač -->
  ${pipe("M 660 120 H 760", "warm", "kotelna.flow", { min: 0.5 })}
  ${pipe("M 660 450 H 760", "warm", "kotelna.flow", { min: 0.5 })}
  <rect x="760" y="80" width="40" height="420" rx="10" fill="#2a2018"
        stroke="#6b3a26" stroke-width="1.5"/>
  ${T(780, 70, "rozdělovač", "lbl-sm", "middle")}
  <rect x="860" y="80" width="40" height="420" rx="10" fill="#1b2836"
        stroke="#3b5170" stroke-width="1.5"/>
  ${T(880, 70, "sběrač", "lbl-sm", "middle")}

  ${pipe("M 800 160 H 1100", "warm", "kotelna.flow", { min: 0.5 })}
  ${pipe("M 1100 420 H 900", "warm", "kotelna.flow", { min: 0.5 })}
  ${T(1010, 140, "do ohřívačů VZT jednotek", "lbl-sm")}
  <!-- zpátečka ze sběrače do obou kotlů, vedená kolem bloků -->
  ${pipe("M 880 500 V 700 H 340 V 660", "cold", "kotelna.flow", { min: 0.5 })}
  ${pipe("M 340 700 H 20 V 180 H 40", "cold", "kotelna.flow", { min: 0.5 })}
  ${T(360, 694, "zpátečka do kotlů", "lbl-sm")}

  <!-- oběhová čerpadla -->
  ${pump(940, 560, "kotelna.hp1", "Oběhové čerpadlo 1")}
  ${pump(1080, 560, "kotelna.hp2", "Oběhové čerpadlo 2")}
  ${T(880, 620, "provoz / záloha se střídají po", "lbl-sm")}
  ${V(1090, 620, "kotelna.sp:changeover_h", { dec: 0, unit: " h", cls: "val-sm" })}
  ${field(880, 660, "motohodiny 1", "kotelna.hp1_hours", { dec: 0, unit: " h", small: true })}
  ${field(1000, 660, "motohodiny 2", "kotelna.hp2_hours", { dec: 0, unit: " h", small: true })}
  ${field(1120, 660, "průtok", "kotelna.flow", { unit: " m³/h", small: true })}

  <!-- souhrn okruhu -->
  ${frame(1160, 30, 240, 470, "Okruh topné vody")}
  ${field(1180, 76, "rozdělovač", "kotelna.t_header_flow", { unit: " °C", big: true })}
  ${field(1180, 140, "sběrač", "kotelna.t_header_return", { unit: " °C", big: true })}
  ${field(1180, 204, "žádaná ekvitermní", "kotelna.sp_calc", { unit: " °C", big: true })}
  ${field(1180, 268, "venkovní teplota", "kotelna.t_outdoor", { unit: " °C", big: true })}
  ${field(1180, 332, "dodávaný výkon", "kotelna.heat_power", { dec: 0, unit: " kW", big: true })}
  ${field(1180, 396, "odběr spotřebičů", "kotelna.load_power", { dec: 0, unit: " kW", big: true })}
  ${field(1180, 452, "tlak systému", "kotelna.p_system", { dec: 2, unit: " bar", small: true })}
  ${field(1290, 452, "dopouštění", "kotelna.makeup_valve", { dec: 0, unit: " %", small: true })}
</svg>`;
