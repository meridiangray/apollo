// The scroll line: the foot of the top bar is a 2px line that fills as the panel being read scrolls (the pattern of hydra's
// wiki scrollline.js, written for Apollo's panels). The panels' own scrollbars are hidden in app.css; wheel, keys and touchpad
// still scroll. The line shows only while a panel overflows.
'use strict';
(() => {
  const SCROLLERS = ['#runs-scroll', '#tabbody', '#cfgpanel', '#srcpanel', '#colpanel', '#hispanel', '#setpanel .xmain', '#portrait', '.rbody', '.tres'];
  const bar = document.querySelector('header.bar');
  if (!bar) return;
  const line = document.createElement('div'), fill = document.createElement('i');
  line.id = 'scrollline';
  line.hidden = true;
  line.setAttribute('aria-hidden', 'true');
  line.appendChild(fill);
  document.body.appendChild(line);
  let last = null;                                     // the panel scrolled most recently
  const room = el => el.scrollHeight - el.clientHeight;
  const shown = el => el.getClientRects().length > 0;
  const live = () => SCROLLERS.flatMap(s => [...document.querySelectorAll(s)]).filter(el => shown(el) && room(el) > 1);
  function draw() {
    const all = live();
    const el = all.includes(last) ? last : all.sort((a, b) => room(b) - room(a))[0];
    line.hidden = !el;
    if (!el) return;
    const b = bar.getBoundingClientRect(), r = parseFloat(getComputedStyle(bar).borderBottomLeftRadius) || 0;
    line.style.left = (b.left + r * .6) + 'px';
    line.style.width = Math.max(0, b.width - r * 1.2) + 'px';
    line.style.top = (b.bottom - 3) + 'px';
    fill.style.width = (100 * Math.min(1, Math.max(0, el.scrollTop / room(el)))).toFixed(2) + '%';
  }
  const soon = () => requestAnimationFrame(draw);
  document.addEventListener('scroll', e => { if (e.target && e.target.nodeType === 1 && SCROLLERS.some(s => e.target.matches(s))) last = e.target; soon(); }, {passive: true, capture: true});
  addEventListener('resize', soon);
  new MutationObserver(soon).observe(document.querySelector('main') || document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class']});
  setInterval(draw, 1000);
  soon();
})();
