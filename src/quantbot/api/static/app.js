/* QuantBot trading desk frontend.
   State flows in over one WebSocket (snapshot + incremental events);
   heavier analytics (metrics, strategies, reports) refresh over REST. */
"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  status: {}, markets: new Map(), events: [], trades: [],
  equity: [], metrics: {}, strategies: [], perf: {},
};

/* ---------------- formatting ---------------- */
const fmt$ = (v) => v == null ? "—" : "$" + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const fmtP = (v, d = 3) => v == null ? "—" : Number(v).toFixed(d);
const fmtPct = (v, sign = true) => v == null ? "—" : ((sign && v > 0) ? "+" : "") + (v * 100).toFixed(2) + "%";
const cls = (v) => v == null ? "" : v >= 0 ? "pos" : "neg";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tShort = (iso) => iso ? new Date(iso).toLocaleTimeString([], {hour12: false}) : "";

/* ---------------- charts ---------------- */
const dark = { backgroundColor: "transparent", textStyle: { color: "#8b96a8" } };
const gridDef = { left: 55, right: 12, top: 18, bottom: 24 };
const axis = { axisLine: { lineStyle: { color: "#2a3140" } }, splitLine: { lineStyle: { color: "#1c2330" } } };
const charts = {};
function chart(id) {
  if (!charts[id]) { charts[id] = echarts.init(document.getElementById(id), null, {renderer: "canvas"}); }
  return charts[id];
}
window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));

function renderEquityChart() {
  if (!state.equity.length) return;
  const eq = state.equity.map((r) => [r.ts, r.equity]);
  let peak = -Infinity;
  const dd = state.equity.map((r) => { peak = Math.max(peak, r.equity); return [r.ts, -(1 - r.equity / peak) * 100]; });
  chart("chart-equity").setOption({
    ...dark, grid: [{...gridDef, height: "55%"}, {...gridDef, top: "72%", height: "20%"}],
    tooltip: { trigger: "axis" },
    xAxis: [{type: "time", ...axis}, {type: "time", gridIndex: 1, ...axis, axisLabel: {show: false}}],
    yAxis: [{type: "value", scale: true, ...axis, axisLabel: {formatter: (v) => "$" + v.toLocaleString()}},
            {type: "value", gridIndex: 1, ...axis, axisLabel: {formatter: "{value}%"}, max: 0}],
    series: [
      {name: "Equity", type: "line", data: eq, showSymbol: false, lineStyle: {color: "#4aa3ff", width: 2},
       areaStyle: {color: "rgba(74,163,255,.08)"}},
      {name: "Drawdown", type: "line", data: dd, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false,
       lineStyle: {color: "#e5636c", width: 1}, areaStyle: {color: "rgba(229,99,108,.15)"}},
    ],
  });
}

function renderExposureChart() {
  const pos = state.status.positions || [];
  const byMkt = {};
  pos.forEach((p) => { byMkt[p.question?.slice(0, 28) || p.token_id.slice(0, 8)] =
    (byMkt[p.question?.slice(0, 28)] || 0) + p.size * p.mark; });
  const names = Object.keys(byMkt), vals = names.map((n) => byMkt[n]);
  chart("chart-exposure").setOption({
    ...dark, grid: {...gridDef, left: 130}, tooltip: {},
    xAxis: {type: "value", ...axis}, yAxis: {type: "category", data: names, ...axis},
    series: [{type: "bar", data: vals, itemStyle: {color: "#4aa3ff", borderRadius: 3}, barMaxWidth: 14}],
  });
}

function renderAllocChart() {
  const pos = state.status.positions || [];
  const data = pos.map((p) => ({name: (p.question || "?").slice(0, 24), value: +(p.size * p.mark).toFixed(2)}));
  data.push({name: "Cash", value: +(state.status.cash || 0).toFixed(2)});
  chart("chart-alloc").setOption({
    ...dark, tooltip: {trigger: "item", formatter: "{b}: ${c} ({d}%)"},
    series: [{type: "pie", radius: ["45%", "72%"], data,
      label: {color: "#8b96a8", fontSize: 10}, itemStyle: {borderColor: "#161b22", borderWidth: 2}}],
  });
}

function renderRollingChart() {
  const rs = state.metrics.rolling_sharpe || [];
  chart("chart-rolling").setOption({
    ...dark, grid: {...gridDef, top: 8, bottom: 18}, tooltip: {trigger: "axis"},
    xAxis: {type: "time", ...axis}, yAxis: {type: "value", scale: true, ...axis, name: "24h Sharpe"},
    series: [{type: "line", data: rs.map((r) => [r.ts, r.v]), showSymbol: false,
      lineStyle: {color: "#d9a13c", width: 1.5}}],
  });
}

/* ---------------- top bar & panels ---------------- */
function renderStatus() {
  const s = state.status;
  $("#kpi-equity").textContent = fmt$(s.equity);
  $("#kpi-cash").textContent = fmt$(s.cash);
  $("#kpi-bp").textContent = fmt$(s.buying_power);
  const up = $("#kpi-upnl"); up.textContent = fmt$(s.unrealized_pnl); up.className = cls(s.unrealized_pnl);
  const rp = $("#kpi-rpnl"); rp.textContent = fmt$(s.realized_pnl); rp.className = cls(s.realized_pnl);
  $("#kpi-dd").textContent = fmtPct(-(s.drawdown || 0), false);
  $("#run-id").textContent = s.run_id || "";
  ["day", "week", "month"].forEach((k, i) => {
    const v = state.perf[["daily", "weekly", "monthly"][i]];
    const el = $("#kpi-" + k); el.textContent = fmtPct(v); el.className = cls(v);
  });
  const ks = $("#kill-badge");
  if (s.halted) { ks.textContent = "KILL SWITCH ACTIVE"; ks.className = "badge bad"; }
  else { ks.textContent = "KS armed"; ks.className = "badge ok"; }
  $("#risk-exposure").textContent = fmt$(s.exposure);
  renderPositions();
  renderExposureChart();
  renderAllocChart();
}

function renderPositions() {
  const tb = $("#tbl-positions tbody");
  const pos = state.status.positions || [];
  tb.innerHTML = pos.length ? pos.map((p) => `
    <tr><td class="q" title="${esc(p.question)}">${esc((p.question || p.token_id).slice(0, 42))}</td>
    <td>${p.outcome}</td><td>${p.size.toFixed(1)}</td><td>${fmtP(p.avg_price)}</td>
    <td>${fmtP(p.mark)}</td><td class="${cls(p.unrealized_pnl)}">${fmt$(p.unrealized_pnl)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="dim">no open positions</td></tr>`;
}

function renderMarkets() {
  const tb = $("#tbl-markets tbody");
  const rows = [...state.markets.values()].map((m) => {
    const dis = disagreement(m.fv_models);
    const imb = m.imbalance ?? 0;
    const book = (m.bids || []).slice(0, 5).reverse().map((l) => `<i class="b" style="height:${barH(l[1], m)}px"></i>`).join("")
      + (m.asks || []).slice(0, 5).map((l) => `<i class="a" style="height:${barH(l[1], m)}px"></i>`).join("");
    return `<tr class="clickable" data-cid="${m.condition_id}">
      <td class="q" title="${esc(m.question)}">${esc(m.question.slice(0, 48))}</td>
      <td>${fmtP(m.best_bid)}</td><td>${fmtP(m.best_ask)}</td><td><b>${fmtP(m.price)}</b></td>
      <td>${fmtP(m.spread)}</td>
      <td>${m.fair_value != null ? `${fmtP(m.fair_value)} <span class="dim">±${fmtP(m.fair_value_std, 2)}</span>` : "<span class='dim'>—</span>"}</td>
      <td><span class="dis-wrap"><span class="bar" style="width:${Math.min(dis * 400, 70)}px;background:${dis > 0.08 ? "var(--red)" : "var(--amber)"}"></span></span></td>
      <td><span class="imb-wrap"><i style="left:${imb >= 0 ? 50 : 50 + imb * 50}%;width:${Math.abs(imb) * 50}%;background:${imb >= 0 ? "var(--green)" : "var(--red)"}"></i></span></td>
      <td><span class="book">${book}</span></td></tr>`;
  });
  tb.innerHTML = rows.join("") || `<tr><td colspan="9" class="dim">waiting for market data…</td></tr>`;
  tb.querySelectorAll("tr[data-cid]").forEach((tr) =>
    tr.addEventListener("click", () => showMarketModal(tr.dataset.cid)));
}
const barH = (size, m) => {
  const max = Math.max(...(m.bids || []).map((l) => l[1]), ...(m.asks || []).map((l) => l[1]), 1);
  return Math.max(2, (size / max) * 20);
};
function disagreement(models) {
  const ps = Object.values(models || {}).map((v) => v.prob).filter((p) => p != null);
  if (ps.length < 2) return 0;
  const mean = ps.reduce((a, b) => a + b, 0) / ps.length;
  return Math.sqrt(ps.reduce((a, p) => a + (p - mean) ** 2, 0) / ps.length);
}

function renderFeed() {
  const feed = $("#feed");
  feed.innerHTML = state.events.slice(0, 80).map((ev) => {
    const d = ev.data; let tag = ev.type, body = "";
    if (ev.type === "signal") body = `<b>${esc(d.strategy)}</b> ${d.side} <span class="q">${esc((d.question || "").slice(0, 44))}</span> edge ${fmtP(d.edge)} conf ${fmtP(d.confidence, 2)}`;
    else if (ev.type === "order") body = `${d.status} ${d.side || ""} ${d.size ? d.size.toFixed(1) : ""} @ ${fmtP(d.price)} <span class="dim">${esc((d.question || "").slice(0, 36))}</span>`;
    else if (ev.type === "fill") body = `<b>${d.side}</b> ${d.size.toFixed(1)} @ ${fmtP(d.price)} <span class="q">${esc((d.question || "").slice(0, 40))}</span> <span class="dim">${esc(d.strategy)}</span>`;
    else if (ev.type === "decision") { tag = d.outcome === "rejected" ? "rejected" : "decision";
      body = `${esc(d.strategy)} → <b>${d.outcome}</b> <span class="dim">${esc(d.risk_reason || "")}</span> <span class="q">${esc((d.market_question || "").slice(0, 36))}</span>`; }
    else if (ev.type === "trade_closed") body = `closed ${esc(d.strategy)} PnL <b class="${cls(d.pnl)}">${fmt$(d.pnl)}</b> (${esc(d.exit_reason)})`;
    else if (ev.type === "alert") body = `<b>${esc(d.message)}</b>`;
    else return "";
    const click = (ev.type === "decision" || ev.type === "fill") && d.decision_id !== undefined || ev.type === "decision";
    return `<div class="ev ${click ? "clickable" : ""}" ${click ? `data-did="${d.decision_id}"` : ""}>
      <span class="t">${tShort(ev.ts)}</span><span class="tag ${tag}">${tag}</span><span>${body}</span></div>`;
  }).join("");
  feed.querySelectorAll("[data-did]").forEach((el) =>
    el.addEventListener("click", () => showDecisionModal(el.dataset.did)));
}

function renderTrades() {
  const tb = $("#tbl-trades tbody");
  tb.innerHTML = state.trades.length ? state.trades.slice(0, 50).map((t, i) => `
    <tr class="clickable" data-i="${i}"><td>${tShort(t.ts)}</td><td>${esc(t.strategy)}</td>
    <td class="q">${esc((t.market_question || "").slice(0, 32))}</td>
    <td class="${cls(t.pnl)}">${fmt$(t.pnl)}</td><td class="dim">${esc(t.exit_reason)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="dim">no closed trades yet</td></tr>`;
  tb.querySelectorAll("tr[data-i]").forEach((tr) =>
    tr.addEventListener("click", () => showTradeModal(state.trades[+tr.dataset.i])));
}

function renderMetrics() {
  const m = state.metrics;
  const items = [["Sharpe", m.sharpe], ["Sortino", m.sortino], ["Win rate", m.win_rate, "pct"],
    ["Profit factor", m.profit_factor], ["Max DD", m.max_drawdown, "pct"], ["Trades", m.n_trades, "int"],
    ["Expectancy", m.expectancy, "$"], ["Turnover", m.turnover]];
  $("#metrics-grid").innerHTML = items.map(([l, v, k]) => `<div><label>${l}</label>
    <b>${v == null ? "—" : k === "pct" ? fmtPct(v, false) : k === "int" ? v : k === "$" ? fmt$(v) : Number(v).toFixed(2)}</b></div>`).join("");
  renderRollingChart();
}

function renderStrategies() {
  $("#strategy-cards").innerHTML = state.strategies.map((s, i) => `
    <div class="scard" data-i="${i}"><div class="rowline"><b>${esc(s.name)}</b>
      <span class="stage ${s.stage}">${s.stage}</span></div>
      <div class="stats">${s.trades || 0} trades · PnL <span class="${cls(s.pnl)}">${fmt$(s.pnl || 0)}</span>
      ${s.win_rate != null ? " · WR " + fmtPct(s.win_rate, false) : ""}${s.enabled ? " · active" : ""}</div></div>`).join("");
  document.querySelectorAll(".scard").forEach((el) =>
    el.addEventListener("click", () => showStrategyModal(state.strategies[+el.dataset.i])));
}

function renderLogs(logs) {
  $("#logfeed").innerHTML = logs.slice(0, 60).map((l) => `
    <div class="ev"><span class="t">${tShort(l.ts)}</span>
    <span class="tag ${l.data.level === "warning" || l.data.level === "error" || l.data.level === "critical" ? "alert" : "decision"}">${esc(l.data.level || "info")}</span>
    <span>${esc(l.data.message)}</span></div>`).join("");
}

/* ---------------- modals ---------------- */
function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
$("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

function kv(rows) {
  return `<table>${rows.filter((r) => r[1] !== undefined && r[1] !== null && r[1] !== "")
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`;
}

async function showDecisionModal(id) {
  const d = await (await fetch(`/api/decisions/${id}`)).json();
  if (!d.decision_id) return;
  const models = Object.entries(d.fair_value_models || {}).map(([m, v]) =>
    `<tr><td>${esc(m)}</td><td>${fmtP(v.prob)} ± ${fmtP(v.std, 3)}</td></tr>`).join("");
  const sz = d.sizing || {};
  openModal(`<h3>Decision ${d.decision_id} — <span class="tag ${d.outcome === "rejected" ? "rejected" : "fill"}">${d.outcome}</span></h3>
    <div class="dim">${esc(d.market_question)}</div>
    <h4>Signal</h4>${kv([["Strategy", esc(d.strategy)], ["Side", esc(d.side)],
      ["Edge (expected value/share)", fmtP(d.signal_edge)], ["Confidence", fmtP(d.signal_confidence, 2)],
      ["Evidence", esc(JSON.stringify(d.signal_metadata))]])}
    <h4>Market evidence</h4>${kv([["Price", fmtP(d.market_price)], ["Best bid/ask", `${fmtP(d.best_bid)} / ${fmtP(d.best_ask)}`],
      ["Spread", fmtP(d.spread)], ["Book imbalance", fmtP(d.book_imbalance, 2)]])}
    <h4>Fair value</h4>${kv([["Ensemble", d.fair_value != null ? `${fmtP(d.fair_value)} ± ${fmtP(d.fair_value_std, 3)}` : "not available"]])}
    ${models ? `<table>${models}</table>` : ""}
    <h4>Position sizing</h4>${kv([["Method", esc(sz.method)], ["Full Kelly fraction", sz.kelly_full != null ? fmtP(sz.kelly_full, 3) : undefined],
      ["× config fraction", sz.kelly_fraction_cfg], ["× confidence", fmtP(sz.confidence, 2)],
      ["After caps", sz.notional != null ? fmt$(sz.notional) + " notional" : undefined],
      ["Result", esc(sz.result)], ["Shares", sz.size_shares != null ? sz.size_shares.toFixed(1) : undefined]])}
    <h4>Risk check</h4>${kv([["Verdict", d.risk_ok ? "✅ approved" : `❌ ${esc(d.risk_reason)}`],
      ["Equity", fmt$((d.risk_state || {}).equity)], ["Exposure before", fmt$((d.risk_state || {}).exposure)]])}
    <h4>Execution</h4>${kv([["Limit price", fmtP(d.limit_price)], ["Fill", d.fill_price != null ? `${d.fill_size?.toFixed(1)} @ ${fmtP(d.fill_price)}` : "—"],
      ["Fee", fmt$(d.fee)], ["Slippage vs mid", d.slippage != null ? fmtP(d.slippage) : "—"]])}
    <h4>Exit policy (declared at entry)</h4><div class="dim">${esc(d.exit_policy)}</div>`);
}

function showTradeModal(t) {
  openModal(`<h3>Post-trade report — <span class="${cls(t.pnl)}">${fmt$(t.pnl)}</span></h3>
    <div class="dim">${esc(t.market_question)}</div>
    <h4>Trade</h4>${kv([["Strategy", esc(t.strategy)], ["Entry", `${fmtP(t.entry_price)} (${t.entry_ts ? new Date(t.entry_ts).toLocaleString() : "?"})`],
      ["Exit", `${fmtP(t.exit_price)} (${new Date(t.ts).toLocaleString()})`], ["Size", t.size.toFixed(1)],
      ["Holding", `${t.holding_hours.toFixed(1)} h`], ["Fees", fmt$(t.fees)], ["Exit reason", esc(t.exit_reason)]])}
    <h4>Hypothesis review</h4>${kv([["Hypothesis", esc(t.hypothesis)],
      ["Entry fair value", t.entry_fair_value != null ? fmtP(t.entry_fair_value) : "n/a"],
      ["Prediction correct?", t.hypothesis_correct == null ? "unresolved" : t.hypothesis_correct ? "✅ yes" : "❌ no"]])}
    <h4>What the strategy learned</h4><div>${esc(t.lessons)}</div>
    ${t.entry_decision_ids?.length ? `<h4>Entry decision</h4><button class="btn" onclick="showDecisionModal('${t.entry_decision_ids[0]}')">View full entry decision</button>` : ""}`);
}

function showStrategyModal(s) {
  openModal(`<h3>${esc(s.name)} — <span class="stage ${s.stage}">${s.stage}</span></h3>
    <h4>Promotion criteria</h4>
    ${s.criteria.map((c) => `<div class="crit"><span class="${c.met ? "ok" : "no"}">${c.met ? "✔" : "✘"}</span>
      <b>${esc(c.name)}</b> <span class="dim">${esc(c.detail)}</span></div>`).join("")}
    <p class="dim" style="margin-top:10px">A strategy reaches <b>candidate</b> automatically when all
    evidence criteria pass. Promotion to <b>live</b> is always a manual, documented decision — see
    HOW_TO_USE_QUANTBOT.md §9.</p>`);
}

function showMarketModal(cid) {
  const m = state.markets.get(cid);
  if (!m) return;
  const models = Object.entries(m.fv_models || {}).map(([name, v]) =>
    `<tr><td>${esc(name)}</td><td>${fmtP(v.prob)} ± ${fmtP(v.std, 3)}</td></tr>`).join("");
  openModal(`<h3>${esc(m.question)}</h3>
    <h4>Order book (top 8)</h4>${kv([["Best bid", `${fmtP(m.best_bid)}`], ["Best ask", `${fmtP(m.best_ask)}`],
      ["Mid", fmtP(m.price)], ["Spread", fmtP(m.spread)], ["Imbalance", fmtP(m.imbalance, 2)]])}
    <h4>Fair value models</h4>
    ${models ? `<table>${models}</table>` : "<div class='dim'>No model can price this market (that's honest, not broken).</div>"}
    ${m.fair_value != null ? `<h4>Ensemble</h4>${kv([["P(YES)", `${fmtP(m.fair_value)} ± ${fmtP(m.fair_value_std, 3)}`],
      ["vs market", `${fmtP(m.price)} → edge ${fmtP(m.fair_value - m.price)}`]])}` : ""}`);
}

/* ---------------- daily report ---------------- */
$("#btn-daily").addEventListener("click", async () => {
  const box = $("#daily-report");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  const r = await (await fetch("/api/report/daily")).json();
  box.textContent = JSON.stringify(r, null, 2);
  box.classList.remove("hidden");
});

/* ---------------- REST refresh ---------------- */
async function refreshSlow() {
  try {
    const [summary, equity, metrics, strategies, experiments, trades, health] = await Promise.all([
      fetch("/api/summary").then((r) => r.json()),
      fetch("/api/equity").then((r) => r.json()),
      fetch("/api/metrics").then((r) => r.json()),
      fetch("/api/strategies").then((r) => r.json()),
      fetch("/api/experiments").then((r) => r.json()),
      fetch("/api/trades?limit=50").then((r) => r.json()),
      fetch("/api/health").then((r) => r.json()),
    ]);
    if (summary.status && Object.keys(summary.status).length) state.status = summary.status;
    state.perf = summary.performance || {};
    state.equity = equity; state.metrics = metrics; state.strategies = strategies;
    state.trades = trades;
    $("#res-experiments").textContent = experiments.length;
    $("#res-health").textContent = health.ok ? "OK" : "DEGRADED";
    $("#res-health").className = health.ok ? "pos" : "neg";
    $("#mode-badge").textContent = health.mode;
    const rl = summary.risk_limits || {};
    $("#risk-cap").textContent = fmt$(rl.max_total_exposure);
    $("#risk-ks").textContent = rl.max_drawdown_pct != null ? fmtPct(-rl.max_drawdown_pct, false) + " DD" : "—";
    renderStatus(); renderEquityChart(); renderMetrics(); renderStrategies(); renderTrades();
    document.dispatchEvent(new CustomEvent("qb-slow", { detail: { summary, metrics, strategies } }));
  } catch (e) { console.error("refresh failed", e); }
}

/* risk caps from config surfaced via summary.status */
async function loadStaticRisk() {
  const h = await (await fetch("/api/health")).json().catch(() => ({}));
  $("#mode-badge").textContent = h.mode || "paper";
}

/* ---------------- WebSocket ---------------- */
let ws, wsRetry = 1000;
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 1000; setBadge("live", "ok"); };
  ws.onclose = () => { setBadge("reconnecting…", "warn"); setTimeout(connect, wsRetry); wsRetry = Math.min(wsRetry * 2, 15000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    // desk.js (command-center layer) listens on this bus
    document.dispatchEvent(new CustomEvent("qb", { detail: msg }));
    if (msg.type === "snapshot") {
      const d = msg.data;
      if (d.status && Object.keys(d.status).length) state.status = d.status;
      (d.markets || []).forEach((m) => state.markets.set(m.condition_id, m));
      state.events = d.events || [];
      renderStatus(); renderMarkets(); renderFeed(); renderLogs(d.logs || []);
    } else if (msg.type === "market") {
      state.markets.set(msg.data.condition_id, msg.data); renderMarkets();
    } else if (msg.type === "status") {
      state.status = msg.data; renderStatus();
    } else if (["signal", "order", "fill", "decision", "alert", "trade_closed"].includes(msg.type)) {
      state.events.unshift(msg); state.events = state.events.slice(0, 200); renderFeed();
      if (msg.type === "trade_closed") refreshSlow();
      if (msg.type === "alert") { renderLogs([msg, ...(state.logsCache || [])]); }
    } else if (msg.type === "log") {
      state.logsCache = [msg, ...(state.logsCache || [])].slice(0, 100);
      renderLogs(state.logsCache);
    }
  };
  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
}
function setBadge(text, klass) { const b = $("#conn-badge"); b.textContent = text; b.className = "badge " + klass; }

/* ---------------- boot ---------------- */
connect();
loadStaticRisk();
refreshSlow();
setInterval(refreshSlow, 20000);
