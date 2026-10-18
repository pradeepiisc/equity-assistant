// Stock Tracker — shell, router and list pages.
import { html, render, useState, useEffect, useMemo } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { api, post, useApi, fmt, Phase, Delta, Tile, EventList, Empty, toast, go, sortRows, PHASES, PHASE_CLS,
         KIND_LABEL } from './ui.js';
import { StockPage } from './stock.js';
import { StatusPage } from './status.js';
import { OrderBooksPage, OrdersPage } from './orders.js';
import { PrioritiesPage } from './priorities.js';

// ── router ───────────────────────────────────────────────────────────────
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { page: parts[0] || 'today', arg: parts[1], params };
}
function useRoute() {
  const [r, setR] = useState(parseHash());
  useEffect(() => {
    const on = () => { setR(parseHash()); window.scrollTo(0, 0); };
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return r;
}
const setParams = (page, params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '')).toString();
  history.replaceState(null, '', `#/${page}${q ? '?' + q : ''}`);
};

// ── shell ────────────────────────────────────────────────────────────────
const NAV = [['today', 'Today'], ['stocks', 'Stocks'], ['priorities', 'Priorities'], ['orderbooks', 'Order books'], ['orders', 'Orders'],
             ['events', 'Events'], ['deals', 'Deals'], ['notes', 'Notes'], ['status', 'Status']];

// Bumps whenever a background tick finishes, so open pages refetch without a reload.
function usePulse() {
  const [p, setP] = useState({ version: 0, tick: null, running: false, next_at: null });
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const r = await api('/pulse');
        if (!alive) return;
        setP(prev => ({ ...r, version: prev.tick && r.tick !== prev.tick ? prev.version + 1 : prev.version }));
      } catch (_) { /* server restarting */ }
    };
    check();
    const t = setInterval(check, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return p;
}

function App() {
  const route = useRoute();
  const pulse = usePulse();
  const v = pulse.version;
  const summary = useApi('/summary', [route.page, v]);
  const unread = summary.data ? Object.values(summary.data.unread).reduce((a, b) => a + b, 0) : 0;
  const page = {
    today: html`<${Today} summary=${summary} version=${v} />`,
    stocks: html`<${Stocks} params=${route.params} version=${v} />`,
    stock: html`<${StockPage} sid=${route.arg} key=${route.arg} version=${v} tab=${route.params.tab} />`,
    priorities: html`<${PrioritiesPage} params=${route.params} version=${v} />`,
    events: html`<${Events} params=${route.params} version=${v} />`,
    deals: html`<${Deals} version=${v} />`,
    notes: html`<${Notes} />`,
    status: html`<${StatusPage} version=${v} />`,
    orderbooks: html`<${OrderBooksPage} params=${route.params} version=${v} />`,
    orders: html`<${OrdersPage} params=${route.params} version=${v} />`,
  }[route.page] || html`<${Empty}>Unknown page.<//>`;
  return html`
    <div class="shell">
      <aside class="side">
        <div class="brand">Stock Tracker<small>portfolio · watchlist · notes</small></div>
        <nav class="nav">
          ${NAV.map(([k, l]) => html`<a href="#/${k}" class=${route.page === k || (k === 'stocks' && route.page === 'stock') ? 'active' : ''}>
            ${l}${k === 'today' && unread ? html`<span class="count" title="unread alerts">${unread}</span>` : null}</a>`)}
          <a href="/legacy" title="The existing workflow launcher">Workflows ↗</a>
        </nav>
        <${SideFoot} s=${summary.data} pulse=${pulse} reload=${summary.reload} />
      </aside>
      <main class="main">${page}</main>
    </div>`;
}

function SideFoot({ s, pulse, reload }) {
  const [busy, setBusy] = useState(false);
  async function sync() {
    setBusy(true);
    try {
      const r = await post('/run/tick');
      toast(r.started ? 'Refreshing — every NSE feed now, plus deals and prices when due…' : `Already running: ${r.running}`);
      const wait = async () => {
        const st = await api('/pulse');
        if (st.running) return setTimeout(wait, 2500);
        setBusy(false);
        toast('Refresh finished');
        reload();
      };
      setTimeout(wait, 2500);
    } catch (e) { toast(`Refresh failed: ${e.message}`); setBusy(false); }
  }
  const j = (s && s.jobs) || {};
  const mins = pulse.next_at ? Math.max(0, Math.round((new Date(pulse.next_at) - Date.now()) / 60000)) : null;
  return html`<div class="side-foot">
    <div title="the dashboard polls NSE in the background while it is open">
      ${pulse.running ? html`<span class="up">●</span> Polling now…`
        : pulse.next_at ? html`<span class="up">●</span> Polling · next ${mins ? `in ${mins}m` : 'now'}`
        : html`<span class="muted">○</span> Not polling`}</div>
    <div>Prices: <b class="ink2">${s && s.as_of ? fmt.date(s.as_of, true) : '—'}</b></div>
    <div>Filings: ${j.feeds ? fmt.ago(j.feeds.finished_at) : 'never'}</div>
    <div>Deals: ${j.deals ? fmt.ago(j.deals.finished_at) : 'never'}</div>
    <button class="btn" onClick=${sync} disabled=${busy || pulse.running}>${busy ? 'Refreshing…' : 'Refresh now'}</button>
    <a href="#/status" class="small">Status & schedule</a>
  </div>`;
}

// ── Today ────────────────────────────────────────────────────────────────
function Today({ summary, version }) {
  const s = summary.data;
  const [mode, setMode] = useState('unread');
  const alerts = useApi(`/alerts?status=${mode}&limit=300`, [mode, version]);
  const stocks = useApi('/stocks?scope=all', [version]);
  if (!s) return html`<div class="muted">Loading…</div>`;
  const p = s.portfolio, b = s.breadth;
  const unreadTotal = Object.values(s.unread).reduce((a, c) => a + c, 0);
  const changed = (stocks.data ? stocks.data.rows : []).filter(r => r.phase_since && r.phase_since === r.d && r.phase !== 'No data');

  async function markAll() {
    await post('/alerts/read', { all: true });
    alerts.reload(); summary.reload();
  }
  return html`
    <div class="page-head"><h1>Today</h1><span class="muted">prices as of ${fmt.date(s.as_of, true)} close</span></div>
    <div class="tiles">
      <${Tile} hero label="Portfolio value" value=${fmt.compact(p.value)} sub=${`${p.holdings} holdings · invested ${fmt.compact(p.invested)}`} />
      <${Tile} label="Day change" value=${html`<span class=${p.day_change >= 0 ? 'up' : 'down'}>${fmt.compact(p.day_change)}</span>`}
               sub=${`${fmt.pct(p.day_change_pct, 2)} vs previous close`} />
      <${Tile} label="Unrealised P&L" value=${html`<span class=${p.pnl >= 0 ? 'up' : 'down'}>${fmt.compact(p.pnl)}</span>`} sub=${fmt.pct(p.pnl_pct)} />
      <${Tile} label="Above DMA200" value=${b.n200 ? `${Math.round(b.dma200 / b.n200 * 100)}%` : '—'} sub=${`${b.dma200} of ${b.n200} holdings with 200+ sessions`} />
      <${Tile} label="Above DMA40" value=${b.n40 ? `${Math.round(b.dma40 / b.n40 * 100)}%` : '—'} sub=${`${b.dma40} of ${b.n40} holdings`} />
      <${Tile} label="Unread alerts" value=${unreadTotal} sub=${['high', 'medium'].map(k => `${s.unread[k] || 0} ${k}`).join(' · ')} />
    </div>
    <div class="grid g-main-side" style="margin-top:16px">
      <div class="card">
        <div class="row" style="margin-bottom:6px">
          <h2 style="margin:0" class="card-title">Alerts</h2>
          <div class="seg"><button class=${mode === 'unread' ? 'on' : ''} onClick=${() => setMode('unread')}>Unread</button>
            <button class=${mode === 'all' ? 'on' : ''} onClick=${() => setMode('all')}>All</button></div>
          <div class="spacer"></div>
          ${mode === 'unread' && unreadTotal ? html`<button class="btn" onClick=${markAll}>Mark all read</button>` : null}
        </div>
        <div class=${alerts.loading ? 'loading' : ''}>
          <${EventList} rows=${alerts.data ? alerts.data.rows : []}
            empty=${mode === 'unread' ? 'You’re all caught up. New crossovers, filings and deals for your stocks land here.' : 'No alerts yet.'} />
        </div>
      </div>
      <div class="stack">
        <div class="card"><h2>Trend phases <span class="muted">price vs DMA40 vs DMA100</span></h2>
          ${['portfolio', 'watchlist', 'notes'].filter(k => Object.keys(s.phases[k] || {}).length).map(k => html`
            <div style="margin-bottom:10px"><div class="small ink2" style="text-transform:capitalize;font-weight:600">${k}</div>
            <${PhaseBar} counts=${s.phases[k]} scope=${k} /></div>`)}
        </div>
        <div class="card"><h2>Phase changes on ${fmt.date(s.as_of)}</h2>
          ${changed.length ? html`<table class="t small"><tbody>${changed.map(r => html`
            <tr class="click" onClick=${() => go(`stock/${encodeURIComponent(r.sid)}`)}><td><span class="sym">${r.sid}</span>
              ${r.in_portfolio ? html` <span class="badge">held</span>` : ''}</td><td><${Phase} p=${r.phase} /></td><td class="n"><${Delta} v=${r.chg_pct} /></td></tr>`)}</tbody></table>`
            : html`<div class="muted small">No stock changed phase in the latest session.</div>`}
        </div>
        <div class="card"><h2>Board meetings & ex-dates</h2>
          ${s.upcoming.length ? html`<table class="t small"><tbody>${s.upcoming.slice(0, 12).map(u => html`<tr class="click" onClick=${() => go(`stock/${encodeURIComponent(u.sid)}`)}>
            <td class="sym">${u.sid}</td><td>${u.title}</td><td class="n nowrap">${u.when || fmt.date(u.ts)}</td></tr>`)}</tbody></table>`
            : html`<div class="muted small">None filed in the last 30 days. They appear here as NSE filings arrive.</div>`}
        </div>
      </div>
    </div>`;
}

function PhaseBar({ counts, scope }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  const open = ph => go(`stocks?scope=${scope}&phase=${encodeURIComponent(ph)}`);
  return html`
    <div class="pbar" role="img" aria-label="phase distribution">
      ${PHASES.filter(ph => counts[ph]).map(ph => html`<button class=${PHASE_CLS[ph]} style=${`flex:${counts[ph]}`}
        title=${`${ph}: ${counts[ph]} (${Math.round(counts[ph] / total * 100)}%)`} onClick=${() => open(ph)}></button>`)}
    </div>
    <div class="plegend">
      ${PHASES.filter(ph => counts[ph]).map(ph => html`<button onClick=${() => open(ph)}><${Phase} p=${ph} /> <b class="num">${counts[ph]}</b></button>`)}
    </div>`;
}

// ── Stocks ───────────────────────────────────────────────────────────────
const COLS = [
  ['sid', 'Stock', false], ['close', 'Price', true], ['chg_pct', '1D', true], ['phase', 'Phase', false],
  ['vs40', 'vs DMA40', true], ['vs200', 'vs DMA200', true], ['from_high', 'From 52w high', true], ['ret_1m', '1M', true],
  ['weight', 'Weight', true], ['pnl_pct', 'P&L', true], ['last_event', 'Latest filing / deal', false], ['stance', 'Note', false],
];

function Stocks({ params, version }) {
  const [scope, setScope] = useState(params.scope || 'portfolio');
  const [phase, setPhase] = useState(params.phase || '');
  const [q, setQ] = useState(params.q || '');
  const [sort, setSort] = useState({ key: scope === 'portfolio' ? 'weight' : 'vs200', dir: 'desc' });
  const { data, loading } = useApi(`/stocks?scope=${scope}`, [scope, version]);
  useEffect(() => setParams('stocks', { scope, phase, q }), [scope, phase, q]);
  const rows = useMemo(() => {
    let r = data ? data.rows : [];
    if (phase) r = r.filter(x => x.phase === phase);
    if (q) { const s = q.toLowerCase(); r = r.filter(x => `${x.sid} ${x.name} ${x.sector}`.toLowerCase().includes(s)); }
    const key = sort.key === 'last_event' ? null : sort.key;
    return key ? sortRows(r, key === 'phase' ? 'phase' : key, sort.dir) : r;
  }, [data, phase, q, sort]);
  const th = ([key, label, num]) => html`<th class="sortable ${num ? 'n' : ''}" onClick=${() => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}>
    ${label}${sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}</th>`;
  return html`
    <div class="page-head"><h1>Stocks</h1><span class="muted">${rows.length} shown</span></div>
    <div class="filters">
      <div class="seg">${[['portfolio', 'Portfolio'], ['watchlist', 'Watchlist'], ['notes', 'From notes'], ['all', 'All']].map(([k, l]) =>
        html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      <button class="chip ${!phase ? 'on' : ''}" onClick=${() => setPhase('')}>Any phase</button>
      ${PHASES.map(ph => html`<button class="chip ${phase === ph ? 'on' : ''}" onClick=${() => setPhase(phase === ph ? '' : ph)}><${Phase} p=${ph} /></button>`)}
      <input class="input" placeholder="Search symbol, name, sector" value=${q} onInput=${e => setQ(e.target.value)} style="width:220px" />
    </div>
    <div class="card" style="padding:0">
      <div class="tablewrap ${loading ? 'loading' : ''}" style="max-height:calc(100vh - 190px)">
        <table class="t">
          <thead><tr>${COLS.map(th)}</tr></thead>
          <tbody>${rows.map(r => html`<tr class="click" onClick=${() => go(`stock/${encodeURIComponent(r.sid)}`)}>
            <td><span class="sym">${r.sid}</span>${r.is_sme ? html` <span class="badge">SME</span>` : ''}${r.unread ? html` <span class="badge" title="unread alerts">${r.unread} new</span>` : ''}
              <span class="cname" title=${r.name}>${r.name}</span></td>
            <td class="n">${fmt.price(r.close)}</td>
            <td class="n"><${Delta} v=${r.chg_pct} /></td>
            <td><${Phase} p=${r.phase} /><div class="tiny muted">${r.phase_since ? 'since ' + fmt.date(r.phase_since) : ''}</div></td>
            <td class="n">${fmt.pct(r.vs40)}</td>
            <td class="n">${fmt.pct(r.vs200)}</td>
            <td class="n">${fmt.pct(r.from_high)}</td>
            <td class="n"><${Delta} v=${r.ret_1m} /></td>
            <td class="n">${r.weight != null ? r.weight.toFixed(2) + '%' : '—'}</td>
            <td class="n">${r.pnl_pct != null ? html`<${Delta} v=${r.pnl_pct} />` : '—'}</td>
            <td class="small"><div class="clip" title=${r.last_event ? r.last_event.title : ''}>${r.last_event ? html`<span class="ink2">${fmt.date(r.last_event.ts)}</span> ${r.last_event.title}` : html`<span class="muted">—</span>`}</div></td>
            <td>${r.stance ? html`<span class="badge">${r.stance}</span>` : ''}</td>
          </tr>`)}</tbody>
        </table>
        ${!rows.length && !loading ? html`<${Empty}>No stocks match.<//>` : null}
      </div>
    </div>`;
}

// ── Events ───────────────────────────────────────────────────────────────
const KINDS = ['filing', 'technical', 'deal', 'sast', 'pledge', 'insider', 'shareholding', 'note'];

function Events({ params, version }) {
  const [scope, setScope] = useState(params.scope || 'all');
  const [kinds, setKinds] = useState(params.kind ? params.kind.split(',') : []);
  const [sev, setSev] = useState(params.sev || 'medium');
  const [days, setDays] = useState(Number(params.days) || 30);
  const [q, setQ] = useState(params.q || '');
  const [debounced, setDebounced] = useState(q);
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 300); return () => clearTimeout(t); }, [q]);
  useEffect(() => setParams('events', { scope, kind: kinds.join(','), sev, days, q }), [scope, kinds, sev, days, q]);
  const path = `/events?scope=${scope}&kind=${kinds.join(',')}&min_severity=${sev}&days=${days}&q=${encodeURIComponent(debounced)}&limit=400`;
  const { data, loading } = useApi(path, [path, version]);
  const toggle = k => setKinds(ks => ks.includes(k) ? ks.filter(x => x !== k) : [...ks, k]);
  return html`
    <div class="page-head"><h1>Events</h1><span class="muted">technical milestones, exchange filings, deals and holding changes</span></div>
    <div class="filters">
      <div class="seg">${[['7', 7], ['30', 30], ['90', 90], ['1Y', 365]].map(([l, n]) => html`<button class=${days === n ? 'on' : ''} onClick=${() => setDays(n)}>${l}</button>`)}</div>
      <div class="seg">${[['all', 'All'], ['portfolio', 'Portfolio'], ['watchlist', 'Watchlist'], ['notes', 'Notes']].map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      <div class="seg">${[['low', 'All'], ['medium', 'Med+'], ['high', 'High']].map(([k, l]) => html`<button class=${sev === k ? 'on' : ''} onClick=${() => setSev(k)}>${l}</button>`)}</div>
      ${KINDS.map(k => html`<button class="chip ${kinds.includes(k) ? 'on' : ''}" onClick=${() => toggle(k)}>${KIND_LABEL[k]}</button>`)}
      <input class="input" placeholder="Search" value=${q} onInput=${e => setQ(e.target.value)} style="width:180px" />
    </div>
    <div class="card ${loading ? 'loading' : ''}">
      <${EventList} rows=${data ? data.rows : []} empty="No events match these filters." />
    </div>`;
}

// ── Deals ────────────────────────────────────────────────────────────────
const CTYPE = { ace: 'Tracked investor', promoter: 'Promoter', fii: 'FII', dii: 'DII', fund: 'Fund / AIF', prop: 'Prop / HFT', other: 'Other' };

function Deals({ version }) {
  const [days, setDays] = useState(30);
  const [prop, setProp] = useState(false);
  const { data, loading } = useApi(`/deals?days=${days}&include_prop=${prop}`, [days, prop, version]);
  const rows = data ? data.rows : [];
  return html`
    <div class="page-head"><h1>Bulk & block deals</h1><span class="muted">NSE · your stocks, plus tracked investors in any stock · latest file ${data ? fmt.date(data.latest, true) : '—'}</span></div>
    <div class="filters">
      <div class="seg">${[['7D', 7], ['30D', 30], ['90D', 90]].map(([l, n]) => html`<button class=${days === n ? 'on' : ''} onClick=${() => setDays(n)}>${l}</button>`)}</div>
      <label class="small row"><input type="checkbox" checked=${prop} onChange=${e => setProp(e.target.checked)} /> include prop / HFT desks
        ${data && data.prop_hidden ? html`<span class="muted">(${data.prop_hidden} hidden)</span>` : ''}</label>
    </div>
    <div class="card" style="padding:0"><div class="tablewrap ${loading ? 'loading' : ''}">
      <table class="t"><thead><tr><th>Date</th><th>Stock</th><th>Client</th><th>Client type</th><th>Side</th><th class="n">Quantity</th><th class="n">Price</th><th class="n">Value</th><th>Deal</th></tr></thead>
      <tbody>${rows.map(d => html`<tr class=${d.sid ? 'click' : ''} onClick=${() => d.sid && go(`stock/${encodeURIComponent(d.sid)}`)}>
        <td class="nowrap">${fmt.date(d.d, true)}</td>
        <td><span class="sym">${d.sid || d.symbol}</span>${!d.sid ? html` <span class="badge">not tracked</span>` : ''}<span class="cname">${d.name || d.security}</span></td>
        <td>${d.investor ? html`<b>${d.investor}</b><div class="tiny muted">${d.client}</div>` : d.client}</td>
        <td>${CTYPE[d.client_type] || d.client_type}</td>
        <td class=${d.side.startsWith('B') ? 'up' : 'down'}>${d.side.startsWith('B') ? '▲ Buy' : '▼ Sell'}</td>
        <td class="n">${fmt.num(d.qty)}</td><td class="n">${fmt.price(d.price)}</td><td class="n">${fmt.cr(d.value_cr)}</td><td>${d.kind}</td></tr>`)}</tbody></table>
      ${!rows.length && !loading ? html`<${Empty}>No deals in this window. NSE publishes the day's bulk/block deals after the close.<//>` : null}
    </div></div>`;
}

// ── Notes ────────────────────────────────────────────────────────────────
const EXAMPLE = `$ROSSTECH add — order book ~3x revenue, aerospace plant by Q4. entry 650 target 1100 sl 560
$ZELIO watch: EV two-wheelers, wait for Q2 numbers
- APOLLOPIPE: reduce, guidance deferred again`;

function Notes() {
  const notes = useApi('/notes');
  const disc = useApi('/discovery');
  const [form, setForm] = useState({ title: '', source: '', channel: '', body: '' });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const set = k => e => setForm({ ...form, [k]: e.target.value });

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await post('/notes', form);
      setResult(r);
      setForm({ title: '', source: '', channel: '', body: '' });
      toast(`Saved notes/${r.id}.md — ${r.mentions.length} stock mention(s)`);
      notes.reload(); disc.reload();
      if (r.added_stocks.length) setTimeout(() => disc.reload(), 4000);
    } catch (err) { toast(`Could not save: ${err.message}`); }
    setBusy(false);
  }
  async function watch(sid) {
    const reason = prompt(`Why watch ${sid}?`, '');
    if (reason === null) return;
    const r = await post('/watchlist', { sid, reason });
    toast(r.added ? `${sid} added to watchlist.yaml` : `${sid} already on the watchlist`);
    disc.reload();
  }
  const d = disc.data;
  return html`
    <div class="page-head"><h1>Notes & discovery</h1><span class="muted">your video notes, as markdown files in notes/</span></div>
    <div class="grid g-main-side">
      <div class="stack">
        <div class="card"><h2>Ideas from your notes <span class="muted">mentioned, not held or watched</span></h2>
          ${d && d.ideas.length ? html`<div class="tablewrap"><table class="t small"><thead><tr><th>Stock</th><th>Stance</th><th>Phase</th><th class="n">vs DMA40</th><th class="n">vs DMA200</th><th class="n">1M</th><th>Last note</th><th></th></tr></thead>
            <tbody>${d.ideas.map(i => html`<tr><td class="click" onClick=${() => go(`stock/${encodeURIComponent(i.sid)}`)}><span class="sym">${i.sid}</span><span class="cname">${i.name}</span></td>
              <td>${(i.stances || '').split(',').map(x => html`<span class="badge">${x}</span> `)}</td><td><${Phase} p=${i.phase} /></td>
              <td class="n">${fmt.pct(i.vs40)}</td><td class="n">${fmt.pct(i.vs200)}</td><td class="n"><${Delta} v=${i.ret_1m} /></td>
              <td class="nowrap">${fmt.date(i.last_note)}</td><td><button class="btn" onClick=${() => watch(i.sid)}>+ Watchlist</button></td></tr>`)}</tbody></table></div>`
            : html`<div class="muted small">Stocks you mention in notes but don't hold or watch show up here, with their trend, so you can decide when to start a position.</div>`}
          ${d && d.unresolved.length ? html`<div class="small" style="margin-top:10px"><b>Couldn't resolve:</b> ${d.unresolved.map(u => html`<code>${u.raw_symbol}</code> `)} — use the NSE symbol or BSE code.</div>` : null}
        </div>
        ${d && d.investor_deals.length ? html`<div class="card"><h2>Tracked investors' deals <span class="muted">last 90 days, any stock</span></h2>
          <table class="t small"><tbody>${d.investor_deals.map(x => html`<tr><td class="nowrap">${fmt.date(x.d)}</td><td><b>${x.investor}</b></td>
            <td class=${x.side.startsWith('B') ? 'up' : 'down'}>${x.side.startsWith('B') ? '▲ bought' : '▼ sold'}</td><td><span class="sym">${x.symbol}</span> <span class="muted">${x.security}</span></td><td class="n">${fmt.cr(x.value_cr)}</td></tr>`)}</tbody></table></div>` : null}
        <div class="card"><h2>All notes</h2>
          ${notes.data && notes.data.rows.length ? notes.data.rows.map(n => html`<div class="ev">
            <div class="ev-when">${fmt.date(n.d, true)}</div>
            <div><div class="row wrap"><b>${n.title}</b>${n.channel && html`<span class="muted small">${n.channel}</span>`}
              ${n.source_url && html`<a class="small" href=${n.source_url} target="_blank" rel="noopener">Source ↗</a>`}</div>
              <div class="ev-meta">${n.mentions.map(m => html`<span class="chip">${m.sid ? html`<a href="#/stock/${encodeURIComponent(m.sid)}">${m.sid}</a>` : html`<span class="err">${m.raw_symbol}?</span>`} · ${m.stance}${m.entry ? ` · entry ${m.entry}` : ''}${m.target ? ` · tgt ${m.target}` : ''}${m.stop ? ` · sl ${m.stop}` : ''}</span>`)}</div>
              <div class="small muted" style="margin-top:4px">notes/${n.id}.md</div>
            </div></div>`) : html`<${Empty}>No notes yet. Write one on the right, or drop markdown files into the notes/ folder.<//>`}
        </div>
      </div>
      <div class="stack">
        <form class="card" onSubmit=${save}>
          <h2>New note</h2>
          <div class="stack" style="gap:8px">
            <input class="input" required placeholder="Title — e.g. video name" value=${form.title} onInput=${set('title')} />
            <div class="row"><input class="input" style="flex:2" placeholder="Source URL (optional)" value=${form.source} onInput=${set('source')} />
              <input class="input" style="flex:1" placeholder="Channel" value=${form.channel} onInput=${set('channel')} /></div>
            <textarea class="input" required placeholder=${EXAMPLE} value=${form.body} onInput=${set('body')}></textarea>
            <div class="row"><button class="btn primary" disabled=${busy}>${busy ? 'Saving…' : 'Save note'}</button>
              <span class="muted small">Saved as markdown in notes/; new stocks start being tracked.</span></div>
          </div>
          ${result && html`<div class="small" style="margin-top:8px">Parsed: ${result.mentions.map(m => html`<span class="chip">${m.sid || m.raw_symbol + '?'} · ${m.stance}</span> `)}</div>`}
        </form>
        <div class="card help"><h2>How notes are read</h2>
          <ul class="clean">
            <li>Mention a stock with <code>$SYMBOL</code> (NSE symbol or BSE code), or start a bullet with <code>- SYMBOL:</code>.</li>
            <li>The first stance word after it sets the stance: <code>add</code>/<code>buy</code>/<code>accumulate</code>, <code>reduce</code>/<code>trim</code>, <code>exit</code>/<code>sell</code>, <code>watch</code>/<code>idea</code>, <code>hold</code>, <code>avoid</code>.</li>
            <li><code>entry 650</code> or <code>buy below 650</code>, <code>target 1100</code>, <code>sl 560</code> set price levels — you get an alert when the close reaches them.</li>
            <li>Mentioned stocks you don't hold become tracked ideas: prices, DMAs, filings and deals, with alerts.</li>
          </ul>
        </div>
      </div>
    </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
