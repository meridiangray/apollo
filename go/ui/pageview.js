/* pageview.js: the page strip and the page box, one component for every view that shows a document's pages (the run view;
   later the Sources rows and card). Plain data in (api/pageview), HTML out; nothing here knows which view it sits in.
     PageView.strip(host, data, {sel, onPick, legend})   the pages, one cell each (records shade, redaction hatch, section band, scroll line)
     PageView.box(host, data, {cat, entities, onStep, onClose})   one page: head, its facts, the groups of `cat` unless `entities` is
                                                   false (the Found list below draws them), Quotes
     PageView.evidence(ev) / PageView.quotes(list) the Quoted / Located bars and the Quotes list, for a document's card
   A part with no data is not drawn. Styles: the `pageview` block of app.css. The design is Hydra's Sources v3. */
(() => {
  const E = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const css = root => {                         // the page allows no style attributes: positions are set from script
    root.querySelectorAll('[data-css]').forEach(e => {
      for (const d of e.dataset.css.split(';')) {
        const i = d.indexOf(':');
        if (i > 0) e.style.setProperty(d.slice(0, i).trim(), d.slice(i + 1).trim());
      }
    });
    root.querySelectorAll('[data-w]').forEach(e => { e.style.width = e.dataset.w + '%'; });
  };
  const done = st => st === 'done' || st === 'escalated' || st === 'gave-up';
  const shade = n => n <= 0 ? 'pz' : 'p' + (n <= 3 ? 1 : n <= 7 ? 2 : n <= 11 ? 3 : n <= 15 ? 4 : 5);
  const cellClass = c => done(c.status) ? shade(c.records) : c.status === 'doing' ? 'pdoing' : c.status === 'empty' || c.status === 'redacted' ? 'pout' : 'ptodo';
  const RED = {partial: 'Partial', mostly: 'Mostly', entire: 'Entire'};
  /* the unit: a PDF has pages (and printed page labels), a text source is read in parts */
  const where = (unit, c, full) => unit === 'part' ? 'Page ' + c.page : c.label ? (full ? 'Page ' : '') + 'p. ' + E(c.label) + ' · PDF p. ' + c.page : 'PDF p. ' + c.page;

  /* ---- the strip */
  function strip(host, data, opt = {}) {
    const pages = data.pages || [];
    if (!pages.length) { host.innerHTML = ''; return; }
    const secs = data.sections || [], by = new Map(pages.map((c, i) => [c.page, i + 1]));
    const first = pages[0].page, n = pages.length;
    let h = `<div class=pv-scroll><div class=pv-cells data-css="grid-template-columns:repeat(${n},minmax(6px,1fr))" role=group aria-label="Pages">`;
    for (const c of pages) {
      const t = `${where(data.unit, c)}${done(c.status) ? ` · ${c.records} ${c.records === 1 ? 'record' : 'records'}` : ''}${c.red ? ' · ' + RED[c.red] + ' redaction' : ''}`;
      h += `<i class="pv-c ${cellClass(c)}" data-p="${c.page}" data-css="grid-column:${by.get(c.page)}" title="${t}">${c.red ? `<b class="pv-rd ${c.red}"></b>` : ''}</i>`;
    }
    secs.forEach(s => {
      const a = by.get(s.a), b = by.get(s.b);
      if (a && b) h += `<b class=pv-sec data-s="${s.i}" data-css="grid-column:${a}/${b + 1};background:var(--pv-sec${s.i % 6})" title="${E(s.title)}"></b>`;
    });
    h += '</div></div><div class=pv-line><i></i></div>';
    if (opt.legend !== false) h += '<div class=pv-legend><span><span class=pv-sw><i class=p1></i><i class=p3></i><i class=p5></i></span>Records</span><span><i class=pz></i>None</span><span><i class=pout></i>Skipped</span>' +
      (data.redaction ? Object.entries(RED).map(([k, w]) => `<span><i class="pv-lh"><b class="pv-rd ${k}"></b></i>${w}</span>`).join('') : '') + '</div>';
    host.innerHTML = h;
    css(host);
    const sc = host.querySelector('.pv-scroll'), ln = host.querySelector('.pv-line');
    const line = () => {                         // the line is the strip's scrollbar: the thumb is the part in view
      const room = sc.scrollWidth - sc.clientWidth;
      ln.classList.toggle('scrolling', room > 1);
      ln.style.setProperty('--a', (room > 1 ? sc.scrollLeft / sc.scrollWidth : 0).toFixed(4));
      ln.style.setProperty('--w', (room > 1 ? sc.clientWidth / sc.scrollWidth : 1).toFixed(4));
    };
    sc.addEventListener('scroll', line, {passive: true});
    if (window.ResizeObserver) new ResizeObserver(line).observe(sc);
    line();
    ln.addEventListener('pointerdown', ev => {
      if (!ln.classList.contains('scrolling') || ev.button) return;
      ev.preventDefault();
      const r = ln.getBoundingClientRect(), k = sc.scrollWidth / r.width, thumb = ln.firstElementChild;
      if (ev.target !== thumb) sc.scrollLeft = (ev.clientX - r.left) * k - sc.clientWidth / 2;
      const x0 = ev.clientX, l0 = sc.scrollLeft;
      ln.classList.add('grab');
      try { ln.setPointerCapture(ev.pointerId); } catch (e) { /* no capture: the drag still works on the line */ }
      const move = m => { sc.scrollLeft = l0 + (m.clientX - x0) * k; };
      const up = () => { ln.classList.remove('grab'); ln.removeEventListener('pointermove', move); ln.removeEventListener('pointerup', up); };
      ln.addEventListener('pointermove', move); ln.addEventListener('pointerup', up);
    });
    host.onclick = ev => {
      const c = ev.target.closest('.pv-c');
      if (c && opt.onPick) opt.onPick(Number(c.dataset.p));
    };
    mark(host, opt.sel);
  }
  function mark(host, page) {
    host.querySelectorAll('.pv-c.sel').forEach(c => c.classList.remove('sel'));
    const c = page && host.querySelector(`.pv-c[data-p="${page}"]`);
    if (!c) return;
    c.classList.add('sel');
    const sc = host.querySelector('.pv-scroll');
    if (sc && sc.scrollWidth > sc.clientWidth) {   // the picked page stays in view
      const x = c.offsetLeft, w = c.offsetWidth;
      if (x < sc.scrollLeft + w * 2 || x + w > sc.scrollLeft + sc.clientWidth - w * 2) sc.scrollLeft = x - (sc.clientWidth - w) / 2;
    }
  }

  /* ---- the page box */
  const ini = t => String(t).replace(/\(.*?\)/g, '').split(/\s+/).filter(w => /^\p{Lu}/u.test(w) && !/^(al|el|bin|ibn|von|van|der|den|de|del|da|di|du|la|le|of|the|mac)$/i.test(w))
    .slice(0, 2).map(w => w[0]).join('') || String(t).trim()[0] || '?';
  const icon = s => typeof entityIcon === 'function' ? entityIcon(s) : '';
  const avatar = (e, sm) => e.schema === 'Person' || !e.schema ? `<span class="pv-av${sm ? ' sm' : ''}" aria-hidden=true>${E(ini(e.name))}</span>`
    : `<span class="pv-av org${sm ? ' sm' : ''}" aria-hidden=true>${icon(e.schema)}</span>`;
  const who = list => list.map(e => `<div class=pv-who title="${E(e.name)}${e.quote ? ' · ' + E(e.quote) : ''}">${avatar(e)}<span class=nm>${E(e.name)}</span><b class=ct>${e.count}</b></div>`).join('');
  const rec = n => `${n} ${n === 1 ? 'record' : 'records'}`;
  function places(p) {
    /* geo-context: c.odd / e.odd = hydra flagged a place here as set against its source: a ring in Apollo's warning colour, stroke only */
    const ring = o => o ? ' style="box-shadow:inset 0 0 0 2px var(--red)"' : '';
    const cards = (p.countries || []).map(c => `<div class=pv-cty${ring(c.odd)} title="${E(c.name)} · ${rec(c.count)}"><svg class=pv-shape viewBox="0 0 ${c.w} ${c.h}" aria-hidden=true><path d="${E(c.d)}"/></svg>` +
      `<span class=pv-cty-t><b>${E(c.name)}</b><span class=ct>${rec(c.count)}</span>${c.places.length ? `<small>${c.places.map(E).join(' · ')}</small>` : ''}</span></div>`).join('');
    const loose = (p.loose || []).map(e => `<span class=pv-chip${ring(e.odd)}>${E(e.name)}<b>${e.count}</b></span>`).join('');
    return (cards ? `<div class=pv-ctys>${cards}</div>` : '') + (loose ? `<div class=pv-loose>${loose}</div>` : '');
  }
  const events = list => `<ul class=pv-evs>${list.map(e => `<li><div class=pv-ev-t>${E(e.title)}${e.date ? `<small>${E(e.date)}</small>` : ''}</div>` +
    (e.summary && e.summary !== e.title ? `<div class=pv-ev-s>${E(e.summary)}</div>` : '') +
    (e.who ? `<div class=pv-ends>${e.who.map(w => `<span class=pv-end>${avatar(w, true)}${E(w.name)}</span>`).join('')}</div>` : '') +
    (e.quote ? `<blockquote class=pv-q>${E(e.quote)}</blockquote>` : '') + '</li>').join('')}</ul>`;
  const quotes = list => list.map(q => `<div class=pv-quote>${E(q.q)}${q.what ? `<small>${E(q.what)}</small>` : ''}</div>`).join('');
  const meter = (l, [n, of]) => `<div class=pv-meter><span>${l}</span><span class=bar><i data-w="${of ? (n / of * 100).toFixed(1) : 0}"></i></span><span class=v>${n} / ${of}</span></div>`;
  const evidence = ev => ev ? (ev.quoted ? meter('Quoted', ev.quoted) : '') + (ev.located && ev.located[1] ? meter('Located', ev.located) : '') : '';
  const CATS = [['people', 'People'], ['bodies', 'Bodies'], ['places', 'Places'], ['events', 'Events'], ['ties', 'Ties'], ['posts', 'Positions']];   // the Found chips' names
  const NAV = (step, label, d) => `<button type=button data-step="${step}" aria-label="${label}" title="${label}"><svg viewBox="0 0 16 16" width=12 height=12 aria-hidden=true><path d="${d}" fill=none stroke=currentColor stroke-width=1.8 stroke-linecap=round stroke-linejoin=round /></svg></button>`;
  let live = null;                               // the open box's handlers, for the arrow keys
  function box(host, d, opt = {}) {
    if (!d || !d.ok) { host.innerHTML = ''; live = null; return; }
    const cat = opt.cat || 'all', c = d.counts || {};
    const head = `<div class=pv-head><div><div class=pv-k>${where(d.unit, d, true)}</div>` +
      (d.section ? `<h3>${E(d.section.title)}</h3>` : '') + '</div>' +
      `<span class=pv-nav>${d.prev ? NAV(-1, 'Previous', 'M10 3.5 5.5 8l4.5 4.5') : ''}${d.next ? NAV(1, 'Next', 'M6 3.5 10.5 8 6 12.5') : ''}${NAV(0, 'Close', 'M4 4l8 8M12 4l-8 8')}</span></div>`;
    // the page's facts only: the categories are chosen with the Found tab's own chips above (one control, not two)
    let chips = `<span class=pv-c2><span>Records</span><b>${c.records || 0}</b></span>`;
    if (c.words) chips += `<span class=pv-c2><span>Words</span><b>${c.words}</b></span>`;
    if (d.redaction) chips += `<span class=pv-c2><span>Redaction</span><b><i class="pv-lh"><b class="pv-rd ${d.redaction}"></b></i>${RED[d.redaction]}</b></span>`;
    const label = k => (CATS.find(x => x[0] === k) || [k, k])[1];
    const part = (k, body) => opt.entities !== false && (cat === 'all' || cat === k) && body ? `<section class=pv-cat><h4>${label(k)}${c[k] ? ` <small>${c[k]}</small>` : ''}</h4>${body}</section>` : '';
    host.innerHTML = `<div class=pv-box>${head}<div class=pv-counts>${chips}</div>` +
      part('people', d.people ? `<div class=pv-whos>${who(d.people)}</div>` : '') +
      part('bodies', d.bodies ? `<div class=pv-whos>${who(d.bodies)}</div>` : '') +
      part('places', d.places ? places(d.places) : '') +
      part('events', d.events ? events(d.events) : '') +
      part('ties', d.ties ? `<div class=pv-whos>${who(d.ties)}</div>` : '') +
      part('posts', d.posts ? `<div class=pv-whos>${who(d.posts)}</div>` : '') +
      (cat === 'all' && d.quotes ? `<section class=pv-cat><h4>Quotes</h4>${quotes(d.quotes)}</section>` : '') +
      (cat === 'all' && d.evidence ? `<section class=pv-cat><h4>Evidence</h4>${evidence(d.evidence)}</section>` : '') + '</div>';
    css(host);
    live = {d, opt};
    host.onclick = ev => {
      const s = ev.target.closest('[data-step]'), k = ev.target.closest('[data-cat]');
      if (s) { const v = Number(s.dataset.step); return v ? step(v) : opt.onClose && opt.onClose(); }
      if (k && opt.onCat) opt.onCat(k.dataset.cat);
    };
  }
  function step(v) {
    if (!live) return;
    const p = v < 0 ? live.d.prev : live.d.next;
    if (p && live.opt.onStep) live.opt.onStep(p);
  }
  document.addEventListener('keydown', e => {   // the arrow keys walk the pages while a box is open
    if (!live || e.target.closest('input,textarea,select') || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  });
  const close = () => { live = null; };

  /* ---- a kind's rich view for a whole run (or one page): the page box's drawings, each thing a button that picks it (data-pick) */
  const pick = (name, schema) => ` data-pick="${E(name)}" data-schema="${E(schema || '')}" role=button tabindex=0`;
  const whoPick = list => list.map(e => `<div class=pv-who${pick(e.name, e.schema)} title="${E(e.name)}${e.quote ? ' · ' + E(e.quote) : ''}">${avatar(e)}<span class=nm>${E(e.name)}</span><b class=ct>${e.count}</b></div>`).join('');
  const tieRows = list => `<ul class=pv-ties>${list.map(t => {
    const end = x => x && x.name ? `<span class=pv-end${pick(x.name, x.schema)}>${avatar(x, true)}${E(x.name)}</span>` : '';
    const when = [t.start, t.end].filter(Boolean).join(' to ') || t.date || '';
    return `<li><div class=pv-ev-t><b>${E(t.kind || t.schema || 'Tie')}</b>${when ? `<small>${E(when)}</small>` : ''}</div>` +
      `<div class=pv-ends>${end(t.from)}<span class=pv-arrow aria-hidden=true>→</span>${end(t.to)}${t.role ? `<small>${E(t.role)}</small>` : ''}</div>` +
      (t.quote ? `<blockquote class=pv-q>${E(t.quote)}</blockquote>` : '') + '</li>';
  }).join('')}</ul>`;
  const eventsPick = list => `<ul class=pv-evs>${list.map(e => `<li${pick(e.title, 'Event')}><div class=pv-ev-t>${E(e.title)}${e.date ? `<small>${E(e.date)}</small>` : ''}</div>` +
    (e.summary && e.summary !== e.title ? `<div class=pv-ev-s>${E(e.summary)}</div>` : '') +
    (e.who && e.who.length ? `<div class=pv-ends>${e.who.map(w => `<span class=pv-end>${avatar(w, true)}${E(w.name)}</span>`).join('')}</div>` : '') +
    (e.quote ? `<blockquote class=pv-q>${E(e.quote)}</blockquote>` : '') + '</li>').join('')}</ul>`;
  function rich(d, group) {
    if (!d || !d.ok) return '';
    const g = d[group];
    if (!g || (Array.isArray(g) && !g.length)) return '';
    if (group === 'places') return `<div class=pv-rich>${places(g)}</div>`;
    if (group === 'events') return `<div class=pv-rich>${eventsPick(g)}</div>`;
    if (group === 'ties') return `<div class=pv-rich>${tieRows(g)}</div>`;
    return `<div class="pv-rich pv-whos">${whoPick(g)}</div>`;
  }
  window.PageView = {strip, mark, box, close, evidence, quotes, where, rich, avatar};
})();
