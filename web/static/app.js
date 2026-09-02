/*
 * Dispečink závodu — spojení, navigace a plnění schémat živými hodnotami.
 *
 * Schéma se poskládá jednou při přepnutí obrazovky. Každou sekundu pak
 * dorazí přes WebSocket stav celého závodu a doplní se jen texty, šířky
 * sloupců a barvy — díky tomu běží animace plynule a nic neproblikává.
 */

const app = {
  meta: null,
  state: { devices: {} },
  view: "prehled",
  hooks: [],          // dopočty závislé na konkrétní obrazovce
  trend: null,
};

// --- pomocné ------------------------------------------------------------------
const $ = sel => document.querySelector(sel);
const fmt = (v, dec) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : v.toFixed(dec).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, " ");

/** Hodnota podle odkazu "zarizeni.klic", nebo "zarizeni.sp:klic" pro žádanou. */
function lookup(ref) {
  const [dev, rest] = ref.split(".");
  const d = app.state.devices[dev];
  if (!d || !d.online) return null;
  if (rest.startsWith("sp:")) return d.setpoints?.[rest.slice(3)] ?? null;
  return d.values?.[rest] ?? null;
}

const MAPS = {
  pump: v => ["off", "warn", "ok", "standby", "bad"][Math.round(v)] || "off",
  motor: v => ["off", "warn", "ok", "standby", "bad"][Math.round(v)] || "off",
  comp: v => ["off", "warn", "ok", "standby", "bad"][Math.round(v)] || "off",
  ahu: v => ["off", "warn", "ok", "warn", "bad"][Math.round(v)] || "off",
  chiller: v => ["off", "standby", "ok", "warn", "bad"][Math.round(v)] || "off",
  burner: v => ["off", "warn", "warn", "ok", "bad"][Math.round(v)] || "off",
};

function stateText(mapName, value) {
  const tables = {
    pumpStates: app.meta.pumpStates, compStates: app.meta.compStates,
    burnerStates: app.meta.burnerStates,
    ahuStates: app.meta.types.ahu.states,
    chillerStates: app.meta.types.chiller.states,
  };
  const t = tables[mapName];
  return t ? (t[String(Math.round(value))] ?? "—") : "—";
}

// --- plnění schématu hodnotami -------------------------------------------------
function paint() {
  const root = $("#screen");

  root.querySelectorAll("[data-v]").forEach(el => {
    const v = lookup(el.dataset.v);
    if (el.dataset.map) {
      el.textContent = v === null ? "—" : stateText(el.dataset.map, v);
      return;
    }
    el.textContent = v === null ? "—"
      : fmt(v, +el.dataset.dec) + (el.dataset.unit || "");
  });

  root.querySelectorAll("[data-bar]").forEach(el => {
    const v = lookup(el.dataset.bar) ?? 0;
    const max = +el.dataset.barMax, w = +el.dataset.barW;
    el.setAttribute("width", Math.max(0, Math.min(1, v / max)) * w);
  });

  root.querySelectorAll("[data-run]").forEach(el => {
    const v = lookup(el.dataset.run) ?? 0;
    const on = v >= (+el.dataset.runMin || 1);
    el.classList.toggle("on", on);        // potrubí
    el.classList.toggle("run", on);       // rotory
    if (el.dataset.speed && el.dataset.speed !== "fixed") {
      // rychlost otáčení podle otáček stroje: 100 % = jedna otáčka za 0,6 s
      const sp = lookup(el.dataset.speed) ?? 0;
      el.style.animationDuration = sp > 2 ? `${Math.max(0.35, 60 / sp)}s` : "0s";
    }
  });

  root.querySelectorAll("[data-led]").forEach(el => {
    const v = lookup(el.dataset.led);
    const cls = v === null ? "off" : MAPS[el.dataset.ledMap](v);
    el.setAttribute("class", `led ${cls}`);
  });

  // filtr mění barvu podle zanesení — zelená, jantarová, červená
  root.querySelectorAll("[data-filter]").forEach(el => {
    const dp = lookup(el.dataset.filter) ?? 0;
    const f = dp / 250;
    el.setAttribute("stroke", f < 0.6 ? "#3fbf7f" : f < 0.85 ? "#e5b13a" : "#e3564c");
    el.setAttribute("stroke-width", f < 0.6 ? 1.4 : 2.4);
  });

  // plamen v kotli je vidět, jen když hořák hoří
  root.querySelectorAll("[data-flame]").forEach(el => {
    const m = lookup(el.dataset.flame) ?? 0;
    el.firstElementChild.setAttribute("opacity", m > 1 ? (0.45 + m / 200).toFixed(2) : 0);
  });

  app.hooks.forEach(fn => fn());
  paintNav();
  paintTopbar();
}

// --- levý sloupec ---------------------------------------------------------------
function deviceStatus(id) {
  const d = app.state.devices[id];
  if (!d || !d.online) return "off";
  return d.alarms && d.alarms.length ? "bad" : "ok";
}

function areaStatus(area) {
  const ids = app.meta.devices.filter(d => d.area === area).map(d => d.id);
  if (ids.every(id => !app.state.devices[id]?.online)) return "off";
  return ids.some(id => deviceStatus(id) === "bad") ? "bad" : "ok";
}

function paintNav() {
  document.querySelectorAll("#nav a[data-status]").forEach(a => {
    const dot = a.querySelector(".dot");
    dot.className = "dot " + (a.dataset.statusKind === "area"
      ? areaStatus(a.dataset.status) : deviceStatus(a.dataset.status));
    const valEl = a.querySelector(".val");
    if (valEl && valEl.dataset.v) {
      const v = lookup(valEl.dataset.v);
      valEl.textContent = v === null ? "—" : fmt(v, +valEl.dataset.dec) + valEl.dataset.unit;
    }
  });
}

function allAlarms() {
  const out = [];
  for (const dev of app.meta.devices) {
    const d = app.state.devices[dev.id];
    if (!d) continue;
    if (!d.online) { out.push({ dev: dev.name, txt: "Zařízení neodpovídá" }); continue; }
    (d.alarms || []).forEach(txt => out.push({ dev: dev.name, txt }));
  }
  return out;
}

function paintTopbar() {
  const alarms = allAlarms();
  const chip = $("#alarm-chip");
  chip.textContent = alarms.length ? `${alarms.length} alarmů` : "bez alarmů";
  chip.classList.toggle("alarm", alarms.length > 0);

  const out = lookup("kotelna.t_outdoor");
  $("#weather").innerHTML = `venku <b>${out === null ? "—" : fmt(out, 1) + " °C"}</b>`;
  const online = app.meta.devices.filter(d => app.state.devices[d.id]?.online).length;
  $("#online").innerHTML = `<b>${online}</b>/${app.meta.devices.length} zařízení`;
}

// --- ovládací panely pod schématem -----------------------------------------------
function setpointPanel(deviceId) {
  const dev = app.meta.devices.find(d => d.id === deviceId);
  const defs = app.meta.types[dev.type].setpoints;
  const rows = defs.map(sp => `
    <div class="sp">
      <label for="sp-${deviceId}-${sp.key}">${sp.name}</label>
      <output id="out-${deviceId}-${sp.key}">—</output>
      <input type="range" id="sp-${deviceId}-${sp.key}"
             min="${sp.min}" max="${sp.max}" step="${sp.step}"
             data-device="${deviceId}" data-key="${sp.key}" data-unit="${sp.unit}">
    </div>`).join("");
  return `<div class="panel"><h3>Žádané hodnoty — ${dev.name}</h3>${rows}</div>`;
}

/** Posuvník se plní ze zařízení, dokud s ním operátor nehýbe. */
function wireSetpoints() {
  document.querySelectorAll("#screen input[type=range]").forEach(input => {
    input.addEventListener("input", () => {
      input.dataset.touched = "1";
      $(`#out-${input.dataset.device}-${input.dataset.key}`).textContent =
        fmt(+input.value, input.step < 1 ? 1 : 0) + " " + input.dataset.unit;
    });
    input.addEventListener("change", async () => {
      await fetch("/api/write", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: input.dataset.device,
                               key: input.dataset.key, value: +input.value }),
      });
      setTimeout(() => { delete input.dataset.touched; }, 2500);
    });
  });
  app.hooks.push(() => {
    document.querySelectorAll("#screen input[type=range]").forEach(input => {
      if (input.dataset.touched) return;
      const v = lookup(`${input.dataset.device}.sp:${input.dataset.key}`);
      if (v === null) return;
      input.value = v;
      $(`#out-${input.dataset.device}-${input.dataset.key}`).textContent =
        fmt(v, input.step < 1 ? 1 : 0) + " " + input.dataset.unit;
    });
  });
}

function alarmPanel(deviceIds) {
  return `<div class="panel"><h3>Alarmy</h3><ul class="alarm-list" id="alarm-list"></ul></div>`;
}

function wireAlarms(deviceIds) {
  app.hooks.push(() => {
    const list = $("#alarm-list");
    if (!list) return;
    const items = allAlarms().filter(a => !deviceIds
      || deviceIds.some(id => app.meta.devices.find(d => d.id === id)?.name === a.dev));
    list.className = "alarm-list" + (items.length ? "" : " empty");
    list.innerHTML = items.length
      ? items.map(a => `<li><span class="dev">${a.dev}</span><span class="txt">${a.txt}</span></li>`).join("")
      : "<li>Žádný alarm — provoz je v pořádku.</li>";
  });
}

// --- trend ----------------------------------------------------------------------
const TREND_COLORS = ["#56b6e0", "#e07a4a", "#3fbf7f", "#e5b13a", "#b48ee0"];

function trendPanel(device, series) {
  const legend = series.map((s, i) =>
    `<span><i style="background:${TREND_COLORS[i]}"></i>${s.label}</span>`).join("");
  return `<div class="panel"><h3>Průběh posledních 15 minut</h3>
    <svg class="trend" id="trend" viewBox="0 0 600 132" preserveAspectRatio="none"></svg>
    <div class="legend">${legend}</div></div>`;
}

async function wireTrend(device, series) {
  const draw = async () => {
    const svg = $("#trend");
    if (!svg) return;
    const keys = series.map(s => s.key).join(",");
    const data = await (await fetch(`/api/history/${device}?keys=${keys}`)).json();
    const n = data.ts?.length || 0;
    if (n < 2) { svg.innerHTML = `<text x="300" y="70" class="axis"
        text-anchor="middle">sbírají se data…</text>`; return; }

    let lo = Infinity, hi = -Infinity;
    series.forEach(s => (data[s.key] || []).forEach(v => {
      if (v === null) return;
      lo = Math.min(lo, v); hi = Math.max(hi, v);
    }));
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    const pad = (hi - lo) * 0.12 || 1;
    lo -= pad; hi += pad;
    const X = i => (i / (n - 1)) * 570 + 26;
    const Y = v => 116 - ((v - lo) / (hi - lo)) * 104;

    const grid = [0, 0.5, 1].map(f => {
      const v = lo + (hi - lo) * f;
      return `<line class="grid" x1="26" y1="${Y(v)}" x2="596" y2="${Y(v)}"/>
              <text class="axis" x="0" y="${Y(v) + 3}">${fmt(v, 1)}</text>`;
    }).join("");

    const paths = series.map((s, i) => {
      const vals = data[s.key] || [];
      const d = vals.map((v, j) => v === null ? "" :
        `${j === 0 ? "M" : "L"} ${X(j).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");
      return `<path d="${d}" fill="none" stroke="${TREND_COLORS[i]}" stroke-width="1.8"/>`;
    }).join("");

    svg.innerHTML = grid + paths;
  };
  draw();
  clearInterval(app.trend);
  app.trend = setInterval(draw, 5000);
}

// --- obrazovky --------------------------------------------------------------------
const VIEWS = {
  prehled: {
    title: "Přehled závodu",
    build: () => SCREENS.prehled() + `<div class="panels">${alarmPanel(null)}</div>`,
    wire: () => wireAlarms(null),
  },
  chlazeni: {
    title: "Výroba chladu",
    build: () => SCREENS.chlazeni() + `<div class="panels">
      ${setpointPanel("chw")}${setpointPanel("vez")}
      ${trendPanel("chw", [
        { key: "t_supply", label: "přívod chlazené vody" },
        { key: "t_return", label: "zpátečka" },
        { key: "flow_sec", label: "průtok sekundáru" }])}
      ${alarmPanel(["chl1", "chl2", "chl3", "vez", "chw"])}</div>`,
    wire: () => {
      wireSetpoints();
      wireAlarms(["chl1", "chl2", "chl3", "vez", "chw"]);
      wireTrend("chw", [{ key: "t_supply" }, { key: "t_return" }, { key: "flow_sec" }]);
    },
  },
  kotelna: {
    title: "Kotelna",
    build: () => SCREENS.kotelna() + `<div class="panels">
      ${setpointPanel("kotelna")}${setpointPanel("kotel1")}
      ${trendPanel("kotelna", [
        { key: "t_header_flow", label: "rozdělovač" },
        { key: "t_header_return", label: "sběrač" },
        { key: "sp_calc", label: "žádaná ekvitermní" }])}
      ${alarmPanel(["kotel1", "kotel2", "kotelna"])}</div>`,
    wire: () => {
      wireSetpoints();
      wireAlarms(["kotel1", "kotel2", "kotelna"]);
      wireTrend("kotelna", [{ key: "t_header_flow" }, { key: "t_header_return" },
                            { key: "sp_calc" }]);
    },
  },
};

function ahuView(id) {
  const dev = app.meta.devices.find(d => d.id === id);
  return {
    title: dev.name,
    build: () => SCREENS.ahu(id) + `<div class="panels">
      ${setpointPanel(id)}
      ${trendPanel(id, [
        { key: "t_extract", label: "teplota v hale" },
        { key: "t_supply", label: "přívod" },
        { key: "t_outdoor", label: "venkovní" }])}
      ${alarmPanel([id])}</div>`,
    wire: () => {
      wireSetpoints();
      wireAlarms([id]);
      wireTrend(id, [{ key: "t_extract" }, { key: "t_supply" }, { key: "t_outdoor" }]);
    },
  };
}

function show(view) {
  const spec = VIEWS[view] || ahuView(view);
  app.view = view;
  app.hooks = [];
  clearInterval(app.trend);
  $("#screen").innerHTML = spec.build();
  $("#view-title").textContent = spec.title;
  spec.wire();
  document.querySelectorAll("#nav a").forEach(a =>
    a.classList.toggle("active", a.dataset.view === view));
  $("#screen").querySelectorAll("[data-goto]").forEach(el =>
    el.addEventListener("click", () => show(el.dataset.goto)));
  paint();
  history.replaceState(null, "", `#${view}`);
}

// --- navigace a start --------------------------------------------------------------
function buildNav() {
  const item = (view, label, status, kind, val) => `
    <a data-view="${view}" data-status="${status}" data-status-kind="${kind}">
      <span class="dot"></span><span>${label}</span>
      ${val ? `<span class="val" data-v="${val.ref}" data-dec="${val.dec}"
               data-unit="${val.unit}">—</span>` : ""}
    </a>`;

  $("#nav nav").innerHTML =
    item("prehled", "Přehled závodu", "vzt", "area") +
    `<div class="group">Vzduchotechnika</div>` +
    app.meta.devices.filter(d => d.type === "ahu").map(d =>
      item(d.id, d.name.split(" — ")[1] || d.name, d.id, "device",
           { ref: `${d.id}.t_extract`, dec: 1, unit: " °C" })).join("") +
    `<div class="group">Chlazení</div>` +
    item("chlazeni", "Výroba chladu", "chlazeni", "area",
         { ref: "chw.t_supply", dec: 1, unit: " °C" }) +
    `<div class="group">Teplo</div>` +
    item("kotelna", "Kotelna", "kotelna", "area",
         { ref: "kotelna.t_header_flow", dec: 1, unit: " °C" });

  document.querySelectorAll("#nav a").forEach(a =>
    a.addEventListener("click", () => show(a.dataset.view)));
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = ev => {
    app.state = JSON.parse(ev.data);
    $("#link-state").classList.add("up");
    $("#link-state span:last-child").textContent = "spojení aktivní";
    paint();
  };
  ws.onclose = () => {
    $("#link-state").classList.remove("up");
    $("#link-state span:last-child").textContent = "spojení přerušeno — obnovuji";
    setTimeout(connect, 2000);
  };
}

(async function start() {
  app.meta = await (await fetch("/api/meta")).json();
  // stav si vyžádáme rovnou, ať obrazovka nečeká na první zprávu z WebSocketu
  app.state = await (await fetch("/api/state")).json();
  buildNav();
  show(location.hash.slice(1) || "prehled");
  connect();
})();
