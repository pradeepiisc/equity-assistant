// Status page: background polling, feed cadence, tick history, job log.
import { html, useState } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { post, useApi, fmt, Tile, Empty, toast } from './ui.js';

const FEED_LABEL = {
  announcements: 'Announcements', sast29: 'SAST Reg 29', sast31: 'SAST Reg 31 (pledges)', encumbrance: 'Encumbrance reasons',
  insider: 'Insider trading (PIT)', shareholding: 'Shareholding pattern', board: 'Board meetings', results: 'Results (integrated filing)',
  corp_action: 'Corporate actions',
};
const SOURCE = { server: 'dashboard', launchd: 'LaunchAgent', manual: 'Refresh now', cli: 'command line' };

const until = ts => {
  if (!ts) return '—';
  const mins = Math.round((new Date(ts).getTime() - Date.now()) / 60000);
  if (mins <= 0) return 'due now';
  return mins < 60 ? `in ${mins}m` : `in ${Math.round(mins / 60)}h`;
};

export function StatusPage({ version }) {
  const { data, loading, reload } = useApi('/status', [version]);
  const [busy, setBusy] = useState(false);
  if (!data) return html`<div class="muted">Loading…</div>`;
  const sc = data.scheduler, srv = sc.server, ld = sc.launchd, last = sc.tick_last;

  async function agent(action) {
    if (action === 'uninstall' && !confirm('Stop background polling when the dashboard is closed?')) return;
    setBusy(true);
    try {
      const r = await post(`/launchd/${action}`);
      toast(r.ok ? (action === 'install' ? 'LaunchAgent installed' : 'LaunchAgent removed') : `Failed: ${r.output}`);
    } catch (e) { toast(`Failed: ${e.message}`); }
    setBusy(false);
    reload();
  }

  return html`
    <div class=${loading ? 'loading' : ''}>
      <div class="page-head"><h1>Status</h1><span class="muted">polling, data freshness and job log · no LLM in this path</span></div>
      <div class="tiles">
        <${Tile} label="Dashboard polling" value=${srv.enabled ? html`<span class="up">● On</span>` : html`<span class="muted">○ Off</span>`}
          sub=${srv.enabled ? (srv.running ? 'ticking now…' : `every ${srv.every_min} min · next ${until(srv.next_at)}`) : 'started with --no-poll'} />
        <${Tile} label="Background polling (LaunchAgent)"
          value=${ld.loaded ? (ld.silent ? html`<span class="down">▲ No ticks</span>` : html`<span class="up">● On</span>`)
            : ld.installed ? html`<span class="down">● Not loaded</span>` : html`<span class="muted">○ Off</span>`}
          sub=${ld.loaded ? `every ${ld.interval_min} min · last tick ${ld.last_tick ? fmt.ago(ld.last_tick) : 'never'}` : 'polls while the dashboard is closed'} />
        <${Tile} label="Last tick" value=${last ? fmt.ago(last.finished) : 'never'}
          sub=${last ? `${SOURCE[last.source] || last.source} · ${last.secs}s · ${last.ok ? 'ok' : 'failed'}` : ''} />
        <${Tile} label="Running now" value=${sc.lock ? 'Tick' : '—'}
          sub=${sc.lock ? `${SOURCE[sc.lock.source] || sc.lock.source} since ${fmt.time(sc.lock.started)}` : 'idle'} />
      </div>

      <div class="grid g-main-side" style="margin-top:16px">
        <div class="stack">
          <div class="card"><h2>NSE feeds <span class="muted">announcements every ${sc.schedule.announcements_every_min} min on weekdays ${sc.schedule.market_from}–${sc.schedule.market_to}; the rest every ${sc.schedule.feeds_every_min} min, ${sc.schedule.feeds_from}–${sc.schedule.feeds_to}</span></h2>
            <div class="tablewrap"><table class="t small">
              <thead><tr><th>Feed</th><th class="n">Cadence now</th><th>Last polled</th><th>Next</th></tr></thead>
              <tbody>${sc.feeds.map(f => html`<tr><td>${FEED_LABEL[f.feed] || f.feed}</td>
                <td class="n">${f.every_min ? `${f.every_min} min` : html`<span class="muted">paused</span>`}</td>
                <td class="nowrap">${f.last_polled ? `${fmt.date(f.last_polled)} ${fmt.time(f.last_polled)} · ${fmt.ago(f.last_polled)}` : 'never'}</td>
                <td class="nowrap">${f.every_min ? until(f.next_due) : html`<span class="muted">at ${sc.schedule.feeds_from}</span>`}</td></tr>`)}</tbody>
            </table></div>
          </div>
          <div class="card"><h2>Recent ticks</h2>
            ${sc.tick_history.length ? html`<div class="tablewrap" style="max-height:340px"><table class="t small">
              <thead><tr><th>Finished</th><th>By</th><th class="n">Time</th><th>Ran</th></tr></thead>
              <tbody>${sc.tick_history.map(t => html`<tr><td class="nowrap">${fmt.date(t.finished)} ${fmt.time(t.finished)}</td>
                <td>${SOURCE[t.source] || t.source}</td><td class="n">${t.secs}s</td>
                <td class=${t.ok ? '' : 'err'}>${t.ran.length ? t.ran.join(' · ') : html`<span class="muted">nothing due</span>`}</td></tr>`)}</tbody>
            </table></div>` : html`<${Empty}>No ticks recorded yet.<//>`}
          </div>
          <div class="card"><h2>Job log</h2>
            <div class="tablewrap" style="max-height:380px"><table class="t small">
              <thead><tr><th>Started</th><th>Job</th><th></th><th>Result</th></tr></thead>
              <tbody>${data.runs.map(r => html`<tr><td class="nowrap">${fmt.date(r.started_at)} ${fmt.time(r.started_at)}</td><td>${r.job}</td>
                <td>${r.ok ? html`<span class="up" title="ok">✓</span>` : html`<span class="down" title="failed">✗</span>`}</td>
                <td><div class="clip" style="max-width:520px" title=${r.message}>${r.message}</div></td></tr>`)}</tbody>
            </table></div>
          </div>
        </div>
        <div class="stack">
          ${ld.silent ? html`<div class="card warn-card"><h2>LaunchAgent is not ticking</h2>
            <p class="small" style="margin:0">It is loaded (${ld.runs || 0} runs) but no tick has finished. Usually macOS is waiting for permission:
              allow <b>python3.11</b> to access your Documents folder (a prompt, or System Settings → Privacy & Security → Files & Folders /
              Full Disk Access). Until then the dashboard's own polling keeps data fresh while it is open.</p></div>` : null}
          <div class="card"><h2>Background polling</h2>
            <p class="small ink2" style="margin-top:0">The dashboard polls while it is open. The LaunchAgent keeps polling when it is closed
              (and catches up after the Mac wakes). Both use one lock, and each job runs only when due, so running both never doubles the requests to NSE.</p>
            <div class="row wrap">
              ${ld.installed
                ? html`<button class="btn" disabled=${busy} onClick=${() => agent('uninstall')}>Uninstall LaunchAgent</button>`
                : html`<button class="btn primary" disabled=${busy} onClick=${() => agent('install')}>Install LaunchAgent</button>`}
              <span class="muted small">${ld.label}</span>
            </div>
            <div class="small muted" style="margin-top:10px">From a terminal:</div>
            <pre class="code">bash scripts/tracker_schedule.sh install
bash scripts/tracker_schedule.sh status
bash scripts/tracker_schedule.sh logs
bash scripts/tracker_schedule.sh uninstall</pre>
            <div class="small muted">Cadence and windows: <code>schedule:</code> in tracker.yaml.</div>
          </div>
          <${LlmCard} llm=${data.llm} />
          <${BseCard} b=${data.bse} />
          <div class="card"><h2>Data</h2>
            <dl class="kv">
              <dt>Tracked stocks</dt><dd>${fmt.num(data.counts.stocks)}</dd>
              <dt>Latest prices</dt><dd>${fmt.date(data.latest_day, true)}</dd>
              <dt>Price sessions</dt><dd>${fmt.num(data.counts.price_days)}</dd>
              <dt>Events</dt><dd>${fmt.num(data.counts.events)}</dd>
              <dt>Feed items (45 days)</dt><dd>${fmt.num(data.counts.feed_items)}</dd>
            </dl>
          </div>
        </div>
      </div>
    </div>`;
}

const DOC_STATUS = [['prepared', 'Waiting for LLM'], ['queued', 'Not read yet'], ['extracted', 'Extracted'],
  ['irrelevant', 'No relevant pages'], ['duplicate', 'Same PDF as another'], ['no_text', 'Scanned (no text)'], ['failed', 'Failed']];

function LlmCard({ llm }) {
  if (!llm) return null;
  const t = llm.today, pct = Math.min(100, Math.round(t.tokens / llm.budget * 100));
  const bfCap = Math.round(llm.budget * llm.backfill_share);
  const q = llm.queue || {};
  const n = (scope, st) => (q[scope] && q[scope][st] ? q[scope][st].n : 0);
  return html`<div class="card"><h2>LLM usage <span class="muted">${llm.client} · document extraction only</span></h2>
    <div class="row" style="justify-content:space-between;align-items:baseline">
      <div><span style="font-size:22px;font-weight:600" class="num">${fmt.num(t.tokens)}</span> <span class="muted small">of ${fmt.num(llm.budget)} tokens today</span></div>
      <div class="num">≈ ₹${t.inr.toFixed(0)}</div>
    </div>
    <div class="meter" role="meter" aria-valuenow=${pct} aria-valuemin="0" aria-valuemax="100" aria-label="daily token budget used">
      <i style=${`width:${pct}%`}></i><b style=${`left:${llm.backfill_share * 100}%`} title="back-fill cap"></b></div>
    <div class="small muted">${`${t.calls} calls · back-fill ${fmt.num(t.backfill_tokens)} / ${fmt.num(bfCap)} (its cap) · `}
      ${t.errors ? html`<span class="err">${t.errors} errors</span>` : 'no errors'}${' · '}${llm.enabled ? 'on' : html`<b>off</b> (tracker.yaml)`}</div>
    <table class="t small" style="margin-top:10px"><thead><tr><th>Documents</th><th class="n">New filings</th><th class="n">Back-fill</th></tr></thead>
      <tbody>${DOC_STATUS.filter(([k]) => n('live', k) || n('backfill', k)).map(([k, l]) => html`<tr><td>${l}</td>
        <td class="n">${fmt.num(n('live', k))}</td><td class="n">${fmt.num(n('backfill', k))}</td></tr>`)}</tbody></table>
    <div class="small muted" style="margin-top:8px">${`Found so far: ${fmt.num(llm.found.order_book_history)} order-book figures · ` +
      `${fmt.num(llm.found.orders)} orders · ${fmt.num(llm.found.guidance)} guidance items · ${fmt.num(llm.found.doc_facts)} other facts · ` +
      `${fmt.num(llm.found.financials_q)} XBRL result rows (no LLM)`}</div>
    ${llm.days.length > 1 ? html`<div class="small muted" style="margin-top:6px">Last ${llm.days.length} days: ${fmt.num(llm.total.tokens)} tokens ≈ ₹${llm.total.inr.toFixed(0)}</div>` : null}
    ${llm.calls.length ? html`<details style="margin-top:8px"><summary class="small">Recent calls</summary>
      <div class="tablewrap"><table class="t small"><tbody>${llm.calls.map(c => html`<tr>
        <td class="nowrap">${fmt.time(c.ts)}</td><td><a href="#/stock/${encodeURIComponent(c.sid)}">${c.sid}</a> <span class="muted">${c.kind || ''}${c.backfill ? ' · back-fill' : ''}</span></td>
        <td class="n">${fmt.num(c.prompt_tokens + c.completion_tokens)}</td><td class="n">${(c.ms / 1000).toFixed(0)}s</td>
        <td>${c.ok ? '' : html`<span class="err" title=${c.error}>failed</span>`}</td></tr>`)}</tbody></table></div></details>` : null}
  </div>`;
}

function BseCard({ b }) {
  if (!b || b.error) return b && b.error ? html`<div class="card"><h2>BSE-only stocks</h2><div class="err small">${b.error}</div></div>` : null;
  const blocked = b.blocked_on && b.blocked_on === new Date().toISOString().slice(0, 10);
  return html`<div class="card"><h2>BSE-only stocks <span class="muted">${b.stocks.length} tracked · Screener fallback</span></h2>
    <div class="small">${!b.enabled ? html`<b>Off</b> (tracker.yaml)`
      : blocked ? html`<span class="err">▲ Paused for today — ${b.block_reason}</span>`
      : !b.chrome ? html`<span class="down">▲ Chrome debug session not running</span> — start it with <code>bash scripts/launch_chrome_debug.sh</code>; nothing is fetched without it.`
      : html`<span class="up">●</span> Ready · ${b.today} of ${b.cap} companies today`}</div>
    <div class="small muted" style="margin-top:4px">${b.due} due now${b.last_request ? ` · last Screener request ${fmt.ago(b.last_request)}` : ''} ·
      one company at a time, ≥9 s between requests, spread over the day; PDFs come straight from BSE.</div>
    ${b.stocks.length ? html`<details style="margin-top:6px"><summary class="small">Stocks</summary><table class="t small"><tbody>
      ${b.stocks.map(s => html`<tr><td><a href="#/stock/${encodeURIComponent(s.sid)}">${s.sid}</a> <span class="muted">${s.name}</span></td>
        <td>${s.due || html`<span class="muted">up to date</span>`}</td><td class="nowrap muted">${s.last ? fmt.date(s.last, true) : '—'}</td></tr>`)}</tbody></table></details>` : null}
  </div>`;
}
