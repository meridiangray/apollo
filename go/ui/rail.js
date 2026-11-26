// The right rail: tabs beside Configure (Flow, Run Definition, Text) and beside a run (Flow, Run Definition, Text, Setup). The tab strip
// stays at the top and the body scrolls. The chosen tab is remembered per view. Flow is the route (flowroute.js); Run Definition is
// the numbered step list with its models and switches (in Configure each step opens its settings under the buttons); Text is the
// selected source's text, page by page.
'use strict';
const RAIL_TABS = {cfg: [['flow', 'Flow'], ['def', 'Run Definition'], ['text', 'Text']],
                   run: [['flow', 'Flow'], ['def', 'Run Definition'], ['text', 'Text'], ['setup', 'Setup']]};
for (const ctx of ['cfg', 'run']) state.rail[ctx].tab = store.get('apollo.rail.' + ctx) || 'flow';

const railTab = ctx => { const t = state.rail[ctx].tab; return RAIL_TABS[ctx].some(x => x[0] === t) ? t : 'flow'; };
const railBox = ctx => $(ctx === 'cfg' ? '#cfgrail' : '#runrail');
function railSource(ctx) {
  if (ctx === 'cfg') return state.cfg && !state.cfg.shell ? state.cfg.source : null;
  const r = state.byId[state.sel];
  return r && r.source;
}
function setRailTab(ctx, t) {
  state.rail[ctx].tab = t;
  store.set('apollo.rail.' + ctx, t);
  drawRail(ctx);
}

// ---- Flow ---------------------------------------------------------------------------------------------------------
// The mark in the left column is the status (a number while waiting, a spinner while live, a check when done, a cross when failed,
// a dash when skipped or off); the model is named on a step only when it differs from the reader's.
const stepMark = (st, i) => st === 'done' ? ic('ok') : st === 'now' ? '<span class=spin aria-hidden=true></span>' : st === 'failed' ? ic('miss') : st === 'skipped' ? '–' : String(i + 1);
function stepRow(s, i, click, pressed) {
  const inner = `<span class=fkm>${stepMark(s.state, i)}</span><span class=fbd><b>${esc(s.label)}${s.detail ? `<em>${esc(s.detail)}</em>` : ''}${s.model ? modelMark(s.model) : ''}</b>${s.note ? `<small>${esc(s.note)}</small>` : ''}</span>`;
  return click ? `<li><button type=button class="fs fsb is-${esc(s.state)}" data-step="${esc(s.key)}" aria-pressed=${!!pressed}>${inner}</button></li>`
               : `<li class="fs is-${esc(s.state)}">${inner}</li>`;
}
function cfgFlowRows(c) {
  const head = c.models[c.flow === 'read' ? 'haiku' : c.flow] || '';
  return flowSteps(c).map(s => {
    const off = (s.on === false && !s.switch) || (s.switch && STEP_ON[s.key] && !c.on[STEP_ON[s.key]]);
    const id = s.who === 'code' ? null : s.who === 'session' ? head : c.models[s.role];
    return {key: sk(s), label: s.label, note: s.note || '', state: off ? 'skipped' : 'next', model: id && id !== head ? modelLabel(id) : ''};
  });
}
function runFlowRows(v) {
  const r = state.byId[state.sel] || {};
  const cfg = v.config || [];
  const val = l => (cfg.find(c => c.label === l) || {}).value;
  const head = r.kind === 'pageflow' ? val('Reader') || '' : modelLabel(r.model);
  const model = st => st.who === 'code' ? '' : st.who === 'session' ? head : st.who === 'haiku' ? val('Reader') || 'Haiku' : val('Hard pages') || 'Sonnet';
  const live = r.status === 'running' || r.status === 'stopping' || r.status === 'starting';
  return ((v.flow && v.flow.steps) || []).filter(st => !(st.who === 'session' && !st.branch)).map(st => {
    let state = st.state, detail = st.detail || '';
    if (state === 'now' && !live) {                // the run ended with this step in hand
      state = r.status === 'failed' || r.status === 'died' ? 'failed' : 'skipped';
      detail = state === 'failed' ? 'Failed' : 'Stopped';
    }
    const m = model(st);
    return {key: st.key || st.label, label: st.label, note: st.note, state, detail, secs: st.secs, model: m && m !== head ? m : ''};
  });
}
// the model's mark (medals): a rank insignia in the model's metal, bronze Haiku, silver Sonnet, gold Opus; the name is the label
const MODEL_TIER = {haiku: 1, sonnet: 2, opus: 3};
// rank insignia at one fixed size (11 px, text height), filled in the tier's metal: Haiku one chevron, Sonnet two stacked, Opus a star
const RANK = {
  1: '<path d="M1 6.7L6 2.4l5 4.3v3.1L6 5.5 1 9.8z"/>',
  2: '<path d="M1 4.4L6 .8l5 3.6v2.3L6 3.1 1 6.7z"/><path d="M1 9.1L6 5.5l5 3.6v2.3L6 7.8 1 11.4z"/>',
  3: '<path d="M6 .7l1.6 3.35 3.65.45-2.7 2.5.72 3.62L6 8.83 2.73 10.62l.72-3.62L.75 4.5l3.65-.45z"/>',
};
function modelMark(model) {
  const word = modelLabel(model), key = Object.keys(MODEL_TIER).find(k => String(model).toLowerCase().includes(k));
  if (!key) return '';
  const n = MODEL_TIER[key];
  return `<span class="mm t${n}" role=img aria-label="${esc(word)}" title="${esc(word)}"><svg width=11 height=11 viewBox="0 0 12 12" aria-hidden=true>${RANK[n]}</svg></span>`;
}
// Run Definition: today's step list. In Configure each step is a button that opens its settings; the Basic flow also holds the
// switch that chains Assemble on the same archive when a Basic run ends, and the Wikidata flow its Answer questions switch.
function defHTML(ctx) {
  const c = state.cfg;
  const rows = ctx === 'cfg' ? (c && c.detail ? cfgFlowRows(c) : []) : (state.view && state.view.tab === 'run' ? runFlowRows(state.view) : []);
  if (!rows.length) return '';
  const click = ctx === 'cfg';
  const after = click && c.flow === 'read'
    ? `<div class=chain><span class=chl>${modelMark('claude-sonnet-5-5')}<span>Assemble</span></span><span class=dim>Run after Basic</span>` +
      `<button type=button class="sw2${c.on.after ? ' on' : ''}" role=switch aria-checked=${!!c.on.after} aria-label="Run after Basic" data-switch=after></button></div>`
    : click && c.flow === 'wikidata'                     // CZ318: the session answers its own Wikidata questions
    ? `<div class=chain><span class=chl><span>Answer questions</span></span><span class=dim></span>` +
      `<button type=button class="sw2${c.on.answer ? ' on' : ''}" role=switch aria-checked=${!!c.on.answer} aria-label="Answer questions" data-switch=answer></button></div>` : '';
  return `<ol class=fsl>${rows.map((s, i) => stepRow(s, i, click, click && c.step === s.key)).join('')}</ol>${after}`;
}

// ---- Setup (a run's configuration) ---------------------------------------------------------------------------------
function setupHTML() {
  const cfg = (state.view && state.view.config) || [];
  return cfg.length ? `<dl class=cfgl>${cfg.map(c => `<dt>${esc(c.label)}</dt><dd${c.title ? ` title="${esc(c.title)}"` : ''}>${esc(c.value)}</dd>`).join('')}</dl>` : '';
}

// ---- Text: one page of the source ----------------------------------------------------------------------------------
const railPage = ctx => {
  const p = state.rail[ctx].page;
  if (p) return p;
  if (ctx === 'cfg') return parseInt(state.cfg && state.cfg.from, 10) || 1;
  const cells = (state.byId[state.sel] || {}).cells || [];
  return cells.length ? cells[0].page : 1;
};
function textHTML(ctx) {
  const src = railSource(ctx);
  if (!src) return '';
  const page = railPage(ctx), total = state.ptTotal[src];
  const rec = state.pt[`${src}|${page}`];
  const pr = rec && rec.ok && rec.kind === 'pages' ? state.pr[prKey(src, page)] : null;       // a text source has no PDF to proofread
  const nav = `<div class=pgnav><button type=button class=btn data-pg=-1${page <= 1 ? ' disabled' : ''}>‹ Prev</button>` +
    `<label class=pgno>p. <input class=pgin value="${page}" inputmode=numeric aria-label=Page>${total ? ` of ${total}` : ''}</label>` +
    `<button type=button class=btn data-pg=1${total && page >= total ? ' disabled' : ''}>Next ›</button>${proofBtns(pr)}</div>${proofMenu(pr, page)}`;
  let body = '';
  if (rec && rec.ok) {
    const pend = pr && pr.page && pr.page.pending;
    if (pend) body = proofDiff(pend);
    else if (rec.layer === 'none') body = '<p class=none>No text layer: run OCR</p>';
    else body = `${rec.layer === 'ocr' ? '<span class=chip>OCR</span>' : ''}${rec.layer === 'proofread' ? proofMark(rec) : ''}<div class=ptext>${esc(rec.text)}</div>`;
    const bad = pr && !pend && pr.job && ((pr.job.errors || {})[page] || (pr.job.status === 'failed' && pr.job.error));
    if (bad) body = `<p class=none>${esc(bad)}</p>` + body;
  } else if (rec) body = `<p class=none>${esc(rec.why)}</p>`;
  return nav + body;
}

// ---- Proofread: Haiku reads the page image beside its text; a diff, then Accept or Reject ---------------------------
// The job runs in the manager (apollo.py proofreadstart); this polls its file while it runs. Accepted text is stored under
// state/proofread/<source>/ and comes before OCR and the text layer. The source file is never changed.
state.pr = {};                                   // `${source}|${page}` -> the manager's answer for that page
state.prMenu = {open: false, from: '', to: ''};
Object.assign(SV, {proof: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', more: '<path d="M6 9l6 6 6-6"/>'});
const prKey = (src, page) => `${src}|${page}`;
const prJob = pr => (pr && pr.job) || null;
const prRunning = pr => !!(prJob(pr) && prJob(pr).status === 'running');
function proofBtns(pr) {
  if (!pr) return '';
  if (!pr.available) {
    return `<span class=prgrp><button type=button class="ib prb" disabled aria-label=Proofread>${ic('proof')}</button>` +
      `<span class=prq><button type=button class=q aria-describedby=tt-pr aria-label="Why Proofread is off">?</button>` +
      `<span class=tt id=tt-pr role=tooltip>${esc(pr.why || 'Proofread needs claude in WSL.')}</span></span></span>`;
  }
  const job = prJob(pr), run = prRunning(pr);
  const n = run && job.total > 1 ? `<span class=prn>${job.done}/${job.total}</span>` : '';
  return `<span class=prgrp><button type=button class="ib prb${run ? ' busy' : ''}" data-pr=this aria-label="Proofread this page" title="Proofread this page. Shift-click: pages A–B"` +
    `${run ? ' aria-busy=true disabled' : ''}>${run ? '<span class=spin aria-hidden=true></span>' : ic('proof')}</button>${n}` +
    `<button type=button class="ib prm" data-pr=menu aria-label="Proofread pages A–B" aria-expanded=${state.prMenu.open}${run ? ' disabled' : ''}>${ic('more')}</button></span>${agentSelect('rail')}`;
}
function proofMenu(pr, page) {
  if (!pr || !pr.available || !state.prMenu.open || prRunning(pr)) return '';
  const m = state.prMenu, max = pr.max_pages || 20;
  return `<div class=prmenu><span>Pages</span><input class=pgin data-prf=from value="${esc(m.from || page)}" inputmode=numeric aria-label="From page">–` +
    `<input class=pgin data-prf=to value="${esc(m.to || page)}" inputmode=numeric aria-label="To page" max=${max}>` +
    `<button type=button class="btn primary" data-pr=range>Proofread</button></div>`;
}
const proofCalls = p => {
  const n = p.calls || 1;
  return `${n} call${n === 1 ? '' : 's'}${showCost() && p.cost_usd != null ? ` · $${Number(p.cost_usd).toFixed(4)}` : ''}`;
};
function proofMark(rec) {
  const p = rec.proofread || {};
  const tip = [(p.changes || []).join(', '), p.model || '', p.at || '', proofCalls(p)].filter(Boolean).join(' · ');
  return `<span class=prmark><span class=chip title="${esc(tip)}">Proofread</span><button type=button class="btn sm" data-pra=revert>Revert</button></span>`;
}
function proofDiff(p) {
  const seg = (p.diff || []).map(s => s.op === 'eq' ? esc(s.text) : `<${s.op === 'del' ? 'del' : 'ins'}>${esc(s.text)}</${s.op === 'del' ? 'del' : 'ins'}>`).join('');
  const chips = [...(p.changes || []), proofCalls(p)].map(c => `<span>${esc(c)}</span>`).join('');
  return `<div class=prhead><div class=pract><button type=button class="btn primary" data-pra=accept>Accept</button><button type=button class=btn data-pra=reject>Reject</button></div>` +
    `<div class=chips>${chips}</div></div><div class="ptext prdiff">${seg}</div>`;
}
async function loadProof(ctx, force) {
  const src = railSource(ctx), page = railPage(ctx), key = prKey(src, page);
  const rec = state.pt[`${src}|${page}`];
  if (!src || !rec || !rec.ok || rec.kind !== 'pages') return;
  if (!force && (key in state.pr || state.pr[key + '|busy'])) return;
  state.pr[key + '|busy'] = true;
  try {
    state.pr[key] = await api(`/api/proofread?source=${encodeURIComponent(src)}&page=${page}`);
  } catch (e) {
    state.pr[key] = null;
  }
  delete state.pr[key + '|busy'];
  const pr = state.pr[key];
  if (pr && prRunning(pr)) setTimeout(() => { if (railSource(ctx) === src) loadProof(ctx, true); }, 2000);
  else if (pr && force) delete state.pt[`${src}|${page}`];               // a job just ended: its pages' text may have changed
  drawRail(ctx);
}
async function proofStart(ctx, pages) {
  const src = railSource(ctx);
  try {
    await api('/api/proofread', {source: src, pages, model: prefs.agent});
    setError('proof', null);
    state.prMenu.open = false;
    const a = parseInt(pages, 10), b = parseInt(String(pages).split('-')[1] || a, 10);
    const pr = state.pr[prKey(src, railPage(ctx))];
    if (pr) pr.job = {status: 'running', done: 0, total: b - a + 1};
    drawRail(ctx);
    loadProof(ctx, true);
  } catch (e) {
    setError('proof', `Proofread: ${e.message}`);
  }
}
async function proofAct(ctx, action) {
  const src = railSource(ctx), page = railPage(ctx);
  try {
    await api('/api/proofread/act', {source: src, page, action});
    setError('proof', null);
  } catch (e) {
    setError('proof', `Proofread: ${e.message}`);
  }
  delete state.pr[prKey(src, page)];
  delete state.pt[`${src}|${page}`];
  drawRail(ctx);
}
async function ensureText(ctx) {
  const src = railSource(ctx), page = railPage(ctx), key = `${src}|${page}`;
  if (!src || key in state.pt || state.pt[key + '|busy']) return;
  state.pt[key + '|busy'] = true;
  try {
    const got = await api(`/api/pagetext?source=${encodeURIComponent(src)}&page=${page}`);
    state.pt[key] = got;
    state.ptTotal[src] = got.total;
  } catch (e) {
    state.pt[key] = {ok: false, why: e.message};
  }
  delete state.pt[key + '|busy'];
  drawRail(ctx);
}
function showText(ctx, page) {                   // a cell of the Pages bar or the heatmap: the rail shows that page's text
  if (!railBox(ctx)) return;
  state.rail[ctx].page = page;
  setRailTab(ctx, 'text');
}
function gotoPage(ctx, n) {
  const src = railSource(ctx), total = state.ptTotal[src] || Infinity;
  if (!(n >= 1)) return;
  state.rail[ctx].page = Math.min(n, total);
  drawRail(ctx);
}

// ---- choices (the Flow tab of a page-flow run) ---------------------------------------------------------------------
// Under the route: the agent's choices, read by the manager from the run's files (choices.py, /api/choices/{id}), each under the
// step it belongs to. Each choice is one line; a click shows its quote or reason. The list is patched line by line (chSync), so a poll never redraws the line the person has open.
const CH_ICON = {Kept: 'ok', Dropped: 'chdrop', Merged: 'chmerge', Asked: 'Asked', Refused: 'chrefuse'};
const CH_GROUPS = {triage: ['Skipped'], names: ['Kept', 'Dropped', 'Returned'], roster: ['New', 'Same', 'Asked', 'Refused'],
                   records: ['Facts', 'Ties', 'Unplaced'], identity: ['Joined', 'Left'], close: ['Flagged']};
const CH_CHUNK = 40;
Object.assign(SV, {chdrop: '<path d="M6 12h12"/>', chmerge: '<path d="M5 5l7 7 7-7M12 12v8"/>', chrefuse: '<circle cx=12 cy=12 r=8 /><path d="M6.5 6.5l11 11"/>'});
state.choices = {mode: 'step', data: {}, at: {}, live: {}, busy: {}, open: {}, item: null, more: {}};

function choicesLive(id) {
  const r = state.byId[id] || {};
  return r.status === 'running' || r.status === 'stopping' || r.status === 'starting';
}
async function loadChoices(id) {
  const c = state.choices, live = choicesLive(id), age = Date.now() - (c.at[id] || 0);
  if (!id || c.busy[id] || (c.data[id] && (live ? age < 2500 : c.live[id] === false))) return;
  c.busy[id] = true;
  try {
    c.data[id] = await api(`/api/choices/${encodeURIComponent(id)}`);
  } catch (e) {
    c.data[id] = c.data[id] || {ticks: [], choices: []};
  }
  c.at[id] = Date.now();
  c.live[id] = live;
  c.busy[id] = false;
  if (state.sel === id && railTab('run') === 'flow') drawRail('run');
}
// the tick's result on the route: one short line under the step's label (the whole text is its title and opens the step's choices)
function tickHTML(key) {
  const d = state.choices.data[state.sel];
  const ts = d ? d.ticks.filter(t => t.key === key) : [];
  if (!ts.length) return '';
  const last = ts[ts.length - 1];
  return `<button type=button class="rk${last.ok ? '' : ' bad'}" data-tick="${esc(key)}" title="${esc(ts.map(t => t.ok ? t.detail || t.line : t.error || t.line).join('\n'))}">${esc(last.line)}</button>`;
}
function chSections(mode, d, rows) {
  const cs = d.choices || [], ts = d.ticks || [], label = k => (rows.find(r => r.key === k) || {}).label || k;
  const rank = (key, g) => { const i = (CH_GROUPS[key] || []).indexOf(g); return i < 0 ? 99 : i; };
  const bucket = (list, keyOf, order) => {
    const m = new Map();
    list.forEach(x => { const k = keyOf(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); });
    return [...m].sort((a, b) => order(a[0]) - order(b[0])).map(([key, items]) => ({key, items}));
  };
  return rows.filter(r => cs.some(c => c.key === r.key) || ts.some(t => t.key === r.key && !t.ok)).map(r => {
    const mine = cs.filter(c => c.key === r.key);
    return {key: r.key, title: r.label, state: r.state, ticks: ts.filter(t => t.key === r.key),
            groups: bucket(mine, c => c.group, g => rank(r.key, g)).map(g => ({...g, title: g.key}))};
  });
}
function chItemHTML(c, on) {
  const has = c.quote || c.why || c.outcome;
  const line = `${ic(CH_ICON[c.kind])}<span class=chv>${esc(c.verb)}${c.who ? modelMark(c.who) : ''}</span><span class=cho>${esc(c.object)}</span><span class=chp>${c.page != null ? `p. ${c.page}` : ''}</span>`;
  const detail = on && has ? `<div class=chd>${c.quote ? `<q>${esc(c.quote)}</q>` : ''}${c.why ? `<p>${esc(c.why)}</p>` : ''}${c.outcome ? `<p class=chot>${esc(c.outcome)}</p>` : ''}</div>` : '';
  return `<button type=button class=chb data-ci="${esc(c.id)}"${has ? ` aria-expanded=${on}` : ''}>${line}</button>${detail}`;
}
function chSync(ul, specs) {
  const have = new Map([...ul.children].map(li => [li.dataset.key, li]));
  let prev = null;
  for (const s of specs) {
    let li = have.get(s.key);
    have.delete(s.key);
    if (!li) { li = document.createElement('li'); li.dataset.key = s.key; }
    const h = s.cls + '\u0001' + s.html;
    if (li.dataset.h !== h) { li.className = s.cls; li.innerHTML = s.html; li.dataset.h = h; }
    const at = prev ? prev.nextSibling : ul.firstChild;
    if (at !== li) ul.insertBefore(li, at);
    prev = li;
  }
  have.forEach(li => li.remove());
}
function chSpecs(mode, secs, id) {
  const c = state.choices, specs = [];
  const firstOpen = (secs.find(s => s.state === 'now' || s.state === 'failed') || secs[mode === 'step' ? secs.length - 1 : 0] || {}).key;
  for (const s of secs) {
    const ok = `${id}|${mode}|${s.key}`, on = ok in c.open ? c.open[ok] : s.key === firstOpen;
    specs.push({key: `${mode}:${s.key}`, cls: `chs${on ? ' on' : ''}`,
                html: `<button type=button class=chh data-cs="${esc(ok)}" aria-expanded=${on}>${ic('more', 'chev')}<span>${esc(s.title)}</span></button>`});
    if (!on) continue;
    (s.ticks || []).filter(t => !t.ok || t.detail).forEach((t, i) => specs.push({key: `${mode}:${s.key}:t${i}`, cls: `cht${t.ok ? '' : ' bad'}`, html: esc(t.ok ? t.detail : t.error || t.line)}));
    for (const g of s.groups) {
      const gk = `${mode}:${s.key}:${g.key}`, shown = (c.more[gk] || 1) * CH_CHUNK;
      if (mode !== 'step' || s.groups.length > 1) specs.push({key: `${gk}:h`, cls: 'chg', html: esc(g.title)});
      g.items.slice(0, shown).forEach(x => specs.push({key: `${mode}:${x.id}`, cls: `chi k-${x.kind}${c.item === x.id ? ' on' : ''}`, html: chItemHTML(x, c.item === x.id)}));
      if (g.items.length > shown) specs.push({key: `${gk}:m`, cls: 'chmore', html: `<button type=button class="btn sm" data-cmore="${esc(gk)}">More</button>`});
    }
  }
  return specs;
}
function drawChoices(body, rows) {
  const id = state.sel, r = state.byId[id], c0 = state.choices;
  if (!r || r.kind !== 'pageflow') { $('.chx', body).innerHTML = ''; return; }
  if (c0.item && c0.itemRun !== id) c0.item = null;
  c0.itemRun = id;
  loadChoices(id);
  const c = state.choices, d = c.data[id];
  chSync($('.chx', body), d ? chSpecs(c.mode, chSections(c.mode, d, rows), id) : []);
}
function chFrame(body) {                       // the route's own box, the choices below it
  if (!$('.chw', body)) body.innerHTML = '<div class=chw></div><ul class=chx></ul>';
  return $('.chw', body);
}
function chClick(e) {
  const c = state.choices, q = s => e.target.closest(s);
  let el;
  if ((el = q('[data-cs]'))) { const k = el.dataset.cs; c.open[k] = !(el.getAttribute('aria-expanded') === 'true'); }
  else if ((el = q('[data-ci]'))) { if (!el.hasAttribute('aria-expanded')) return true; c.item = c.item === el.dataset.ci ? null : el.dataset.ci; }
  else if ((el = q('[data-cmore]'))) { c.more[el.dataset.cmore] = (c.more[el.dataset.cmore] || 1) + 1; }
  else if ((el = q('[data-tick]'))) {
    c.open[`${state.sel}|step|${el.dataset.tick}`] = true;
  } else return false;
  drawRail('run');
  if (el.dataset.tick) { const h = $(`[data-cs="${state.sel}|step|${el.dataset.tick}"]`); if (h) h.scrollIntoView({block: 'nearest'}); }
  return true;
}
document.addEventListener('keydown', e => {   // Esc deselects the open choice before anything else takes it
  if (e.key !== 'Escape' || state.choices.item == null) return;
  state.choices.item = null;
  e.stopImmediatePropagation();
  drawRail('run');
}, true);

// ---- the rail ------------------------------------------------------------------------------------------------------
function flowRows(ctx) {
  const c = state.cfg;
  if (ctx === 'cfg') return {rows: c && c.detail ? cfgFlowRows(c).map(r => ({...r, state: r.state === 'skipped' ? 'skipped' : 'next'})) : [], id: `cfg:${c && c.flow}`, preview: true};
  const v = state.view && state.view.tab === 'run' ? state.view : null;
  return {rows: v ? runFlowRows(v) : [], id: state.sel, preview: false, v, r: state.byId[state.sel]};
}
function drawRail(ctx) {
  const box = railBox(ctx);
  if (!box) return;
  const tab = railTab(ctx);
  if (!$('.rtabs', box)) box.innerHTML = '<div class=rtabs role=tablist></div><div class=rbody></div>';
  const strip = $('.rtabs', box), body = $('.rbody', box);
  const tabs = RAIL_TABS[ctx].map(([k, w]) => `<button type=button role=tab data-rtab="${k}" aria-selected=${k === tab}>${w}</button>`).join('');
  if (strip.dataset.k !== tabs) { strip.dataset.k = tabs; strip.innerHTML = tabs; }
  const switched = box.dataset.t !== tab;
  const top = !switched ? body.scrollTop : 0;
  box.dataset.t = tab;
  if (tab === 'flow') {
    if (switched) { body.dataset.k = ''; body.innerHTML = ''; }
    const f = flowRows(ctx);
    if (ctx === 'run') {
      drawRoute(ctx, chFrame(body), f.rows, f.id, f.v, f.r, f.preview);
      drawChoices(body, f.rows);
    } else drawRoute(ctx, body, f.rows, f.id, f.v, f.r, f.preview);
  } else {
    const html = tab === 'def' ? defHTML(ctx) : tab === 'text' ? textHTML(ctx) : setupHTML();
    if (switched || body.dataset.k !== html) { body.dataset.k = html; body.innerHTML = html; body.scrollTop = top; }
  }
  if (switched) motionFade(body);
  if (tab === 'text') { ensureText(ctx); loadProof(ctx); }
}
document.addEventListener('click', e => {
  const rail = e.target.closest('.rail');
  if (!rail) return;
  const ctx = rail.dataset.ctx;
  if (ctx === 'run' && chClick(e)) return;
  const t = e.target.closest('[data-rtab]');
  if (t) { setRailTab(ctx, t.dataset.rtab); return; }
  const pg = e.target.closest('[data-pg]');
  if (pg) { gotoPage(ctx, railPage(ctx) + Number(pg.dataset.pg)); return; }
  const pr = e.target.closest('[data-pr]');
  if (pr) {
    const page = railPage(ctx), kind = pr.dataset.pr;
    if (kind === 'this' && !e.shiftKey) proofStart(ctx, String(page));
    else if (kind === 'this' || kind === 'menu') {
      state.prMenu = {open: !state.prMenu.open || kind === 'this', from: String(page), to: String(page)};
      drawRail(ctx);
    } else if (kind === 'range') {
      const a = parseInt(state.prMenu.from, 10), b = parseInt(state.prMenu.to, 10);
      if (!(a >= 1 && b >= a)) setError('proof', 'Proofread: pages A-B, with A at most B.');
      else if (b - a + 1 > 20) setError('proof', 'Proofread: at most 20 pages at a time.');
      else proofStart(ctx, a === b ? String(a) : `${a}-${b}`);
    }
    return;
  }
  const pa = e.target.closest('[data-pra]');
  if (pa) proofAct(ctx, pa.dataset.pra);
});
document.addEventListener('input', e => {
  const f = e.target.dataset && e.target.dataset.prf;
  if (f && e.target.closest('.rail')) state.prMenu[f] = e.target.value;
});
document.addEventListener('change', e => {
  if (!e.target.matches('.rail .pgin:not([data-prf])')) return;
  gotoPage(e.target.closest('.rail').dataset.ctx, parseInt(e.target.value, 10));
});
