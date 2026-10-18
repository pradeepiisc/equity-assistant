// Price chart (lightweight-charts) and small SVG bar / line charts.
import { html, useEffect, useRef, useState } from 'https://unpkg.com/htm@3.1.1/preact/standalone.module.js';
import { fmt, polarity } from './ui.js';

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function useScheme() {
  const [scheme, setScheme] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const on = () => setScheme(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return scheme;
}

const LINES = [
  { key: 'close', label: 'Close', color: '--s-price' },
  { key: 'dma40', label: 'DMA40', color: '--s1' },
  { key: 'dma100', label: 'DMA100', color: '--s2' },
  { key: 'dma200', label: 'DMA200', color: '--s3' },
];

export function PriceChart({ bars, markers }) {
  const box = useRef();
  const tip = useRef();
  const scheme = useScheme();

  useEffect(() => {
    if (!box.current || !bars.length || !window.LightweightCharts) return;
    const LW = window.LightweightCharts;
    const chart = LW.createChart(box.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: css('--surface') }, textColor: css('--muted'), fontSize: 11,
                fontFamily: css('--font') },
      grid: { vertLines: { visible: false }, horzLines: { color: css('--grid') } },
      rightPriceScale: { borderColor: css('--axis') },
      timeScale: { borderColor: css('--axis'), fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: LW.CrosshairMode.Magnet,
                   vertLine: { color: css('--axis'), width: 1, style: LW.LineStyle.Solid, labelVisible: false },
                   horzLine: { visible: false, labelVisible: false } },
      handleScale: { mouseWheel: false, pinch: true }, handleScroll: { mouseWheel: false, pressedMouseMove: true },
    });
    const series = {};
    for (const l of LINES) {
      series[l.key] = chart.addLineSeries({
        color: css(l.color), lineWidth: 2, priceLineVisible: false, lastValueVisible: l.key === 'close',
        crosshairMarkerVisible: l.key === 'close', crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: css('--surface'), crosshairMarkerBorderWidth: 2,
      });
      series[l.key].setData(bars.filter(b => b[l.key] != null).map(b => ({ time: b.t, value: b[l.key] })));
    }
    const inRange = new Set(bars.map(b => b.t));
    const byDay = {};
    for (const m of markers || []) {
      if (!inRange.has(m.d)) continue;
      (byDay[m.d] = byDay[m.d] || []).push(m);
    }
    const marks = Object.keys(byDay).sort().map(d => {
      const ms = byDay[d];
      const p = ms.map(polarity);
      const pos = p.includes('pos') && !p.includes('neg'), neg = p.includes('neg') && !p.includes('pos');
      return { time: d, position: neg ? 'aboveBar' : 'belowBar',
               color: pos ? css('--good') : neg ? css('--critical') : css('--muted'),
               shape: pos ? 'arrowUp' : neg ? 'arrowDown' : 'circle', size: 1 };
    });
    series.close.setMarkers(marks);
    chart.timeScale().fitContent();

    const byT = Object.fromEntries(bars.map(b => [b.t, b]));
    chart.subscribeCrosshairMove(param => {
      const el = tip.current;
      if (!el) return;
      if (!param.time || !param.point || param.point.x < 0) { el.style.display = 'none'; return; }
      const b = byT[param.time];
      if (!b) { el.style.display = 'none'; return; }
      el.replaceChildren();
      const date = document.createElement('div');
      date.className = 't-date';
      date.textContent = `${fmt.date(b.t, true)} · ${b.phase}`;
      el.appendChild(date);
      for (const l of LINES) {
        if (b[l.key] == null) continue;
        const row = document.createElement('div');
        row.className = 't-row';
        const lab = document.createElement('span');
        const key = document.createElement('i');
        key.style.cssText = `width:12px;height:2px;border-radius:2px;display:inline-block;background:${css(l.color)}`;
        lab.append(key, l.label);
        const val = document.createElement('b');
        val.textContent = fmt.price(b[l.key]);
        row.append(val, lab);
        el.appendChild(row);
      }
      for (const m of byDay[b.t] || []) {
        const ev = document.createElement('div');
        ev.className = 't-ev';
        ev.textContent = m.title;
        el.appendChild(ev);
      }
      el.style.display = 'block';
      const w = box.current.clientWidth;
      const x = param.point.x + 16 + 190 > w ? param.point.x - 206 : param.point.x + 16;
      el.style.left = `${Math.max(0, x)}px`;
      el.style.top = '8px';
    });
    return () => chart.remove();
  }, [bars, markers, scheme]);

  return html`
    <div>
      <div class="chart-legend">
        ${LINES.map(l => html`<span class="lk"><i style="background:var(${l.color})"></i>${l.label}</span>`)}
        <span class="lk"><span class="pol pos">▲</span> positive event</span>
        <span class="lk"><span class="pol neg">▼</span> negative event</span>
      </div>
      <div class="chart-wrap">
        <div class="chart" ref=${box} role="img" aria-label="Price with DMA40, DMA100 and DMA200"></div>
        <div class="tip" ref=${tip} style="display:none"></div>
      </div>
    </div>`;
}

// Bars: ≤24px, 4px rounded data end, square at the baseline; value on the last and the extreme bar.
export function BarMini({ labels, values, height = 150, format = v => fmt.num(v), title }) {
  const W = Math.max(260, labels.length * 44), H = height, padT = 18, padB = 22, padL = 4, padR = 4;
  const vals = values.map(v => (v == null ? null : v));
  const finite = vals.filter(v => v != null);
  if (!finite.length) return html`<div class="muted small">No data</div>`;
  const max = Math.max(0, ...finite), min = Math.min(0, ...finite);
  const span = max - min || 1;
  const y = v => padT + (max - v) / span * (H - padT - padB);
  const band = (W - padL - padR) / labels.length;
  const bw = Math.min(24, band * 0.6);
  const extreme = finite.length ? vals.indexOf(finite.reduce((a, b) => Math.abs(b) > Math.abs(a) ? b : a)) : -1;
  const last = vals.length - 1 - [...vals].reverse().findIndex(v => v != null);
  const r = 4;
  return html`
    <svg class="mini" viewBox="0 0 ${W} ${H}" width="100%" height=${H} role="img" aria-label=${title || 'bar chart'}>
      <line class="base" x1=${padL} x2=${W - padR} y1=${y(0)} y2=${y(0)} />
      ${labels.map((lab, i) => {
        const v = vals[i];
        const cx = padL + band * i + band / 2;
        const x = cx - bw / 2;
        const lbl = html`<text x=${cx} y=${H - 6} text-anchor="middle">${lab}</text>`;
        if (v == null) return html`<g>${lbl}</g>`;
        const y0 = y(0), y1 = y(v);
        const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0));
        const rr = Math.min(r, h, bw / 2);
        const up = v >= 0;
        const d = up
          ? `M${x},${y0} V${top + rr} Q${x},${top} ${x + rr},${top} H${x + bw - rr} Q${x + bw},${top} ${x + bw},${top + rr} V${y0} Z`
          : `M${x},${y0} V${y0 + h - rr} Q${x},${y0 + h} ${x + rr},${y0 + h} H${x + bw - rr} Q${x + bw},${y0 + h} ${x + bw},${y0 + h - rr} V${y0} Z`;
        const showVal = i === last || i === extreme;
        return html`<g>
          <rect x=${cx - band / 2} y=${padT - 14} width=${band} height=${H - padT - padB + 14} fill="transparent">
            <title>${lab}: ${format(v)}</title></rect>
          <path class="bar ${up ? '' : 'neg'}" d=${d}><title>${lab}: ${format(v)}</title></path>
          ${showVal && html`<text class="val" x=${cx} y=${up ? top - 4 : y0 + h + 12} text-anchor="middle">${format(v)}</text>`}
          ${lbl}
        </g>`;
      })}
    </svg>`;
}

export function LineMini({ labels, values, height = 110, format = v => `${v.toFixed(2)}%`, title }) {
  const W = Math.max(240, labels.length * 40), H = height, padT = 16, padB = 22, padX = 18;
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 1) return html`<div class="muted small">No data</div>`;
  const vs = pts.map(p => p[1]);
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (hi - lo < 1) { const mid = (hi + lo) / 2; lo = mid - 0.5; hi = mid + 0.5; }
  const x = i => padX + (labels.length === 1 ? 0 : i / (labels.length - 1)) * (W - 2 * padX);
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ');
  const first = pts[0], last = pts[pts.length - 1];
  return html`
    <svg class="mini" viewBox="0 0 ${W} ${H}" width="100%" height=${H} role="img" aria-label=${title || 'line chart'}>
      <line class="grid" x1=${padX} x2=${W - padX} y1=${y(lo)} y2=${y(lo)} />
      <path class="line" d=${d} />
      ${pts.map(([i, v]) => html`<g>
        <circle cx=${x(i)} cy=${y(v)} r="12" fill="transparent"><title>${labels[i]}: ${format(v)}</title></circle>
        <circle class="dot" cx=${x(i)} cy=${y(v)} r="4"><title>${labels[i]}: ${format(v)}</title></circle></g>`)}
      <text class="val" x=${x(first[0])} y=${y(first[1]) - 8} text-anchor="start">${format(first[1])}</text>
      ${last !== first && html`<text class="val" x=${x(last[0])} y=${y(last[1]) - 8} text-anchor="end">${format(last[1])}</text>`}
      ${labels.map((l, i) => (i === 0 || i === labels.length - 1 || labels.length <= 6) &&
        html`<text x=${x(i)} y=${H - 6} text-anchor=${i === 0 ? 'start' : i === labels.length - 1 ? 'end' : 'middle'}>${l}</text>`)}
    </svg>`;
}
