// Priorities (forward estimates + Add/Hold/Trim/Exit/Watch score) and the stock page's score tab.
import { html, useState, useEffect, useMemo } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { useApi, fmt, Empty, sortRows, Phase } from './ui.js';
import { AiBanner, Trust } from './orders.js';

const ACTIONS = ['Add', 'Hold', 'Trim', 'Exit', 'Watch'];
const GLYPH = { Add: '▲', Hold: '●', Trim: '▼', Exit: '✕', Watch: '○' };
const BASIS = { guidance: 'Guidance', order_book: 'Order book', trend: 'Trend' };
const COMP = {
  order_book_growth: 'Order-book growth', book_to_revenue: 'Book / revenue', guidance_delivery: 'Guidance delivered',
  margin_trend: 'Margin trend', eps_vs_pe: 'EPS growth vs P/E', technical: 'Technical phase', promoter_flow: 'Promoter holding',
  institution_flow: 'FII + DII flows', anchor_flow: 'Anchor / tracked investors', pledge: 'Pledges', valuation: 'Valuation vs fair value',
};

export const Action = ({ a, small }) => html`<span class="act ${(a || '').toLowerCase()} ${small ? 'sm' : ''}">${GLYPH[a] || ''} ${a || '—'}</span>`;

function Pct({ v }) {
  if (v == null) return html`<span class="muted">—</span>`;
  return html`<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''} num">${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(Math.abs(v) >= 100 ? 0 : 1)}%</span>`;
}

const COLS = [['name', 'Stock', false], ['action', 'Action', false], ['score', 'Score', true], ['rev_fwd', 'Expected revenue', true],
  ['rev_add', '+ ₹ Cr', true], ['rev_g', 'Growth', true], ['basis', 'Basis', false], ['eps_fwd', 'Fwd EPS', true],
  ['pe_fwd', 'Fwd P/E', true], ['reasons', 'Why', false]];

export function PrioritiesPage({ params, version }) {
  const [scope, setScope] = useState(params.scope || 'all');
  const [acts, setActs] = useState(params.action ? params.action.split(',') : []);
  const [q, setQ] = useState(params.q || '');
  const [sort, setSort] = useState({ key: params.sort || 'rev_add', dir: 'desc' });
  const { data, loading } = useApi(`/scores?scope=${scope}`, [scope, version]);
  useEffect(() => {
    const p = new URLSearchParams(Object.entries({ scope, action: acts.join(','), q, sort: sort.key }).filter(([, v]) => v)).toString();
    history.replaceState(null, '', `#/priorities${p ? '?' + p : ''}`);
  }, [scope, acts, q, sort]);
  const rows = useMemo(() => {
    let r = (data ? data.rows : []).map(x => ({ ...x, rev_fwd: x.forward.revenue_fwd, rev_add: x.forward.revenue_add_cr,
      rev_g: x.forward.revenue_growth_pct, basis: x.forward.basis, eps_fwd: x.forward.eps_fwd, pe_fwd: x.forward.pe_fwd }));
    if (acts.length) r = r.filter(x => acts.includes(x.action));
    if (q) { const s = q.toLowerCase(); r = r.filter(x => `${x.sid} ${x.name} ${x.sector || ''}`.toLowerCase().includes(s)); }
    return sortRows(r, sort.key === 'reasons' ? 'score' : sort.key, sort.dir);
  }, [data, acts, q, sort]);
  const counts = useMemo(() => Object.fromEntries(ACTIONS.map(a => [a, (data ? data.rows : []).filter(r => r.action === a).length])), [data]);
  const toggle = a => setActs(xs => xs.includes(a) ? xs.filter(x => x !== a) : [...xs, a]);
  const th = ([key, label, num]) => html`<th class="sortable ${num ? 'n' : ''}" onClick=${() => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}>
    ${label}${sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}</th>`;
  return html`
    <div class="page-head"><h1>Priorities</h1><span class="muted">expected growth in ₹, and a rule-based Add / Hold / Trim / Exit / Watch · decision support, not an order</span></div>
    <div class="filters">
      <div class="seg">${[['all', 'All tracked'], ['portfolio', 'Portfolio'], ['watchlist', 'Watchlist']].map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      ${ACTIONS.map(a => html`<button class="chip ${acts.includes(a) ? 'on' : ''}" onClick=${() => toggle(a)}><${Action} a=${a} small /> <b class="num">${counts[a] || 0}</b></button>`)}
      <input class="input" placeholder="Search stock or sector" value=${q} onInput=${e => setQ(e.target.value)} style="width:190px" />
    </div>
    <div class="card only-narrow ${loading ? 'loading' : ''}" style="padding:4px 12px">
      ${rows.slice(0, 80).map(r => html`<a class="pcard" href="#/stock/${encodeURIComponent(r.sid)}?tab=score">
        <div class="row"><b>${r.nse || r.sid}</b>${r.held ? html`<span class="badge">held</span>` : ''}<span class="spacer"></span>
          <${Action} a=${r.action} small /><span class="num small muted">${r.score > 0 ? '+' : ''}${r.score.toFixed(1)}</span></div>
        <div class="row small" style="margin-top:2px"><span class="ink2 clip" style="max-width:180px">${r.name}</span><span class="spacer"></span>
          <span class="num">${fmt.cr(r.rev_fwd)}</span> <${Pct} v=${r.rev_g} /></div>
        ${r.top_reasons[0] ? html`<div class="tiny muted" style="margin-top:2px"><span class=${r.top_reasons[0].w > 0 ? 'up' : 'down'}>${r.top_reasons[0].w > 0 ? '▲' : '▼'}</span> ${r.top_reasons[0].text}</div>` : null}
      </a>`)}
      ${rows.length > 80 ? html`<div class="small muted" style="padding:8px 0">Showing 80 of ${rows.length} — filter or search to narrow.</div>` : null}
      ${!rows.length && !loading ? html`<${Empty}>No stocks match.<//>` : null}
    </div>
    <div class="card only-wide" style="padding:0">
      <div class="tablewrap ${loading ? 'loading' : ''}" style="max-height:calc(100vh - 200px)">
        <table class="t">
          <thead><tr>${COLS.map(th)}</tr></thead>
          <tbody>${rows.map(r => html`<tr>
            <td><a class="sym" href="#/stock/${encodeURIComponent(r.sid)}?tab=score">${r.nse || r.sid}</a>${r.held ? html` <span class="badge">held</span>` : ''}
              <span class="cname" title=${r.name}>${r.name}</span></td>
            <td><${Action} a=${r.action} />${r.note ? html`<div class="tiny muted" title=${`your note: ${r.note.title}`}>note: ${r.note.stance}</div>`
              : r.components_with_data < 4 ? html`<div class="tiny muted">thin data</div>` : ''}</td>
            <td class="n"><span class="num" title=${`${r.components_with_data} of ${r.components_total} components have data`}>${r.score > 0 ? '+' : ''}${r.score.toFixed(1)}</span></td>
            <td class="n">${fmt.cr(r.rev_fwd)}<div class="tiny muted">from ${fmt.cr(r.forward.revenue_ttm)}</div></td>
            <td class="n">${r.rev_add != null ? html`<span class=${r.rev_add >= 0 ? 'up' : 'down'}>${r.rev_add >= 0 ? '+' : '−'}${fmt.cr(Math.abs(r.rev_add))}</span>` : '—'}</td>
            <td class="n"><${Pct} v=${r.rev_g} /></td>
            <td>${r.basis ? html`<span class="badge" title=${(r.forward.options.find(o => o.basis === r.basis) || {}).note}>${BASIS[r.basis]}</span>` : html`<span class="muted">—</span>`}</td>
            <td class="n">${r.eps_fwd != null ? fmt.price(r.eps_fwd) : '—'}${r.forward.eps_ttm != null ? html`<div class="tiny muted">TTM ${fmt.price(r.forward.eps_ttm)}</div>` : ''}</td>
            <td class="n">${r.pe_fwd != null ? r.pe_fwd.toFixed(1) : '—'}</td>
            <td class="small"><ul class="reasons">${r.top_reasons.map(t => html`<li><span class=${t.w > 0 ? 'up' : 'down'}>${t.w > 0 ? '▲' : '▼'}</span> ${t.text}</li>`)}</ul></td>
          </tr>`)}</tbody>
        </table>
        ${!rows.length && !loading ? html`<${Empty}>No stocks match.<//>` : null}
      </div>
    </div>
    <div class="small muted" style="margin-top:8px">Expected revenue = next twelve months: management guidance when there is one, else the order book over its
      execution period (24 months when not stated), else the trailing growth rate (capped at −30…+60%). Forward EPS scales TTM EPS with revenue
      (at the guided margin if given). Weights and thresholds: <code>score:</code> in tracker.yaml. A note with add / reduce / exit overrides the action.</div>`;
}

export function ScoreTab({ sid }) {
  const { data } = useApi(`/score/${encodeURIComponent(sid)}`, [sid]);
  if (!data) return html`<div class="muted">Loading…</div>`;
  const f = data.forward;
  const missing = data.components.filter(c => c.points == null);
  return html`<div class="stack">
    <div class="grid g-2">
      <div class="card"><h2>Suggestion <span class="muted">rule-based · decision support, not an order</span></h2>
        <div class="row" style="gap:14px;align-items:baseline">
          <span style="font-size:24px;font-weight:650"><${Action} a=${data.action} /></span>
          <span class="num" style="font-size:18px">${data.score > 0 ? '+' : ''}${data.score.toFixed(2)}</span>
          <span class="muted small">${data.components_with_data} of ${data.components_total} components have data</span>
        </div>
        ${data.note ? html`<div class="small" style="margin-top:6px">Your note (${fmt.date(data.note.d, true)}, “${data.note.title}”) says <b>${data.note.stance}</b> — it overrides the computed <${Action} a=${data.computed_action} small />.</div>` : null}
        <div class="small muted" style="margin-top:6px">${data.held
          ? `Held: Add ≥ ${data.weights.add_at}, Trim ≤ ${data.weights.trim_at}, Exit ≤ ${data.weights.exit_at}.`
          : `Not held: Add (start a position) ≥ ${data.weights.watch_add_at}, otherwise Watch.`}</div>
      </div>
      <div class="card"><h2>Expected revenue <span class="muted">next twelve months · ₹ Cr</span></h2>
        <div class="row" style="gap:14px;align-items:baseline;flex-wrap:wrap">
          <span style="font-size:22px;font-weight:600">${fmt.cr(f.revenue_fwd)}</span><${Pct} v=${f.revenue_growth_pct} />
          <span class="muted small">from ${fmt.cr(f.revenue_ttm)} (${f.revenue_label})</span>
        </div>
        <table class="t small" style="margin-top:8px"><thead><tr><th>Basis</th><th class="n">Estimate</th><th>How</th></tr></thead>
          <tbody>${f.options.map(o => html`<tr class=${o.basis === f.basis ? '' : 'dim'}><td>${BASIS[o.basis]}${o.basis === f.basis ? ' ✓' : ''}</td>
            <td class="n">${fmt.cr(o.value)}</td><td>${o.note}</td></tr>`)}</tbody></table>
        ${!f.options.length ? html`<div class="muted small">No guidance, order book or revenue trend to project from.</div>` : null}
        <dl class="kv" style="margin-top:10px">
          <dt>EPS (TTM → forward)</dt><dd>${f.eps_ttm != null ? fmt.price(f.eps_ttm) : '—'} → ${f.eps_fwd != null ? fmt.price(f.eps_fwd) : '—'} <span class="muted small">${f.eps_fwd != null ? f.eps_note : ''}</span></dd>
          <dt>P/E (TTM → forward)</dt><dd>${f.pe_ttm != null ? f.pe_ttm.toFixed(1) : '—'} → ${f.pe_fwd != null ? f.pe_fwd.toFixed(1) : '—'}</dd>
          <dt>EPS growth (TTM)</dt><dd><${Pct} v=${f.eps_growth_pct} /></dd>
          <dt>Operating margin</dt><dd>${f.opm_prev != null ? f.opm_prev + '%' : '—'} → ${f.opm_ttm != null ? f.opm_ttm + '%' : '—'} <span class="muted small">${f.fin_source || ''}</span></dd>
        </dl>
      </div>
    </div>
    <div class="card"><h2>Why <span class="muted">each component −2…+2 × weight</span></h2>
      <div class="tablewrap"><table class="t small"><thead><tr><th>Component</th><th class="n hide-narrow">Points</th><th class="n hide-narrow">Weight</th><th class="n">Score</th><th>Reason</th></tr></thead>
        <tbody>${data.components.filter(c => c.points != null).sort((a, b) => Math.abs(b.weighted) - Math.abs(a.weighted)).map(c => html`<tr>
          <td>${COMP[c.key] || c.key}</td><td class="n hide-narrow">${c.points > 0 ? '+' : ''}${c.points}</td><td class="n hide-narrow">${c.weight}</td>
          <td class="n"><span class=${c.weighted > 0 ? 'up' : c.weighted < 0 ? 'down' : 'muted'}>${c.weighted > 0 ? '▲ +' : c.weighted < 0 ? '▼ ' : ''}${c.weighted}</span></td>
          <td>${c.reason}</td></tr>`)}</tbody></table></div>
      ${missing.length ? html`<div class="small muted" style="margin-top:6px">No data: ${missing.map(c => `${COMP[c.key] || c.key} (${c.reason.toLowerCase()})`).join(' · ')}</div>` : null}
    </div>
    <div class="card"><h2>Guidance found in filings</h2>
      ${data.guidance.length ? html`<${AiBanner} />
        <div class="tablewrap"><table class="t small"><thead><tr><th>Metric</th><th>Period</th><th class="n">Range</th><th>Said</th><th>Check</th><th>Source</th></tr></thead>
        <tbody>${data.guidance.map(g => html`<tr><td>${(g.metric || '').replace(/_/g, ' ')}</td><td>${g.period || '—'}</td>
          <td class="n nowrap">${g.low === g.high ? g.low : `${g.low ?? '?'}–${g.high ?? '?'}`}${g.unit === 'pct' ? '%' : g.unit === 'cr' ? ' Cr' : ''}</td>
          <td><div class="small">“${g.quote}”</div><div class="tiny muted">${fmt.date(g.stated_on, true)}</div></td>
          <td><${Trust} v=${g.verified} /></td>
          <td class="nowrap">${g.link ? html`<a href=${g.link} target="_blank" rel="noopener">PDF${g.page ? ` p.${g.page}` : ''} ↗</a>` : '—'}</td></tr>`)}</tbody></table></div>`
        : html`<${Empty}>No verified guidance extracted yet for this stock.<//>`}
    </div>
  </div>`;
}
