// Motion (GSAP, the page's own copy in vendor/): short (0.3 to 0.6 s), eased out, once per change, never looping, and off when
// the system asks for reduced motion. Arrivals (new log rows and questions slide in), Flip (the Found, Collections and Sources
// lists glide when filtered or regrouped), the rail's tab crossfade and Ready's one pulse. The route and the heatmap use
// motionOK() the same way.
'use strict';
const motionOK = () => !!window.gsap && !(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
if (window.gsap) gsap.registerPlugin(window.DrawSVGPlugin, window.Flip);

// Flip: remember where the items stand, change the list, and let the ones that are still there glide to their new places.
// Items match by data-flip-id; new ones fade in.
function motionFlip(box, selector, apply) {
  if (!motionOK() || !window.Flip || !box.querySelector(selector)) { apply(); return; }
  const before = Flip.getState(box.querySelectorAll(selector));
  apply();
  const now = box.querySelectorAll(selector);
  if (!now.length) return;
  Flip.from(before, {targets: now, duration: .45, ease: 'power2.out', absolute: false, nested: false, onEnter: els => gsap.fromTo(els, {opacity: 0}, {opacity: 1, duration: .35, ease: 'power2.out'})});
}

// Arrivals: rows that were not there at the last draw slide up and in. `seen` is the set of keys of the last draw; the first
// draw of a list only records it, so opening a run does not animate its whole log.
function motionArrive(box, selector, keyOf, seen) {
  const els = [...box.querySelectorAll(selector)];
  const had = seen.size > 0;
  const fresh = els.filter(e => !seen.has(keyOf(e)));
  for (const e of els) seen.add(keyOf(e));
  if (!had || !fresh.length || !motionOK()) return;
  gsap.fromTo(fresh.slice(-12), {y: 12, opacity: 0}, {y: 0, opacity: 1, duration: .4, ease: 'power2.out', stagger: .04, clearProps: 'transform,opacity'});
}

// the rail's body fades in when the tab changes
function motionFade(el) {
  if (!motionOK() || !el) return;
  gsap.fromTo(el, {opacity: 0}, {opacity: 1, duration: .28, ease: 'power2.out', clearProps: 'opacity'});
}

// Ready: one pulse when its state changes
function motionPulse(el) {
  if (!motionOK() || !el) return;
  gsap.fromTo(el, {scale: 1.22}, {scale: 1, duration: .55, ease: 'power2.out', clearProps: 'transform'});
}
