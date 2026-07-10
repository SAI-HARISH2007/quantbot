/* desk.js — the command-center layer.
   Listens to the "qb" event bus dispatched by app.js and adds: animated
   KPI counters, the market ticker tape, the AI thinking stream, the
   execution-pipeline pulse, the signal radar, toasts, and shortcuts. */
"use strict";
(() => {
const $$ = (s) => document.querySelector(s);

/* ---------- animated numbers: tween any KPI change ---------- */
const tweens = new Map();
function tween(el, to, fmt) {
  if (to == null || Number.isNaN(to)) return;
  const from = tweens.get(el) ?? to;
  tweens.set(el, to);
  if (from === to) { el.textContent = fmt(to); return; }
  const t0 = performance.now(), dur = 600;
  el.classList.add("tick-" + (to >= from ? "up" : "down"));
  setTimeout(() => el.classList.remove("tick-up", "tick-down"), 700);
  (function step(t) {
    const k = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmt(from + (to - from) * e);
    if (k < 1 && tweens.get(el) === to) requestAnimationFrame(step);
  })(t0);
}
const money = (v) => "$" + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

/* ---------- ticker tape ---------- */
const tickerData = new Map(); // cid -> {label, price, prev}
function renderTicker() {
  const items = [...tickerData.values()].map((t) => {
    const d = t.prev == null ? 0 : t.price - t.prev;
    const arrow = d > 0.0005 ? "▲" : d < -0.0005 ? "▼" : "·";
    const c = d > 0.0005 ? "pos" : d < -0.0005 ? "neg" : "dim";
    return `<span class="titem"><b>${t.label}</b> ${t.price.toFixed(3)} <span class="${c}">${arrow}</span></span>`;
  }).join("");
  const track = $$("#ticker-track");
  track.innerHTML = items + items; // duplicate for seamless loop
}

/* ---------- execution pipeline pulses ---------- */
function pulse(nodeId, kind) {
  const el = $$(nodeId);
  if (!el) return;
  el.classList.remove("pulse-ok", "pulse-warn", "pulse-bad");
  void el.offsetWidth; // restart animation
  el.classList.add("pulse-" + kind);
}

/* ---------- AI thinking stream ---------- */
const thoughts = [];
function pushThought(d) {
  thoughts.unshift(d);
  if (thoughts.length > 24) thoughts.pop();
  $$("#thinking").innerHTML = thoughts.map((t, i) =>
    `<div class="thought" style="opacity:${Math.max(1 - i * 0.05, .35)}">
      <span class="ph ${t.phase}">${t.phase}</span> ${escd(t.text)}</div>`).join("");
}
const escd = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

/* ---------- signal radar ---------- */
const radarPts = []; // {edge, conf, label, born, filled}
let radarChart;
function radar() {
  if (!radarChart) radarChart = echarts.init(document.getElementById("chart-radar"));
  const now = Date.now();
  for (let i = radarPts.length - 1; i >= 0; i--)
    if (now - radarPts[i].born > 120000) radarPts.splice(i, 1);
  radarChart.setOption({
    backgroundColor: "transparent", textStyle: {color: "#8b96a8"},
    grid: {left: 48, right: 14, top: 22, bottom: 28},
    tooltip: {formatter: (p) => p.data[3]},
    xAxis: {name: "edge", type: "value", min: 0, max: 0.2,
      axisLine: {lineStyle: {color: "#2a3140"}}, splitLine: {lineStyle: {color: "#1c2330"}}},
    yAxis: {name: "confidence", type: "value", min: 0, max: 1,
      axisLine: {lineStyle: {color: "#2a3140"}}, splitLine: {lineStyle: {color: "#1c2330"}}},
    series: [{
      type: "scatter",
      data: radarPts.map((p) => [Math.min(p.edge, 0.2), p.conf,
        1 - (now - p.born) / 120000, p.label, p.filled]),
      symbolSize: (d) => 6 + d[2] * 16,
      itemStyle: {color: (p) => p.data[4] ? "rgba(63,178,127," + (0.25 + p.data[2] * 0.75) + ")"
                                          : "rgba(74,163,255," + (0.2 + p.data[2] * 0.7) + ")"},
    }],
  });
}
setInterval(radar, 4000);

/* ---------- toasts ---------- */
function toast(kind, text, ms = 5000) {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  $$("#toasts").appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 400); }, ms);
}

/* ---------- market context (technical analysis layer) ---------- */
function renderTechnical(t) {
  if (!t || !t.snapshots) return;
  $$("#tech-provider").textContent = `via ${t.provider} — analysis only, never a trade trigger`;
  const recColor = (r) => r.includes("BUY") ? "pos" : r.includes("SELL") ? "neg" : "dim";
  $$("#tech-snaps").innerHTML = Object.values(t.snapshots).map((s) => `
    <div class="ev"><b>${escd(s.symbol)}</b>
      <span class="${recColor(s.recommendation || "")}">${escd(s.recommendation)}</span>
      <span class="dim">RSI ${s.rsi ? s.rsi.toFixed(0) : "—"} ·
      BBW ${s.bb_width ? s.bb_width.toFixed(3) : "—"} ·
      ADX ${s.adx ? s.adx.toFixed(0) : "—"}</span></div>`).join("");
  const scans = t.scans || {};
  $$("#tech-scans").innerHTML = Object.entries(scans).map(([kind, rows]) => `
    <div class="thought"><span class="ph models">${kind.replace("_", " ")}</span>
    ${rows.slice(0, 5).map((r) => `${escd(r.symbol)}${r.change_pct != null ? " " + (r.change_pct > 0 ? "+" : "") + r.change_pct.toFixed(1) + "%" : ""}${r.bbw != null ? " bbw " + r.bbw.toFixed(3) : ""}`).join(" · ")}</div>`).join("");
}

/* ---------- event bus ---------- */
document.addEventListener("qb", (e) => {
  const { type, data } = e.detail;
  if (type === "technical") renderTechnical(data);
  if (type === "snapshot" && data.technical) renderTechnical(data.technical);
  if (type === "market" || type === "snapshot") {
    const ms = type === "snapshot" ? (data.markets || []) : [data];
    ms.forEach((m) => {
      const prev = tickerData.get(m.condition_id)?.price;
      tickerData.set(m.condition_id, {
        label: (m.slug || m.question || "?").slice(0, 22), price: m.price, prev,
      });
    });
    renderTicker();
    if (type === "market") pulse("#n-data", "ok");
    if (type === "snapshot") (data.thinking || []).slice().reverse()
      .forEach((t) => pushThought(t.data));
  } else if (type === "thinking") {
    pushThought(data);
    pulse(data.phase === "models" ? "#n-models" : "#n-data", "ok");
  } else if (type === "signal") {
    pulse("#n-signal", "warn");
    radarPts.push({edge: data.edge, conf: data.confidence,
      label: `${data.strategy}: ${(data.question || "").slice(0, 40)}`,
      born: Date.now(), filled: false});
    radar();
  } else if (type === "decision") {
    pulse("#n-risk", data.risk_ok ? "ok" : "bad");
    if (data.outcome === "filled" && radarPts.length)
      radarPts[radarPts.length - 1].filled = true;
    if (data.outcome === "rejected")
      pushThought({phase: "risk", text:
        `Rejected ${data.strategy} on “${(data.market_question || "").slice(0, 40)}” — ${data.risk_reason}`});
  } else if (type === "fill") {
    pulse("#n-exec", "ok");
    toast("fill", `${data.side} ${Number(data.size).toFixed(1)} @ ${Number(data.price).toFixed(3)} — ${(data.question || "").slice(0, 44)}`);
  } else if (type === "trade_closed") {
    const pnl = Number(data.pnl);
    toast(pnl >= 0 ? "win" : "loss",
      `Closed ${data.strategy}: ${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)} (${data.exit_reason})`, 7000);
  } else if (type === "alert") {
    toast(data.level === "critical" ? "crit" : "warn", data.message, 9000);
  } else if (type === "status") {
    tween($$("#kpi-equity"), data.equity, money);
    tween($$("#kpi-cash"), data.cash, money);
    tween($$("#kpi-upnl"), data.unrealized_pnl, money);
    tween($$("#kpi-rpnl"), data.realized_pnl, money);
    tween($$("#kpi-bp"), data.buying_power, money);
    const r = data.regime || {};
    const badge = $$("#regime-badge");
    if (badge && r.label) {
      badge.textContent = `regime: ${r.label}` +
        (r.vol_percentile != null ? ` · vol p${Math.round(r.vol_percentile * 100)}` : "");
      badge.className = "regime " + r.label.replace(" ", "-");
    }
  }
});

/* ---------- keyboard shortcuts ---------- */
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  const k = e.key;
  if (k === "?") $$("#help-overlay").classList.toggle("hidden");
  else if (k === "Escape") { $$("#help-overlay").classList.add("hidden"); $$("#modal").classList.add("hidden"); }
  else if (k === "d") $$("#btn-daily").click();
  else if (k === "t") $$("#p-trades").scrollIntoView({behavior: "smooth"});
  else if (k === "m") $$("#p-markets").scrollIntoView({behavior: "smooth"});
  else if (k === "/") {
    e.preventDefault();
    let inp = $$("#mkt-filter");
    if (!inp) {
      inp = document.createElement("input");
      inp.id = "mkt-filter"; inp.placeholder = "filter markets… (Esc to clear)";
      $$("#p-markets h2").appendChild(inp);
      inp.addEventListener("input", () => {
        const q = inp.value.toLowerCase();
        document.querySelectorAll("#tbl-markets tbody tr").forEach((tr) =>
          tr.style.display = tr.textContent.toLowerCase().includes(q) ? "" : "none");
      });
      inp.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { inp.value = ""; inp.dispatchEvent(new Event("input")); inp.blur(); }
        ev.stopPropagation();
      });
    }
    inp.focus();
  }
});
$$("#help-overlay").addEventListener("click", () => $$("#help-overlay").classList.add("hidden"));
})();
