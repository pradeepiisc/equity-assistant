// Stock detail page.
import { html, useState, useMemo, useEffect } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { api, post, useApi, fmt, Phase, Delta, Tile, EventList, Empty, Markdown, toast, KIND_LABEL } from './ui.js';
import { PriceChart, BarMini, LineMini } from './charts.js';
import { StockOrderBook } from './orders.js';
import { ScoreTab, Action } from './priorities.js';
import { CompanyNotes } from './notes.js';
import { CompanyDetail } from './companies.js';

const RANGES = [['3M', 63], ['6M', 126], ['1Y', 250], ['All', 9999]];
const TABS = [['fin', 'Financials & estimates'], ['timeline', 'Timeline'], ['score', 'Score'], ['orders', 'Orders'], ['outlook', 'Concall & outlook'],
              ['holding', 'Shareholding & deals'], ['notes', 'Notes'], ['research', 'Reports']];
const qlabel = q => { const [y, m] = q.split('-'); return `${['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m]} ${y.slice(2)}`; };

export function StockPage({ sid, version, tab: initialTab }) {
  const { data, error, loading, reload } = useApi(`/stock/${encodeURIComponent(sid)}`, [sid, version]);
  const chart = useApi(`/stock/${encodeURIComponent(sid)}/chart?days=400`, [sid]);
  const score = useApi(`/score/${encodeURIComponent(sid)}`, [sid, version]);
  const [tab, setTab] = useState(initialTab || 'fin');

  if (error) return html`<div class="card"><p class="err">Couldn't load ${sid}: ${error.message}</p><a href="#/companies">← Companies</a></div>`;
  if (!data) return html`<div class="muted">Loading ${sid}…</div>`;
  const { stock: s, technicals: t, holding: h, research: r } = data;

  async function addWatch() {
    const reason = prompt(`Why watch ${s.sid}? (saved to watchlist.yaml)`, '');
    if (reason === null) return;
    const res = await post('/watchlist', { sid: s.sid, reason });
    toast(res.added ? `${s.sid} added to watchlist.yaml` : `${s.sid} is already on the watchlist`);
    reload();
  }

  return html`
    <div class=${loading ? 'loading' : ''}>
      <div class="s-head">
        <div>
          <div class="row small muted" style="margin-bottom:4px">
            <a href="#/companies">Companies</a><span>/</span><span>${s.sector || 'Sector n/a'}</span>
          </div>
          <h1>${s.sid} <span class="ink2" style="font-weight:400;font-size:18px">${s.name}</span></h1>
          <div class="row wrap" style="margin-top:6px">
            ${s.in_portfolio ? html`<span class="badge">Portfolio</span>` : null}
            ${s.in_watchlist ? html`<span class="badge">Watchlist</span>` : null}
            ${s.in_notes ? html`<span class="badge">In notes</span>` : null}
            ${s.is_sme ? html`<span class="badge">SME</span>` : null}
            <span class="badge">${s.price_exch}${s.nse_series ? ' · ' + s.nse_series : ''}</span>
            <a class="small" href=${data.links.screener} target="_blank" rel="noopener">Screener ↗</a>
            ${data.links.nse && html`<a class="small" href=${data.links.nse} target="_blank" rel="noopener">NSE ↗</a>`}
            ${data.links.bse && html`<a class="small" href=${data.links.bse} target="_blank" rel="noopener">BSE ↗</a>`}
          </div>
        </div>
        <div class="spacer"></div>
        ${t && html`<div style="text-align:right">
          <div class="price num">${fmt.price(t.close)}</div>
          <div class="row" style="justify-content:flex-end"><${Delta} v=${t.chg_pct} /><span class="muted small">on ${fmt.date(t.d)}</span></div>
        </div>`}
        ${!s.in_watchlist && !s.in_portfolio ? html`<button class="btn" onClick=${addWatch}>+ Watchlist</button>` : null}
      </div>

      ${data.profile && html`<${About} p=${data.profile} />`}

      <div class="tiles">
        <${Tile} label="Trend phase" value=${html`<${Phase} p=${t && t.phase} />`}
                 sub=${t && t.phase_since ? `since ${fmt.date(t.phase_since)}` : ''} />
        <${Tile} label="vs DMA40" value=${t ? fmt.pct(t.vs40) : '—'} sub=${t ? fmt.price(t.dma40) : ''} />
        <${Tile} label="vs DMA200" value=${t ? fmt.pct(t.vs200) : '—'}
                 sub=${t && t.dma200 ? fmt.price(t.dma200) : (t ? `needs 200 sessions (has ${t.bars})` : '')} />
        <${Tile} label="52-week range" value=${t ? fmt.pct(t.from_high) : '—'}
                 sub=${t ? `from high · ${fmt.price(t.lo52)}–${fmt.price(t.hi52)}` : ''} />
        ${h ? html`<${Tile} label="Holding" value=${fmt.compact(h.value)}
                     sub=${`${fmt.num(h.quantity)} sh @ ${fmt.price(h.avg_price)} · ${fmt.pct(h.pnl_pct)} · ${h.accounts.join(', ')}`} />`
            : html`<${Tile} label="Holding" value="—" sub="Not held" />`}
        ${r.valuation && r.valuation.fair_low ? html`<${Tile} label="Fair value (research)"
              value=${`${fmt.price(r.valuation.fair_low).replace('.00', '')}–${fmt.price(r.valuation.fair_high).replace('.00', '')}`}
              sub=${`${r.valuation.entry_signal || r.valuation.vs_fair || ''} · ${fmt.date(r.valuation.as_of, true)}`} />` : null}
        ${score.data ? html`<${Tile} label="Suggestion (rule-based)" value=${html`<${Action} a=${score.data.action} />`}
              sub=${html`<a href="javascript:void 0" onClick=${() => setTab('score')}>score ${score.data.score > 0 ? '+' : ''}${score.data.score.toFixed(1)} · why</a>`} />` : null}
        ${r.master && r.master.rating ? html`<${Tile} label="Research rating" value=${r.master.rating}
              sub=${`master report · ${fmt.date(r.master.as_of, true)}`} />` : null}
      </div>

      <div class="card" style="margin-top:16px">
        <${ChartBlock} chart=${chart.data} />
      </div>

      <div class="tabs" role="tablist">
        ${TABS.map(([k, label]) => html`<button role="tab" aria-selected=${tab === k} class=${tab === k ? 'on' : ''} onClick=${() => setTab(k)}>${label}</button>`)}
      </div>
      ${tab === 'fin' && html`<div class="card"><${CompanyDetail} sid=${s.sid} version=${version} /></div>`}
      ${tab === 'timeline' && html`<${Timeline} events=${data.events} />`}
      ${tab === 'outlook' && html`<div class="stack"><div class="card"><h2>Company notes <span class="muted">last four quarters → latest view</span></h2>
        <${CompanyNotes} sid=${s.sid} version=${version} /></div><${Outlook} r=${r} /></div>`}
      ${tab === 'score' && html`<${ScoreTab} sid=${s.sid} />`}
      ${tab === 'orders' && html`<${Orders} data=${data} />`}
      ${tab === 'holding' && html`<${Holding} data=${data} />`}
      ${tab === 'notes' && html`<${NotesTab} mentions=${data.mentions} sid=${s.sid} />`}
      ${tab === 'research' && html`<${Reports} r=${r} />`}
    </div>`;
}

// what the company does and which markets its revenue depends on (profiles.py, written by the local model)
function About({ p }) {
  const markets = asList(p.end_markets).filter(m => m && m.market);
  return html`<div class="card about-card" style="margin-top:12px">
    <h2>About the company <span class="muted">${p.sector || ''}${p.business_type ? ' · ' + p.business_type : ''}</span></h2>
    <p style="margin:6px 0 10px">${p.description}</p>
    ${markets.length ? html`<div class="row wrap" style="gap:6px;margin-bottom:8px">
      <span class="muted small">Markets</span>
      ${markets.map(m => html`<span class="chip" title=${m.how || ''}>${m.market}<span class="muted"> · ${m.link === 'indirect' ? 'indirect' : 'direct'}</span></span>`)}
    </div>` : null}
    ${asList(p.exposures).length ? html`<div class="row wrap" style="gap:6px;margin-bottom:6px">
      <span class="muted small">Exposures</span>${asList(p.exposures).map(x => html`<span class="chip">${x}</span>`)}
    </div>` : null}
    ${asList(p.sensitive_to).length ? html`<div class="small muted">Sensitive to: ${asList(p.sensitive_to).join(' · ')}</div>` : null}
    <div class="small muted" style="margin-top:6px">Profile written ${fmt.date(p.updated_at, true)} from Screener, concall notes and research reports</div>
  </div>`;
}

function ChartBlock({ chart }) {
  const [range, setRange] = useState(250);
  const [table, setTable] = useState(false);
  const bars = useMemo(() => (chart && chart.bars ? chart.bars.slice(-range) : []), [chart, range]);
  if (!chart) return html`<div class="chart muted">Loading chart…</div>`;
  if (!chart.bars.length) return html`<${Empty}>No price history yet — run a price sync.<//>`;
  return html`
    <div class="row" style="margin-bottom:8px">
      <div class="card-title" style="margin:0">Price & moving averages <span class="muted">daily close, split/bonus adjusted</span></div>
      <div class="spacer"></div>
      <div class="seg">${RANGES.map(([l, n]) => html`<button class=${range === n ? 'on' : ''} onClick=${() => setRange(n)}>${l}</button>`)}</div>
      <button class="btn" onClick=${() => setTable(!table)}>${table ? 'Chart' : 'Table'}</button>
    </div>
    ${table ? html`<${BarsTable} bars=${bars} />` : html`<${PriceChart} bars=${bars} markers=${chart.markers} />`}`;
}

function BarsTable({ bars }) {
  const rows = bars.slice(-60).reverse();
  return html`<div class="tablewrap" style="max-height:360px"><table class="t">
    <thead><tr><th>Date</th><th class="n">Close</th><th class="n">DMA40</th><th class="n">DMA100</th><th class="n">DMA200</th><th>Phase</th><th class="n">Volume</th></tr></thead>
    <tbody>${rows.map(b => html`<tr><td>${fmt.date(b.t, true)}</td><td class="n">${fmt.price(b.close)}</td>
      <td class="n">${fmt.price(b.dma40)}</td><td class="n">${fmt.price(b.dma100)}</td><td class="n">${fmt.price(b.dma200)}</td>
      <td><${Phase} p=${b.phase} /></td><td class="n">${fmt.num(b.volume)}</td></tr>`)}</tbody></table></div>`;
}

function Timeline({ events }) {
  const kinds = useMemo(() => [...new Set(events.map(e => e.kind))], [events]);
  const [on, setOn] = useState(null);
  const [showRoutine, setShowRoutine] = useState(false);
  const rows = events.filter(e => (!on || e.kind === on) && (showRoutine || e.severity !== 'info'));
  return html`<div class="card">
    <div class="filters">
      <button class="chip ${!on ? 'on' : ''}" onClick=${() => setOn(null)}>All</button>
      ${kinds.map(k => html`<button class="chip ${on === k ? 'on' : ''}" onClick=${() => setOn(k)}>${KIND_LABEL[k] || k}</button>`)}
      <div class="spacer"></div>
      <label class="small row"><input type="checkbox" checked=${showRoutine} onChange=${e => setShowRoutine(e.target.checked)} /> show routine filings</label>
    </div>
    <${EventList} rows=${rows} showStock=${false} empty="No events for this stock yet." />
  </div>`;
}

const asList = v => (Array.isArray(v) ? v : v ? [v] : []);
const txt = v => (v == null ? '' : typeof v === 'string' ? v : typeof v === 'object'
  ? Object.values(v).filter(x => typeof x === 'string' || typeof x === 'number').join(' — ') : String(v));

function Outlook({ r }) {
  if (!r.available) return html`<div class="card"><${Empty}>No research folder for this stock yet. Onboard it from the legacy workflows (Onboard → onboard_company) to generate concall analysis.<//></div>`;
  const c = r.concall, m = r.master, fin = r.financials;
  return html`<div class="stack">
    ${m && html`<div class="grid g-2">
      <div class="card"><h2>Decision framework <span class="muted">master report · ${fmt.date(m.as_of, true)}</span></h2>
        ${m.action && html`<p style="margin-top:0">${m.action}</p>`}
        ${Object.entries(m.decision_framework || {}).map(([k, v]) => html`<div style="margin-top:8px">
          <div class="small" style="font-weight:600;text-transform:capitalize">${k.replace(/_/g, ' ')}</div>
          <ul class="clean small">${asList(v).map(x => html`<li>${txt(x)}</li>`)}</ul></div>`)}
      </div>
      <div class="card"><h2>Monitoring triggers & catalysts</h2>
        <ul class="clean small">${asList(m.monitoring_triggers).map(x => html`<li>${txt(x)}</li>`)}</ul>
        ${m.catalysts.length ? html`<div class="small" style="font-weight:600;margin-top:10px">Catalysts</div>
          <ul class="clean small">${m.catalysts.map(x => html`<li>${txt(x)}</li>`)}</ul>` : null}
        ${m.risks.length ? html`<div class="small" style="font-weight:600;margin-top:10px">Risks</div>
          <ul class="clean small">${m.risks.map(x => html`<li>${txt(x)}</li>`)}</ul>` : null}
      </div></div>`}

    ${fin && fin.quarters && html`<div class="card"><h2>Quarterly results <span class="muted">Screener · fetched ${fmt.date(fin.fetched, true)} · ₹ Cr</span></h2>
      <div class="grid g-2">
        ${['Sales', 'Revenue', 'Net Profit', 'EPS in Rs', 'OPM %', 'Financing Margin %'].filter(k => fin.rows[k]).map(k => html`<div>
          <div class="small ink2" style="font-weight:600">${k}</div>
          <${BarMini} labels=${fin.quarters.map(q => q.replace(/^(\w{3}) (\d{2})(\d{2})$/, '$1 $3'))} values=${fin.rows[k]}
            format=${v => k.includes('%') ? `${v.toFixed(0)}%` : k.startsWith('EPS') ? v.toFixed(2) : fmt.num(v)} title=${k} /></div>`)}
      </div></div>`}

    ${c ? html`
      <div class="grid g-2">
        <div class="card"><h2>Management tone <span class="muted">concall analysis · ${fmt.date(c.as_of, true)}</span></h2>
          ${c.tone && html`<p style="margin-top:0"><b style="text-transform:capitalize">${c.tone.rating || ''}</b></p>
            <ul class="clean small">${asList(c.tone.evidence).slice(0, 5).map(x => html`<li>${txt(x)}</li>`)}</ul>`}
          ${c.executive_summary && html`<p class="small ink2">${c.executive_summary}</p>`}
          ${c.transcripts_used.length ? html`<div class="small muted">Based on: ${c.transcripts_used.map(t => t.quarter).filter(Boolean).join(', ')}</div>` : null}
        </div>
        <div class="card"><h2>Forward estimates & guidance</h2>
          ${['revenue_targets', 'earnings_guidance', 'capex_plans'].map(k => asList(c.forward_estimates[k]).length ? html`
            <div class="small" style="font-weight:600;margin-top:6px;text-transform:capitalize">${k.replace(/_/g, ' ')}</div>
            <ul class="clean small">${asList(c.forward_estimates[k]).map(x => html`<li>${txt(x)}</li>`)}</ul>` : null)}
          ${c.forward_estimates.confidence_level && html`<div class="small muted" style="margin-top:6px">Confidence: ${c.forward_estimates.confidence_level}</div>`}
        </div>
      </div>
      ${c.guidance_vs_delivery.length ? html`<div class="card"><h2>Guidance vs delivery <span class="muted">did management do what it said?</span></h2>
        <div class="tablewrap"><table class="t"><thead><tr><th>Quarter</th><th>Promised</th><th>Delivered</th><th>Verdict</th></tr></thead>
        <tbody>${c.guidance_vs_delivery.map(g => html`<tr><td class="nowrap">${g.quarter}</td><td class="small">${g.what_was_promised}</td>
          <td class="small">${g.what_was_delivered}</td><td><span class="verdict ${g.verdict}">${g.verdict === 'missed' ? '▼ ' : g.verdict === 'delivered' || g.verdict === 'exceeded' ? '▲ ' : ''}${g.verdict}</span></td></tr>`)}</tbody></table></div></div>` : null}
      ${(c.guidance_evolution.topics || []).length ? html`<div class="card"><h2>How guidance evolved</h2>
        ${c.guidance_evolution.pattern_summary && html`<p class="small" style="margin-top:0">${c.guidance_evolution.pattern_summary}</p>`}
        ${c.guidance_evolution.topics.map(tp => html`<div style="margin:10px 0">
          <div class="row"><b class="small">${tp.topic}</b><span class="badge">${(tp.consistency || '').replace(/_/g, ' ')}</span></div>
          <ul class="clean small">${asList(tp.timeline).map(x => html`<li><b>${x.quarter}</b>: ${x.what_was_said}</li>`)}</ul></div>`)}
      </div>` : null}
      <div class="grid g-2">
        <div class="card"><h2>Expansion & new products</h2>
          ${c.new_facilities.length ? html`<ul class="clean small">${c.new_facilities.map(x => html`<li><b>${x.name || ''}</b> ${x.status ? '· ' + x.status : ''} ${x.expected_timeline ? '· ' + x.expected_timeline : ''}<div class="ink2">${x.revenue_impact || ''}</div></li>`)}</ul>` : html`<div class="muted small">None captured.</div>`}
        </div>
        <div class="card"><h2>Signals from the calls</h2>
          ${c.positive_signals.length ? html`<div class="small" style="font-weight:600"><span class="pol pos">▲</span> Positive</div><ul class="clean small">${c.positive_signals.map(x => html`<li>${txt(x)}</li>`)}</ul>` : null}
          ${c.red_flags.length ? html`<div class="small" style="font-weight:600;margin-top:8px"><span class="pol neg">▼</span> Red flags</div><ul class="clean small">${c.red_flags.map(x => html`<li>${txt(x)}</li>`)}</ul>` : null}
        </div>
      </div>
      ${c.kpis.length ? html`<div class="card"><h2>Sector KPIs</h2><div class="tablewrap"><table class="t">
        <thead><tr><th>Metric</th><th>Latest</th><th>Previous</th><th>Trend</th><th>Comment</th></tr></thead>
        <tbody>${c.kpis.map(k => html`<tr><td>${k.metric}</td><td class="nowrap">${k.latest_value}</td><td class="nowrap">${k.previous_value || ''}</td>
          <td>${k.trend || ''}</td><td class="small ink2">${k.management_comment || ''}</td></tr>`)}</tbody></table></div></div>` : null}`
    : html`<div class="card"><${Empty}>No concall analysis for this stock yet (transcript_analysis.json). Phase 2 adds automatic extraction when a new transcript is filed.<//></div>`}
    ${r.insights && html`<${InsightsTable} ins=${r.insights} />`}
  </div>`;
}

function InsightsTable({ ins }) {
  const qs = ins.quarters.slice(0, 6);
  return html`<div class="card"><h2>Quarterly business metrics <span class="muted">extracted from transcripts & presentations</span></h2>
    <div class="tablewrap"><table class="t"><thead><tr><th>Metric</th>${qs.map(q => html`<th class="n">${q}</th>`)}</tr></thead>
    <tbody>${Object.entries(ins.metrics).map(([name, vals]) => html`<tr><td class="small">${name}</td>${qs.map(q => html`<td class="n small">${vals[q] ?? '—'}</td>`)}</tr>`)}</tbody></table></div></div>`;
}

function Orders({ data }) {
  const ao = data.announced_orders;
  const wins = data.events.filter(e => e.type === 'order_win');
  const ob = data.research.order_book || [];
  return html`<div class="stack">
    <${StockOrderBook} sid=${data.stock.sid} />
    <div class="grid g-2">
      <div class="card"><h2>Orders announced on the exchange <span class="muted">by quarter, from NSE filings</span></h2>
        ${ao.length ? html`<${BarMini} labels=${ao.map(b => b.quarter)} values=${ao.map(b => b.value_cr)} format=${v => fmt.cr(v)} title="Announced order value by quarter" />
          <table class="t small"><thead><tr><th>Quarter</th><th class="n">Orders</th><th class="n">Value</th><th class="n">Undisclosed value</th></tr></thead>
          <tbody>${ao.map(b => html`<tr><td>${b.quarter}</td><td class="n">${b.count}</td><td class="n">${fmt.cr(b.value_cr)}</td><td class="n">${b.unvalued}</td></tr>`)}</tbody></table>`
          : html`<${Empty}>No order-win filings captured yet. They appear here as NSE announcements arrive (tracking started ${fmt.date(data.events.length ? data.events[data.events.length - 1].d : '', true)}).<//>`}
        ${data.stock.sales_ttm_cr ? html`<div class="small muted">Trailing-12M revenue ≈ ${fmt.cr(data.stock.sales_ttm_cr)} — order wins ≥ 5% of it are flagged high.</div>` : null}
      </div>
      <div class="card"><h2>Order book</h2>
        ${data.disclosed_order_book ? html`<div style="margin-bottom:10px"><div class="small ink2">Latest figure disclosed in a filing</div>
          <div style="font-size:22px;font-weight:600">${fmt.cr(data.disclosed_order_book.value_cr)}</div>
          <div class="small muted">${fmt.date(data.disclosed_order_book.d, true)} · ${data.disclosed_order_book.title}
            ${data.disclosed_order_book.url ? html` · <a href=${data.disclosed_order_book.url} target="_blank" rel="noopener">filing ↗</a>` : null}</div></div>` : null}
        <div class="small ink2" style="font-weight:600;margin-bottom:4px">From research reports</div>
        ${ob.length ? html`<div class="tablewrap"><table class="t small"><thead><tr><th>Metric</th><th>Value</th><th>Source</th></tr></thead><tbody>
          ${ob.map(o => html`<tr><td>${o.metric}</td><td>${o.series ? Object.entries(o.series).map(([q, v]) => `${q}: ${v}`).join(' · ') : `${o.value ?? ''}${o.previous ? ` (prev ${o.previous})` : ''}`}
            ${o.comment ? html`<div class="ink2">${o.comment}</div>` : null}</td><td class="nowrap muted">${o.source}${o.quarter ? ' · ' + o.quarter : ''}</td></tr>`)}
        </tbody></table></div>` : html`<${Empty}>No order-book figures in the existing reports.<//>`}
      </div>
    </div>
    <div class="card"><h2>Order-win filings</h2><${EventList} rows=${wins} showStock=${false} empty="None yet." /></div>
  </div>`;
}

function Holding({ data }) {
  const sh = data.shareholding;
  const labels = sh.map(r => r.quarter || r.qdate.slice(0, 7));
  const groups = [['promoters', 'Promoters'], ['fiis', 'FIIs'], ['diis', 'DIIs'], ['public', 'Public']];
  return html`<div class="stack">
    <div class="card"><h2>Shareholding by quarter <span class="muted">% of equity</span></h2>
      ${sh.length ? html`<div class="grid g-2">${groups.map(([k, l]) => html`<div><div class="small ink2" style="font-weight:600">${l}</div>
        <${LineMini} labels=${labels} values=${sh.map(r => r[k])} title=${l} /></div>`)}</div>
        <div class="tablewrap"><table class="t small"><thead><tr><th>Quarter</th>${groups.map(([, l]) => html`<th class="n">${l}</th>`)}<th class="n">Shareholders</th></tr></thead>
        <tbody>${[...sh].reverse().map(r => html`<tr><td>${r.quarter || r.qdate}</td>${groups.map(([k]) => html`<td class="n">${r[k] != null ? r[k].toFixed(2) + '%' : '—'}</td>`)}<td class="n">${fmt.num(r.shareholders)}</td></tr>`)}</tbody></table></div>`
        : html`<${Empty}>No shareholding data yet for this stock.<//>`}
      ${data.promoters.length ? html`<div class="small muted" style="margin-top:8px">Promoter group (used to spot promoter bulk deals): ${data.promoters.slice(0, 8).join(', ')}${data.promoters.length > 8 ? '…' : ''}</div>` : null}
    </div>
    ${Object.keys(data.holders || {}).length ? html`<${NamedHolders} holders=${data.holders} />` : html`<div class="card"><h2>Named holders</h2>
      <${Empty}>No named-holder data yet. It comes from NSE shareholding-pattern filings (NSE stocks) and Screener shareholding files.<//></div>`}
    <div class="card"><h2>Bulk & block deals</h2>
      ${data.deals.length ? html`<div class="tablewrap"><table class="t small"><thead><tr><th>Date</th><th>Client</th><th>Type</th><th>Side</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Value</th></tr></thead>
      <tbody>${data.deals.map(d => html`<tr><td class="nowrap">${fmt.date(d.d, true)}</td><td>${d.investor ? html`<b>${d.investor}</b> · ` : ''}${d.client}</td><td>${d.client_type}</td>
        <td class=${d.side.startsWith('B') ? 'up' : 'down'}>${d.side.startsWith('B') ? '▲ Buy' : '▼ Sell'}</td><td class="n">${fmt.num(d.qty)}</td><td class="n">${fmt.price(d.price)}</td><td class="n">${fmt.cr(d.value_cr)}</td></tr>`)}</tbody></table></div>`
        : html`<${Empty}>No bulk or block deals recorded.<//>`}
    </div>
    <div class="card"><h2>Insider, SAST & pledge filings</h2>
      <${EventList} rows=${data.events.filter(e => ['sast', 'pledge', 'insider'].includes(e.kind) || e.type === 'insider_disclosure')} showStock=${false} empty="None recorded yet (NSE feeds are tracked from now on)." />
    </div>
  </div>`;
}

const H_ORDER = ['anchor', 'fii', 'dii', 'promoter'];
const H_NOTE = {
  anchor: 'non-institutional public holders with ≥1% — individuals, HUFs, bodies corporate',
  fii: 'foreign portfolio investors with ≥1%', dii: 'mutual funds, insurers, AIFs with ≥1%',
  promoter: 'every promoter-group entity',
};

function HolderStatus({ r }) {
  if (r.status === 'new') return html`<span class="pol pos">▲ New</span>`;
  if (r.status === 'exited') return html`<span class="pol neg">▼ Exited / below 1%</span>`;
  if (r.status === 'gone') return html`<span class="muted">Earlier</span>`;
  if (r.change == null || r.status === 'flat') return html`<span class="muted">—</span>`;
  return html`<span class=${r.change > 0 ? 'up num' : 'down num'}>${r.change > 0 ? '▲' : '▼'} ${Math.abs(r.change).toFixed(2)} pp</span>`;
}

function NamedHolders({ holders }) {
  const groups = H_ORDER.filter(g => holders[g]);
  const [grp, setGrp] = useState(groups[0]);
  const [showGone, setShowGone] = useState(false);
  const g = holders[grp] || holders[groups[0]];
  if (!g) return null;
  const rows = g.rows.filter(r => showGone || r.status !== 'gone');
  const gone = g.rows.length - g.rows.filter(r => r.status !== 'gone').length;
  const src = q => g.sources[q] === 'nse_xbrl' ? 'NSE filing' : 'Screener';
  const counts = { new: g.rows.filter(r => r.status === 'new').length, exited: g.rows.filter(r => r.status === 'exited').length };
  return html`<div class="card">
    <div class="row wrap" style="margin-bottom:8px">
      <h2 style="margin:0">Named holders</h2>
      <div class="seg">${groups.map(k => html`<button class=${grp === k ? 'on' : ''} onClick=${() => setGrp(k)}>${holders[k].label}
        <span class="muted"> ${holders[k].rows.filter(r => r.latest != null).length}</span></button>`)}</div>
      <div class="spacer"></div>
      ${gone ? html`<label class="small row"><input type="checkbox" checked=${showGone} onChange=${e => setShowGone(e.target.checked)} /> show ${gone} earlier holder${gone > 1 ? 's' : ''}</label>` : null}
    </div>
    <div class="small muted" style="margin-bottom:6px">${H_NOTE[grp]} · % of equity ·${' '}
      ${counts.new ? html`<span class="pol pos">${counts.new} new</span> · ` : ''}${counts.exited ? html`<span class="pol neg">${counts.exited} exited</span> · ` : ''}
      latest ${qlabel(g.quarters[g.quarters.length - 1])} from ${src(g.quarters[g.quarters.length - 1])}
      ${g.urls[g.quarters[g.quarters.length - 1]] ? html` <a href=${g.urls[g.quarters[g.quarters.length - 1]]} target="_blank" rel="noopener">↗</a>` : ''}</div>
    <div class="tablewrap"><table class="t small">
      <thead><tr><th>Holder</th>${g.quarters.map(q => html`<th class="n" title=${`source: ${src(q)}`}>${qlabel(q)}</th>`)}<th>Change</th></tr></thead>
      <tbody>${rows.map(r => html`<tr>
        <td><span class=${r.status === 'exited' || r.status === 'gone' ? 'muted' : ''}>${r.name}</span>
          ${r.tracked ? html` <span class="badge" title=${`tracked investor: ${r.tracked}`}>Tracked</span>` : ''}</td>
        ${g.quarters.map(q => html`<td class="n">${r.values[q] != null ? r.values[q].toFixed(2) + '%' : html`<span class="muted">—</span>`}</td>`)}
        <td class="nowrap"><${HolderStatus} r=${r} /></td></tr>`)}</tbody>
    </table></div>
    ${!rows.length ? html`<${Empty}>No ${g.label.toLowerCase()} above 1% in these quarters.<//>` : null}
  </div>`;
}

function NotesTab({ mentions, sid }) {
  if (!mentions.length) return html`<div class="card"><${Empty}>No notes mention ${sid}. Add one on the <a href="#/notes?tab=yours">Notes</a> page using <code>$${sid}</code>.<//></div>`;
  return html`<div class="card"><div class="evlist">${mentions.map(m => html`<div class="ev">
    <div class="ev-when">${fmt.date(m.d, true)}</div>
    <div><div class="row"><span class="badge">${m.stance}</span><b>${m.title}</b>${m.channel && html`<span class="muted small">${m.channel}</span>`}
      ${m.source_url && html`<a class="small" href=${m.source_url} target="_blank" rel="noopener">Source ↗</a>`}</div>
      <div class="ev-detail">${m.text}</div>
      ${(m.entry || m.target || m.stop) ? html`<div class="ev-meta">${m.entry ? html`<span>Entry ${fmt.price(m.entry)}</span>` : ''}${m.target ? html`<span>Target ${fmt.price(m.target)}</span>` : ''}${m.stop ? html`<span>Stop ${fmt.price(m.stop)}</span>` : ''}</div>` : null}
    </div></div>`)}</div></div>`;
}

function Reports({ r }) {
  const [open, setOpen] = useState(null);
  const [content, setContent] = useState('');
  async function show(path) {
    setOpen(path);
    setContent('Loading…');
    try { setContent((await api(`/report?path=${encodeURIComponent(path)}`)).content); }
    catch (e) { setContent(`Could not load: ${e.message}`); }
  }
  if (!r.available) return html`<div class="card"><${Empty}>No research folder for this stock.<//></div>`;
  return html`<div class="grid g-main-side">
    <div class="card">${open ? html`<${Markdown} text=${content} />` : html`<${Empty}>Pick a report on the right.<//>`}</div>
    <div class="stack">
      <div class="card"><h2>Reports</h2>${r.reports.map(x => html`<div class="row" style="padding:4px 0">
        <a href="javascript:void 0" onClick=${() => show(x.path)} style=${open === x.path ? 'font-weight:600' : ''}>${x.name.replace(/_/g, ' ')}</a>
        <span class="spacer"></span><span class="muted small">${fmt.date(x.date, true)}</span></div>`)}</div>
      <div class="card"><h2>Documents</h2>${r.documents.length ? r.documents.map(d => html`<div class="row small" style="padding:3px 0">
        <a href=${`/api/t/file?path=${encodeURIComponent(d.path)}`} target="_blank" rel="noopener">${d.kind} ${d.period}</a>
        <span class="spacer"></span><span class="muted">${d.name.length > 34 ? d.name.slice(0, 32) + '…' : d.name}</span></div>`) : html`<div class="muted small">No PDFs downloaded.</div>`}</div>
    </div>
  </div>`;
}
