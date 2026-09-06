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
  forecast: null,
  alarmTimer: null,
};

// --- pomocné ------------------------------------------------------------------
const $ = sel => document.querySelector(sel);
const fmt = (v, dec) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : v.toFixed(dec).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, " ");

/**
 * Hodnota podle odkazu:
 *   "vzt1.t_supply"        měřená veličina zařízení
 *   "vzt1.sp:sp_room"      žádaná hodnota zařízení
 *   "energy.total.cost"    položka energetické bilance
 */
function lookup(ref) {
  if (ref.startsWith("energy.")) {
    return ref.split(".").slice(1)
      .reduce((o, k) => (o == null ? null : o[k]), app.state.energy) ?? null;
  }
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
/** Barva kontrolky zařízení — alarmy regulátoru i vyhodnocení provozu. */
function deviceStatus(id) {
  const d = app.state.devices[id];
  if (!d || !d.online) return "off";
  if (d.alarms && d.alarms.length) return "bad";
  const diag = d.diagnostics || [];
  if (diag.some(f => f.level === "bad")) return "bad";
  if (diag.some(f => f.level === "warn")) return "warn";
  return "ok";
}

function areaStatus(area) {
  const ids = app.meta.devices.filter(d => d.area === area).map(d => d.id);
  if (ids.every(id => !app.state.devices[id]?.online)) return "off";
  return ids.some(id => deviceStatus(id) === "bad") ? "bad" : "ok";
}

function paintNav() {
  const counts = app.state.alarm_counts || {};
  const dot = $("#nav-alarm-dot");
  if (dot) {
    dot.className = "dot " + (counts.unacked ? "bad" : counts.active ? "warn" : "ok");
    $("#nav-alarm-count").textContent = counts.active ? `${counts.active}` : "—";
  }
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

/** Skloňování po česku: 1 alarm, 2–4 alarmy, 5 a víc alarmů. */
function plural(n, one, few, many) {
  return n === 1 ? one : (n >= 2 && n <= 4 ? few : many);
}

function paintTopbar() {
  const counts = app.state.alarm_counts || {};
  const n = counts.active || 0;
  const chip = $("#alarm-chip");
  chip.textContent = n
    ? `${n} ${plural(n, "alarm", "alarmy", "alarmů")}`
      + (counts.unacked ? ` · ${counts.unacked} nekvitovaných` : " · kvitováno")
    : "bez alarmů";
  chip.classList.toggle("alarm", (counts.unacked || 0) > 0);
  chip.style.cursor = "pointer";
  chip.onclick = () => show("alarmy");

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
  // u jednoho zařízení má smysl nabídnout i kvitování rovnou tady
  const one = deviceIds && deviceIds.length === 1 ? deviceIds[0] : null;
  return `<div class="panel"><h3>Alarmy</h3>
    <ul class="alarm-list" id="alarm-list"></ul>
    ${one ? `<div class="acts" style="display:flex;gap:8px;margin-top:12px">
      <button class="btn small" id="dev-ack">Kvitovat alarmy</button>
      <button class="btn small danger" id="dev-reset">Odblokovat poruchu</button>
    </div>
    <div class="legend">Kvitování jen zapíše, kdo poruchu vzal na vědomí.
      Odblokování sáhne na zařízení a dovolí stroji znovu naběhnout — když
      závada trvá, porucha naskočí hned znovu.</div>` : ""}</div>`;
}

function wireAlarms(deviceIds) {
  const one = deviceIds && deviceIds.length === 1 ? deviceIds[0] : null;
  if (one) {
    const ack = $("#dev-ack"), reset = $("#dev-reset");
    if (ack) ack.addEventListener("click", async () => {
      ack.disabled = true;
      await post("/api/alarms/ack", { device: one });
      setTimeout(() => { ack.disabled = false; }, 1500);
    });
    if (reset) reset.addEventListener("click", async () => {
      reset.disabled = true;
      await post("/api/alarms/reset", { device: one });
      setTimeout(() => { reset.disabled = false; }, 1500);
    });
  }
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

// --- vyhodnocení provozu ---------------------------------------------------------
function diagPanel() {
  return `<div class="panel wide"><h3>Vyhodnocení provozu
    <span class="count" id="diag-count"></span></h3>
    <ul class="diag" id="diag-list"></ul></div>`;
}

function wireDiag(deviceId) {
  app.hooks.push(() => {
    const list = $("#diag-list");
    if (!list) return;
    const findings = app.state.devices[deviceId]?.diagnostics;
    if (!findings) {
      list.innerHTML = `<li><span class="mark wait"></span>
        <div class="head">Vyhodnocení se počítá z průběhu provozu — chvíli to potrvá.</div></li>`;
      $("#diag-count").textContent = "";
      return;
    }
    const bad = findings.filter(f => f.level === "bad").length;
    const warn = findings.filter(f => f.level === "warn").length;
    const count = $("#diag-count");
    count.className = "count" + (bad ? " bad" : warn ? " warn" : "");
    count.textContent = bad ? `${bad} k řešení`
      : warn ? `${warn} ke sledování` : "vše v pořádku";

    list.innerHTML = findings.map(f => `<li class="${f.level}">
      <span class="mark ${f.level}"></span>
      <div>
        <div class="head"><b>${f.title}</b> — ${f.msg}</div>
        ${f.detail ? `<div class="detail">${f.detail}</div>` : ""}
      </div></li>`).join("");
  });
}

// --- predikce zanesení filtru ----------------------------------------------------
function forecastPanel() {
  return `<div class="panel"><h3>Predikce výměny filtru</h3>
    <svg class="forecast" id="forecast" viewBox="0 0 600 170"
         preserveAspectRatio="none"></svg>
    <div class="legend">
      <span><i style="background:#56b6e0"></i>měřená tlaková ztráta</span>
      <span><i style="background:#e5b13a"></i>proložený trend</span>
      <span><i style="background:#e3564c"></i>mez výměny</span>
    </div></div>`;
}

/**
 * Tlaková ztráta filtru proti PROVOZNÍM HODINÁM jednotky, s proloženým
 * trendem dotaženým až na mez výměny. Vodorovná osa jsou provozní hodiny,
 * protože filtr se zanáší chodem ventilátoru, ne tím, že plyne čas.
 */
async function wireForecast(deviceId) {
  const draw = async () => {
    const svg = $("#forecast");
    if (!svg) return;
    const f = (app.state.devices[deviceId]?.diagnostics || [])
      .find(x => x.key === "filter_dp_sup");
    const chart = f?.chart;
    if (!chart || chart.slope === undefined) {
      svg.innerHTML = `<text x="300" y="85" class="axis" text-anchor="middle">
        trend se ještě sbírá…</text>`;
      return;
    }
    const data = await (await fetch(
      `/api/history/${deviceId}?keys=${chart.x},${chart.y}`)).json();
    const xs = (data[chart.x] || []), ys = (data[chart.y] || []);
    if (xs.length < 2) return;

    // vodorovná osa: od začátku měření až tam, kde trend narazí na mez
    const now = xs[xs.length - 1], dpNow = ys[ys.length - 1];
    const xLimit = (chart.limit - chart.intercept) / chart.slope;
    const x0 = xs[0];
    const x1 = Math.max(now + (now - x0) * 0.15, Math.min(xLimit * 1.02, x0 + 1e5));
    const y1 = Math.max(chart.limit * 1.12, Math.max(...ys) * 1.12);
    const PX = x => 46 + ((x - x0) / (x1 - x0 || 1)) * 536;
    const PY = y => 132 - (y / y1) * 116;

    const grid = [0, 0.5, 1].map(t => {
      const v = y1 * t;
      return `<line class="grid" x1="46" y1="${PY(v)}" x2="582" y2="${PY(v)}"/>
              <text class="axis" x="0" y="${PY(v) + 3}">${Math.round(v)} Pa</text>`;
    }).join("");

    const meas = xs.map((x, i) =>
      `${i === 0 ? "M" : "L"} ${PX(x).toFixed(1)} ${PY(ys[i]).toFixed(1)}`).join(" ");
    const trend = `M ${PX(x0)} ${PY(chart.slope * x0 + chart.intercept)}`
                + ` L ${PX(x1)} ${PY(chart.slope * x1 + chart.intercept)}`;

    // svislice "teď" odděluje naměřený úsek od extrapolace
    const nowLine = `<line class="now" x1="${PX(now)}" y1="14" x2="${PX(now)}"
          y2="132"/><text class="axis" x="${PX(now) + 5}" y="128">teď</text>
      <circle class="dot" cx="${PX(now)}" cy="${PY(dpNow)}" r="3.5"/>
      <text class="axis" x="${PX(now) + 5}" y="${PY(dpNow) - 6}">
        ${Math.round(dpNow)} Pa</text>`;

    const hit = (xLimit > now && xLimit <= x1)
      ? `<circle class="hit" cx="${PX(xLimit)}" cy="${PY(chart.limit)}" r="4"/>
         <text class="axis" x="${PX(xLimit) - 6}" y="${PY(chart.limit) + 16}"
               text-anchor="end">mez za ${Math.round(xLimit - now)} h provozu</text>` : "";

    svg.innerHTML = grid
      + `<line class="limit" x1="46" y1="${PY(chart.limit)}" x2="582"
               y2="${PY(chart.limit)}"/>`
      + `<text class="axis" x="46" y="${PY(chart.limit) - 6}">mez výměny
               ${Math.round(chart.limit)} Pa</text>`
      + `<path class="trend" d="${trend}"/><path class="meas" d="${meas}"/>`
      + nowLine + hit
      + `<text class="axis" x="46" y="152">${Math.round(x0)} h provozu jednotky</text>`
      + `<text class="axis" x="582" y="152" text-anchor="end">${Math.round(x1)} h</text>`;
  };
  draw();
  clearInterval(app.forecast);
  app.forecast = setInterval(draw, 5000);
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

// --- alarmy a kvitování -----------------------------------------------------------
// pozor na názvy: screens.js sdílí stejný jmenný prostor a má vlastní T()
const hhmmss = iso => new Date(iso).toLocaleTimeString("cs-CZ",
  { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dateTime = iso => new Date(iso).toLocaleString("cs-CZ",
  { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });

/** Doba trvání alarmu, čitelně. */
function dur(fromIso, toIso) {
  const s = Math.max(0, ((toIso ? new Date(toIso) : new Date()) - new Date(fromIso)) / 1000);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1).replace(".", ",")} h`;
  return `${Math.round(s / 86400)} dní`;
}

/** Jméno obsluhy se pamatuje v prohlížeči, ať se nepíše u každého kvitování. */
function operator() {
  try { return localStorage.getItem("operator") || ""; } catch { return ""; }
}
function setOperator(name) {
  try { localStorage.setItem("operator", name); } catch { /* soukromé okno */ }
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, by: operator() || "dispečink" }) });
  return r.json();
}

const devName = id => app.meta.devices.find(d => d.id === id)?.name || id;

function alarmScreen() {
  return `
  <div class="who">
    <label for="op">Kvituje</label>
    <input id="op" placeholder="vaše jméno" value="${operator()}">
    <span class="hint">jméno se zapíše ke každému kvitování, ať je dohledatelné,
      kdo poruchu vzal na vědomí</span>
  </div>

  <div class="panels">
    <div class="panel wide"><h3>Aktivní alarmy
      <span class="count" id="act-count"></span></h3>
      <ul class="alarms" id="active-list"></ul></div>
  </div>

  <div class="panels">
    <div class="panel wide"><h3>Historie alarmů
      <span class="count" id="hist-count"></span></h3>
      <div class="log-wrap"><table class="log">
        <thead><tr><th>Zařízení</th><th>Alarm</th><th>Vznik</th><th>Konec</th>
          <th>Trvání</th><th>Kvitoval</th></tr></thead>
        <tbody id="log-body"></tbody></table></div>
      <div class="legend">Záznamník sleduje změny: každý vznik i zánik alarmu
        má svůj čas. Porucha, která v noci naskočila a do rána zmizela, tak
        nezůstane nepovšimnutá.</div></div>
  </div>`;
}

/** Řádek aktivního alarmu s tlačítky. */
function activeRow(a) {
  const acked = !!a.acked_at;
  return `<li class="${acked ? "acked" : ""}" data-id="${a.id}" data-dev="${a.device}">
    <span class="mark"></span>
    <div>
      <div class="txt"><span class="dev">${devName(a.device)}</span> — ${a.text}</div>
      <div class="meta">trvá ${dur(a.raised_at, null)}, od ${hhmmss(a.raised_at)}${
        acked ? ` · kvitoval ${a.acked_by} v ${hhmmss(a.acked_at)}` : " · nekvitováno"}</div>
    </div>
    <div class="acts">
      <button class="btn ack" ${acked ? "disabled" : ""}>Kvitovat</button>
      <button class="btn danger reset">Odblokovat poruchu</button>
    </div>
  </li>`;
}

function wireAlarmScreen() {
  const op = $("#op");
  if (op) op.addEventListener("change", () => setOperator(op.value.trim()));

  const loadHistory = async () => {
    const body = $("#log-body");
    if (!body) return;
    const r = await (await fetch("/api/alarms?scope=history&limit=200")).json();
    $("#hist-count").textContent = `${r.rows.length} záznamů`;
    body.innerHTML = r.rows.length ? r.rows.map(a => `<tr>
      <td>${devName(a.device)}</td>
      <td class="${a.cleared_at ? "" : "on"}">${a.text}</td>
      <td class="t">${dateTime(a.raised_at)}</td>
      <td class="t">${a.cleared_at ? dateTime(a.cleared_at) : "trvá"}</td>
      <td class="dur">${dur(a.raised_at, a.cleared_at)}</td>
      <td class="who-cell">${a.acked_by || "—"}</td></tr>`).join("")
      : `<tr><td colspan="6" class="who-cell">Zatím žádný záznam.</td></tr>`;
  };

  // kliknutí se odchytává na seznamu, ať se nemusí věšet na každý řádek zvlášť
  const list = $("#active-list");
  if (list) list.addEventListener("click", async ev => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const li = btn.closest("li");
    btn.disabled = true;
    if (btn.classList.contains("ack")) {
      await post("/api/alarms/ack", { ids: [+li.dataset.id] });
    } else {
      await post("/api/alarms/reset", { device: li.dataset.dev });
    }
    loadHistory();
  });

  app.hooks.push(() => {
    const el = $("#active-list");
    if (!el) return;
    const rows = app.state.alarms_active || [];
    const counts = app.state.alarm_counts || {};
    const c = $("#act-count");
    c.className = "count" + (counts.unacked ? " bad" : rows.length ? " warn" : "");
    c.textContent = rows.length
      ? `${rows.length} ${plural(rows.length, "aktivní", "aktivní", "aktivních")}`
        + (counts.unacked ? `, ${counts.unacked} nekvitovaných` : ", vše kvitováno")
      : "žádný";
    el.className = "alarms" + (rows.length ? "" : " empty");
    el.innerHTML = rows.length ? rows.map(activeRow).join("")
      : "Žádný aktivní alarm — provoz je v pořádku.";
  });

  loadHistory();
  clearInterval(app.alarmTimer);
  app.alarmTimer = setInterval(loadHistory, 10000);
}

// --- energetika -------------------------------------------------------------------
/** Kachlička se souhrnným číslem. */
function tile(cls, cap, ref, o = {}) {
  const { dec = 0, unit = "", sub = "" } = o;
  return `<div class="tile ${cls}">
    <div class="cap">${cap}</div>
    <div class="big"><span data-v="${ref}" data-dec="${dec}">—</span>${
      unit ? `<small>${unit}</small>` : ""}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function energyScreen() {
  const kc = "Kč";
  return `
  <div class="tiles">
    ${tile("accent", "Elektřina — okamžitý příkon", "energy.electricity.power_kw",
      { dec: 1, unit: "kW", sub: `<span data-v="energy.electricity.energy_kwh"
        data-dec="0"></span> kWh od zapnutí · <span
        data-v="energy.electricity.per_day_kwh" data-dec="0"></span> kWh/den` })}
    ${tile("warm", "Plyn — okamžitý příkon", "energy.gas.power_kw",
      { dec: 1, unit: "kW", sub: `<span data-v="energy.gas.energy_kwh"
        data-dec="0"></span> kWh · <span data-v="energy.gas.volume_m3"
        data-dec="0"></span> m³ od zapnutí` })}
    ${tile("money", "Náklady na energie", "energy.total.per_day_cost",
      { dec: 0, unit: `${kc}/den`, sub: `celkem <span data-v="energy.total.cost"
        data-dec="0"></span> ${kc} od zapnutí` })}
    ${tile("cold", "Vyrobený chlad", "energy.produced.cool_kwh",
      { dec: 0, unit: "kWh", sub: `za <span data-v="energy.produced.cool_price"
        data-dec="2"></span> ${kc}/kWh` })}
  </div>

  <div class="split">
    <div class="panel"><h3>Kde se spotřebovává elektřina</h3>
      <ul class="usage" id="usage-el"></ul></div>
    <div class="panel"><h3>Měrné ukazatele</h3>
      <ul class="kpi" id="kpi-list"></ul></div>
  </div>

  <div class="tiles">
    ${tile("cold", "Ušetřila rekuperace", "energy.savings.energy_kwh",
      { dec: 0, unit: "kWh", sub: `to je <span data-v="energy.savings.cost"
        data-dec="0"></span> ${kc} · <span data-v="energy.savings.share"
        data-dec="0"></span> % práce výměníků` })}
    ${tile("loss", "Zmařeno topením proti chlazení", "energy.waste.energy_kwh",
      { dec: 0, unit: "kWh", sub: `to je <span data-v="energy.waste.cost"
        data-dec="0"></span> ${kc} · <span data-v="energy.waste.per_day_cost"
        data-dec="0"></span> ${kc}/den` })}
    ${tile("warm", "Dodané teplo", "energy.produced.heat_kwh",
      { dec: 0, unit: "kWh", sub: `za <span data-v="energy.produced.heat_price"
        data-dec="2"></span> ${kc}/kWh` })}
    ${tile("", "Voda do chladicí věže", "energy.water.volume_m3",
      { dec: 1, unit: "m³", sub: `to je <span data-v="energy.water.cost"
        data-dec="0"></span> ${kc}` })}
  </div>

  <div class="split">
    <div class="panel"><h3>Plyn po kotlích</h3>
      <ul class="usage" id="usage-gas"></ul></div>
    <div class="panel"><h3>Měrný příkon ventilátorů</h3>
      <ul class="kpi" id="sfp-list"></ul>
      <div class="legend">Kolik elektřiny spotřebují ventilátory na protlačený
        metr kubický za sekundu. Roste se zanášením filtrů — nad 2,5 se vyplatí
        podívat se na tlakové ztráty.</div></div>
  </div>`;
}

function wireEnergy() {
  const row = (i, cls) => `<li>
    <div class="top"><span class="nm">${i.name}</span>
      <span class="ar">${i.area}</span>
      <span class="num"><b>${fmt(i.power_kw, 1)}</b> kW ·
        ${fmt(i.energy_kwh, 0)} kWh · <b>${fmt(i.cost, 0)}</b> Kč</span></div>
    <div class="track"><div class="fill ${cls}" style="width:${i.share}%"></div></div>
  </li>`;

  app.hooks.push(() => {
    const e = app.state.energy;
    if (!e) return;
    const el = $("#usage-el");
    if (el) el.innerHTML = e.electricity.items.map(i => row(i, "")).join("");
    const gas = $("#usage-gas");
    if (gas) gas.innerHTML = e.gas.items.map(i => row(i, "gas")).join("");

    const kpi = $("#kpi-list");
    if (kpi) kpi.innerHTML = e.kpi.map(k => `<li>
      <div><div class="nm">${k.name}</div><div class="note">${k.note}</div></div>
      <span class="val">${k.value === null ? "—"
        : fmt(k.value, k.dec) + (k.unit ? " " + k.unit : "")}</span></li>`).join("");

    const sfp = $("#sfp-list");
    if (sfp) sfp.innerHTML = e.sfp.length
      ? e.sfp.map(s => `<li><div class="nm">${s.name}</div>
          <span class="val">${fmt(s.value, 2)} kW/(m³/s)</span></li>`).join("")
      : `<li><div class="note">Žádná jednotka neběží.</div></li>`;
  });
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
  alarmy: {
    title: "Alarmy a kvitování",
    build: () => alarmScreen(),
    wire: () => wireAlarmScreen(),
  },
  energie: {
    title: "Energie a náklady",
    build: () => energyScreen(),
    wire: () => wireEnergy(),
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

/** Energetika jedné jednotky — co spotřebuje a co ušetří nebo zmaří. */
function ahuEnergyPanel(id) {
  const rows = [
    ["Elektřina ventilátorů", `${id}.el_energy`, "kWh"],
    ["Odebrané teplo", `${id}.heat_energy`, "kWh"],
    ["Odebraný chlad", `${id}.cool_energy`, "kWh"],
    ["Ušetřeno rekuperací", `${id}.recup_energy`, "kWh"],
    ["Zmařeno topením proti chlazení", `${id}.waste_energy`, "kWh"],
  ];
  return `<div class="panel"><h3>Energie jednotky</h3>
    ${rows.map(([n, ref, u]) => `<div class="row"><span>${n}</span>
      <span><span data-v="${ref}" data-dec="0"></span> ${u}</span></div>`).join("")}
    <div class="row"><span>Měrný příkon ventilátorů</span>
      <span id="sfp-one">—</span></div>
    <div class="legend">Počítadla narůstají od zapnutí regulátoru, stejně jako
      na skutečném měřidle.</div></div>`;
}

function wireAhuEnergy(id) {
  app.hooks.push(() => {
    const el = $("#sfp-one");
    if (!el) return;
    const s = (app.state.energy?.sfp || []).find(x => x.device === id);
    el.textContent = s ? `${fmt(s.value, 2)} kW/(m³/s)` : "—";
  });
}

function ahuView(id) {
  const dev = app.meta.devices.find(d => d.id === id);
  return {
    title: dev.name,
    build: () => SCREENS.ahu(id) + `<div class="panels">
      ${diagPanel()}
      ${setpointPanel(id)}
      ${trendPanel(id, [
        { key: "t_extract", label: "teplota v hale" },
        { key: "t_supply", label: "přívod" },
        { key: "t_outdoor", label: "venkovní" }])}
      ${forecastPanel()}
      ${ahuEnergyPanel(id)}
      ${alarmPanel([id])}</div>`,
    wire: () => {
      wireSetpoints();
      wireDiag(id);
      wireAhuEnergy(id);
      wireAlarms([id]);
      wireTrend(id, [{ key: "t_extract" }, { key: "t_supply" }, { key: "t_outdoor" }]);
      wireForecast(id);
    },
  };
}

function show(view) {
  const spec = VIEWS[view] || ahuView(view);
  app.view = view;
  app.hooks = [];
  clearInterval(app.trend);
  clearInterval(app.forecast);
  clearInterval(app.alarmTimer);
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
         { ref: "kotelna.t_header_flow", dec: 1, unit: " °C" }) +
    `<div class="group">Energie</div>` +
    item("energie", "Energie a náklady", "vzt", "area",
         { ref: "energy.electricity.power_kw", dec: 0, unit: " kW" }) +
    `<div class="group">Provoz</div>` +
    `<a data-view="alarmy"><span class="dot" id="nav-alarm-dot"></span>
       <span>Alarmy a kvitování</span>
       <span class="val" id="nav-alarm-count">—</span></a>`;

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
  $("#version").textContent = `rozhraní ${app.meta.version}`;
  show(location.hash.slice(1) || "prehled");
  connect();
})();
