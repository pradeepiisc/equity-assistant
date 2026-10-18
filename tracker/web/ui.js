// Shared formatting, data hooks and small components.
import { html, useState, useEffect, useRef, useCallback } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';

export { html };

// ── API ──────────────────────────────────────────────────────────────────
export async function api(path, opts) {
  const r = await fetch('/api/t' + path, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}
export const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

// Keeps the previous data while refetching (no skeleton flash).
export function useApi(path, deps = []) {
  const [state, set] = useState({ data: null, error: null, loading: true });
  const seq = useRef(0);
  const load = useCallback(() => {
    if (!path) return;
    const n = ++seq.current;
    set(s => ({ ...s, loading: true }));
    api(path).then(
      data => n === seq.current && set({ data, error: null, loading: false }),
      error => n === seq.current && set(s => ({ ...s, error, loading: false })),
    );
  }, [path]);
  useEffect(load, [load, ...deps]);
  return { ...state, reload: load };
}

// ── formatting ───────────────────────────────────────────────────────────
const IN0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const IN2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const fmt = {
  inr: v => v == null ? '—' : (v < 0 ? '−₹' : '₹') + IN0.format(Math.abs(Math.round(v))),
  compact(v) {
    if (v == null) return '—';
    const a = Math.abs(v), s = v < 0 ? '−' : '';
    if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1)} L`;
    return `${s}₹${IN0.format(Math.round(a))}`;
  },
  price: v => v == null ? '—' : '₹' + IN2.format(v),
  pct(v, digits = 1) {
    if (v == null || Number.isNaN(v)) return '—';
    const d = Math.abs(v) >= 100 ? 0 : digits;
    return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}%`;
  },
  cr(v) {
    if (v == null) return '—';
    if (v >= 100) return `₹${IN0.format(Math.round(v))} Cr`;
    if (v >= 1) return `₹${v.toFixed(1)} Cr`;
    return `₹${Math.round(v * 100)} L`;
  },
  num: v => v == null ? '—' : IN0.format(v),
  date(s, withYear) {
    if (!s) return '—';
    const [y, m, d] = s.slice(0, 10).split('-').map(Number);
    const thisYear = new Date().getFullYear();
    return `${d} ${MON[m - 1]}${withYear || y !== thisYear ? ' ' + y : ''}`;
  },
  time: ts => (ts && ts.length > 16 && ts.slice(11, 16) !== '00:00') ? ts.slice(11, 16) : '',
  ago(ts) {
    if (!ts) return '—';
    const mins = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
    return `${Math.round(mins / 1440)}d ago`;
  },
};

// ── event semantics ──────────────────────────────────────────────────────
const POS = new Set(['cross_dma40_up', 'cross_dma200_up', 'golden_cross', 'phase_bullish', 'high_52w', 'order_win',
  'capacity', 'investor_entry', 'investor_up', 'promoters_up', 'fiis_up', 'diis_up', 'note_target_hit']);
const NEG = new Set(['cross_dma40_down', 'cross_dma200_down', 'death_cross', 'phase_bearish', 'low_52w', 'pledge_reg31',
  'pledge_reason', 'pledge', 'auditor_resignation', 'kmp_resignation', 'litigation', 'investor_exit', 'investor_down',
  'promoters_down', 'fiis_down', 'diis_down', 'note_stop_hit']);

export function polarity(e) {
  const t = e.type || '';
  const d = (e.data && typeof e.data === 'object') ? e.data : {};
  if (t === 'credit_rating') return d.direction === 'upgrade' ? 'pos' : d.direction === 'downgrade' ? 'neg' : 'neu';
  if (t.endsWith('_deal_buy')) return ['ace', 'promoter', 'fii', 'dii', 'fund'].includes(d.client_type) ? 'pos' : 'neu';
  if (t.endsWith('_deal_sell')) return ['ace', 'promoter', 'fii', 'dii', 'fund'].includes(d.client_type) ? 'neg' : 'neu';
  if (t === 'volume_spike') return (d.chg_pct || 0) >= 0 ? 'pos' : 'neg';
  if (POS.has(t)) return 'pos';
  if (NEG.has(t)) return 'neg';
  return 'neu';
}

export const KIND_LABEL = {
  technical: 'Technical', filing: 'Filing', deal: 'Deal', sast: 'SAST', pledge: 'Pledge', insider: 'Insider',
  shareholding: 'Holding', note: 'Note', system: 'System',
};
export const SEV_LABEL = { high: 'High', medium: 'Med', low: 'Low', info: 'Info' };
export const PHASES = ['Strong Bullish', 'Bullish Pullback', 'Neutral', 'Bearish Rally', 'Bearish', 'No data'];
export const PHASE_CLS = { 'Strong Bullish': 'sb', 'Bullish Pullback': 'bp', 'Neutral': 'n', 'Bearish Rally': 'br', 'Bearish': 'b', 'No data': 'nd' };

// ── components ───────────────────────────────────────────────────────────
export const Sev = ({ s }) => html`<span class="sev ${s}" title="${s} importance">${SEV_LABEL[s] || s}</span>`;

export function Pol({ e }) {
  const p = polarity(e);
  if (p === 'neu') return null;
  const glyph = p === 'pos' ? '▲' : '▼';
  const label = p === 'pos' ? 'Positive' : p === 'neg' ? 'Negative' : 'Neutral';
  return html`<span class="pol ${p}" title=${label} aria-label=${label}>${glyph}</span>`;
}

export const Phase = ({ p }) => html`<span class="phase" data-p=${p || 'No data'}><i></i>${p || 'No data'}</span>`;

export function Delta({ v, digits = 1 }) {
  if (v == null) return html`<span class="muted">—</span>`;
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'muted';
  const g = v > 0 ? '▲ ' : v < 0 ? '▼ ' : '';
  return html`<span class="${cls} num nowrap">${g}${fmt.pct(v, digits).replace(/^[+−]/, '')}</span>`;
}

export const Tile = ({ label, value, sub, hero, children }) => html`
  <div class="card tile">
    <div class="label">${label}</div>
    <div class="value ${hero ? 'hero' : ''}">${value}</div>
    ${sub && html`<div class="sub">${sub}</div>`}
    ${children}
  </div>`;

export const Empty = ({ children }) => html`<div class="empty">${children}</div>`;

export function go(path) { location.hash = '#/' + path; }

export function StockLink({ sid, name }) {
  if (!sid) return html`<span class="badge">Not tracked</span>`;
  return html`<a class="ev-stock" href="#/stock/${encodeURIComponent(sid)}" title=${name || sid}>${sid}</a>`;
}

export function EventItem({ e, showStock = true }) {
  const unread = e.alert_id && !e.read_at;
  return html`
    <div class="ev ${unread ? 'unread' : ''}">
      <div class="ev-when">
        <div class="num">${fmt.date(e.ts)}</div>
        <div class="num">${fmt.time(e.ts)}</div>
      </div>
      <div>
        <div class="row" style="gap:8px;align-items:baseline">
          <${Pol} e=${e} />
          <span class="ev-title">${e.title}</span>
        </div>
        ${e.detail && html`<div class="ev-detail">${e.detail}</div>`}
        <div class="ev-meta">
          ${showStock && html`<${StockLink} sid=${e.sid} name=${e.name} />`}
          ${showStock && e.name && html`<span class="ink2">${e.name}</span>`}
          <${Sev} s=${e.severity} />
          <span class="badge">${KIND_LABEL[e.kind] || e.kind}</span>
          ${e.value_cr != null && html`<span class="num">${fmt.cr(e.value_cr)}</span>`}
          ${e.url && html`<a href=${e.url} target="_blank" rel="noopener">Source ↗</a>`}
          ${e.source && html`<span>${e.source}</span>`}
        </div>
      </div>
    </div>`;
}

export function EventList({ rows, showStock = true, empty = 'Nothing here yet.' }) {
  if (!rows || !rows.length) return html`<${Empty}>${empty}<//>`;
  const out = [];
  let day = null;
  for (const e of rows) {
    const d = (e.ts || e.d || '').slice(0, 10);
    if (d !== day) {
      day = d;
      out.push(html`<div class="daysep">${fmt.date(d, true)}</div>`);
    }
    out.push(html`<${EventItem} e=${e} showStock=${showStock} key=${e.id} />`);
  }
  return html`<div class="evlist">${out}</div>`;
}

export function Markdown({ text }) {
  const ref = useRef();
  useEffect(() => {
    if (!ref.current) return;
    const raw = window.marked ? window.marked.parse(text || '') : (text || '');
    ref.current.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(raw) : '';
    if (!window.DOMPurify) ref.current.textContent = text || '';
  }, [text]);
  return html`<div class="md" ref=${ref}></div>`;
}

let toastTimer;
export function toast(msg) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 4000);
}

export function sortRows(rows, key, dir) {
  const m = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * m;
  });
}
