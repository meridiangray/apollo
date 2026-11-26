// Tools that launch an agent (Proofread, Catalogue, later Structure): the Tool Agent select, and the Catalogue client and card
// parts shared by Configure and the Sources card. The select is a compact list of Haiku, Sonnet and Opus, each with its model
// mark; the choice is kept in this browser and sent with the job.
'use strict';
const AGENTS = [['haiku', 'Haiku', 'claude-haiku-5-5'], ['sonnet', 'Sonnet', 'claude-sonnet-5-5'], ['opus', 'Opus', 'claude-opus-5-5']];
prefs.agent = AGENTS.some(a => a[0] === store.get('apollo-agent')) ? store.get('apollo-agent') : 'haiku';
state.agentOpen = null;                                // where the open list is: 'cfg', 'card' or 'rail'
state.cat = {};                                        // source -> the manager's Catalogue answer
Object.assign(SV, {
  catalogue: '<g transform="translate(-1 0)"><path d="M5 4m0 1a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z"/><path d="M9 4m0 1a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z"/><path d="M5 8h4"/><path d="M9 16h4"/><path d="M13.803 4.56l2.184 -.53c.562 -.135 1.133 .19 1.282 .732l3.695 13.418a1.02 1.02 0 0 1 -.634 1.219l-.133 .041l-2.184 .53c-.562 .135 -1.133 -.19 -1.282 -.732l-3.695 -13.418a1.02 1.02 0 0 1 .634 -1.219l.133 -.041z"/><path d="M14 9l4 -1"/><path d="M16 16l3.923 -.98"/></g>',
  sparkle: '<path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2z"/><path d="M16 6a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2z"/><path d="M9 18a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z"/>',
  reset: '<path d="M9 14l-4 -4l4 -4"/><path d="M5 10h11a4 4 0 1 1 0 8h-1"/>',
  'minus-circle': '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
  history: '<path d="M12 8l0 4l2 2"/><path d="M3.05 11a9 9 0 1 1 .5 4m-.5 5v-5h5"/>'
});

function agentSelect(where) {
  const cur = AGENTS.find(a => a[0] === prefs.agent) || AGENTS[0];
  const open = state.agentOpen === where;
  const opts = AGENTS.map(([k, w, id]) => `<li role=option data-agent="${k}" aria-selected=${k === cur[0]} tabindex=-1>${modelMark(id)}<span>${w}</span></li>`).join('');
  return `<span class="agsel${open ? ' open' : ''}" data-where="${where}"><button type=button class=agb data-agentbtn="${where}" aria-haspopup=listbox aria-expanded=${open} aria-label="Tool agent" title="Tool agent">` +
    `${modelMark(cur[2])}<span>${cur[1]}</span>${ic('more')}</button>${open ? `<ul class=agl role=listbox aria-label="Tool agent">${opts}</ul>` : ''}</span>`;
}
function agentRedraw() {
  if (state.page === 'sources') drawSources();
  if (state.cfg && state.cfg.detail) drawStage();
  if (typeof drawRail === 'function') { if (state.cfg) drawRail('cfg'); if (state.sel) drawRail('run'); }
  const st = state.cfg && state.cfg.detail && stageOf(state.cfg);
  if (st && (st.kind === 'tool' || st.kind === 'agent')) aboutSoon(state.cfg, st.key);
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-agentbtn]');
  if (b) { state.agentOpen = state.agentOpen === b.dataset.agentbtn ? null : b.dataset.agentbtn; agentRedraw(); return; }
  const o = e.target.closest('[data-agent]');
  if (o) { prefs.agent = o.dataset.agent; store.set('apollo-agent', prefs.agent); state.agentOpen = null; agentRedraw(); return; }
  if (state.agentOpen && !e.target.closest('.agsel')) { state.agentOpen = null; agentRedraw(); }
});

// ---- the Catalogue client ------------------------------------------------------------------------------------------
const catJob = c => (c && c.job) || null;
const catRunning = c => !!(catJob(c) && catJob(c).status === 'running');
async function catLoad(slug, force) {
  if (!slug || (!force && state.cat[slug] && !catRunning(state.cat[slug]))) return;
  try {
    state.cat[slug] = await api(`/api/catalogue?source=${encodeURIComponent(slug)}`);
  } catch (e) {
    state.cat[slug] = null;
    return;
  }
  catRedraw();
  if (catRunning(state.cat[slug])) setTimeout(() => catLoad(slug, true), 2000);
}
function catRedraw() {
  if (state.page === 'sources') drawSources();
  if (state.cfg && state.cfg.detail && state.cfg.sel === 'catalogue') { drawStage(); drawGo(state.cfg, stageOf(state.cfg)); }
}
async function catStart(slug) {
  try {
    await api('/api/catalogue', {source: slug, model: prefs.agent, args: toolVals(state.cfg && state.cfg.source === slug ? state.cfg : null, 'catalogue')});
    setError('cat', null);
    state.cat[slug] = {...(state.cat[slug] || {ok: true, available: true}), job: {status: 'running'}};
    catRedraw();
    setTimeout(() => catLoad(slug, true), 1500);
  } catch (e) {
    setError('cat', `Catalogue: ${e.message}`);
  }
}
async function catAct(slug, action) {
  try {
    await api('/api/catalogue/act', {source: slug, action});
    setError('cat', null);
  } catch (e) {
    setError('cat', `Catalogue: ${e.message}`);
  }
  await catLoad(slug, true);
}
// Catalogue's action on the Sources page: a split button at the right edge of the main column. The arrow opens the tool's own arguments,
// the same ones (and the same remembered values) as New run > Tools > Catalogue.
state.catPop = false;
function catAction() {
  const slug = state.src.sel, r = srcSel(), c = slug ? state.cat[slug] : null;
  const run = catRunning(c), off = !!c && c.available === false;
  const dis = !slug || (r && r.kind === 'media') || run || off;
  const tip = off ? ` title="${esc(c.why || 'Catalogue needs claude in WSL.')}"` : '';
  const open = state.catPop && !dis;
  return `<span class=csplit><button type=button class="btn run catact${run ? ' busy' : ''}" data-cat=start${dis ? ' disabled' : ''}${run ? ' aria-busy=true' : ''}${tip}>` +
    `${run ? '<span class=spin aria-hidden=true></span>' : ic('catalogue')}Catalogue</button>` +
    `<button type=button class="btn run catchev${run ? ' busy' : ''}" data-catpop aria-label="Catalogue arguments" aria-haspopup=dialog aria-expanded=${open}${dis ? ' disabled' : ''}>${ic('more')}</button>${open ? catPopHTML(slug) : ''}</span>`;
}
function catPopHTML(slug) {
  const a = state.about[aboutKey('catalogue', slug)];
  const vals = {...((a && a.values) || {}), ...toolVals({source: slug}, 'catalogue')};
  const f = d => `<div class="f${d.type === 'text' ? ' wide' : ''}"><span>${esc(d.label)}</span>${argControl(d, vals[d.name], {})}</div>`;
  const fields = a ? a.args.filter(d => d.name !== 'agent').map(f).join('') : '';
  return `<div class=catpop id=catpop role=dialog aria-label="Catalogue arguments">${fields ? `<div class=targs>${fields}</div>` : ''}<p class=sab id=catpopabout>${a ? aboutText({source: slug}, {key: 'catalogue', about: ''}) : ''}</p></div>`;
}
function catPopSet(open, back) {
  state.catPop = open;
  const slug = state.src.sel;
  if (open && slug && !state.about[aboutKey('catalogue', slug)]) aboutLoad({source: slug}, 'catalogue').then(() => { if (state.catPop) drawSources(); });
  drawSources();
  if (open) { const f = $('#catpop input,#catpop select'); if (f) f.focus(); } else if (back) { const b = $('[data-catpop]'); if (b) b.focus(); }
}
const catArgSave = (n, v) => { (state.targs.catalogue = state.targs.catalogue || {})[n] = v; store.set('apollo-targs-catalogue', JSON.stringify(state.targs.catalogue)); };
document.addEventListener('click', e => {
  if (e.target.closest && e.target.closest('[data-catpop]')) { catPopSet(!state.catPop); return; }
  if (state.catPop && !e.target.closest('#catpop')) { state.catPop = false; drawSources(); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && state.catPop) { e.preventDefault(); catPopSet(false, true); }
});
// a value typed in the popover is the tool's value everywhere (New run > Tools > Catalogue reads the same) and is kept in this browser
let catPopTimer = 0;
document.addEventListener('input', e => {
  const n = e.target.dataset && e.target.dataset.arg;
  if (!n || !e.target.closest('#catpop')) return;
  catArgSave(n, e.target.value);
  e.target.dataset.dirty = '1';
  clearTimeout(catPopTimer);
  catPopTimer = setTimeout(async () => {
    const slug = state.src.sel;
    if (!slug) return;
    await aboutLoad({source: slug}, 'catalogue');
    const box = $('#catpopabout');
    if (box) box.innerHTML = aboutText({source: slug}, {key: 'catalogue', about: ''});
  }, 250);
});
document.addEventListener('change', e => {
  if (!e.target.closest || !e.target.closest('#catpop')) return;
  delete e.target.dataset.dirty;
  if (e.target.dataset.arg) catArgSave(e.target.dataset.arg, e.target.value);
});
// the small mark on a source row: its Catalogue record is none, pending (waiting for Accept) or accepted
function catMark(r) {
  if (r.kind === 'media') return '';
  const c = state.cat[r.slug];
  const k = c && !catRunning(c) ? (c.pending ? 'pending' : c.accepted ? 'accepted' : 'none') : r.catalogue || 'none';
  const w = {none: 'None', pending: 'Pending', accepted: 'Accepted'}[k];
  return `<span class="cmark ${k}" role=img title="${w}" aria-label="Catalogue: ${w}">${ic('catalogue')}</span>`;
}
// the record as rows: a plain label, then one line per value. No tag numbers; a page goes in the line's hover title.
const CAT_LABEL = {title: {title: 'Title', responsibility: 'By'}, author: 'Author', imprint: {place: 'Place', issuer: 'Issuer', date: 'Date'}, extent: 'Extent',
  note: 'Note', access: 'Classification', declass: {authority: 'Declassified', date: 'Released', iscap: 'ISCAP', document: 'Document'}, language: 'Language', summary: 'Summary', subject: 'Subjects'};
// an identifier's scheme in plain words: "MORI DocID" is DocID, "ISCAP No." is ISCAP
function idLabel(kind) {
  let w = String(kind || '').replace(/\s*(no\.?|number|#)$/i, '').trim().split(/\s+/).filter(Boolean);
  if (w.length > 1 && w[0] === w[0].toUpperCase() && /[a-z]/.test(w[1])) w = w.slice(1);
  const t = w.join(' ');
  return t ? t[0].toUpperCase() + t.slice(1) : 'Identifier';
}
function catLabel(it, names) {
  if (it.key === 'identifier') return idLabel(it.kind);
  const m = CAT_LABEL[it.key];
  return typeof m === 'string' ? m : (m && m[it.kind]) || (m && m.title) || names[it.key] || it.key;
}
// the fields that are free text: two lines in the card. Everything else (title, author, dates, identifiers, access, language) is shown whole.
const LONG_FIELDS = new Set(['summary', 'note', 'subject', 'contents']);
// one value, editable in place on the accepted record and on the one waiting for Accept (data-pend); a long field shows two lines until it is
// edited; a waiting record's value can also be rejected
function catLine(it, i, slug, edit, pend, solo) {
  const long = LONG_FIELDS.has(it.key);
  const rj = pend ? (solo ? '' : rejectBtn(i, it.rejected)) : edit ? removeBtn(i) : '';
  const shown = catDisp(it.key, it.kind, it.value);
  const tip = [it.page ? `p. ${it.page}` : '', shown !== it.value ? it.value : ''].filter(Boolean).join(' · ');
  return `<div class="cline${long ? ' clamp' : ''}${edit ? ' inl' : ''}${it.rejected ? ' rej' : ''}"${edit ? ` data-edit=cat data-i="${i}" data-key="${esc(it.key)}" data-kind="${esc(it.kind || '')}"${pend ? ' data-pend=1' : ''}${long ? ' data-long=1' : ''} tabindex=0` : ''} data-v="${esc(it.value)}"${tip ? ` title="${esc(tip)}"` : ''}>${rj}${esc(shown)}</div>`;
}
// capitals read in normal case: a stamped marking ("TOP SECRET UMBRA") shows as "Top Secret Umbra"; the printed text stays in the hover title
const CAPS_KEEP = new Set(['CIA', 'FBI', 'NSA', 'DIA', 'NISC', 'NIS', 'NFOIO', 'NOSIC', 'ISCAP', 'MORI', 'USA', 'US', 'UK', 'USSR', 'UN', 'NATO', 'OSS', 'KGB', 'SCI', 'DOD', 'DOJ', 'ISBN', 'OCR', 'PDF']);
function capsFix(v) {
  const s = String(v || '');
  if (!/[A-Z]/.test(s)) return s;
  // a word in capitals reads in normal case ("McCLURE" → "McClure", "JOHN" → "John"); roman numerals and known acronyms stay
  const word = (pre, w) => /^[MDCLXVI]+$/.test(w) || CAPS_KEEP.has(w) ? pre + w : pre + w[0] + w.slice(1).toLowerCase();
  if (/[a-z]/.test(s)) return s.replace(/\b(Mc|Mac)?([A-Z]{4,})\b/g, (m, pre, w) => word(pre || '', w));
  return capsWords(s).replace(/(?!^)\b(Of|The|And|An?|In|On|At|To|For|By|With|From)\b/g, w => w.toLowerCase());   // "Author of the Life of…"
}
function capsWords(s) {
  return s.replace(/\b(MC)?([A-Z][A-Z'’.]*)/g, (m, pre, w) => {
    if (/^[MDCLXVI]+$/.test(w) || CAPS_KEEP.has(w.replace(/\.$/, ''))) return (pre || '') + w;
    const p = pre ? pre[0] + pre.slice(1).toLowerCase() : '';
    return p + w[0] + w.slice(1).toLowerCase();
  });
}
// how a record value reads in the card: the Classification group in normal case, the release date as a date
function catDisp(key, kind, v) {
  if (key === 'declass' && kind === 'date' && /^\d{4}-\d{2}-\d{2}$/.test(String(v))) return longWhen(v);
  return key === 'identifier' ? String(v == null ? '' : v) : capsFix(v);      // owner: no ALL CAPS anywhere in the catalogue; the printed form stays in the hover
}
const rejectBtn = (i, on) => `<button type=button class=rjm data-rej="${i}" aria-pressed=${!!on} aria-label="${on ? 'Restore' : 'Reject'}" title="${on ? 'Restore' : 'Reject'}">${ic('minus-circle')}</button>`;
// an accepted value's minus removes it at once; adding it again brings it back
const removeBtn = i => `<button type=button class=rjm data-del="${i}" aria-label=Remove title=Remove>${ic('minus-circle')}</button>`;
// "+ Field": the fields of the record's kind that it does not hold yet, as [value, label] with value "key|kind"; none left, no button
function catAddBtn(c, items, pend) {
  const names = Object.fromEntries(c.fields.map(f => [f.key, f.name])), seen = new Set(items.filter(i => !(pend && i.rejected)).map(i => catLabel(i, names))), opts = [];
  for (const f of c.fields) for (const kind of f.kinds.length ? f.kinds : ['']) {
    const label = catLabel({key: f.key, kind}, names);
    if (seen.has(label) || items.some(i => i.key === f.key && (i.kind || '') === kind && !(pend && i.rejected))) continue;
    seen.add(label);
    opts.push([`${f.key}|${kind}`, label]);
  }
  return opts.length ? `<button type=button class="btn sm" data-add=${pend ? 'pend' : 'acc'} data-opts="${esc(JSON.stringify(opts))}">+ Field</button>` : '';
}
// a value the card already shows elsewhere is not shown twice: a Title equal to the heading, a By that is only the Author's name
function catRepeat(it, items, heading) {
  const n = catNorm(it.value);
  if (it.key === 'title' && it.kind !== 'responsibility') return !!heading && n === catNorm(heading);
  if (it.key === 'title') return items.some(x => x.key === 'author' && !x.rejected && catNorm(x.value) && n.includes(catNorm(x.value)));
  return false;
}
const catNorm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
function catRows(c, items, slug, edit, pend) {
  const names = Object.fromEntries(c.fields.map(f => [f.key, f.name]));
  const groups = [], sel = typeof srcSel === 'function' ? srcSel() : null, heading = sel && sel.title;
  for (const f of c.fields) for (const it of items.filter(x => x.key === f.key && !catRepeat(x, items, heading) && !(pend && x.rejected))) {
    const label = catLabel(it, names);
    let g = groups.find(x => x[0] === label);
    if (!g) groups.push(g = [label, []]);
    g[1].push(it);
  }
  return groups.map(([l, its]) => {
    const solo = pend && its.length === 1;                       // one value: its label carries the minus; several: each value does
    const rej = solo && its[0].rejected;
    const cg = its[0].key === 'access' || its[0].key === 'declass';
    const lines = its.map(it => catLine(it, items.indexOf(it), slug, edit, pend, solo)).join('');
    return `<tr class="${rej ? 'rej ' : ''}${cg ? 'cg' : ''}" data-k="${its[0].key}"><th scope=row>${solo ? rejectBtn(items.indexOf(its[0]), rej) : ''}${esc(l)}</th><td>${lines}</td></tr>`;
  }).join('');
}
function catTip(rec) {
  const calls = `${rec.calls || 1} call${(rec.calls || 1) === 1 ? '' : 's'}${showCost() && rec.cost_usd != null ? ` · $${Number(rec.cost_usd).toFixed(4)}` : ''}`;
  return [rec.model, String(rec.at || '').replace('T', ' '), calls].filter(Boolean).join(' · ');
}
// the accepted record: rows at the foot of the infobox, each value editable in place, then the exports in one row
function catAcceptedRows(slug) {
  const c = state.cat[slug];
  if (!c || !c.accepted) return '';
  const a = c.accepted;
  const head = `<tr class=cathead><th colspan=2><span class=chip title="${esc(catTip(a))}">Catalogue</span>${modelMark(a.model)}</th></tr>`;
  const add = catAddBtn(c, a.record, false);
  return head + catRows(c, a.record, slug, true) + (add ? `<tr class=catadd><td colspan=2>${add}</td></tr>` : '');
}
// a record waiting for Accept or Reject, with its check against what Apollo holds: one item per row, label above value
function catPending(slug) {
  const c = state.cat[slug];
  if (!c || !c.pending) return '';
  const p = c.pending;
  const none = '<span class=dim>none</span>';
  const diff = (c.check || []).map(r => `<div class="ci${r.differs ? ' differs' : ''}"><div class=cl>${esc(r.field)}</div>` +
    `<div class=cv><span>Apollo</span>${r.held == null ? none : esc(r.held)}</div><div class=cv><span>Record</span>${r.record == null ? none : esc(r.record)}</div></div>`).join('');
  const check = '';                                            // owner: no Apollo / Record comparison here
  return `<div class=catpend><div class=cathead2><span class=chip title="${esc(catTip(p))}">Catalogue</span>${modelMark(p.model)}<span class=grow></span>` +
    `<button type=button class="btn primary sm" data-cat=accept>Accept</button><button type=button class="btn sm" data-cat=reject>Reject</button></div>` +
    `<table class=infobox2><tbody>${catRows(c, p.record, slug, true, true)}</tbody></table><div class=catadd>${catAddBtn(c, p.record, true)}</div>${check}</div>`;
}
// ---- the model: one constant pill at the top right of Sources, shaped like Hydra's chat enum ------------------------------
function modelPill() {
  const cur = AGENTS.find(a => a[0] === prefs.agent) || AGENTS[0];
  return `<label class=ch-enum><span class=ch-k>Model</span>${modelMark(cur[2])}<select class=ch-sel id=modelpill aria-label=Model title=Model>` +
    `${AGENTS.map(([k, w]) => `<option value="${k}"${k === cur[0] ? ' selected' : ''}>${w}</option>`).join('')}</select></label>`;
}
// a select is as wide as its longest option: size the pill to the chosen one
function modelFit() {
  const s = $('#modelpill');
  if (!s || !s.isConnected) return;
  const cs = getComputedStyle(s), c = modelFit.c || (modelFit.c = document.createElement('canvas').getContext('2d'));
  c.font = cs.font;
  const t = s.options[s.selectedIndex] ? s.options[s.selectedIndex].text : '';
  s.style.width = Math.ceil(c.measureText(t).width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 2) + 'px';
}
document.addEventListener('change', e => {
  if (e.target.id !== 'modelpill') return;
  prefs.agent = e.target.value;
  store.set('apollo-agent', prefs.agent);
  agentRedraw();
});
// Configure's Catalogue tool: its arguments as fields, then what the tool will do with them
function agentPanel(c, st) {
  return argPanel(c, st);
}

// ---- tool arguments: each tool declares its own (toolargs.py); the panel draws them as fields -------------------------------
state.targs = {};                                      // tool -> the values set here: only what was changed, and the pages
try { state.targs.catalogue = JSON.parse(store.get('apollo-targs-catalogue') || '{}') || {}; } catch (_) { state.targs.catalogue = {}; }
state.about = {};                                      // tool|source -> the manager's answer {args, values, about, problem}
const TOOL_AGENT = {catalogue: 1, proofread: 1};
const aboutKey = (tool, src) => `${tool}|${src || ''}`;
// the values that go with a run: what was set, the agent where the tool has one, and the Configure range as OCR's and Text's pages
function toolVals(c, tool) {
  const v = {...(state.targs[tool] || {})};
  if (TOOL_AGENT[tool]) v.agent = prefs.agent;
  if (c && !('pages' in v) && (tool === 'ocr' || tool === 'text')) { const p = toolPages(c); if (p) v.pages = p; }
  return v;
}
let aboutTimer = 0;
async function aboutLoad(c, tool) {
  const key = aboutKey(tool, c && c.source), want = JSON.stringify(toolVals(c, tool));
  let got;
  try {
    got = await api(`/api/tool/about?tool=${tool}&source=${encodeURIComponent((c && c.source) || '')}&args=${encodeURIComponent(want)}`);
  } catch (e) {
    return;
  }
  if (JSON.stringify(toolVals(c, tool)) !== want) return;           // typed on since: a newer answer is coming
  const first = !state.about[key];
  state.about[key] = got;
  if (first) { if (state.cfg === c && c.detail) drawStage(); } else aboutDraw(c, tool);
}
function aboutSoon(c, tool) {
  clearTimeout(aboutTimer);
  aboutTimer = setTimeout(() => aboutLoad(c, tool), 250);
}
function aboutEnsure(c, tool) {
  if (c.source && !state.about[aboutKey(tool, c.source)]) aboutLoad(c, tool);
}
function aboutText(c, st) {
  const a = state.about[aboutKey(st.key, c.source)];
  return a ? `${a.problem ? `<span class=err>${esc(a.problem)}</span> ` : ''}${esc(a.about)}` : esc(st.about);
}
function aboutDraw(c, tool) {
  const box = $('#toolabout'), st = state.cfg && stageOf(state.cfg);
  if (box && st && st.key === tool) box.innerHTML = aboutText(c, st);
}
function argControl(d, v, c) {
  const id = `arg-${d.name}`;
  if (d.name === 'agent') return agentSelect('cfg');
  if (d.type === 'select') return `<select id="${id}" data-arg="${d.name}" aria-label="${esc(d.label)}">${d.choices.map(([k, w]) => `<option value="${esc(k)}"${String(v) === k ? ' selected' : ''}>${esc(w)}</option>`).join('')}</select>`;
  if (d.type === 'switch') return `<button type=button class="sw2${v ? ' on' : ''}" role=switch aria-checked=${!!v} aria-label="${esc(d.label)}" data-arg-switch="${d.name}"></button>`;
  const cls = {pages: 'argp', number: 'n', text: 'argt'}[d.type];
  const ph = d.type === 'pages' ? (d.default || 'All pages') : '';
  return `<input id="${id}" class="${cls}" data-arg="${d.name}" value="${esc(v == null ? '' : v)}" placeholder="${esc(ph)}"${d.type === 'number' ? ' inputmode=numeric' : ''} aria-label="${esc(d.label)}" autocomplete=off>`;
}
// the fields of a tool's arguments, then the sentence of what it will do
function argPanel(c, st) {
  const a = state.about[aboutKey(st.key, c.source)];
  const vals = {...((a && a.values) || {}), ...toolVals(c, st.key)};
  const fields = a ? a.args.map(d => `<div class=f><span>${esc(d.label)}</span>${argControl(d, vals[d.name], c)}</div>`).join('') : '';
  return `${fields ? `<div class="tqf targs">${fields}</div>` : ''}<p class=sab id=toolabout>${aboutText(c, st)}</p>`;
}
document.addEventListener('input', e => {
  const n = e.target.dataset && e.target.dataset.arg, c = state.cfg, st = c && c.detail && stageOf(c);
  if (!n || !st) return;
  (state.targs[st.key] = state.targs[st.key] || {})[n] = e.target.value;
  aboutSoon(c, st.key);
});
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-arg-switch]'), c = state.cfg, st = c && c.detail && stageOf(c);
  if (!b || !st) return;
  const t = state.targs[st.key] = state.targs[st.key] || {}, n = b.dataset.argSwitch;
  t[n] = !(n in t ? t[n] : b.classList.contains('on'));
  drawStage();
  aboutSoon(c, st.key);
});
