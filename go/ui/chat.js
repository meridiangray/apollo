// chat.js — Hydra's chat bubble and window (hydra-git wiki/assets/act.js, AR-chat and BH1), brought into Apollo without the mini
// rail: the circle floats over every view (bottom right, a drag carries it anywhere, kept per browser, brought back inside on
// resize; a drag never presses); pressed, the chat window opens by it. The window: the Chat tab, the Model pill (Haiku, Sonnet,
// Opus) and Clear on top, the messages, the composer (Enter sends, Shift+Enter a new line). No suggested questions and no
// canned replies: an empty conversation and the input. The conversation lives in this page until it reloads; each message
// goes to POST /api/chat with the whole thread and what Apollo shows (the view, the run, the source), and the reply is polled
// at /api/chat/<id> (chat.go, chat.py). Esc closes the window.
'use strict';
(() => {
  const SV = (d, n) => `<svg class=ico aria-hidden=true width=${n} height=${n} viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round>${d}</svg>`;
  const CHATP = '<path d="M3 20l1.3 -3.9c-2.324 -3.437 -1.426 -7.872 2.1 -10.374c3.526 -2.501 8.59 -2.296 11.845 .48c3.255 2.777 3.695 7.266 1.029 10.501c-2.666 3.235 -7.615 4.215 -11.574 2.293l-4.7 1"/>';
  const SENDP = '<path d="M10 14l11 -11"/><path d="M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"/>';
  const CLEARP = '<path d="M19 20h-10.5l-4.21 -4.3a1 1 0 0 1 0 -1.41l10 -10a1 1 0 0 1 1.41 0l5 5a1 1 0 0 1 0 1.41l-9.2 9.3"/><path d="M18 13.3l-6.3 -6.3"/>';
  const LIST = typeof AGENTS !== 'undefined' ? AGENTS
    : [['haiku', 'Haiku', 'claude-haiku-5-5'], ['sonnet', 'Sonnet', 'claude-sonnet-5-5'], ['opus', 'Opus', 'claude-opus-5-5']];
  const BTNK = 'apollo-chat-btn', WINK = 'apollo-chat-pos', MODK = 'apollo-chat-model';
  const CS = 56, EDGE = 8, GAP = 10, POLL = 700;
  const keep = {get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } },
                set: (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) { /* no storage */ } }};
  const mk = (tag, attrs = {}, ...kids) => {
    const x = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) x.setAttribute(k, v);
    x.append(...kids.filter(k => k != null));
    return x;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, Math.max(lo, hi)));
  const narrow = () => matchMedia('(max-width: 640px)').matches;
  const readPos = k => { try { const a = JSON.parse(keep.get(k) || 'null'); return Array.isArray(a) && a.length === 2 && a.every(Number.isFinite) ? a : null; } catch (_) { return null; } };

  let model = LIST.some(a => a[0] === keep.get(MODK)) ? keep.get(MODK) : 'sonnet';
  const msgs = [];                       // {role: user | assistant, text, err?}: this page's one conversation
  let session = '', waiting = false, gen = 0, at = readPos(BTNK), cur = null;

  // ---- the elements ------------------------------------------------------------------------------------------------
  const btn = mk('button', {type: 'button', class: 'ch-btn', title: 'Chat', 'aria-label': 'Chat', 'aria-expanded': 'false', 'aria-controls': 'chatwin'});
  btn.innerHTML = SV(CHATP, 24);
  const tab = mk('span', {class: 'ch-tab'});
  tab.innerHTML = SV(CHATP, 14) + 'Chat';
  const pill = mk('label', {class: 'ch-enum'});
  const clr = mk('button', {type: 'button', class: 'ch-clear', title: 'Clear', 'aria-label': 'Clear'});
  clr.innerHTML = SV(CLEARP, 14);
  const head = mk('div', {class: 'ch-head', title: 'Drag to move · double-click to pair'}, tab, mk('span', {class: 'ch-tools'}, clr));
  const log = mk('ol', {class: 'ch-log', 'aria-live': 'polite'});
  const ta = mk('textarea', {name: 'm', rows: '1', placeholder: 'Message', 'aria-label': 'Message'});
  const send = mk('button', {type: 'submit', class: 'ch-send', title: 'Send', 'aria-label': 'Send'});
  send.innerHTML = SV(SENDP, 15);
  const form = mk('form', {class: 'ch-form'}, ta, mk('div', {class: 'ch-bar'}, pill, mk('span', {class: 'grow'}), send));        // the model sits with the composer, as in Hydra's window
  const grab = mk('div', {class: 'ch-grab', 'aria-hidden': 'true'}, mk('i'));
  const win = mk('section', {id: 'chatwin', class: 'ch-win', role: 'dialog', 'aria-label': 'Chat'}, grab,
                 mk('div', {class: 'drawer'}, head, log, mk('div', {class: 'ch-compose'}, form)));

  // ---- the Model pill: Apollo's (app.css .ch-enum), the rank glyph of the chosen model before its name -------------------
  function drawPill() {
    const c = LIST.find(a => a[0] === model) || LIST[0];
    const mark = typeof modelMark === 'function' ? modelMark(c[2]) : '';
    pill.innerHTML = `<span class=ch-k>Model</span>${mark}<select class=ch-sel aria-label=Model title=Model>` +
      LIST.map(([k, w]) => `<option value="${k}"${k === c[0] ? ' selected' : ''}>${w}</option>`).join('') + '</select>';
    const s = pill.querySelector('select');
    s.addEventListener('change', () => { model = s.value; keep.set(MODK, model); drawPill(); });
    fit(s);
  }
  // a select is as wide as its longest option: size the pill to the chosen one
  function fit(s) {
    if (!s.isConnected) return;
    const cs = getComputedStyle(s), c = fit.c || (fit.c = document.createElement('canvas').getContext('2d'));
    c.font = cs.font;
    const t = s.options[s.selectedIndex] ? s.options[s.selectedIndex].text : '';
    s.style.width = Math.ceil(c.measureText(t).width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 2) + 'px';
  }

  // ---- the messages: kept at the newest unless the reader scrolled up (mine always brings it down) ----------------------
  function draw(mine) {
    const stick = mine || log.scrollHeight - log.scrollTop - log.clientHeight < 24;
    const items = msgs.map(m => mk('li', {class: m.role === 'user' ? 'ch-me' : 'ch-reply' + (m.err ? ' ch-err' : '')}, m.text));
    if (waiting) {
      const d = mk('li', {class: 'ch-reply ch-wait', role: 'status', 'aria-label': 'Waiting for a reply'});
      d.innerHTML = '<i></i><i></i><i></i>';
      items.push(d);
    }
    log.replaceChildren(...items);
    send.disabled = waiting;
    if (stick) log.scrollTop = log.scrollHeight;
  }

  // what Apollo shows: the view, the run selected on Runs, the source in hand, the archive open in Collections
  function context() {
    const c = {};
    try {
      c.view = state.cfg ? 'configure' : state.page || 'runs';
      const run = state.byId[state.sel];
      if (c.view === 'runs' && state.sel) c.run = state.sel;
      const src = state.cfg ? (!state.cfg.shell && state.cfg.source) : c.view === 'sources' ? state.src && state.src.sel : c.view === 'runs' && run ? run.source : '';
      if (src) c.source = src;
      if (c.view === 'collections' && state.col && state.col.archive) c.archive = state.col.archive;
    } catch (_) { /* the page is still starting */ }
    return c;
  }
  const getJSON = async (path, body) => {
    if (typeof api === 'function') return api(path, body);
    const r = await fetch(path, body === undefined ? {} : {method: 'POST', body: JSON.stringify(body),
      headers: {'Content-Type': 'application/json', 'X-Apollo-Token': document.querySelector('meta[name=apollo-token]').content}});
    const j = await r.json().catch(() => null);
    if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
    return j;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function reply(id, mine) {
    for (let miss = 0; mine === gen;) {
      await sleep(POLL);
      if (mine !== gen) return null;
      let j;
      try { j = await getJSON('/api/chat/' + encodeURIComponent(id)); } catch (e) { if (++miss > 5) return {err: true, text: e.message}; continue; }
      miss = 0;
      if (j.status === 'done') return {text: j.text};
      if (j.status === 'failed') return {err: true, text: j.error || 'No answer came back. Try again.'};
    }
    return null;
  }
  async function ask(text) {
    const mine = ++gen;
    msgs.push({role: 'user', text});
    waiting = true;
    draw(true);
    let got;
    try {
      const j = await getJSON('/api/chat', {session, model, context: context(),
                                            messages: msgs.filter(m => !m.err).map(m => ({role: m.role, text: m.text}))});
      if (mine !== gen) return;
      session = j.session || session;
      got = await reply(j.id, mine);
    } catch (e) { got = {err: true, text: e.message}; }
    if (mine !== gen || !got) return;                    // cleared meanwhile
    waiting = false;
    msgs.push({role: 'assistant', text: got.text, ...(got.err ? {err: true} : {})});
    draw();
  }
  function clear() {
    gen++;
    msgs.length = 0;
    session = '';
    waiting = false;
    draw();
    ta.focus({preventScroll: true});
  }
  form.addEventListener('submit', ev => {
    ev.preventDefault();
    const m = ta.value.trim();
    if (!m || waiting) return;
    ta.value = ''; ta.style.height = '';
    ask(m);
  });
  ta.addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); } });
  ta.addEventListener('input', () => { ta.style.height = ''; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; });
  clr.addEventListener('click', clear);

  // ---- the circle: its kept spot (else bottom right), clamped into the window; narrow screens keep it to the right edge ----
  const home = () => [innerWidth - CS - 16, innerHeight - CS - 30];
  function put(x, y) {
    if (x == null) [x, y] = at || home();
    if (narrow()) x = innerWidth - CS - 12;
    cur = [Math.round(clamp(x, EDGE, innerWidth - CS - EDGE)), Math.round(clamp(y, EDGE, innerHeight - CS - EDGE))];
    btn.classList.add('placed');
    btn.style.left = cur[0] + 'px';
    btn.style.top = cur[1] + 'px';
    return cur;
  }
  const on = () => win.classList.contains('up');
  // THE placement rule (Hydra's minirail.js): above the circle, else below it, else beside it, else on the roomier side with its
  // height capped to the room; aligned to the circle's edge toward the middle; inside the window
  function place() {
    if (!on()) return;
    if (sheet()) {
      const top = innerHeight - (parseFloat(getComputedStyle(win).bottom) || 0) - win.offsetHeight;      // where it will rest, not mid-slide
      btn.style.top = Math.round(clamp(top - CS - 8, EDGE, innerHeight - CS - EDGE)) + 'px';
      return;
    }
    if (win.classList.contains('ch-free')) return;
    const a = btn.getBoundingClientRect(), bot = innerHeight - EDGE, rgt = innerWidth - EDGE;
    win.style.maxHeight = '';
    const W = win.offsetWidth;
    let H = win.offsetHeight;
    const up = a.top - GAP - EDGE, down = bot - a.bottom - GAP, inward = a.left + a.width / 2 <= innerWidth / 2;
    let x = inward ? a.left : a.right - W, y;
    if (H <= up) y = a.top - GAP - H;
    else if (H <= down) y = a.bottom + GAP;
    else {
      const roomR = rgt - a.right - GAP, roomL = a.left - GAP - EDGE;
      if (W <= roomR || W <= roomL) {
        x = (inward ? W <= roomR : W > roomL) ? a.right + GAP : a.left - GAP - W;
        if (H > bot - EDGE) { win.style.maxHeight = (bot - EDGE) + 'px'; H = win.offsetHeight; }
        y = a.bottom - H;
      } else {
        win.style.maxHeight = Math.max(80, Math.max(up, down)) + 'px';
        H = win.offsetHeight;
        y = up >= down ? a.top - GAP - H : a.bottom + GAP;
      }
    }
    win.style.left = Math.round(clamp(x, EDGE, rgt - W)) + 'px';
    win.style.top = Math.round(clamp(y, EDGE, bot - H)) + 'px';
  }
  function sheet() {
    const s = narrow();
    win.classList.toggle('ch-sheet', s);
    if (s) { win.classList.remove('ch-free'); win.style.left = win.style.top = win.style.maxHeight = ''; }
    return s;
  }
  // a window dropped by its header stays there (kept per browser) until a double-click pairs it with the circle again
  function free(x, y) {
    const w = win.offsetWidth || 352, h = win.offsetHeight || 200;
    x = clamp(x, EDGE, innerWidth - w - EDGE);
    y = clamp(y, EDGE, innerHeight - Math.min(h, innerHeight - 16) - EDGE);
    win.classList.add('ch-free');
    win.style.left = Math.round(x) + 'px';
    win.style.top = Math.round(y) + 'px';
    return [Math.round(x), Math.round(y)];
  }

  function open() {
    sheet();
    win.classList.add('up');
    const p = readPos(WINK);
    if (!narrow() && p) free(p[0], p[1]); else win.classList.remove('ch-free');
    drawPill();
    draw(true);
    place();
    mark(true);
    ta.focus({preventScroll: true});
  }
  function close() {
    if (!on()) return;
    win.classList.remove('up');
    mark(false);
    put();
  }
  const mark = v => { btn.setAttribute('aria-expanded', String(v)); btn.classList.toggle('on', v); };

  // ---- the circle by pointer: follows the finger or mouse 1:1, drops where released, no click after a drag ----------------
  {
    let d = null, moved = false;
    btn.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      const r = btn.getBoundingClientRect();
      d = {sx: ev.clientX, sy: ev.clientY, ox: r.left, oy: r.top, on: false};
      moved = false;
      try { btn.setPointerCapture(ev.pointerId); } catch (_) { /* gone */ }
    });
    btn.addEventListener('pointermove', ev => {
      if (!d) return;
      if (!d.on && Math.hypot(ev.clientX - d.sx, ev.clientY - d.sy) > 6) { d.on = true; moved = true; btn.classList.add('dragging'); }
      if (d.on) { put(d.ox + ev.clientX - d.sx, d.oy + ev.clientY - d.sy); place(); }
    });
    const up = () => {
      if (!d) return;
      const was = d.on;
      d = null;
      btn.classList.remove('dragging');
      if (!was) return;
      at = cur.slice();
      keep.set(BTNK, JSON.stringify(at));
    };
    btn.addEventListener('pointerup', up);
    btn.addEventListener('pointercancel', up);
    btn.addEventListener('click', ev => { if (moved) { moved = false; ev.preventDefault(); return; } if (on()) close(); else open(); });
  }
  // ---- the window by its header: a drag frees it where it is dropped; a double-click pairs it again ------------------------
  head.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || ev.target.closest('.ch-tools') || narrow()) return;
    const r = win.getBoundingClientRect(), dx = ev.clientX - r.left, dy = ev.clientY - r.top;
    let moved = false, got = null;
    try { head.setPointerCapture(ev.pointerId); } catch (_) { /* gone */ }
    const mv = e => {
      if (!moved && Math.hypot(e.clientX - ev.clientX, e.clientY - ev.clientY) < 4) return;
      moved = true;
      got = free(e.clientX - dx, e.clientY - dy);
    };
    const up = () => {
      head.removeEventListener('pointermove', mv); head.removeEventListener('pointerup', up); head.removeEventListener('pointercancel', up);
      if (moved && got) keep.set(WINK, JSON.stringify(got));
    };
    head.addEventListener('pointermove', mv); head.addEventListener('pointerup', up); head.addEventListener('pointercancel', up);
  });
  head.addEventListener('dblclick', ev => {
    if (ev.target.closest('.ch-tools')) return;
    keep.set(WINK, null);
    win.classList.remove('ch-free');
    place();
  });
  // the sheet's handle: a swipe down closes it
  grab.addEventListener('pointerdown', ev => {
    const y0 = ev.clientY;
    try { grab.setPointerCapture(ev.pointerId); } catch (_) { /* gone */ }
    win.classList.add('ch-pull');
    const mv = e => { win.style.transform = `translateY(${Math.max(0, e.clientY - y0)}px)`; };
    const up = e => {
      grab.removeEventListener('pointermove', mv); grab.removeEventListener('pointerup', up); grab.removeEventListener('pointercancel', up);
      win.classList.remove('ch-pull');
      win.style.transform = '';
      if (e.clientY - y0 > 70) close();
    };
    grab.addEventListener('pointermove', mv); grab.addEventListener('pointerup', up); grab.addEventListener('pointercancel', up);
  });

  // Esc closes the window before anything else on the page reads it (app.js would deselect the run)
  addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || !on() || document.querySelector('dialog[open]')) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    close();
    btn.focus({preventScroll: true});
  }, true);
  addEventListener('resize', () => { put(); place(); });
  if (window.ResizeObserver) new ResizeObserver(() => { if (narrow() && on()) place(); }).observe(win);   // the sheet grows as it fills: keep the circle above it
  if (window.visualViewport) {                                                                           // the sheet rides above the on-screen keyboard
    const vv = visualViewport, fitVV = () => {
      document.documentElement.style.setProperty('--ch-bot', Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop)) + 'px');
      document.documentElement.style.setProperty('--ch-vh', Math.round(vv.height) + 'px');
      place();
    };
    vv.addEventListener('resize', fitVV); vv.addEventListener('scroll', fitVV);
  }

  const start = () => { document.body.append(btn, win); put(); drawPill(); draw(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
