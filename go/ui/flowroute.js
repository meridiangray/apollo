// The Flow tab: a route. The steps are stations on one vertical line; the finished stretch is drawn solid over a faint track, a
// dot glides to the step in hand and the stretch behind it draws itself in (GSAP, DrawSVGPlugin). Each station shows the step's
// name and a small live count, and its time once done. In Configure the same route shows with nothing drawn. The route is built
// once per set of steps and then patched in place, so a second's poll changes text, not the picture; it moves only when the step
// in hand changes.
'use strict';
let ROUTE_H = 58;                                 // a station's height (taller in a run, where a tick's line sits under its label)
const ROUTE_X = 14;                               // the line's place from the left
const routeMem = {};                              // per rail: {id, idx} of what was last drawn

function routeSig(rows) { return rows.map(r => r.key).join('|'); }
const routeY = i => i * ROUTE_H + 19;       // the middle of a station's name line
function routeCount(row, v, r) {
  if (!v) return '';
  const heat = v.heat || [], p = (r && r.progress) || {};
  const recs = heat.reduce((n, h) => n + (h.records || 0), 0);
  const calls = ((r && r.calls) || {}).hydra;
  if (row.key === 'names') return row.state === 'done' ? (p.pages_content ? `${p.pages_content} pages` : '') : row.state === 'now' && p.pages_content ? `${p.pages_read || 0} of ${p.pages_content} pages` : '';
  if (row.key === 'records') return row.state === 'next' || row.state === 'skipped' ? '' : `${recs} records`;
  if (row.key === 'hard') { const n = heat.filter(h => h.status === 'escalated' || h.status === 'gave-up').length; return n ? `${n} ${n === 1 ? 'page' : 'pages'}` : ''; }
  if (row.state === 'now' && calls != null) return `${calls} calls`;
  return row.detail || '';
}
function stationHTML(row, i, v, r, preview) {
  const t = !preview && row.state === 'done' && row.secs != null ? elapsed(row.secs) : '';
  const c = preview ? '' : routeCount(row, v, r);
  return `<li class="rs is-${esc(row.state)}" data-key="${esc(row.key)}"><b>${esc(row.label)}${row.model ? modelMark(row.model) : ''}</b>` +
    `<span class=rc>${esc(c)}</span><time>${esc(t)}</time>${preview ? '' : tickHTML(row.key)}</li>`;
}
function routeShell(rows) {
  const n = rows.length, h = n * ROUTE_H;
  const d = n > 1 ? `M${ROUTE_X} ${routeY(0)}V${routeY(n - 1)}` : '';
  const dots = rows.map((_, i) => `<circle class=rtn cx=${ROUTE_X} cy=${routeY(i)} r=4.5 data-i=${i} />`).join('');
  return `<div class=rt data-sig="${esc(routeSig(rows))}"><svg class=rtl width=${ROUTE_X * 2} height=${h} viewBox="0 0 ${ROUTE_X * 2} ${h}" aria-hidden=true>` +
    `<path class=rt-track d="${d}"/><path class=rt-prog d="${d}"/>${dots}<circle class=rt-dot cx=${ROUTE_X} cy=${routeY(0)} r=7 /></svg><ol class=rts>${rows.map((r, i) => stationHTML(r, i, null, null, true)).join('')}</ol></div>`;
}
// where the line should reach: the step in hand, else the last one finished (all done: the end)
function routeIndex(rows) {
  let at = rows.findIndex(r => r.state === 'now' || r.state === 'failed');
  if (at < 0) { for (let i = rows.length - 1; i >= 0; i--) if (rows[i].state === 'done') { at = i; break; } }
  return at;
}
// Draw the route into `body`. rows: [{key, label, state, detail, secs, model}]; id names what is shown (a run, or the preview),
// v and r are the run's view and summary for the counts; preview: nothing drawn.
function drawRoute(ctx, body, rows, id, v, r, preview) {
  if (!rows.length) { body.innerHTML = ''; routeMem[ctx] = null; return; }
  ROUTE_H = preview ? 58 : 72;
  let rt = $('.rt', body);
  const fresh = !rt || rt.dataset.sig !== routeSig(rows) || rt.style.getPropertyValue('--rh') !== ROUTE_H + 'px';
  if (fresh) { body.innerHTML = routeShell(rows); rt = $('.rt', body); rt.style.setProperty('--rh', ROUTE_H + 'px'); }   // by script: the page's CSP has no inline styles
  const lis = [...rt.querySelectorAll('.rs')];
  rows.forEach((row, i) => {
    const html = stationHTML(row, i, v, r, preview);
    if (lis[i].dataset.k !== html) {
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      const n = tmp.firstChild;
      lis[i].className = n.className;
      lis[i].innerHTML = n.innerHTML;
      lis[i].dataset.k = html;
    }
  });
  const svg = $('.rtl', rt), prog = $('.rt-prog', svg), dot = $('.rt-dot', svg);
  const circles = [...svg.querySelectorAll('.rtn')];
  const idx = preview ? -1 : routeIndex(rows), last = Math.max(1, rows.length - 1);
  circles.forEach((c, i) => c.setAttribute('class', `rtn ${i <= idx ? 'on' : ''} ${rows[i].state === 'skipped' ? 'skip' : ''}`));
  dot.style.display = idx < 0 ? 'none' : '';
  const mem = routeMem[ctx];
  const same = mem && mem.id === id && !fresh;
  const from = same ? mem.idx : -1;
  routeMem[ctx] = {id, idx};
  const pct = i => Math.max(0, i) / last * 100;
  if (!window.gsap || !prog.getTotalLength || rows.length < 2) { dot.setAttribute('cy', routeY(Math.max(0, idx))); return; }
  if (idx < 0) { gsap.set(prog, {drawSVG: '0% 0%'}); return; }
  if (from === idx) { gsap.set(prog, {drawSVG: `0% ${pct(idx)}%`}); dot.setAttribute('cy', routeY(idx)); return; }
  if (!motionOK()) { gsap.set(prog, {drawSVG: `0% ${pct(idx)}%`}); dot.setAttribute('cy', routeY(idx)); return; }
  gsap.killTweensOf([prog, dot]);
  gsap.set(prog, {drawSVG: `0% ${pct(Math.max(0, from))}%`});
  gsap.set(dot, {attr: {cy: routeY(Math.max(0, from))}});
  gsap.to(prog, {drawSVG: `0% ${pct(idx)}%`, duration: Math.min(.8, .4 + Math.abs(idx - from) * .08), ease: 'power2.out'});
  gsap.to(dot, {attr: {cy: routeY(idx)}, duration: Math.min(.8, .4 + Math.abs(idx - from) * .08), ease: 'power2.out'});
}
