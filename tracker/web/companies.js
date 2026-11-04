// Companies: one row per held / watched company — price and trend, results with estimates, order book, bear / base /
// bull prices, the score or the watch signal — opening into the Screener-style table of actual and estimated
// periods, the company's own KPIs, the valuation memo and the quarterly notes. The same detail is the stock
// page's "Financials & estimates" tab.
import { html, useState, useEffect, useMemo, useRef } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { api, useApi, fmt, Delta, Phase, Empty, toast } from './ui.js';
import { Action } from './priorities.js';
import { CompanyNotes } from './notes.js';

const SCOPES = [['portfolio', 'Portfolio'], ['watchlist', 'Watchlist & exited'], ['all', 'All']];
const SORTS = [['default', 'Held first'], ['score', 'Score (Add → Exit)'], ['upside', 'Base-case upside'], ['growth', 'Next-year revenue growth'],
               ['latest_yoy', 'Latest revenue YoY'], ['book', 'Order book ÷ revenue'], ['day', 'Day change'], ['name', 'Name']];
const SIGNAL_ORDER = { Consider: 0, Improving: 1, Wait: 2 };

// revenue bars: reported periods solid, estimates hatched
function SalesBars({ pts, w = 120, h = 30 }) {
  const vals = (pts || []).filter(p => p.v != null);
  if (!vals.length) return html`<span class="muted">—</span>`;
  const max = Math.max(...vals.map(p => p.v)) || 1;
  const n = pts.length, gap = 2, bw = Math.max(3, Math.min(9, (w - gap * (n - 1)) / n));
  const total = n * bw + (n - 1) * gap;
  return html`<svg class="spark sbars" width=${total} height=${h} viewBox="0 0 ${total} ${h}" role="img"
      aria-label="Revenue by period: reported and estimated">
    ${pts.map((p, i) => {
      if (p.v == null) return null;
      const bh = Math.max(2, p.v / max * (h - 2));
      return html`<rect class=${p.est ? 'est' : ''} x=${i * (bw + gap)} y=${h - bh} width=${bw} height=${bh} rx="1.5">
        <title>${p.label}${p.est ? ' (estimate)' : ''}: ${fmt.cr(p.v)}</title></rect>`;
    })}</svg>`;
}

function MiniBars({ vals, w = 70, h = 22 }) {
  const v = (vals || []).filter(x => x != null);
  if (!v.length) return null;
  const max = Math.max(...v) || 1, n = v.length, gap = 2, bw = Math.min(8, (w - gap * (n - 1)) / n);
  return html`<svg class="spark" width=${n * bw + (n - 1) * gap} height=${h} aria-hidden="true">
    ${v.map((x, i) => { const bh = Math.max(2, x / max * (h - 2)); return html`<rect class=${i === n - 1 ? 'last' : ''} x=${i * (bw + gap)} y=${h - bh} width=${bw} height=${bh} rx="1.5" />`; })}</svg>`;
}

const Up = ({ v }) => v == null ? html`<span class="muted">—</span>` : html`<span class=${v >= 0 ? 'up' : 'down'}>${fmt.pct(v, 0)}</span>`;

function Dma({ r }) {
  if (!r.close || !r.dma40) return null;
  const a40 = r.close >= r.dma40, a200 = r.dma200 ? r.close >= r.dma200 : null;
  return html`<span class="tiny nowrap"><span class=${a40 ? 'up' : 'down'} title="vs DMA40">${a40 ? '▲' : '▼'}40</span>
    ${a200 != null ? html` <span class=${a200 ? 'up' : 'down'} title="vs DMA200">${a200 ? '▲' : '▼'}200</span>` : ''}</span>`;
}

function Cases({ s, compact }) {
  if (!s) return html`<span class="muted small">—</span>`;
  return html`<div class="cases ${compact ? 'compact' : ''}">${['bear', 'base', 'bull'].map(k => {
    const c = s[k] || {};
    return html`<div class=${'case ' + k} title=${c.rationale || ''}><span class="tiny muted">${k}</span>
      <b class="num">${c.price ? fmt.inr(c.price) : '—'}</b><${Up} v=${c.upside_pct} /></div>`;
  })}</div>`;
}

function Signal({ r }) {
  if (r.held) {
    return html`<div><${Action} a=${r.action} small /><div class="tiny muted reason">${r.reasons[0] || ''}</div></div>`;
  }
  const g = r.signal || {};
  return html`<div><span class=${'sig ' + (g.label || '').toLowerCase()}>${g.label || '—'}</span>
    <div class="tiny muted reason">${(g.reasons || []).slice(0, 2).join(' · ')}</div></div>`;
}

function sortKey(r, key) {
  const y1 = (r.years || [])[1] || {};
  return {
    upside: (r.scenarios && r.scenarios.base && r.scenarios.base.upside_pct) ?? null,
    growth: y1.sales_yoy ?? null, latest_yoy: r.latest ? r.latest.sales_yoy : null,
    book: r.ob ? r.ob.book_to_rev : null, day: r.chg_pct, name: r.name, score: r.score,
  }[key];
}

export function CompaniesPage({ params, version }) {
  const [scope, setScope] = useState({ notes: 'all' }[params.scope] || params.scope || 'portfolio');
  const [q, setQ] = useState(params.q || '');
  const [sort, setSort] = useState(SORTS.some(([k]) => k === params.sort) ? params.sort : (params.action ? 'score' : (params.scope || 'portfolio') === 'portfolio' ? 'score' : 'default'));
  const [group, setGroup] = useState(params.group || '');
  const [actions, setActions] = useState(params.action ? params.action.split(',') : []);
  const [phase, setPhase] = useState(params.phase || '');
  const [open, setOpen] = useState(params.open || null);
  const { data, loading, error } = useApi(`/companies?scope=${scope}`, [scope, version]);
  useEffect(() => {
    const p = new URLSearchParams(Object.entries({ scope, q, sort: sort === 'default' ? '' : sort, group, open,
      action: actions.join(','), phase }).filter(([, v]) => v)).toString();
    history.replaceState(null, '', `#/companies${p ? '?' + p : ''}`);
  }, [scope, q, sort, group, open, actions, phase]);
  const actionCounts = useMemo(() => {
    const c = {};
    (data ? data.rows : []).forEach(r => { if (r.action) c[r.action] = (c[r.action] || 0) + 1; });
    return c;
  }, [data]);
  const groups = useMemo(() => [...new Set((data ? data.rows : []).map(r => r.group).filter(Boolean))], [data]);
  const rows = useMemo(() => {
    let r = data ? data.rows : [];
    if (group) r = r.filter(x => x.group === group);
    if (actions.length) r = r.filter(x => actions.includes(x.action));
    if (phase) r = r.filter(x => (x.phase || 'No data') === phase);
    if (q) { const s = q.toLowerCase(); r = r.filter(x => `${x.sid} ${x.name} ${x.sector || ''} ${x.one_line || ''}`.toLowerCase().includes(s)); }
    if (sort === 'default') {
      return [...r].sort((a, b) => (b.held - a.held) || ((SIGNAL_ORDER[(a.signal || {}).label] ?? 3) - (SIGNAL_ORDER[(b.signal || {}).label] ?? 3))
        || a.sid.localeCompare(b.sid));
    }
    const dir = sort === 'name' ? 1 : -1;
    return [...r].sort((a, b) => {
      const x = sortKey(a, sort), y = sortKey(b, sort);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir;
    });
  }, [data, q, sort, group, actions, phase]);
  return html`
    <div class="page-head"><h1>Companies</h1><span class="muted">results and estimates, order book, bear / base / bull — click a row for the quarter-by-quarter model</span></div>
    <div class="filters">
      <div class="seg">${SCOPES.map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => { setScope(k); setOpen(null); }}>${l}</button>`)}</div>
      <input class="input" placeholder="Search company, sector" value=${q} onInput=${e => setQ(e.target.value)} style="width:200px" />
      <label class="small row">Sort <select class="input" value=${sort} onChange=${e => setSort(e.target.value)}>
        ${SORTS.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></label>
      ${groups.length ? html`<div class="row small">${['', ...groups].map(g => html`<button class=${'chip' + (group === g ? ' on' : '')} onClick=${() => setGroup(g)}>${g || 'All groups'}</button>`)}</div>` : null}
      ${Object.keys(actionCounts).length ? html`<div class="row small" aria-label="action">${['Add', 'Hold', 'Trim', 'Exit', 'Watch'].filter(a => actionCounts[a]).map(a => html`
        <button class=${'chip' + (actions.includes(a) ? ' on' : '')} onClick=${() => setActions(actions.includes(a) ? actions.filter(x => x !== a) : [...actions, a])}>${a} ${actionCounts[a]}</button>`)}</div>` : null}
      ${phase ? html`<button class="chip on" onClick=${() => setPhase('')} title="clear the phase filter">${phase} ✕</button>` : null}
      ${data ? html`<span class="small muted">${rows.length} companies</span>` : null}
    </div>
    ${error ? html`<div class="card"><p class="err">${error.message}</p></div>` : null}
    <div class="card" style="padding:0"><div class=${'tablewrap ' + (loading ? 'loading' : '')}>
      <table class="t co-t">
        <thead><tr><th>Company</th><th class="n hide-narrow">Price</th><th class="hide-narrow">Revenue <span class="tiny muted">reported · est.</span></th>
          <th class="hide-narrow">Latest period</th><th class="hide-narrow">Estimates</th><th class="hide-narrow">Order book</th>
          <th class="hide-narrow">Bear · base · bull</th><th class="hide-narrow">${scope === 'portfolio' ? 'Action' : 'Signal'}</th><th></th></tr></thead>
        <tbody>${rows.map(r => {
          const y0 = (r.years || [])[0] || {}, y1 = (r.years || [])[1] || {}, L = r.latest || {};
          return html`
          <tr class="click" onClick=${() => setOpen(open === r.sid ? null : r.sid)} aria-expanded=${open === r.sid}>
            <td><a class="sym" href="#/stock/${encodeURIComponent(r.sid)}" onClick=${e => e.stopPropagation()}>${r.sid}</a>
              ${r.held ? html` <span class="badge">held</span>` : r.exited_on ? html` <span class="badge" title="exited on ${r.exited_on}">exited ${fmt.date(r.exited_on)}</span>` : ''}
              ${r.group ? html` <span class="badge">${r.group}</span>` : ''}
              <span class="cname" title=${r.name}>${r.name}</span><span class="tiny muted">${r.sector && r.sector !== 'Unknown' ? r.sector : ''}</span>
              <div class="only-narrow small" style="margin-top:6px">
                <div class="row"><b class="num">${fmt.price(r.close)}</b> <${Delta} v=${r.chg_pct} /> <${Dma} r=${r} /></div>
                <div class="row" style="margin-top:4px"><${SalesBars} pts=${r.sales} w=${110} h=${24} />
                  <span class="tiny">${L.label || ''} rev <${Up} v=${L.sales_yoy} /> · PAT <${Up} v=${L.pat_yoy} /></span></div>
                <div style="margin-top:4px"><${Cases} s=${r.scenarios} compact /></div>
                <div style="margin-top:4px"><${Signal} r=${r} /></div></div></td>
            <td class="n hide-narrow"><b class="num">${fmt.price(r.close)}</b><div><${Delta} v=${r.chg_pct} /></div><${Dma} r=${r} />
              ${r.phase ? html`<div class="tiny"><${Phase} p=${r.phase} /></div>` : null}</td>
            <td class="hide-narrow"><${SalesBars} pts=${r.sales} /></td>
            <td class="hide-narrow small">${L.label ? html`<span class="est-l"><b>${L.label}</b> ${fmt.cr(L.sales)}</span>
              <div class="tiny">rev <${Up} v=${L.sales_yoy} /> · PAT <${Up} v=${L.pat_yoy} />${L.opm != null ? ` · OPM ${L.opm.toFixed(1)}%` : ''}</div>` : html`<span class="muted">—</span>`}</td>
            <td class="hide-narrow small">${y0.label ? html`<div class="est-l">${y0.label} ${fmt.cr(y0.sales)} <${Up} v=${y0.sales_yoy} /></div>
              <div class="est-l">${y1.label || ''} ${fmt.cr(y1.sales)} <${Up} v=${y1.sales_yoy} /></div>
              <div class="tiny muted">${y1.eps != null ? `EPS ${y1.eps.toFixed(1)}` : ''} · ${r.est_source === 'memo' ? 'memo' : 'rule'}</div>` : html`<span class="muted">—</span>`}</td>
            <td class="hide-narrow small">${r.ob && r.ob.latest ? html`<${MiniBars} vals=${r.ob.series} /><div>${fmt.cr(r.ob.latest)}${r.ob.book_to_rev ? html` <span class="muted">· ${r.ob.book_to_rev}× rev</span>` : ''}</div>` : html`<span class="muted">—</span>`}</td>
            <td class="hide-narrow"><${Cases} s=${r.scenarios} />${r.scen_fy ? html`<div class="tiny muted">on ${r.scen_fy} EPS · ${r.scen_source === 'memo' ? 'memo' : 'rule'}</div>` : ''}</td>
            <td class="hide-narrow"><${Signal} r=${r} /></td>
            <td class="muted">${open === r.sid ? '▴' : '▾'}</td></tr>
          ${open === r.sid ? html`<tr class="expand"><td colspan="9"><${CompanyDetail} sid=${r.sid} version=${version} />
            <div class="small" style="margin-top:8px"><a href="#/stock/${encodeURIComponent(r.sid)}">Open ${r.sid} →</a></div></td></tr>` : null}`;
        })}</tbody>
      </table>
      ${!rows.length && !loading ? html`<${Empty}>No companies here.<//>` : null}
    </div></div>
    <p class="tiny muted">Estimates and price cases are model outputs from the tracker's data (guidance, order book, trend, the valuation memo) — research, not advice.</p>`;
}

// ── one company: the model ───────────────────────────────────────────────
// ₹ Cr throughout (the heading says so): never switches to lakh, keeps the sign
const IN = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }), IN1 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });
const crN = v => (v == null ? '—' : (Math.abs(v) >= 100 ? IN : IN1).format(v));
const kpiN = v => new Intl.NumberFormat('en-IN', { maximumFractionDigits: Math.abs(v) < 10 ? 2 : Math.abs(v) < 100 ? 1 : 0 }).format(v);
const F = { sales: crN, pct: v => (v == null ? '—' : `${v.toFixed(1)}%`), eps: v => (v == null ? '—' : v.toFixed(2)) };
const ROWS = [['sales', 'Revenue', F.sales], ['sales_yoy', 'YoY %', v => (v == null ? '—' : fmt.pct(v, 0))], ['ebitda', 'EBITDA', F.sales],
              ['opm', 'EBITDA margin', F.pct], ['pat', 'PAT', F.sales], ['pat_yoy', 'PAT YoY %', v => (v == null ? '—' : fmt.pct(v, 0))],
              ['eps', 'EPS ₹', F.eps]];

function FinTable({ cols, extra, label }) {
  const wrap = useRef(), placed = useRef(false);
  useEffect(() => {                        // open once at the boundary between reported and estimated
    const el = wrap.current;
    if (!el || placed.current) return;
    const first = el.querySelector('th.est');
    if (first) { el.scrollLeft = Math.max(0, first.offsetLeft - el.clientWidth * 0.55); placed.current = true; }
  });
  return html`<div class="tablewrap fin-wrap" ref=${wrap}><table class="t fin-t">
    <thead><tr><th class="lab">${label}</th>${cols.map(c => html`<th class=${'n ' + (c.est ? 'est' : '')} title=${c.source || ''}>${c.label}${c.est ? 'E' : ''}</th>`)}</tr></thead>
    <tbody>${ROWS.map(([k, name, f]) => html`<tr><td class="lab">${name}</td>${cols.map(c => html`<td class=${'n ' + (c.est ? 'est' : '') + (k.endsWith('_yoy') && c[k] != null ? (c[k] >= 0 ? ' up' : ' down') : '')}>${f(c[k])}</td>`)}</tr>`)}
      ${(extra || []).map(x => html`<tr class="kpi"><td class="lab" title=${x.name}>${x.name}</td>${cols.map(c => {
        const cell = x.cells[c.label];
        return html`<td class=${'n ' + (c.est ? 'est' : '')} title=${cell ? cell.t : ''}>${cell ? (cell.v != null ? `${kpiN(cell.v)}${cell.unit ? ' ' + cell.unit : ''}` : '•') : ''}</td>`;
      })}</tr>`)}</tbody></table></div>`;
}

function Memo({ m }) {
  if (!m) return null;
  const p = m.pipeline || {}, mac = m.macro || {}, v = m.valuation || {}, r = m.risks || {};
  const list = xs => (xs && xs.length ? html`<ul class="clean small">${xs.map(x => html`<li>${typeof x === 'string' ? x : Object.values(x).filter(Boolean).join(' · ')}</li>`)}</ul>` : null);
  return html`<div class="memo">
    ${m.one_line ? html`<p class="vline">${m.one_line}</p>` : null}
    <details open><summary>1 · Management commitments & revenue pipeline</summary>${list(p.summary)}
      ${p.guidance && p.guidance.length ? html`<div class="qlabel">Guidance</div>${list(p.guidance.map(g => `${g.metric} ${g.period || ''}: ${g.value}${g.source ? ` (${g.source})` : ''}`))}` : null}
      ${p.order_book && p.order_book.value_cr ? html`<div class="small">Order book ${fmt.cr(p.order_book.value_cr)}${p.order_book.as_of ? ` as of ${fmt.date(p.order_book.as_of, true)}` : ''}${p.order_book.execution_months ? ` · executes over ${p.order_book.execution_months} months` : ''}${p.order_book.note ? ` — ${p.order_book.note}` : ''}</div>` : null}
      ${p.capex && p.capex.length ? html`<div class="qlabel">Capex & capacity</div>${list(p.capex.map(c => `${c.what}${c.amount_cr ? ` · ${fmt.cr(c.amount_cr)}` : ''}${c.capacity ? ` · ${c.capacity}` : ''}${c.revenue_from ? ` · revenue from ${c.revenue_from}` : ''}${c.peak_revenue_cr ? ` · peak ${fmt.cr(c.peak_revenue_cr)}` : ''}`))}` : null}
      ${p.deals && p.deals.length ? html`<div class="qlabel">Deals & partnerships</div>${list(p.deals)}` : null}</details>
    <details><summary>2 · Macro & regulatory transmission</summary>${list(mac.summary)}
      ${[['Rates & debt', mac.rates], ['Inflation & pass-through', mac.inflation], ['Currency', mac.currency]].filter(([, x]) => x).map(([k, x]) => html`<div class="small"><b>${k}:</b> ${x}</div>`)}
      ${mac.policy && mac.policy.length ? html`<div class="qlabel">Policy</div>${list(mac.policy)}` : null}</details>
    <details><summary>3 · Forward valuation matrix</summary>
      ${v.multiple_rationale ? html`<div class="small"><b>Multiple:</b> ${v.multiple_rationale}${v.roe_pct ? ` (ROE ${v.roe_pct}%)` : ''}</div>` : null}
      ${list(m.assumptions)}
      ${v.entry_slippage ? html`<div class="small"><b>Entry:</b> ${v.entry_slippage}</div>` : null}</details>
    <details open><summary>5 · Risk dashboard & exit triggers</summary>
      ${r.red_flags && r.red_flags.length ? html`<div class="qlabel">Red flags that would break the model</div>${list(r.red_flags)}` : null}
      ${r.expensive_above && (r.expensive_above.price || r.expensive_above.multiple) ? html`<div class="small"><b>Expensive above:</b> ${r.expensive_above.price ? fmt.price(r.expensive_above.price) : ''}${r.expensive_above.multiple ? ` · ${r.expensive_above.multiple}× P/E` : ''}${r.expensive_above.why ? ` — ${r.expensive_above.why}` : ''}</div>` : null}
      ${r.exit_triggers && r.exit_triggers.length ? html`<div class="qlabel">Exit triggers</div>${list(r.exit_triggers)}` : null}</details>
  </div>`;
}

// inside the Companies table the detail spans the visible width and stays put while the table scrolls sideways
function useFitToTable() {
  const ref = useRef();
  useEffect(() => {
    const el = ref.current, wrap = el && el.closest('.tablewrap');
    if (!wrap) return;
    const fit = () => { el.style.width = `${wrap.clientWidth - 24}px`; };
    el.style.position = 'sticky';
    el.style.left = '12px';
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);
  return ref;
}

export function CompanyDetail({ sid, version }) {
  const box = useFitToTable();          // one stable element, so the fit survives loading → loaded
  return html`<div class="co-detail" ref=${box}><${DetailBody} sid=${sid} version=${version} /></div>`;
}

function DetailBody({ sid, version }) {
  const [bump, setBump] = useState(0);
  const { data, loading, error } = useApi(`/company/${encodeURIComponent(sid)}/model`, [sid, version, bump]);
  const [busy, setBusy] = useState(false);
  if (error) return html`<p class="err small">${error.message}</p>`;
  if (!data) return html`<div class="muted small">${loading ? 'Loading the model…' : ''}</div>`;
  const e = data.estimates || {}, s = data.scenarios || {};
  const acts = data.periods.filter(p => p.sales != null).slice(-8);
  const cols = [...acts, ...(e.periods || [])];
  const years = [...data.annual.filter(a => a.sales != null).slice(-4), ...(e.years || [])];
  const kpis = (data.kpis && data.kpis.metrics) || [];
  const ob = data.order_book || {};
  const obRow = Object.keys(ob).length ? [{ name: 'Order book ₹ Cr', cells: Object.fromEntries(Object.entries(ob).map(([k, v]) => [k, { v: Math.round(v), unit: '', t: `₹${v} Cr` }])) }] : [];
  async function writeMemo() {
    setBusy(true);
    try {
      await api(`/company/${encodeURIComponent(sid)}/valuation`, { method: 'POST' });
      toast(`Valuation memo for ${sid} written`);
      setBump(b => b + 1);
    } catch (err) { toast(`Memo not written: ${err.message}`); }
    setBusy(false);
  }
  return html`<div>
    <div class="row" style="flex-wrap:wrap;gap:12px;margin-bottom:8px">
      <span class="small"><b>Estimates:</b> ${e.source === 'memo' ? `valuation memo of ${fmt.date(e.memo_at, true)}` : e.available ? 'rule-based (guidance → order book → trend)' : (e.why || 'not enough history')}</span>
      ${data.stale ? html`<span class="tiny muted">memo: ${data.stale}</span>` : null}
      <span class="spacer"></span>
      <button class="btn small" disabled=${busy} onClick=${writeMemo}>${busy ? 'Writing memo… (about a minute)' : data.memo ? 'Rewrite valuation memo' : 'Write valuation memo'}</button>
    </div>
    ${s.available ? html`<div class="scen">
      ${['bear', 'base', 'bull'].map(k => {
        const c = s[k] || {};
        return html`<div class=${'scard ' + k}><div class="tiny muted">${k} · ${s.fy || ''}</div>
          <div class="sprice num">${c.price ? fmt.price(Math.round(c.price)) : '—'} <${Up} v=${c.upside_pct} /></div>
          <div class="tiny">EPS ${c.eps != null ? c.eps.toFixed(2) : '—'} × ${c.pe != null ? c.pe + '×' : '—'}${c.revenue ? ` · revenue ${fmt.cr(c.revenue)}` : ''}${c.margin != null ? ` · margin ${c.margin}%` : ''}</div>
          ${c.rationale ? html`<div class="tiny muted">${c.rationale}</div>` : null}</div>`;
      })}
      ${s.three_year && s.three_year.price ? html`<div class="scard"><div class="tiny muted">3-year · ${s.three_year.fy || ''}</div><div class="sprice num">${fmt.price(Math.round(s.three_year.price))} <${Up} v=${s.three_year.upside_pct} /></div><div class="tiny">EPS ${s.three_year.eps} × ${s.three_year.pe}×${s.three_year.market_cap_cr ? ` · mcap ${fmt.cr(s.three_year.market_cap_cr)}` : ''}</div></div>` : null}
      ${s.five_year && s.five_year.price ? html`<div class="scard"><div class="tiny muted">5-year · ${s.five_year.fy || ''}</div><div class="sprice num">${fmt.price(Math.round(s.five_year.price))} <${Up} v=${s.five_year.upside_pct} /></div><div class="tiny">EPS ${s.five_year.eps} × ${s.five_year.pe}×${s.five_year.market_cap_cr ? ` · mcap ${fmt.cr(s.five_year.market_cap_cr)}` : ''}</div></div>` : null}
    </div>
    <div class="tiny muted" style="margin:4px 0 10px">${s.source === 'memo' ? `Multiples from the memo: ${(s.pe_basis && s.pe_basis.base) || ''}` : `P/E: ${(s.pe_basis && s.pe_basis.base) || ''}${s.pe_basis && s.pe_basis.own && s.pe_basis.own.median ? ` (own ${s.pe_basis.own.p25}–${s.pe_basis.own.p75}, median ${s.pe_basis.own.median})` : ''}${s.pe_basis && s.pe_basis.sector && s.pe_basis.sector.median ? ` · sector median ${s.pe_basis.sector.median} (${s.pe_basis.sector.n} peers)` : ''}`}
      ${s.expensive_above && s.expensive_above.price ? html` · expensive above ${fmt.price(s.expensive_above.price)}` : ''}</div>` : html`<p class="small muted">No price cases: ${data.per_year && !(e.available) ? 'not enough reported history yet' : 'no EBITDA / EPS line (banks and NBFCs are valued on book value)'}.</p>`}
    <div class="qlabel">${data.half ? 'Half-years' : 'Quarters'} <span class="tiny muted">₹ Cr · shaded = estimate · hover a heading for its source</span></div>
    <${FinTable} cols=${cols} extra=${[...obRow, ...kpis]} label=${data.half ? 'Half-year' : 'Quarter'} />
    <div class="qlabel" style="margin-top:10px">Fiscal years</div>
    <${FinTable} cols=${years} label="Year" />
    ${e.notes && e.notes.length ? html`<ul class="clean tiny muted" style="margin-top:6px">${e.notes.map(n => html`<li>${n}</li>`)}</ul>` : null}
    ${data.memo ? html`<div class="qlabel" style="margin-top:12px">Valuation memo <span class="tiny muted">${fmt.date(data.memo.created_at, true)} · your analyst prompt over the tracker's data</span></div><${Memo} m=${data.memo} />` : null}
    <details style="margin-top:10px"><summary class="small">Quarterly notes & latest view</summary><${CompanyNotes} sid=${sid} compact version=${version} /></details>
  </div>`;
}
