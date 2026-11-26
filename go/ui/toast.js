// Notices: small one-line toast bubbles at the bottom right, in the theme's colours (error, warn, info). Each goes away by itself
// after about four seconds (the pointer over it keeps it), at most three show at once, and Esc dismisses them. Each is also
// written to the log (tool_runs, tool=notice), so a notice that went by is not lost.
'use strict';
const TOAST_MS = 4000, TOAST_MAX = 3;
function toastBox() {
  let b = document.getElementById('toasts');
  if (!b) {
    b = document.createElement('div');
    b.id = 'toasts';
    b.setAttribute('role', 'status');
    b.setAttribute('aria-live', 'polite');
    document.body.append(b);
  }
  return b;
}
function closeToast(t, now) {
  if (!t.isConnected || t.dataset.gone) return;
  t.dataset.gone = '1';
  clearTimeout(t._timer);
  const done = () => t.remove();
  if (!now && window.motionOK && motionOK()) gsap.to(t, {opacity: 0, y: 8, duration: .25, ease: 'power2.out', onComplete: done}); else done();
}
function notify(level, text) {
  const msg = String(text || '').split('\n')[0].trim();
  if (!msg) return;
  const lv = ['error', 'warn', 'info'].includes(level) ? level : 'info';
  const box = toastBox();
  for (const t of box.children) if (t.dataset.text === msg && !t.dataset.gone) { arm(t); return; }     // the same line is shown once
  const t = document.createElement('div');
  t.className = `toast ${lv}`;
  t.dataset.text = msg;
  const s = document.createElement('span');
  s.textContent = msg;
  t.append(s);
  box.append(t);
  const live = [...box.children].filter(x => !x.dataset.gone);
  for (const old of live.slice(0, Math.max(0, live.length - TOAST_MAX))) closeToast(old, true);
  if (window.motionOK && motionOK()) gsap.fromTo(t, {opacity: 0, y: 10}, {opacity: 1, y: 0, duration: .35, ease: 'power2.out'});
  t.addEventListener('mouseenter', () => clearTimeout(t._timer));
  t.addEventListener('mouseleave', () => arm(t));
  arm(t);
  if (typeof api === 'function') api('/api/notice', {level: lv, text: msg.slice(0, 400)}).catch(() => {});
}
function arm(t) { clearTimeout(t._timer); t._timer = setTimeout(() => closeToast(t), TOAST_MS); }
function dismissToasts() {                      // Esc: true when there was one to dismiss
  const box = document.getElementById('toasts');
  const live = box ? [...box.children].filter(x => !x.dataset.gone) : [];
  live.forEach(t => closeToast(t));
  return live.length > 0;
}
