(() => {
  "use strict";
  const NAMES = { stocks: "Stocks account", crypto: "Crypto account" };
  const WAITING = { stocks: "Orders waiting for the next market open", crypto: "Orders waiting for the next day to start (midnight UTC)" };
  const VALUED = { stocks: "Valued at the market close on", crypto: "Valued at the last price of" };
  const BENCH = { stocks: "an S&P 500 fund (SPY)", crypto: "bitcoin" };
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n, digits = 2) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const price = (n) => money(n, n < 1 ? 5 : n < 100 ? 3 : 2);
  const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + money(Math.abs(n));
  const pct = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(2) + "%";
  const tone = (n) => (n > 0.004 ? "up" : n < -0.004 ? "down" : "");
  const units = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: n < 1 ? 6 : 4 });
  const day = (iso) => new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: iso.length === 10 ? "UTC" : undefined });
  const clock = (iso) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

  async function get(path) {
    const res = await fetch(path + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error(path + " " + res.status);
    return path.endsWith(".json") ? res.json() : res.text();
  }

  function chart(points) {
    if (points.length < 2) return '<p class="quiet">The chart begins once two days are on the books.</p>';
    const W = 560, H = 190, L = 4, R = 52, T = 10, B = 22;
    const vals = points.flatMap((p) => [p.equity, p.benchmark]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.12, 4);
    lo -= pad; hi += pad;
    const x = (i) => L + (i / (points.length - 1)) * (W - L - R);
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const path = (key) => points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p[key]).toFixed(1)).join("");
    const ticks = [lo + pad, (lo + hi) / 2, hi - pad];
    const grid = ticks.map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/><text x="${W - R + 8}" y="${(y(v) + 4).toFixed(1)}">${money(v, 0)}</text>`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Account value compared with the benchmark, day by day">
      ${grid}
      <path class="bench" d="${path("benchmark")}"/><path class="line" d="${path("equity")}"/>
      <g class="hover" style="display:none"><line class="cursor" y1="${T}" y2="${H - B}"/><circle class="dot b" r="3.5"/><circle class="dot" r="3.5"/></g>
      <text x="${L}" y="${H - 5}">${esc(day(points[0].date))}</text>
      <text x="${W - R}" y="${H - 5}" text-anchor="end">${esc(day(points.at(-1).date))}</text>
    </svg>`;
  }

  function wireChart(sheet, points) {
    const svg = sheet.querySelector("svg"), readout = sheet.querySelector(".readout");
    if (!svg) return;
    const hover = svg.querySelector(".hover"), [cursor, dotB, dotE] = hover.children;
    const W = 560, H = 190, L = 4, R = 52, T = 10, B = 22;
    const vals = points.flatMap((p) => [p.equity, p.benchmark]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.12, 4); lo -= pad; hi += pad;
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const move = (ev) => {
      const box = svg.getBoundingClientRect();
      const frac = ((ev.clientX - box.left) / box.width * W - L) / (W - L - R);
      const i = Math.max(0, Math.min(points.length - 1, Math.round(frac * (points.length - 1))));
      const px = L + (i / (points.length - 1)) * (W - L - R), p = points[i];
      cursor.setAttribute("x1", px); cursor.setAttribute("x2", px);
      dotE.setAttribute("cx", px); dotE.setAttribute("cy", y(p.equity));
      dotB.setAttribute("cx", px); dotB.setAttribute("cy", y(p.benchmark));
      hover.style.display = "";
      readout.textContent = `${day(p.date)}: account ${money(p.equity)}, benchmark ${money(p.benchmark)}`;
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", () => { hover.style.display = "none"; readout.textContent = ""; });
  }

  function sheet(name, acct, points, journal) {
    const el = document.createElement("article");
    el.className = "sheet";
    const started = acct.benchmark.start_date;
    if (!started) {
      el.innerHTML = `<h2>${NAMES[name]}</h2><p class="worth">${money(acct.cash)}</p><p class="versus">All in cash. The first valuation is on its way.</p>`;
      return el;
    }
    const equity = acct.equity, start = acct.starting_cash, bench = acct.benchmark_value;
    const gain = equity - start, gap = equity - bench;
    const rows = Object.entries(acct.positions).sort((a, b) => b[1].value - a[1].value).map(([sym, p]) => {
      const pnl = p.value - p.cost;
      return `<tr><td class="asset">${esc(sym)}</td><td>${units(p.units)}</td><td>${price(p.price)}</td><td>${money(p.value)}</td><td>${(p.value / equity * 100).toFixed(1)}%</td><td class="${tone(pnl)}">${signed(pnl)}</td></tr>`;
    }).join("");
    const pending = journal.filter((e) => e.portfolio === name).flatMap((e) => e.orders.filter((o) => o.status === "pending"));
    el.innerHTML = `
      <h2>${NAMES[name]}</h2>
      <p class="worth">${money(equity)}</p>
      <p class="since"><span class="${tone(gain)}">${signed(gain)} (${pct(gain / start * 100)})</span> since ${esc(day(started))}</p>
      <p class="versus">$1,000 left in ${BENCH[name]} would be ${money(bench)}, so the account is ${Math.abs(gap) < 0.005 ? "level with it" : `<span class="${tone(gap)}">${gap > 0 ? "ahead" : "behind"} by ${money(Math.abs(gap))}</span>`}.</p>
      <div class="chart">${chart(points)}</div>
      ${points.length > 1 ? `<p class="key"><span>This account</span><span class="b">Benchmark</span><span class="readout" aria-live="polite"></span></p>` : ""}
      <div class="table-wrap"><table>
        <thead><tr><th>Holding</th><th>Units</th><th>Price</th><th>Value</th><th>Share</th><th>Gain</th></tr></thead>
        <tbody>${rows}<tr><td class="asset">Cash</td><td></td><td></td><td>${money(acct.cash)}</td><td>${(acct.cash / equity * 100).toFixed(1)}%</td><td></td></tr></tbody>
      </table></div>
      ${pending.length ? `<div class="waiting"><h3>${WAITING[name]}</h3><ul>${pending.map((o) => `<li>${orderText(o)}</li>`).join("")}</ul></div>` : ""}
      <p class="versus">${VALUED[name]} ${esc(day(acct.last_bar_date))}${name === "crypto" ? " (UTC)" : ""}.</p>`;
    wireChart(el, points);
    return el;
  }

  function orderText(o) {
    const size = o.usd != null ? money(o.usd, 0) + " of " : o.pct === 100 ? "all of " : o.pct + "% of ";
    return `${o.side === "BUY" ? "Buy" : "Sell"} ${size}${esc(o.symbol)}`;
  }

  function waitingText(entry) {
    if (entry.portfolio === "stocks") return "waiting for the next market open";
    const seen = new Date(entry.first_seen_utc);
    const fillAt = new Date(Date.UTC(seen.getUTCFullYear(), seen.getUTCMonth(), seen.getUTCDate() + 1));
    if (fillAt <= new Date()) return "filled at midnight UTC, waiting to be entered in the books";
    const when = fillAt.toLocaleString("en-US", { weekday: "long", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    return `fills ${when} (midnight UTC), when the next day's first price is set`;
  }

  function orderLine(o, entry) {
    let state = waitingText(entry);
    if (o.status === "filled") state = `filled ${day(o.fill.date)} at ${price(o.fill.price)} for ${money(o.fill.usd)}` + (o.fill.realized_pnl != null ? `, ${o.fill.realized_pnl >= 0 ? "a gain" : "a loss"} of ${money(Math.abs(o.fill.realized_pnl))}` : "");
    if (o.status === "rejected") state = "not filled: " + (o.note || "rejected");
    if (o.status === "expired") state = "expired without filling: " + (o.note || "no price was available");
    const note = o.status === "filled" && o.note ? ` (${esc(o.note)})` : "";
    return `<li><strong>${orderText(o)}</strong> <span class="state">${esc(state)}${note}</span>${o.reason ? `<span class="why">${esc(o.reason)}</span>` : ""}</li>`;
  }

  const paras = (text) => esc(text).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");

  function renderJournal(journal, filter) {
    const box = $("#entries");
    const list = journal.filter((e) => e.portfolio && (filter === "all" || e.portfolio === filter)).reverse();
    if (!list.length) {
      box.innerHTML = `<p class="empty">No decisions on the books yet. Stock decisions arrive on weekday mornings before the US market opens. Crypto decisions arrive every afternoon, US Central time.</p>`;
      return;
    }
    box.innerHTML = list.map((e) => `
      <article class="entry">
        <p class="when"><strong>${esc(day(e.first_seen_utc))}</strong>${NAMES[e.portfolio].replace(" account", "")}, ${esc(clock(e.first_seen_utc))}</p>
        <div class="body">
          <h3>${esc(e.summary)}</h3>
          ${e.orders.length ? `<ul class="orders">${e.orders.map((o) => orderLine(o, e)).join("")}</ul>` : `<ul class="orders"><li><strong>No trades</strong></li></ul>`}
          ${e.strategy_change ? `<div class="change"><p><strong>Strategy change.</strong> ${esc(e.strategy_change)}</p></div>` : ""}
          <details><summary>Reasoning and sources</summary>
            ${paras(e.thesis)}
            ${e.risks ? `<p><strong>What could go wrong.</strong> ${esc(e.risks)}</p>` : ""}
            ${e.sources && e.sources.length ? `<ul class="sources">${e.sources.map((s) => `<li><a href="${esc(s)}" rel="noopener nofollow">${esc(s.replace(/^https?:\/\/(www\.)?/, "").slice(0, 90))}</a></li>`).join("")}</ul>` : ""}
          </details>
        </div>
      </article>`).join("");
  }

  function markdown(text) {
    const out = []; let list = null;
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    const flush = () => { if (list) { out.push("<ul>" + list.map((li) => `<li>${inline(li)}</li>`).join("") + "</ul>"); list = null; } };
    for (const block of text.replace(/\r/g, "").split(/\n{2,}/)) {
      for (const line of block.split("\n")) {
        const head = line.match(/^(#{1,3})\s+(.*)/), item = line.match(/^[-*]\s+(.*)/);
        if (head) { flush(); out.push(`<h${head[1].length + 2}>${inline(head[2])}</h${head[1].length + 2}>`); }
        else if (item) { (list = list || []).push(item[1]); }
        else if (list && /^\s+\S/.test(line)) { list[list.length - 1] += " " + line.trim(); }
        else if (line.trim()) { flush(); const last = out.at(-1); if (last && last.startsWith("<p>") && !last.endsWith("</p>")) out[out.length - 1] += " " + inline(line); else out.push("<p>" + inline(line)); }
      }
      flush();
      if (out.length && out.at(-1).startsWith("<p>") && !out.at(-1).endsWith("</p>")) out[out.length - 1] += "</p>";
    }
    return out.join("");
  }

  async function main() {
    const stamp = $("#stamp");
    let state, history, journal, status;
    try {
      [state, history, journal, status] = await Promise.all(["state", "history", "journal", "status"].map((n) => get(`data/${n}.json`)));
    } catch (err) {
      stamp.textContent = "The books could not be loaded. Reload the page to try again.";
      stamp.classList.add("problem");
      return;
    }
    const sheets = $("#sheets");
    for (const name of ["stocks", "crypto"]) sheets.append(sheet(name, state.portfolios[name], history[name] || [], journal));
    if (!status.last_run_utc) stamp.textContent = "The first valuation is on its way.";
    else {
      stamp.textContent = `Books last updated ${day(status.last_run_utc)} at ${clock(status.last_run_utc)}.` + (status.ok ? "" : " Some prices could not be fetched on that run, so figures may be a day behind.");
      stamp.classList.toggle("problem", !status.ok);
    }
    let filter = "all";
    renderJournal(journal, filter);
    document.querySelectorAll(".filters button").forEach((btn) => btn.addEventListener("click", () => {
      filter = btn.dataset.filter;
      document.querySelectorAll(".filters button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      renderJournal(journal, filter);
    }));
    for (const name of ["stocks", "crypto"]) {
      Promise.all([get(`strategy/${name}.md`), get(`lessons/${name}.md`).catch(() => "")])
        .then(([plan, lessons]) => { $(`#plan-${name}`).innerHTML = markdown(plan) + markdown(lessons.replace(/^# /m, "## ")); })
        .catch(() => { $(`#plan-${name}`).innerHTML = '<p class="quiet">The plan could not be loaded.</p>'; });
    }
  }
  main();
})();
