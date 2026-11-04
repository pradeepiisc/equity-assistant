// Order-book dashboard: Order books (+ history overlay), Orders (by company / feed), and the
// per-stock order-book card used on the stock page.
import { html, useState, useEffect, useMemo, useRef } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { useApi, fmt, Empty, sortRows } from './ui.js';

const WINDOWS = [['3M', 3], ['6M', 6], ['1Y', 12]];
const SCOPES = [['all', 'All tracked'], ['portfolio', 'Portfolio'], ['watchlist', 'Watchlist']];
const KIND = { presentation: 'Investor presentation', transcript: 'Concall transcript', results: 'Results', order_win: 'Order-win filing',
  press_release: 'Press release', capacity: 'Capacity filing', acquisition: 'Acquisition filing', meet: 'Analyst meet' };
const PAGE = 25;

const setQuery = (page, params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '' && v !== false)).toString();
  history.replaceState(null, '', `#/${page}${q ? '?' + q : ''}`);
};
const codes = r => [r.nse, r.bse].filter(Boolean).join(' · ');
const months = n => n === 12 ? '1Y' : `${n}M`;

export function AiBanner({ children }) {
  return html`<div class="banner" role="note"><b>AI-extracted — may contain errors.</b> Verify against the source PDF: every figure links
    to its filing and page, with the sentence it came from. ${children}</div>`;
}

export function Trust({ v }) {
  if (v >= 1) return html`<span class="trust ok" title="the quote was found word for word in the PDF, with the number in it">✓ verbatim</span>`;
  if (v >= 0.5) return html`<span class="trust near" title="close match, or the number and the term sit on the same page (slide layouts)">≈ approximate</span>`;
  return html`<span class="trust bad" title="the quote could not be found in the PDF — not used in growth or history">✗ not found in PDF</span>`;
}

function Growth({ v }) {
  if (v == null) return html`<span class="muted">—</span>`;
  return html`<span class="${v > 0 ? 'up' : v < 0 ? 'down' : 'muted'} num nowrap">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(v >= 100 || v <= -100 ? 0 : 1)}%</span>`;
}

// History in the de-emphasis tone, the latest bar in the accent.
export function Spark({ hist, w = 92, h = 26, onClick }) {
  if (!hist || !hist.length) return html`<span class="muted">—</span>`;
  const max = Math.max(...hist.map(p => p.v)) || 1;
  const n = hist.length, gap = 2, bw = Math.min(10, (w - gap * (n - 1)) / n);
  const total = n * bw + (n - 1) * gap;
  return html`<button class="spark-btn" onClick=${onClick} title="Open order-book history" aria-label="Open order-book history">
    <svg class="spark" width=${total} height=${h} viewBox="0 0 ${total} ${h}">
      ${hist.map((p, i) => {
        const bh = Math.max(2, p.v / max * (h - 2));
        return html`<rect class=${i === n - 1 ? 'last' : ''} x=${i * (bw + gap)} y=${h - bh} width=${bw} height=${bh} rx="1.5">
          <title>${p.q}: ${fmt.cr(p.v)}</title></rect>`;
      })}
    </svg></button>`;
}

function useWidth(fallback = 600) {
  const ref = useRef();
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// Quarterly bars; click/keyboard selects a bar to show the sentence it came from.
function ObBars({ bars, selected, onSelect, height = 190 }) {
  const [box, boxW] = useWidth();
  const n = bars.length;
  const W = Math.max(boxW, n * 40), H = height, padT = 20, padB = 24, padX = 6;
  const max = Math.max(...bars.map(b => b.value_cr)) || 1;
  const y = v => padT + (1 - v / max) * (H - padT - padB);
  const band = (W - 2 * padX) / n, bw = Math.min(24, band - 2);
  const extreme = bars.reduce((k, b, i) => (b.value_cr > bars[k].value_cr ? i : k), 0);
  const every = Math.ceil(n / 12);
  return html`<div class="tablewrap" ref=${box}><svg class="mini obbars" viewBox="0 0 ${W} ${H}" width=${W} height=${H}
      role="img" aria-label="Order book by quarter">
    <line class="base" x1=${padX} x2=${W - padX} y1=${y(0)} y2=${y(0)} />
    ${bars.map((b, i) => {
      const cx = padX + band * i + band / 2, x = cx - bw / 2, top = y(b.value_cr), y0 = y(0);
      const r = Math.min(4, (y0 - top), bw / 2);
      const d = `M${x},${y0} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${y0} Z`;
      const label = n <= 8 || i === n - 1 || i === extreme || i === selected;
      return html`<g class="ob-bar ${i === selected ? 'sel' : ''} ${b.verified < 1 ? 'approx' : ''}" tabindex="0" role="button"
          aria-label=${`${b.quarter}: ${fmt.cr(b.value_cr)}`} onClick=${() => onSelect(i)}
          onKeyDown=${e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(i))}>
        <rect x=${cx - band / 2} y=${padT - 16} width=${band} height=${H - padT - padB + 16} fill="transparent" />
        <path class="bar" d=${d}><title>${b.quarter} · ${fmt.cr(b.value_cr)} as of ${fmt.date(b.as_of, true)}</title></path>
        ${label && html`<text class="val" x=${cx} y=${top - 5} text-anchor="middle">${fmt.cr(b.value_cr).replace(' Cr', '')}</text>`}
        ${(i % every === 0 || i === n - 1) && html`<text x=${cx} y=${H - 7} text-anchor="middle">${b.quarter.replace('FY', ' FY')}</text>`}
      </g>`;
    })}
  </svg></div>`;
}

function Quote({ p }) {
  if (!p) return null;
  return html`<div class="ob-quote">
    <div class="small ink2" style="font-weight:600">From the filing</div>
    <blockquote class="quote">“${p.quote || '—'}”</blockquote>
    <div class="row wrap small muted">
      <${Trust} v=${p.verified} />
      <span>${KIND[p.kind] || (p.method === 'insights' ? 'Earlier report' : 'Document')}${p.page ? ` · page ${p.page}` : ''}</span>
      ${p.filed_at ? html`<span>filed ${fmt.date(p.filed_at, true)}</span>` : null}
      ${p.link ? html`<a href=${p.link} target="_blank" rel="noopener">Open PDF ↗</a>` : null}
    </div></div>`;
}

function useEscape(onClose) {
  useEffect(() => {
    const on = e => e.key === 'Escape' && onClose();
    addEventListener('keydown', on);
    return () => removeEventListener('keydown', on);
  }, [onClose]);
}

export function ObOverlay({ sid, onClose }) {
  const { data } = useApi(`/orderbook/${encodeURIComponent(sid)}`, [sid]);
  const [sel, setSel] = useState(null);
  const close = useRef();
  useEscape(onClose);
  useEffect(() => { close.current && close.current.focus(); }, []);
  useEffect(() => { if (data && data.bars.length) setSel(data.bars.length - 1); }, [data]);
  const g = data ? data.growth : {};
  return html`<div class="modal-back" onClick=${e => e.target === e.currentTarget && onClose()}>
    <div class="modal card" role="dialog" aria-modal="true" aria-label=${`Order-book history ${sid}`}>
      <div class="row" style="align-items:flex-start">
        <div><h2 style="margin:0;font-size:16px">${data ? data.name : sid}</h2>
          <div class="small muted">${data ? codes(data) : ''} · order-book history</div></div>
        <div class="spacer"></div>
        <a class="small" href="#/stock/${encodeURIComponent(sid)}" onClick=${onClose}>Stock page</a>
        <button class="btn" ref=${close} onClick=${onClose} aria-label="Close">✕</button>
      </div>
      ${!data ? html`<div class="muted" style="padding:20px 0">Loading…</div>` : !data.bars.length
        ? html`<${Empty}>No verified order-book figure for this stock yet.<//>` : html`
        <div class="tiles" style="margin:12px 0">
          <div class="card tile"><div class="label">Latest order book</div><div class="value">${fmt.cr(data.latest.value_cr)}</div>
            <div class="sub">as of ${fmt.date(data.latest.as_of, true)}</div></div>
          ${WINDOWS.map(([l, m]) => html`<div class="card tile"><div class="label">${l} growth</div>
            <div class="value"><${Growth} v=${g[m] ? g[m].pct : null} /></div>
            <div class="sub">${g[m] ? `vs ${fmt.cr(g[m].from)} on ${fmt.date(g[m].from_as_of, true)}` : 'no figure that far back'}</div></div>`)}
          <div class="card tile"><div class="label">Book / revenue</div><div class="value">${data.book_to_revenue != null ? data.book_to_revenue.toFixed(2) + '×' : '—'}</div>
            <div class="sub">${data.revenue.label}</div></div>
          <div class="card tile"><div class="label">Data points</div><div class="value">${data.points.length}</div>
            <div class="sub">${data.bars.length} quarters</div></div>
        </div>
        <div class="card-title">Order book by quarter <span class="muted">₹ Cr · latest figure in each quarter · select a bar for its source</span></div>
        <${ObBars} bars=${data.bars} selected=${sel} onSelect=${setSel} />
        <${Quote} p=${sel != null ? data.bars[sel] : data.latest} />
        <details style="margin-top:12px"><summary class="small">Every figure found (${data.all.length})</summary>
          <div class="tablewrap"><table class="t small">
            <thead><tr><th>As of</th><th class="n">Order book</th><th>Scope</th><th>Check</th><th>Source</th><th>Quote</th></tr></thead>
            <tbody>${data.all.map(p => html`<tr class=${p.used ? '' : 'dim'}>
              <td class="nowrap">${fmt.date(p.as_of, true)}</td><td class="n">${fmt.cr(p.value_cr)}</td><td>${p.scope}</td>
              <td><${Trust} v=${p.verified} /></td>
              <td class="nowrap">${p.link ? html`<a href=${p.link} target="_blank" rel="noopener">${KIND[p.kind] || p.method}${p.page ? ` p.${p.page}` : ''} ↗</a>` : '—'}</td>
              <td><div class="clip" style="max-width:340px" title=${p.quote}>${p.quote}</div></td></tr>`)}</tbody>
          </table></div>
          <div class="small muted">Greyed rows are not plotted: another document gives a better-checked figure for the same date, it is a segment, or its quote was not found.</div>
        </details>`}
    </div></div>`;
}

// ── Order books ──────────────────────────────────────────────────────────
const OB_COLS = [['name', 'Company', false], ['g', 'Growth', true], ['order_book_cr', 'Order book', true],
  ['revenue_cr', 'Revenue (TTM)', true], ['book_to_revenue', 'Book / revenue', true], ['mcap_cr', 'Market cap', true],
  ['as_of', 'As of', false], ['updated', 'Last updated', false], ['hist', 'Order-book history', false]];

export function OrderBooksPage({ params, version }) {
  const [win, setWin] = useState(Number(params.win) || 12);
  const [scope, setScope] = useState(params.scope || 'all');
  const [minBook, setMinBook] = useState(params.min_book || '');
  const [minMcap, setMinMcap] = useState(params.min_mcap || '');
  const [q, setQ] = useState(params.q || '');
  const [sort, setSort] = useState({ key: 'g', dir: 'desc' });
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState(params.open || null);
  const { data, loading } = useApi(`/orderbooks?scope=${scope}`, [scope, version]);
  useEffect(() => setQuery('orderbooks', { win, scope, min_book: minBook, min_mcap: minMcap, q, open }), [win, scope, minBook, minMcap, q, open]);
  useEffect(() => setPage(0), [win, scope, minBook, minMcap, q, sort]);

  const rows = useMemo(() => {
    let r = (data ? data.rows : []).map(x => ({ ...x, g: x[`g${win}`] }));
    if (minBook) r = r.filter(x => x.order_book_cr >= Number(minBook));
    if (minMcap) r = r.filter(x => x.mcap_cr != null && x.mcap_cr >= Number(minMcap));
    if (q) { const s = q.toLowerCase(); r = r.filter(x => `${x.sid} ${x.name} ${x.nse || ''} ${x.bse || ''} ${x.sector || ''}`.toLowerCase().includes(s)); }
    return sortRows(r, sort.key === 'hist' ? 'points' : sort.key, sort.dir);
  }, [data, win, minBook, minMcap, q, sort]);
  const gainers = rows.filter(r => r.g != null).sort((a, b) => b.g - a.g).slice(0, 8);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  const th = ([key, label, num]) => html`<th class="sortable ${num ? 'n' : ''}" aria-sort=${sort.key === key ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
    onClick=${() => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}>
    ${key === 'g' ? `Growth (${months(win)})` : label}${sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}</th>`;

  return html`
    <div class="page-head"><h1>Order books</h1><span class="muted">tracked stocks · from exchange filings (NSE) and the PDFs already on disk</span></div>
    <${AiBanner}>Figures whose quote is not in the PDF are left out.<//>
    <div class="filters">
      <div class="seg" aria-label="growth window">${WINDOWS.map(([l, m]) => html`<button class=${win === m ? 'on' : ''} onClick=${() => setWin(m)}>${l}</button>`)}</div>
      <label class="small row">Min order book ₹ Cr <input class="input num" type="number" min="0" style="width:92px" value=${minBook} onInput=${e => setMinBook(e.target.value)} /></label>
      <label class="small row">Min market cap ₹ Cr <input class="input num" type="number" min="0" style="width:92px" value=${minMcap} onInput=${e => setMinMcap(e.target.value)} /></label>
      <div class="seg">${SCOPES.map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      <input class="input" placeholder="Search company, code, sector" value=${q} onInput=${e => setQ(e.target.value)} style="width:210px" />
    </div>

    ${gainers.length ? html`<div class="card" style="margin-bottom:12px">
      <h2>Top gainers <span class="muted">order-book growth, last ${months(win)}</span></h2>
      <div class="gainers">${gainers.map((r, i) => html`<button class="gcard" onClick=${() => setOpen(r.sid)}>
        <div class="row"><span class="muted small num">#${i + 1}</span><span class="spacer"></span><${Growth} v=${r.g} /></div>
        <div class="gname" title=${r.name}>${r.name}</div><div class="tiny muted">${r.nse || r.bse || r.sid}</div>
        <div class="row" style="align-items:flex-end;margin-top:6px"><${Spark} hist=${r.history} w=${70} h=${24} />
          <span class="spacer"></span><b class="num small">${fmt.cr(r.order_book_cr)}</b></div>
      </button>`)}</div></div>` : null}

    <div class="card" style="padding:0">
      <div class="tablewrap ${loading ? 'loading' : ''}">
        <table class="t">
          <thead><tr>${OB_COLS.map(th)}</tr></thead>
          <tbody>${shown.map(r => html`<tr>
            <td><a href="#/stock/${encodeURIComponent(r.sid)}" class="sym">${r.name}</a>
              <span class="cname">${codes(r)}${r.in_portfolio ? ' · held' : ''}</span></td>
            <td class="n"><${Growth} v=${r.g} /></td>
            <td class="n">${fmt.cr(r.order_book_cr)}${r.verified < 1 ? html`<span class="muted" title="approximate match in the PDF"> ≈</span>` : ''}
              ${r.orders_since_n ? html`<div class="small muted" title=${`last disclosed ${fmt.cr(r.order_book_cr)} + ${r.orders_since_n} order(s) won since ${fmt.date(r.as_of, true)} (before execution)`}>≈ ${fmt.cr(r.book_est_cr)} incl. +${fmt.cr(r.orders_since_cr)} since</div>` : null}</td>
            <td class="n" title=${r.revenue_label}>${fmt.cr(r.revenue_cr)}<div class="tiny muted">${r.revenue_label.split(' · ')[1] || ''}</div></td>
            <td class="n">${r.book_to_revenue != null ? r.book_to_revenue.toFixed(2) + '×' : '—'}</td>
            <td class="n">${fmt.cr(r.mcap_cr)}</td>
            <td class="nowrap">${fmt.date(r.as_of, true)}</td>
            <td class="nowrap muted">${fmt.date(r.updated, true)}</td>
            <td><div class="row" style="gap:8px"><${Spark} hist=${r.history} onClick=${() => setOpen(r.sid)} />
              <span class="small num">${fmt.cr(r.order_book_cr).replace(' Cr', '')}</span></div></td>
          </tr>`)}</tbody>
        </table>
        ${!rows.length && !loading ? html`<${Empty}>${data && data.rows.length ? 'No company matches these filters.' : 'No order-book figures yet.'}<//>` : null}
      </div>
      <div class="pager row small">
        <span class="muted">${data ? `${rows.length} of ${data.tracked} tracked stocks have an order book on record` : ''}${data && data.pending_docs ? ` · ${fmt.num(data.pending_docs)} documents still to read (within the daily LLM budget)` : ''}</span>
        <span class="spacer"></span>
        ${pages > 1 ? html`<button class="btn" disabled=${page === 0} onClick=${() => setPage(page - 1)}>‹ Prev</button>
          <span class="num">${page + 1} / ${pages}</span>
          <button class="btn" disabled=${page >= pages - 1} onClick=${() => setPage(page + 1)}>Next ›</button>` : null}
      </div>
    </div>
    ${open ? html`<${ObOverlay} sid=${open} onClose=${() => setOpen(null)} />` : null}`;
}

// ── Orders ───────────────────────────────────────────────────────────────
function OrderRows({ orders, showCompany }) {
  return html`<div class="tablewrap"><table class="t small">
    <thead><tr><th>Date</th>${showCompany ? html`<th>Company</th>` : null}<th>Customer</th><th>Order type</th><th class="n">Contract value</th>
      <th class="n">Duration</th><th class="n">Annual value</th><th class="n">% of revenue</th><th>Source</th></tr></thead>
    <tbody>${orders.map(o => html`<tr>
      <td class="nowrap">${fmt.date(o.date, true)}</td>
      ${showCompany ? html`<td><a class="sym" href="#/stock/${encodeURIComponent(o.sid)}">${o.nse || o.sid}</a><span class="cname">${o.name}</span></td>` : null}
      <td><div title=${o.quote}>${o.customer || html`<span class="muted">not named</span>`}</div>
        ${o.description ? html`<div class="tiny muted clip" style="max-width:300px" title=${o.description}>${o.description}</div>` : null}</td>
      <td>${o.order_type || html`<span class="muted">—</span>`}</td>
      <td class="n">${fmt.cr(o.value_cr)}</td>
      <td class="n">${o.duration_months ? `${Math.round(o.duration_months)} mo` : '—'}</td>
      <td class="n">${fmt.cr(o.annual_value_cr)}</td>
      <td class="n">${o.revenue_pct != null ? o.revenue_pct.toFixed(1) + '%' : '—'}</td>
      <td class="nowrap">${o.link ? html`<a href=${o.link} target="_blank" rel="noopener" title=${o.quote}>PDF${o.page ? ` p.${o.page}` : ''} ↗</a>` : '—'}
        <div class="tiny">${o.method === 'llm' ? html`<span class="badge" title="AI-extracted">AI</span>` : html`<span class="badge" title="value read from the NSE announcement text, no LLM">Filing text</span>`}
        ${o.verified < 1 ? html` <span class="muted" title="quote matched approximately">≈</span>` : ''}</div></td>
    </tr>`)}</tbody></table></div>`;
}

function OrderFeed({ orders, showCompany = true }) {
  if (!orders.length) return null;
  return html`<div class="evlist">${orders.map(o => html`<div class="ev" key=${o.id}>
    <div class="ev-when num">${fmt.date(o.date, true)}</div>
    <div>
      <div class="row wrap" style="gap:6px 10px;align-items:baseline">
        ${showCompany ? html`<a class="ev-stock" href="#/stock/${encodeURIComponent(o.sid)}">${o.nse || o.sid}</a>` : null}
        <span class="ev-title">${o.customer || 'Customer not named'}</span>
        ${o.value_cr != null ? html`<b class="num">${fmt.cr(o.value_cr)}</b>` : html`<span class="muted small">value not stated</span>`}
      </div>
      <div class="ev-meta">
        ${showCompany ? html`<span class="ink2">${o.name}</span>` : null}
        ${o.order_type ? html`<span class="badge">${o.order_type}</span>` : null}
        ${o.duration_months ? html`<span>${Math.round(o.duration_months)} months · ${fmt.cr(o.annual_value_cr)}/yr</span>` : null}
        ${o.revenue_pct != null ? html`<span>${o.revenue_pct.toFixed(1)}% of TTM revenue</span>` : null}
      </div>
      ${o.quote ? html`<div class="ev-detail">“${o.quote}”</div>` : null}
      <div class="ev-meta">
        ${o.method === 'llm' ? html`<span class="badge" title="AI-extracted">AI</span>` : html`<span class="badge" title="value read from the NSE announcement text, no LLM">Filing text</span>`}
        <${Trust} v=${o.verified} />
        ${o.link ? html`<a href=${o.link} target="_blank" rel="noopener">${KIND[o.kind] || 'Filing'}${o.page ? ` p.${o.page}` : ''} ↗</a>` : null}
        ${o.also_in && o.also_in.length ? html`<span title="the same order mentioned again later">also in ${o.also_in.length} later document${o.also_in.length > 1 ? 's' : ''}</span>` : null}
      </div>
    </div></div>`)}</div>`;
}

export function OrdersPage({ params, version }) {
  const [view, setView] = useState(params.view || 'company');
  const [win, setWin] = useState(Number(params.win) || 6);
  const [scope, setScope] = useState(params.scope || 'all');
  const [q, setQ] = useState(params.q || '');
  const [openSid, setOpenSid] = useState(null);
  const days = Math.round(win * 30.44);
  useEffect(() => setQuery('orders', { view, win, scope, q }), [view, win, scope, q]);
  const path = view === 'company' ? `/orders/companies?days=${days}&scope=${scope}` : `/orders?days=${days}&scope=${scope}`;
  const { data, loading } = useApi(path, [path, version]);
  const match = r => !q || `${r.sid} ${r.name} ${r.nse || ''} ${r.customer || ''}`.toLowerCase().includes(q.toLowerCase());
  const rows = (data ? data.rows : []).filter(match);
  return html`
    <div class="page-head"><h1>Orders</h1><span class="muted">individual orders from order-win filings, presentations and concalls of tracked stocks</span></div>
    <${AiBanner}>Orders marked “Filing text” come straight from the NSE announcement, without the LLM.<//>
    <div class="filters">
      <div class="seg">${[['company', 'By company'], ['feed', 'All orders']].map(([k, l]) => html`<button class=${view === k ? 'on' : ''} onClick=${() => setView(k)}>${l}</button>`)}</div>
      <div class="seg" aria-label="timeframe">${WINDOWS.map(([l, m]) => html`<button class=${win === m ? 'on' : ''} onClick=${() => setWin(m)}>${l}</button>`)}</div>
      <div class="seg">${SCOPES.map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      <input class="input" placeholder="Search company or customer" value=${q} onInput=${e => setQ(e.target.value)} style="width:210px" />
    </div>
    ${view === 'company' ? html`<div class="card" style="padding:0"><div class="tablewrap ${loading ? 'loading' : ''}"><table class="t">
        <thead><tr><th>Company</th><th class="n hide-narrow">Orders as % of revenue</th><th class="n hide-narrow">Orders</th><th class="n hide-narrow">Total order value</th>
          <th class="n hide-narrow">Revenue (TTM)</th><th class="n hide-narrow">Market cap</th><th class="hide-narrow">Latest</th><th></th></tr></thead>
        <tbody>${rows.map(r => html`
          <tr class="click" onClick=${() => setOpenSid(openSid === r.sid ? null : r.sid)} aria-expanded=${openSid === r.sid}>
            <td><span class="sym">${r.name}</span><span class="cname">${codes(r)}</span>
              <div class="only-narrow small" style="margin-top:2px"><b>${r.pct_of_revenue != null ? r.pct_of_revenue.toFixed(1) + '% of revenue' : '—'}</b>
                <span class="muted">${` · ${r.count} order${r.count > 1 ? 's' : ''} · ${fmt.cr(r.total_cr)} · latest ${fmt.date(r.latest, true)}`}</span></div></td>
            <td class="n hide-narrow"><b>${r.pct_of_revenue != null ? r.pct_of_revenue.toFixed(1) + '%' : '—'}</b></td>
            <td class="n hide-narrow">${r.count}${r.valued < r.count ? html`<div class="tiny muted">${r.count - r.valued} without value</div>` : ''}</td>
            <td class="n hide-narrow">${fmt.cr(r.total_cr)}</td>
            <td class="n hide-narrow" title=${r.revenue_label}>${fmt.cr(r.revenue_cr)}</td>
            <td class="n hide-narrow">${fmt.cr(r.mcap_cr)}</td>
            <td class="nowrap hide-narrow">${fmt.date(r.latest, true)}</td>
            <td class="muted">${openSid === r.sid ? '▴' : '▾'}</td></tr>
          ${openSid === r.sid ? html`<tr class="expand"><td colspan="8"><div class="only-wide"><${OrderRows} orders=${r.orders} /></div>
            <div class="only-narrow"><${OrderFeed} orders=${r.orders} showCompany=${false} /></div></td></tr>` : null}`)}</tbody>
      </table>
      ${!rows.length && !loading ? html`<${Empty}>No dated orders in the last ${months(win)}. They arrive with NSE order-win filings and from presentations as documents are read.<//>` : null}
      </div></div>`
    : html`<div class="card">
        <div class=${loading ? 'loading' : ''}>${rows.length ? html`<${OrderFeed} orders=${rows} />`
          : !loading ? html`<${Empty}>No orders in the last ${months(win)}.<//>` : null}</div></div>`}`;
}

// ── stock page card ──────────────────────────────────────────────────────
export function StockOrderBook({ sid }) {
  const ob = useApi(`/orderbook/${encodeURIComponent(sid)}`, [sid]);
  const orders = useApi(`/orders?days=3650&sid=${encodeURIComponent(sid)}&undated=true`, [sid]);
  const [sel, setSel] = useState(null);
  const d = ob.data;
  useEffect(() => { if (d && d.bars.length) setSel(d.bars.length - 1); }, [d]);
  return html`<div class="stack">
    <${AiBanner} />
    <div class="card"><h2>Order book <span class="muted">from filings and presentations · ₹ Cr</span></h2>
      ${!d ? html`<div class="muted small">Loading…</div>` : !d.bars.length ? html`<${Empty}>No verified order-book figure yet. It appears when a presentation, concall or press release stating one is read.<//>` : html`
        <div class="row wrap small" style="gap:16px;margin-bottom:6px">
          <span>Latest <b>${fmt.cr(d.latest.value_cr)}</b> as of ${fmt.date(d.latest.as_of, true)}</span>
          ${WINDOWS.map(([l, m]) => html`<span>${l} <${Growth} v=${d.growth[m] ? d.growth[m].pct : null} /></span>`)}
          <span>Book / revenue <b>${d.book_to_revenue != null ? d.book_to_revenue.toFixed(2) + '×' : '—'}</b> <span class="muted">(${d.revenue.label})</span></span>
        </div>
        <${ObBars} bars=${d.bars} selected=${sel} onSelect=${setSel} height=${170} />
        <${Quote} p=${sel != null ? d.bars[sel] : d.latest} />`}
    </div>
    <div class="card"><h2>Orders <span class="muted">named contracts found in filings</span></h2>
      ${orders.data && orders.data.rows.length ? html`<${OrderFeed} orders=${orders.data.rows} showCompany=${false} />` : html`<${Empty}>No individual orders found yet.<//>`}
    </div>
  </div>`;
}
