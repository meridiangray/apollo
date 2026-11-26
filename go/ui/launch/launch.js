// Apollo launch screen: a drifting grainy splash in the theme's colours, the name, and one row: the phase word, a thin
// bar and the percent (the look of Hydra's desktop opening screen). GSAP drives the drift, the bar and the word.
// The bar is real: it moves when something Apollo waits for at start has finished, never on a timer.
//   page (the document is parsed) -> Starting; /api/ping answers (fetched here) -> Runs; the first /api/runs
//   answers (app.js calls window.apolloReady) -> Sources; the configs have loaded (app.js calls
//   window.apolloPhase('sources')) -> Ready.
// The overlay fades once all of them are done (MIN is a short hold so the full bar is seen), or after MAX ms whatever
// happens. Click or Esc skips it. Reduced motion: a still splash, the bar jumps. Load this after gsap, in <head>.
(function () {
  'use strict';
  var MIN = 300, MAX = 8000, FADE = 380;
  var root = document.documentElement;
  var hash = new URLSearchParams(location.hash.slice(1));

  // Follow the app's mode early (app.js reads the same two places later), so there is no light flash in dark mode.
  try {
    var m = hash.get('mode') || localStorage.getItem('apollo-mode');
    var th = hash.get('theme') || localStorage.getItem('apollo-theme') || 'dune';
    var lightOnly = th === 'tan' || th === 'light';
    var dark = !lightOnly && (m === 'dark' || (m === 'system' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches));
    root.dataset.theme = /^(dune|shore|tide|tan|light)$/.test(th) ? th : 'dune';
    root.dataset.mode = dark ? 'dark' : 'light';
  } catch (_) { /* keep the page's own default */ }

  var reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var g = window.gsap;
  // the bar once a phase is done, and the word that then names what is awaited
  var STEPS = { page: [.15, 'Starting'], ping: [.35, 'Runs'], runs: [.65, 'Sources'], sources: [1, 'Ready'] };
  var seen = {}, shown = { v: 0 }, word = 'Starting', front = 'w2', box, done = false, drift = [];

  function $(sel) { return box && box.querySelector(sel); }
  function draw() {
    var p = Math.round(shown.v * 100);
    $('.apl-fill').style.width = (shown.v * 100) + '%';
    $('.apl-pct').textContent = p + '%';
    $('.apl-track').setAttribute('aria-valuenow', p);
  }
  function setBar(f, then) {
    if (!g || reduced) { shown.v = f; draw(); if (then) then(); return; }
    g.to(shown, { v: Math.max(shown.v, f), duration: .7, ease: 'power2.out', overwrite: true, onUpdate: draw, onComplete: then });
  }
  function setWord(s) {
    if (s === word) return;
    word = s;
    var back = front === 'w1' ? 'w2' : 'w1';
    var a = $('.apl-' + front), b = $('.apl-' + back);
    front = back;
    b.textContent = s;
    if (!g || reduced) { b.style.opacity = 1; a.style.opacity = 0; return; }
    g.fromTo(b, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: .45, ease: 'power2.out' });
    g.to(a, { opacity: 0, y: -6, duration: .35, ease: 'power1.in' });
  }

  function leave() {
    if (done) return;
    done = true;
    drift.forEach(function (t) { t.kill(); });
    if (!box) return;
    box.classList.add('out');
    var gone = function () { if (box.parentNode) box.parentNode.removeChild(box); };
    box.addEventListener('transitionend', gone);
    setTimeout(gone, FADE + 200);
  }

  function phase(name) {
    var st = STEPS[name];
    if (!st || seen[name]) return;
    seen[name] = true;
    if (!box) return;
    var f = 0, w = 'Starting';
    for (var k in STEPS) if (seen[k] && STEPS[k][0] >= f) { f = STEPS[k][0]; w = STEPS[k][1]; }
    setWord(w);
    setBar(f, f >= 1 ? function () { setTimeout(leave, MIN); } : null);
  }

  function ambient() {
    if (!g || reduced) return;
    var R = g.utils.random;
    ['.apl-f1', '.apl-f2', '.apl-f3', '.apl-f4'].forEach(function (f) {
      drift.push(g.to($(f), { x: R(-6, 6) + 'cqw', y: R(-5, 5) + 'cqw', duration: R(26, 38), ease: 'sine.inOut', repeat: -1, yoyo: true }).progress(R(0, 1)));
      drift.push(g.to($(f), { scale: R(1.06, 1.16), duration: R(18, 28), ease: 'sine.inOut', repeat: -1, yoyo: true }).progress(R(0, 1)));
    });
    drift.push(g.to($('.apl-sheen'), { xPercent: 380, duration: 2.6, ease: 'sine.inOut', repeat: -1, repeatDelay: .8 }));
  }

  var HTML = '<div class="apl-splash" aria-hidden="true"><i class="apl-f apl-f1"></i><i class="apl-f apl-f2"></i><i class="apl-f apl-f3"></i><i class="apl-f apl-f4"></i></div>' +
    '<svg class="apl-grain" aria-hidden="true" width="100%" height="100%"><filter id="apl-grain" x="0" y="0" width="100%" height="100%">' +
    '<feTurbulence type="fractalNoise" baseFrequency=".8" numOctaves="2" stitchTiles="stitch" seed="7"/><feColorMatrix type="saturate" values="0"/></filter>' +
    '<rect width="100%" height="100%" filter="url(#apl-grain)"/></svg>' +
    '<div class="apl-load" role="status" aria-live="polite"><h1 class="apl-name">Apollo</h1><div class="apl-bar">' +
    '<span class="apl-stage"><b class="apl-w1"></b><b class="apl-w2">Starting</b></span>' +
    '<span class="apl-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span class="apl-fill"><span class="apl-sheen"></span></span></span>' +
    '<span class="apl-pct">0%</span></div></div>';

  function start() {
    if (done) return;
    box = document.createElement('div');
    box.className = 'apl';
    box.innerHTML = HTML;
    box.addEventListener('click', leave);
    root.appendChild(box);
    ambient();
    setTimeout(leave, MAX);
    var early = Object.keys(seen);       // phases that finished before the overlay existed
    seen = {};
    phase('page');
    early.forEach(phase);
    fetch('/api/ping').then(function (r) { if (r.ok) phase('ping'); }).catch(function () { /* the cap covers it */ });
  }

  window.apolloReady = function () { phase('ping'); phase('runs'); };
  window.apolloPhase = phase;
  document.addEventListener('keydown', function (e) { if (!done && e.key === 'Escape') leave(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
