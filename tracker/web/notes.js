// Company notes: one row per company; it opens into the last four quarters' notes (oldest → newest)
// with the latest view as the last column. Also used on the stock page.
import { html, useState, useEffect, useMemo, useRef } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { useApi, fmt, Empty } from './ui.js';

const SCOPES = [['all', 'All tracked'], ['portfolio', 'Portfolio'], ['watchlist', 'Watchlist']];
const SOURCE = { concall: 'Concall', presentation: 'Presentation', reports: 'Earlier reports' };
const STATUS = {
  on_track: ['up', 'on track'], done: ['up', 'done'], delivered: ['up', 'delivered'], ahead: ['up', 'ahead'],
  delayed: ['down', 'delayed'], at_risk: ['down', 'at risk'], missed: ['down', 'missed'], behind: ['down', 'behind'],
  partly: ['ink2', 'partly'], changed: ['ink2', 'changed'], pending: ['muted', 'pending'], unclear: ['muted', 'unclear'],
};
const Status = ({ s }) => { const [c, l] = STATUS[s] || ['muted', s || '—']; return html`<span class="stat ${c}">${l}</span>`; };
const list = v => (Array.isArray(v) ? v : v ? [v] : []);
const txt = v => (typeof v === 'string' ? v : v && typeof v === 'object' ? (v.what || v.trigger || v.text || Object.values(v).join(' — ')) : String(v ?? ''));

function Section({ label, items, render }) {
  const xs = list(items).filter(Boolean);
  if (!xs.length) return null;
  return html`<div class="qsec"><div class="qlabel">${label}</div>
    <ul class="clean">${xs.map(x => html`<li>${render ? render(x) : txt(x)}</li>`)}</ul></div>`;
}

function QuarterCard({ q }) {
  const n = q.note || {};
  return html`<div class="qcard">
    <div class="row" style="margin-bottom:6px"><b>${q.period}</b><span class="badge">${SOURCE[q.source] || q.source}</span>
      <span class="spacer"></span>${q.url ? html`<a class="small" href=${q.url} target="_blank" rel="noopener">source ↗</a>` : null}</div>
    <${Section} label="Quarter" items=${n.business} />
    <${Section} label="Guidance" items=${n.guidance} />
    <${Section} label="Commitments" items=${n.commitments} render=${c => html`${txt(c)}${c && c.by_when ? html` <span class="muted">· by ${c.by_when}</span>` : ''}`} />
    <${Section} label="Triggers" items=${n.triggers} render=${t => html`${txt(t)}${t && t.expected_by ? html` <span class="muted">· ${t.expected_by}</span>` : ''}`} />
    <${Section} label="Tailwinds & policy" items=${[...list(n.tailwinds), ...list(n.policy)]} />
    <${Section} label="Headwinds & risks" items=${[...list(n.headwinds), ...list(n.risks)]} />
    ${n.tone ? html`<div class="tiny muted" style="margin-top:4px">Tone: ${n.tone}</div>` : null}
  </div>`;
}

function ViewCard({ v, updated, delivery }) {
  if (!v) return html`<div class="qcard vcard"><div class="muted small">View not written yet — it is written from the notes when the daily LLM budget allows.</div></div>`;
  return html`<div class="qcard vcard">
    <div class="row" style="margin-bottom:6px"><b>Latest view</b><span class="spacer"></span><span class="tiny muted">${updated ? fmt.date(updated, true) : ''}</span></div>
    ${v.one_line ? html`<p class="vline">${v.one_line}</p>` : null}
    ${v.what_it_does ? html`<div class="small ink2">${v.what_it_does}${v.sector ? ` · ${v.sector}` : ''}</div>` : null}
    ${v.story ? html`<div class="qsec"><div class="qlabel">Story — when it plays out</div><div class="small">${v.story}</div></div>` : null}
    <${Section} label="Triggers" items=${v.triggers} render=${t => html`${txt(t)}${t && t.expected_by ? html` <span class="muted">· ${t.expected_by}</span>` : ''}${t && t.status ? html` <${Status} s=${t.status} />` : ''}`} />
    <${Section} label="Watch next quarter" items=${v.watch} />
    ${v.delivery || (delivery && Object.values(delivery).some(Boolean)) ? html`<div class="qsec"><div class="qlabel">Delivery record</div><div class="small">${v.delivery || ''}
      ${delivery && Object.values(delivery).some(Boolean) ? html`<div class="tiny muted">${['delivered', 'partly', 'missed', 'pending'].filter(k => delivery[k]).map(k => `${delivery[k]} ${k}`).join(' · ')}</div>` : null}</div></div>` : null}
    ${v.trend ? html`<div class="qsec"><div class="qlabel">Trend</div><div class="small">${v.trend}</div></div>` : null}
    <${Section} label="Sector drivers" items=${v.sector_drivers} />
    <${Section} label="Risks" items=${v.risks} />
  </div>`;
}

export function CompanyNotes({ sid, compact, version }) {
  const { data, loading } = useApi(`/notes/company/${encodeURIComponent(sid)}`, [sid, version]);
  const grid = useRef();
  // open at the right end: the newest quarters and the latest view first; older quarters scroll in from the left
  useEffect(() => { if (grid.current) grid.current.scrollLeft = grid.current.scrollWidth; }, [data]);
  if (!data) return html`<div class="muted small">${loading ? 'Loading…' : ''}</div>`;
  const qs = [...data.quarters].reverse();         // oldest → newest, then the view
  if (!qs.length && !data.view) {
    return html`<${Empty}>No notes for ${sid} yet. They are condensed from its earlier concall reports (if any), and written from each new concall transcript or presentation.<//>`;
  }
  return html`<div>
    <div class="qgrid" ref=${grid}>${qs.map(q => html`<${QuarterCard} q=${q} />`)}<${ViewCard} v=${data.view} updated=${data.view_updated} delivery=${data.delivery} /></div>
    ${!compact && data.checks.length ? html`<details style="margin-top:10px"><summary class="small">Commitments checked (${data.checks.length})</summary>
      <div class="tablewrap"><table class="t small"><thead><tr><th>Commitment</th><th>Said</th><th>Checked</th><th>Status</th><th>Evidence</th></tr></thead>
      <tbody>${data.checks.map(c => html`<tr><td>${c.commitment}</td><td class="nowrap">${c.said_in || '—'}</td><td class="nowrap">${c.checked_in || '—'}</td>
        <td><${Status} s=${c.status} /></td><td>${c.evidence}${c.url ? html` <a href=${c.url} target="_blank" rel="noopener">${c.page ? `p.${c.page}` : 'source'} ↗</a>` : ''}</td></tr>`)}</tbody></table></div></details>` : null}
    ${!compact && data.mentions.length ? html`<div class="small qmentions" style="margin-top:8px"><b>Your notes:</b> ${data.mentions.map(m => html`<span class="chip">${fmt.date(m.d)} · ${m.stance} · ${m.title}</span> `)}</div>` : null}
    <div class="tiny muted" style="margin-top:6px">AI-condensed from filings and earlier reports — open the sources to check.</div>
  </div>`;
}

export function CompanyNotesPage({ params, version }) {
  const [scope, setScope] = useState(params.scope || 'portfolio');
  const [q, setQ] = useState(params.q || '');
  const [only, setOnly] = useState(params.only !== '0');
  const [open, setOpen] = useState(params.open || null);
  const { data, loading } = useApi(`/notes/companies?scope=${scope}`, [scope, version]);
  useEffect(() => {
    const p = new URLSearchParams(Object.entries({ tab: 'companies', scope, q, only: only ? '' : '0', open }).filter(([, v]) => v)).toString();
    history.replaceState(null, '', `#/notes?${p}`);
  }, [scope, q, only, open]);
  const rows = useMemo(() => {
    let r = data ? data.rows : [];
    if (only) r = r.filter(x => x.quarters || x.one_line);
    if (q) { const s = q.toLowerCase(); r = r.filter(x => `${x.sid} ${x.name} ${x.sector || ''} ${x.one_line || ''}`.toLowerCase().includes(s)); }
    return r;
  }, [data, only, q]);
  return html`
    <div class="filters">
      <div class="seg">${SCOPES.map(([k, l]) => html`<button class=${scope === k ? 'on' : ''} onClick=${() => setScope(k)}>${l}</button>`)}</div>
      <label class="small row"><input type="checkbox" checked=${only} onChange=${e => setOnly(e.target.checked)} /> only companies with notes</label>
      <input class="input" placeholder="Search company, sector, story" value=${q} onInput=${e => setQ(e.target.value)} style="width:220px" />
      ${data && data.pending_backfill ? html`<span class="small muted">${data.pending_backfill} more being condensed from earlier reports (daily LLM budget)</span>` : null}
    </div>
    <div class="card" style="padding:0"><div class=${loading ? 'loading' : ''}>
      <table class="t notes-t">
        <thead><tr><th>Company</th><th class="hide-narrow">Latest view</th><th class="hide-narrow">Next trigger</th><th class="hide-narrow">Delivery</th><th class="hide-narrow">Notes</th><th></th></tr></thead>
        <tbody>${rows.map(r => html`
          <tr class="click" onClick=${() => setOpen(open === r.sid ? null : r.sid)} aria-expanded=${open === r.sid}>
            <td><span class="sym">${r.sid}</span>${r.held ? html` <span class="badge">held</span>` : ''}<span class="cname" title=${r.name}>${r.name}</span>
              <span class="tiny muted">${r.sector && r.sector !== 'Unknown' ? r.sector : ''}</span>
              <div class="only-narrow small" style="margin-top:4px">${r.one_line || html`<span class="muted">No notes yet</span>`}</div>
              ${r.next_trigger ? html`<div class="tiny muted only-narrow">Next: ${r.next_trigger.trigger} · ${r.next_trigger.expected_by || '?'}</div>` : null}</td>
            <td class="hide-narrow"><div class="small">${r.one_line || html`<span class="muted">—</span>`}</div></td>
            <td class="hide-narrow small">${r.next_trigger ? html`${r.next_trigger.trigger}<div class="tiny muted">${r.next_trigger.expected_by || 'timing not given'} · <${Status} s=${r.next_trigger.status} /></div>` : html`<span class="muted">—</span>`}</td>
            <td class="hide-narrow small" title="commitments delivered · partly · missed">${r.delivered + r.partly + r.missed ? html`<span class="up">${r.delivered} ✓</span> <span class="ink2">${r.partly} ½</span> <span class="down">${r.missed} ✗</span>` : html`<span class="muted">—</span>`}</td>
            <td class="hide-narrow small">${r.quarters ? html`${r.last_period}<div class="tiny muted">${r.quarters} quarters · ${SOURCE[r.last_source] || r.last_source}</div>` : html`<span class="muted">none</span>`}</td>
            <td class="muted">${open === r.sid ? '▴' : '▾'}</td></tr>
          ${open === r.sid ? html`<tr class="expand"><td colspan="6"><${CompanyNotes} sid=${r.sid} version=${version} />
            <div class="small" style="margin-top:6px"><a href="#/stock/${encodeURIComponent(r.sid)}">Open ${r.sid} →</a></div></td></tr>` : null}`)}</tbody>
      </table>
      ${!rows.length && !loading ? html`<${Empty}>${only ? 'No company has notes in this view yet — untick "only companies with notes" to see them all.' : 'No companies.'}<//>` : null}
    </div></div>`;
}
