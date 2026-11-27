// Apollo's page: the runs (polled every second), the selected run's route, measures and tabs (polled while shown), the
// recipes in the bar and Configure (read only), Start, Stop, Folder. Everything comes from the Go app's API over apollo.py;
// every POST carries the page's token. The page allows no inline styles: sizes and positions are set from data-w and
// data-css attributes after each render.
'use strict';
const TOKEN = document.querySelector('meta[name=apollo-token]').content;
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const state = {runs: [], byId: {}, sel: null, tab: 'run', view: null, viewKey: '', runsKey: '', quotes: false,
               configs: {recipes: [], sources: [], briefs: []}, errors: {}, busy: {}, gone: false,
               cfg: null, tools: {}, toolAsked: {}, toolWatch: {},     // cfg: the open Configure {name, source, detail, prompt, text, dry, from, to}
               page: null,                                              // the open view: null (Runs), 'sources' or 'collections'
               rail: {cfg: {tab: 'flow', page: 0}, run: {tab: 'flow', page: 0}}, pt: {}, ptTotal: {}};   // the right rail, one per view
const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---- the API ---------------------------------------------------------------------------------------------------
async function api(path, body) {
  const opt = body === undefined ? {} : {method: 'POST', body: JSON.stringify(body),
    headers: {'Content-Type': 'application/json', 'X-Apollo-Token': TOKEN}};
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (_) { /* no JSON */ }
  if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
  return j;
}

function setError(key, msg) {          // a notice, once per change: the toast says it, the log keeps it
  if (!msg) { delete state.errors[key]; return; }
  if (state.errors[key] === msg) return;
  state.errors[key] = msg;
  notify('error', msg);
}

// ---- icons (the Tasks page's set, as the design draws them) ------------------------------------------------------
const SV = {
  play: '<path d="M7 4.5v15l12-7.5z"/>', flow: '<path d="M6 4h12M6 12h12M6 20h12"/><circle cx=4 cy=4 r=.6 /><circle cx=4 cy=12 r=.6 /><circle cx=4 cy=20 r=.6 />',
  smoke: '<path d="M5 19h14M8 15V9M12 15V5M16 15v-3"/>', agent: '<rect x=5 y=8 width=14 height=11 rx=3 /><path d="M12 4v4M9 13h.01M15 13h.01"/>',
  rate: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.9z"/>', lag: '<circle cx=12 cy=12 r=8 /><path d="M12 7.5V12l3 2"/>',
  Asked: '<path d="M5 6h14v9H10l-4 3.5V15H5z"/>', Found: '<circle cx=11 cy=11 r=6 /><path d="M20 20l-4.5-4.5"/>', Added: '<path d="M12 5v14M5 12h14"/>',
  Cited: '<path d="M7 7h4v4c0 3-1.5 5-4 6M14 7h4v4c0 3-1.5 5-4 6"/>', Ran: '<path d="M7 4.5v15l12-7.5z"/>', Escalated: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  calls: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>', ok: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', miss: '<path d="M6 6l12 12M18 6L6 18"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 010 11H11"/>',
  seal: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>'
};
const ic = (n, c = '') => `<span class="ic ${c}"><svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round aria-hidden=true>${SV[n] || SV.calls}</svg></span>`;

// ---- small formats ---------------------------------------------------------------------------------------------
const secs = ms => ms == null ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
const kChars = n => n < 1000 ? String(n) : `${(n / 1000).toFixed(n < 10000 ? 1 : 1)}k`;
function elapsed(s) {
  if (s == null) return '';
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
}
const kindText = k => k === 'pageflow' ? 'page flow' : k === 'mcp-session' ? 'session' : k;
const runName = s => s.flow || s.name || s.id;                              // "Read the source"; a brief session keeps its own name
const runWhat = s => s.flow ? (s.source_title || '') : kindText(s.kind);        // what it works on, for the line under the name
const modelText = m => String(m || '').replace(/^claude-/, '');
const modelLabel = m => { const x = modelText(m).split('-'); return x.length >= 2 ? `${x[0][0].toUpperCase()}${x[0].slice(1)} ${x.slice(1).join('.')}` : modelText(m); };
const showCost = () => !!prefs.cost;                       // money is hidden unless the cog's "Show cost" is on; calls always show
const detailText = d => d === 'stopped-budget' ? 'Stopped: the cost cap was reached' : d;
const usd = v => v == null ? '' : `$${Number(v).toFixed(2)}`;
const dollars = v => v == null ? '' : `$${Number(v) % 1 ? Number(v).toFixed(2) : Number(v)}`;
const secText = s => s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
function applyCss(root) {   // the bars' widths and the grid positions: set here, since the page allows no inline styles
  root.querySelectorAll('[data-w]').forEach(e => { e.style.width = e.dataset.w + '%'; });
  root.querySelectorAll('[data-css]').forEach(e => {
    for (const d of e.dataset.css.split(';')) {
      const i = d.indexOf(':');
      if (i > 0) e.style.setProperty(d.slice(0, i).trim(), d.slice(i + 1).trim());
    }
  });
}
const meter = (u, cap) => `<span class="mt${cap != null && u >= cap ? ' full' : ''}"><i data-w="${cap ? Math.min(100, u / cap * 100).toFixed(1) : 0}"></i></span>`;

// ---- the page cells: records shade one hue, effort the accent ---------------------------------------------------
const bin = n => n >= 11 ? 5 : n >= 7 ? 4 : n >= 4 ? 3 : n >= 2 ? 2 : 1;
const binC = n => Math.min(5, Math.max(1, n));                       // a page takes one call a pass, more when repaired: one shade a call
const binT = s => s >= 180 ? 5 : s >= 140 ? 4 : s >= 100 ? 3 : s >= 60 ? 2 : 1;
const isDone = st => st === 'done' || st === 'escalated' || st === 'gave-up';
const off = st => st === 'empty' ? 'skip' : st === 'redacted' ? 'redacted' : 'todo';
const SW = {done: 'sd', escalated: 'se', 'gave-up': 'sg', doing: 'doing', empty: 'skip', redacted: 'redacted', todo: 'todo'};
const WORD = {done: 'done', escalated: 'escalated', 'gave-up': 'gave up', doing: 'reading', empty: 'skipped', redacted: 'redacted', todo: 'to do'};
// a measure fills as soon as the page has any of it (a page is read in several passes, so it stays "reading" until the last);
// none on a finished page is the empty mark
const cellRec = (st, n) => n ? 's' + bin(n) : isDone(st) ? 'z' : off(st);
// the tab bar of the run view and of Sources: one definition. `attr` is the data attribute a tab's key goes in; a tab in `counted` has a
// count beside its name (filled by the page that owns the tab: empty while it has none)
const RUN_TABS = [['run', 'Run'], ['found', 'Found'], ['questions', 'Questions'], ['calls', 'Calls'], ['runs', 'Runs'], ['raw', 'Raw'], ['report', 'Report'], ['data', 'Data']];
const RUN_COUNTED = ['found', 'questions', 'calls', 'runs'];
function tabBar(tabs, current, attr, counted, counts = {}) {
  return `<nav class=tabs role=tablist${attr === 'tab' ? ' id=runtabs' : ''}>${tabs.map(([k, w]) => `<button type=button role=tab data-${attr}=${k}${current ? ` aria-selected=${current === k}` : ''}>${w}` +
    `${counted.includes(k) ? ` <small>${counts[k] || ''}</small>` : ''}</button>`).join('')}</nav>`;
}
$('#runtabs').outerHTML = tabBar(RUN_TABS, '', 'tab', RUN_COUNTED);
const escMod = h => h.status === 'escalated' || h.status === 'gave-up' ? ' esc' : '';     // escalated: a pattern over its shade, like redacted (owner)
const HEAT = {
  named: h => state.namedPages && state.namedPages.has(h.page) ? 'tk' : 'tk0',     // Found: where the selected thing is named
  asked: h => h.asked ? 'e' + Math.min(4, h.asked + 1) : 'tk0',                     // Run: where questions were put to the person
  changed: h => h.changed ? 's' + bin(h.changed) : 'tk0',                           // a workflow run: where it added, changed or removed records
  status: h => SW[h.status] || 'todo',
  records: h => (h.status === 'doing' && !h.records ? 'doing' : cellRec(h.status, h.records)) + escMod(h),
  quotes: h => cellRec(h.status, h.quotes),
  calls: h => h.calls ? 'e' + binC(h.calls) : off(h.status),
  seconds: h => h.seconds ? 'e' + binT(h.seconds) : off(h.status)
};
const HEAT_LABEL = {changed: 'Changed', asked: 'Asked', status: 'Status', records: 'Records', quotes: 'Quotes', places: 'Places', calls: 'Calls', seconds: 'Seconds'};
/* heat groups */
const GROUPS = [['people', 'People'], ['bodies', 'Bodies'], ['places', 'Places'], ['events', 'Events'], ['ties', 'Ties'], ['posts', 'Positions']];
const GROUP_LABEL = Object.fromEntries(GROUPS);
for (const [k] of GROUPS) HEAT[k] = h => { const c = cellRec(h.status, (h.groups || {})[k]); return c === 'z' ? 'zg' : c; };   // none of one category on a read page is normal: a quiet cell, not the None outline
Object.assign(HEAT_LABEL, GROUP_LABEL);
const HEAT_N = {changed: h => h.changed || 0, asked: h => h.asked || 0, records: h => h.records, quotes: h => h.quotes, calls: h => h.calls, seconds: h => Math.round(h.seconds)};
for (const [k] of GROUPS) HEAT_N[k] = h => (h.groups || {})[k] || 0;
const wfRun = () => ((state.byId || {})[state.sel] || {}).kind === 'mcp-session';          // a workflow run's pages: scope, changed, asked
const heatMeasures = rows => ['records', ...GROUPS.map(g => g[0]).filter(k => rows.some(r => (r.groups || {})[k])), ...(rows.some(r => r.changed) ? ['changed'] : []), ...(rows.some(r => r.asked) ? ['asked'] : [])];
// the legend says only what the rows show: records by shade, skipped, escalated; redacted and reading when there are any
const heatKeys = (rows, asked = false) => rows.length && wfRun() ? ['rec', ...(rows.some(r => r.changed) ? ['chg'] : []), ...(asked && rows.some(r => r.asked) ? ['asked'] : []), ...(rows.some(r => r.status === 'empty') ? ['skip'] : []), ...(rows.some(r => r.status === 'redacted') ? ['red'] : [])] : ['rec', 'skip', 'esc', ...(asked && rows.some(r => r.asked) ? ['asked'] : []), ...(rows.some(r => r.status === 'redacted' || r.red) ? ['red'] : []), ...(rows.some(r => r.status === 'doing') ? ['doing'] : [])];
const heatTitle = (m, r) => pgw(r.page) + (HEAT_N[m] ? ` · ${HEAT_N[m](r)}` : '');
const strip3 = cells => `<div class="strip3${cells.length > 40 ? ' dense' : ''}">${cells.map(c => `<i class="${cellRec(c.status, c.records)}" title="p. ${c.page}"></i>`).join('')}</div>`;
const ppText = cells => cells.length ? `Pages ${cells[0].page}–${cells[cells.length - 1].page}` : '';      // owner: Page 40, not Part 40, for every source
const pgw = p => 'Page ' + p;

// ---- the runs --------------------------------------------------------------------------------------------------
function dotOf(s) {
  if (s.status === 'running' || s.status === 'starting' || s.status === 'stopping') return 'live';
  if (s.status === 'done') return 'done';
  if (s.status === 'stopped') return 'budget';
  return 'failed';
}
const stageText = s => {
  const p = s.progress || {};
  const ck = s.status === 'running' && p.chunk && p.chunk.of > 1 ? ` · ${p.chunk.at} of ${p.chunk.of}` : '';
  return (p.label || '') + ck;
};
function modelMeters(s) {
  const c = s.cost || {};
  if (showCost() && c.budget_usd != null) return `<div class=meters>${meter(c.usd || 0, c.budget_usd)}<span><b>${dollars(c.usd || 0)}</b> of ${dollars(c.budget_usd)}</span></div>`;
  if (c.haiku) {
    return `<div class=meters><span>Haiku</span>${meter(c.haiku[0], c.haiku[1])}<span><b>${c.haiku[0]}</b> / ${c.haiku[1] ?? '∞'}</span>` +
      (c.sonnet ? `<span>Sonnet</span>${meter(c.sonnet[0], c.sonnet[1])}<span><b>${c.sonnet[0]}</b> / ${c.sonnet[1] ?? '∞'}</span>` : '') + '</div>';
  }
  if (showCost() && c.budget_usd != null) return `<div class=meters><span>Cap</span>${meter(c.usd || 0, c.budget_usd)}<span><b>$${(c.usd || 0).toFixed(0)}</b> / ${c.budget_usd}</span></div>`;
  return '<span class=dim>—</span>';
}
// ---- the runs, one entry a source: a light for each flow that has run on it (owner: "like an engine light") --------
// A source is a run's `source`; a workflow run started for a source carries its slug at the end of its name; a run with
// neither belongs to its archive.
const FLOW_KEY = {'Read the source': 'read', 'Suggest links': 'wikidata', 'Place names': 'places', 'Fill stubs': 'stubs', 'Enrich': 'enrich',
                  Basic: 'read', Read: 'read', Links: 'wikidata', Places: 'places', Stubs: 'stubs', Assemble: 'assemble', Refine: 'refine', Timeline: 'timeline',
                  Wikidata: 'wikidata', Finance: 'finance', links: 'wikidata', Mark: 'mark', Fit: 'fit'};     // Links became Wikidata (CZ318): old runs show there
const LIGHTS = [['read', 'Basic'], ['mark', 'Mark'], ['fit', 'Fit'], ['assemble', 'Assemble'], ['refine', 'Refine'], ['timeline', 'Timeline'], ['places', 'Places'], ['stubs', 'Stubs'],
                ['enrich', 'Enrich'], ['wikidata', 'Wikidata'], ['finance', 'Finance']];
Object.assign(SV, {person: '<path d="M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0"/><path d="M6 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2"/>', download: '<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2"/><path d="M7 11l5 5l5 -5"/><path d="M12 4l0 12"/>', links: '<path d="M9 15l6 -6"/><path d="M11 6l.463 -.536a5 5 0 0 1 7.071 7.072l-.534 .464"/><path d="M13 18l-.397 .534a5.068 5.068 0 0 1 -7.127 0a4.972 4.972 0 0 1 0 -7.071l.524 -.463"/>'});
const flowKey = s => s.flow_key || FLOW_KEY[s.flow] || (s.kind === 'pageflow' ? 'read' : 'other');
const isLive = s => s.status === 'running' || s.status === 'starting' || s.status === 'stopping';
const archName = p => { const b = String(p || '').split('/').filter(Boolean).pop() || ''; return b ? b[0].toUpperCase() + b.slice(1) : 'Archive'; };
function runSource(s) {
  const known = state.configs.sources || [];
  const slug = s.source || (known.find(k => k.name && String(s.name || '').endsWith('-' + k.name)) || {}).name;
  if (slug) {
    const k = known.find(x => x.name === slug);
    return {key: 'src:' + slug, title: (k && k.title) || (s.source === slug && s.source_title) || slug};
  }
  return {key: 'arc:' + (s.archive || ''), title: archName(s.archive_name || s.archive)};
}
// state.runs is newest first with live runs on top, so each group's runs keep that order and the groups follow their newest run
function sourceGroups(runs) {
  const out = new Map();
  for (const s of runs) {
    const o = runSource(s);
    if (!out.has(o.key)) out.set(o.key, {...o, runs: [], archive: s.archive});
    out.get(o.key).runs.push(s);
  }
  return [...out.values()];
}
const groupOf = id => (state.groups || []).find(g => g.runs.some(r => r.id === id));
// times, one way everywhere (owner): "November 25, 2026 6:36 PM"; a time of day alone is "6:36:22 PM". A stamp without an offset is
// local time; a bare date has no time.
function longWhen(v) {
  if (!v) return '';
  const s = String(v).trim().replace(' ', 'T'), dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(s);
  const d = dateOnly ? new Date(s + 'T00:00:00') : new Date(s);
  if (isNaN(d)) return String(v);
  const day = d.toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'});
  return dateOnly ? day : `${day} ${d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'})}`;
}
function dayTime(t) {                                   // "18:25:22" → "6:25:22 PM"
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(String(t || ''));
  if (!m) return t || '';
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]}${m[3] ? ':' + m[3] : ''} ${h < 12 ? 'AM' : 'PM'}`;
}
const clock = longWhen;
function lightsHTML(g) {
  return '<span class=lights>' + LIGHTS.map(([k, label]) => {
    const rs = g.runs.filter(r => flowKey(r) === k);
    if (!rs.length) return `<span class=lt title="${esc(label)}: not run">${ic(k)}</span>`;
    const top = rs[0], n = rs.length;
    return `<button type=button class="lt ${dotOf(top)}" data-id="${esc(top.id)}" title="${esc(`${label}: ${top.status}${n > 1 ? ` · ${n} runs` : ''}`)}">${ic(k)}</button>`;
  }).join('') + '</span>';
}
// a mini heat row per flow with a run on the entry's source that has page cells (its newest), in Apollo's flow order, Basic first; with
// more than one row each carries its flow's icon
function miniRows(g) {
  const rows = LIGHTS.map(([k, label]) => ({k, label, run: g.runs.find(r => flowKey(r) === k && (r.cells || []).length)})).filter(x => x.run);
  return rows.map(({k, label, run}) => `<div class="mini fl-${k}${rows.length > 1 ? " has-mi" : ""}" title="${esc(k === 'read' ? ppText(run.cells) : `${label} · ${ppText(run.cells)}`)}">` +
    `${rows.length > 1 ? `<span class=mi>${ic(k)}</span>` : ''}${run.cells.map(c => `<i class="${cellRec(c.status, c.records)}"></i>`).join('')}</div>`).join('');
}
function srcItem(g) {
  const top = g.runs[0];
  const sel = g.runs.some(r => r.id === state.sel);
  const meta = [archName(g.archive) !== g.title ? archName(g.archive) : '', isLive(top) ? stageText(top) || top.status : longWhen(top.started)].filter(Boolean).join(' · ');
  return `<li data-src="${esc(g.key)}"${sel ? ' class=sel' : ''}><i class="dot ${dotOf(top)}"></i><b title="${esc(g.title)}">${esc(g.title)}</b>` +
    `<span></span><small>${esc(meta)}</small>${lightsHTML(g)}` +
    miniRows(g) + '</li>';
}
// the run on show in the run's head, and a select of the source's other runs, newest first (owner: one run, not a row of them)
function runSwitch() {
  const g = groupOf(state.sel), r = state.byId[state.sel];
  if (!g || !r || g.runs.length < 2) return '';
  const opts = g.runs.map(x => `<option value="${esc(x.id)}"${x.id === state.sel ? ' selected' : ''}>${esc(runName(x))} · ${esc(clock(x.started))}</option>`).join('');
  return `<div class=runsw><i class="dot ${dotOf(r)}"></i>${ic(flowKey(r))}<select class=runsel aria-label=Runs title="${esc(r.id)}">${opts}</select></div>`;
}

async function pollRuns() {
  if (state.gone || state.busy.runs) return;
  state.busy.runs = true;
  try {
    const rows = await api('/api/runs');
    setError('runs', null);
    if (window.apolloReady) window.apolloReady();   // the launch screen (launch/launch.js) leaves after the first good fetch
    // newest first, a running run stays on top
    const live = r => r.status === 'running' || r.status === 'starting' || r.status === 'stopping';
    state.runs = rows.slice().reverse().sort((a, b) => live(b) - live(a));
    state.byId = Object.fromEntries(rows.map(r => [r.id, r]));
    state.groups = sourceGroups(state.runs);
    const html = state.groups.map(srcItem).join('');
    if (html !== state.runsKey) {
      state.runsKey = html;
      $('#runlist').innerHTML = html;
    }
    $('#nruns').textContent = state.groups.length || '';
    if (state.sel && !state.byId[state.sel]) select(null);
    if (!state.sel && state.groups.length) select(state.groups[0].runs[0].id);   // the landing view is the newest source's newest run
    head();
  } catch (e) {
    setError('runs', `Runs: ${e.message}`);
  } finally {
    state.busy.runs = false;
  }
}

// a light opens that flow's newest run; the rest of the entry opens the source (the run on show stays if it is the source's)
$('#runlist').addEventListener('click', e => {
  const lt = e.target.closest('[data-id]'), li = e.target.closest('[data-src]');
  if (!lt && !li) return;
  const g = (state.groups || []).find(x => x.key === (li && li.dataset.src));
  const id = lt ? lt.dataset.id : g && (g.runs.some(r => r.id === state.sel) ? state.sel : g.runs[0].id);
  if (id) { closeCfg(); state.page = null; select(id); layout(); }
});
$('#route').addEventListener('change', e => {
  if (e.target.matches('.runsel') && e.target.value) select(e.target.value);
});

function layout() {
  const w = $('#wrap');
  const cfg = !!state.cfg, pg = state.page;
  w.classList.toggle('cfgmode', cfg);
  w.classList.toggle('srcmode', pg === 'sources');
  w.classList.toggle('colmode', pg === 'collections');
  w.classList.toggle('hismode', pg === 'history');
  w.classList.toggle('setmode', pg === 'settings');                                    // CZ330: Settings
  w.classList.toggle('sel', !cfg && !pg);                // the runs: the sources on the left, the selected run beside them
  w.classList.toggle('one', cfg || !!pg);
  $('#home').setAttribute('aria-current', cfg || pg ? 'false' : 'page');
  $('#nrun').setAttribute('aria-pressed', String(cfg));
  $('#nsrc').setAttribute('aria-pressed', String(pg === 'sources'));
  $('#ncol').setAttribute('aria-pressed', String(pg === 'collections'));
  $('#nhis').setAttribute('aria-pressed', String(pg === 'history'));
  $('#cog').setAttribute('aria-pressed', String(pg === 'settings'));
}

function select(id) {
  if (id === state.sel) return;
  state.sel = id;
  state.view = null;
  state.viewKey = '';
  $('#path').hidden = true;
  state.runsKey = '';
  $('#tabbody').innerHTML = '';
  $('#tabbody').dataset.run = '';
  $('#route').innerHTML = '';
  $('#route').dataset.k = '';
  tools();
  layout();
  head();
  pollRuns();
  if (id) pollView();
}

// ---- the selected run's head: state, the route, the measures -----------------------------------------------------
// limits: a run's caps on or off; a live run's switch is the server's, a finished run's only changes the display
const limOv = {};
const limitsOn = s => s.id in limOv ? limOv[s.id] : s.limits !== false;
function stat(label, v) { return `<span><label>${label}</label>${v}</span>`; }
function head() {
  const s = state.byId[state.sel];
  const d = $('#detail');
  d.classList.toggle('none', !s);
  if (!s) return;
  const g = groupOf(s.id);
  const ft = $('[data-tab=found]').firstChild, fw = s.kind === 'pageflow' || s.kind === 'engine' ? 'Found ' : 'Changes ';
  if (ft.textContent !== fw) ft.textContent = fw;
  $('[data-tab=report]').hidden = s.kind !== 'pageflow';
  if (state.tab === 'report' && s.kind !== 'pageflow') setTab('run');
  $('#dname').textContent = (g ? g.title : runName(s)) + (s.dry ? ' (dry)' : '');   // the source; the run is picked below it
  $('#dname').title = s.id;
  $('[data-tab=runs] small').textContent = (g ? g.runs.length : 0) || '';
  const sw = runSwitch();
  if ($('#route').dataset.k !== sw) { $('#route').dataset.k = sw; $('#route').innerHTML = sw; }
  // one status line: state, where it is, the pages, the time; the live sentence only while it runs
  const word = s.detail === 'stopped-budget' ? detailText(s.detail) : s.status;
  $('#dstatus').innerHTML = `<i class="dot ${dotOf(s)}"></i>${esc(word)}`;
  const live = isLive(s);
  $('#dnow').textContent = live ? s.now || '' : '';
  $('#dnow').title = live ? s.now || '' : '';
  const parts = [live ? stageText(s) : '', ppText(s.cells || []), elapsed(s.elapsed_s)];
  $('#dsub').textContent = parts.filter(Boolean).join(' · ');
  const c = s.cost || {};
  const lim = limitsOn(s);                                   // limits: the Calls label is the switch
  let h = `<span><button type=button class=limsw aria-pressed="${lim}" title="Limits">Calls</button>${ic('calls')}<span>${(s.calls || {}).hydra ?? 0}</span></span>`;
  const calls = [c.haiku && `Haiku ${c.haiku[0]} of ${c.haiku[1] ?? '∞'} calls`, c.sonnet && `Sonnet ${c.sonnet[0]} of ${c.sonnet[1] ?? '∞'} calls`].filter(Boolean).join(', ');
  if (lim && showCost() && c.budget_usd != null) h += stat('Cost', `<span class=cm title="${esc(calls)}">${meter(c.usd || 0, c.budget_usd)}<span>${dollars(c.usd || 0)} <span class=dim>of ${dollars(c.budget_usd)}</span></span></span>`);
  else {
    if (c.haiku) h += stat(modelMark('haiku') + 'Haiku', lim ? `${meter(c.haiku[0], c.haiku[1])}<span>${c.haiku[0]} <span class=dim>/ ${c.haiku[1] ?? '∞'}</span></span>` : `<span>${c.haiku[0]}</span>`);
    if (c.sonnet) h += stat(modelMark('sonnet') + 'Sonnet', lim ? `${meter(c.sonnet[0], c.sonnet[1])}<span>${c.sonnet[0]} <span class=dim>/ ${c.sonnet[1] ?? '∞'}</span></span>` : `<span>${c.sonnet[0]}</span>`);
    if (showCost() && c.usd != null) h += stat('Cost', usd(c.usd));
  }
  // a finished run with cost hidden: what it would have cost at list price, greyed (owner)
  if (!showCost() && c.usd != null && c.usd > 0 && !['running', 'starting', 'stopping'].includes(s.status))
    h += `<span class=notional title="List price of the model calls; a subscription is not charged this">${stat('Cost', `≈${dollars(c.usd)}`)}</span>`;
  if (c.turns) h += stat('Turns', c.turns);
  if ($('#stats').dataset.k !== h) { $('#stats').dataset.k = h; $('#stats').innerHTML = h; applyCss($('#stats')); }
  $('#stop').hidden = !(s.status === 'running' || s.status === 'starting');
  $('#clear').disabled = s.status === 'running' || s.status === 'starting' || s.status === 'stopping';
  $('#rerun').hidden = !(s.recipe || /^workflow-/.test(s.name || ''));
  $('#rerun').disabled = $('#clear').disabled;
  $('#resume').hidden = !(s.status === 'stopped' && s.kind === 'pageflow' && s.resumable);
}


// ---- the tabs --------------------------------------------------------------------------------------------------
document.querySelectorAll('.tabs [data-tab]').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));
function setTab(t) {
  state.tab = t;
  document.querySelectorAll('.tabs [data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === t)));
  state.viewKey = '';
  $('#tabbody').innerHTML = '';
  $('#tabbody').dataset.run = '';
  $('#tabbody').scrollTop = 0;
  tools();
  pollView();
}

function tools() { $('#tabtools').innerHTML = ''; }

function renderRunsTab() {
  const g = groupOf(state.sel), html = sourceRuns(g ? g.runs : []), body = $('#tabbody');
  if (body.dataset.run === `${state.sel}/runs` && body.dataset.k === html) return;
  body.dataset.run = `${state.sel}/runs`;
  body.dataset.k = html;
  body.innerHTML = `<div class=stab>${html}</div>`;
}
$('#tabbody').addEventListener('click', e => {
  const rn = state.tab === 'runs' && e.target.closest('[data-run]');
  if (rn) { select(rn.dataset.run); setTab('run'); }
});
async function pollView() {
  const id = state.sel, tab = state.tab;
  if (id && !state.gone && tab === 'data') { renderData(); return; }     // the Data tab asks the log itself (data.js)
  if (id && !state.gone && tab === 'runs') { renderRunsTab(); return; }       // the source's runs, as Sources lists them (sources.js)
  if (!id || state.gone || state.busy.view) return;
  state.busy.view = true;
  try {
    const v = await api(`/api/view/${encodeURIComponent(id)}?tab=${tab === 'calls' ? 'raw' : tab}`);   // Calls is the raw files' call sections
    v.tab = tab;
    setError('view', null);
    if (id !== state.sel || tab !== state.tab) return;
    state.view = v;
    render();
  } catch (e) {
    if (id === state.sel) setError('view', `${tab[0].toUpperCase() + tab.slice(1)}: ${e.message}`);
  } finally {
    state.busy.view = false;
  }
}

function render() {
  const v = state.view;
  if (!v) return;
  const tab = v.tab;
  if (tab === 'found') $('[data-tab=found] small').textContent = (v.changes ? v.changes.length : v.entity_count) || '';
  if (tab === 'calls') $('[data-tab=calls] small').textContent = ((v.raw || {}).hydra || []).length || '';
  if (tab === 'questions') $('[data-tab=questions] small').textContent = qRows(v).filter(q => q.status === 'open').length || '';
  if (tab === 'run') return renderRun(v);
  if (tab === 'found') return renderFound(v);
  if (tab === 'raw' || tab === 'calls') return renderRaw(v);
  const html = {questions: questionsTab, report: reportTab}[tab](v);
  if (html === state.viewKey) return;
  state.viewKey = html;
  const body = $('#tabbody');
  const y = body.scrollTop;
  body.innerHTML = html;
  applyCss(body);
  body.scrollTop = y;
  if (tab === 'questions') {
    const k = `${state.sel}/questions`;
    if (state.seenQ.run !== k) state.seenQ = {run: k, seen: new Set()};
    motionArrive(body, '.qc', e => e.dataset.q, state.seenQ.seen);
    const on = $('.qc.sel', body);
    if (on && state.q.reveal) on.scrollIntoView({block: 'nearest'});
    state.q.reveal = false;
  }
}

// ---- the heatmap: pages across, a row a measure; cells fill (GSAP) as pages finish ------------------------------
function legend(keys) {
  const L = {
    status: '<span><span class=sw><i class=sd></i></span>Done</span><span><i class=doing></i>Reading</span><span><i class=se></i>Escalated</span><span><i class=sg></i>Gave up</span>',
    statusd: '<span><i class=sd></i>Done</span><span><i class=se></i>Escalated</span><span><i class=sg></i>Gave up</span>',
    rec: '<span><span class=sw><i class=z></i><i class=s1></i><i class=s3></i><i class=s5></i></span>Records</span>',
    esc: '<span><i class="s3 esc"></i>Escalated</span>', doing: '<span><i class=doing></i>Reading</span>', named: '<span><i class="tk lgtk"></i>Named</span>',
    eff: '<span><span class=sw><i class=e1></i><i class=e3></i><i class=e5></i></span>Effort</span>',
    none: '<span><i class=z></i>None</span>', todo: '<span><i class=todo></i>To do</span>', skip: '<span><i class=skip></i>Skipped</span>',
    chg: '<span><span class=sw><i class=s1></i><i class=s3></i><i class=s5></i></span>Changed</span>',
    red: '<span><i class=redacted></i>Redacted</span>', asked: '<span><span class=sw><i class=e2></i><i class=e4></i></span>Questions</span>'
  };
  return `<div class=lg>${keys.map(k => L[k]).join('')}</div>`;
}
function heatGrid(rows, measures) {
  const np = rows.length, from = rows[0].page, to = rows[np - 1].page;
  const pos = p => ((p - from + .5) / np * 100).toFixed(2) + '%';
  let h = measures.map(m => `<span class=l>${HEAT_LABEL[m]}</span><div class="r${m === 'named' || m === 'asked' ? ' ticks' : m === 'records' || m === 'status' || measures.length === 1 ? ' tall' : ''}" data-row=${m} data-css="--np:${np}">` +
    rows.map(r => `<i class="${HEAT[m](r)}" data-p="${r.page}" title="${heatTitle(m, r)}"></i>`).join('') + '</div>').join('');
  const ticks = [];
  const step = np > 400 ? 100 : np > 150 ? 50 : 10;                 // axis numbers that do not collide on a long source
  for (let p = Math.ceil(from / step) * step; p <= to; p += step) ticks.push(p);
  h += `<span></span><div class=ax>${ticks.map(p => `<span data-css="left:${pos(p)}">${p}</span>`).join('')}</div>`;
  return h;
}
function animateCells(cells, initial) {
  if (REDUCED || !window.gsap || !cells.length) return;
  if (initial) {
    gsap.fromTo(cells, {scale: .2, opacity: 0}, {scale: 1, opacity: 1, duration: .35, ease: 'power2.out',
      stagger: {each: Math.min(.025, 1.2 / cells.length)}, clearProps: 'transform,opacity'});
  } else {
    gsap.fromTo(cells, {scale: .25, opacity: .35}, {scale: 1, opacity: 1, duration: .55, ease: 'back.out(2)', clearProps: 'transform,opacity'});
  }
}
function tipText(h) {
  const small = [];
  for (const [k, n] of Object.entries(h.problems || {})) small.push(`${k}: ${n} ${n === 1 ? 'problem' : 'problems'}`);
  if (isDone(h.status) && wfRun()) small.push(`${h.records} in scope`, ...(h.changed ? [`${h.changed} changed`] : []), ...(h.asked ? [`${h.asked} asked`] : []));
  else if (isDone(h.status)) small.push(`${h.records} records`, `${h.quotes} quoted`, `${h.places || 0} places`, `${h.calls} calls`, `${Math.round(h.seconds)} s`);
  if (h.reason) small.push(h.reason);
  return `<b>${pgw(h.page)}</b> · ${esc(WORD[h.status] || h.status)}${small.length ? `<small>${esc(small.join(' · '))}</small>` : ''}`;
}

function renderRun(v) {
  const body = $('#tabbody');
  const rows = v.heat || [];
  const key = `${state.sel}/run/${rows.length ? rows[0].page + '-' + rows[rows.length - 1].page : ''}`;
  let built = false;
  if (body.dataset.run !== key) {
    body.dataset.run = key;
    state.logf = {kind: 'all', q: '', page: state.pendingPage || null};   // a new run starts with the whole log; a page picked on Found shows its lines
    state.pendingPage = null;
    state.openRow = null;
    body.innerHTML = '<div class=rcols><div class=rleft>' + (rows.length ? `<div class=sect>Pages <small>${ppText(rows)}</small></div><div class="hm tipped"></div>` +
      legend(heatKeys(rows, true)) : '') + '<div class=sect>Log</div>' + logBar() + '<ol class=log></ol></div>' +
      '<aside class=rail id=runrail data-ctx=run aria-label=Rail></aside></div>';
    const hm = $('.hm', body);
    if (hm) {
      hm.innerHTML = heatGrid(rows, heatMeasures(rows)) + '<div class=tip hidden></div>';
      applyCss(hm);
      hm.dataset.m = heatMeasures(rows).join();
      animateCells([...hm.querySelectorAll('.r i')], true);
      state.heatRows = rows;
    }
    built = true;
  }
  const hm = $('.hm', body);
  if (hm && !built) {
    const changed = [];
    state.heatRows = rows;
    const ms = heatMeasures(rows);                       /* heat groups: a category's row appears once it has a record */
    if (hm.dataset.m !== ms.join()) {
      hm.innerHTML = heatGrid(rows, ms) + '<div class=tip hidden></div>';
      applyCss(hm);
    }
    hm.dataset.m = ms.join();
    for (const r of hm.querySelectorAll('.r')) {
      const m = r.dataset.row;
      r.querySelectorAll('i').forEach((c, i) => {
        const cls = HEAT[m](rows[i]), t = heatTitle(m, rows[i]);
        if (c.title !== t) c.title = t;
        if (c.className !== cls) { c.className = cls; if (m === 'records' || GROUP_LABEL[m]) changed.push(c); }
      });
    }
    syncSel();
    animateCells(changed, false);
  }
  state.logRows = v.rows || [];
  pvStrip(null);                                         // pageview: the unit (parts or pages) only; the heatmap is the Run tab's pages
  drawLog();
  drawRail('run');
}

// ---- the log's filter: kind buttons, a find box, and a page picked on the heatmap -------------------------------
const LOG_KINDS = [
  ['all', 'All', () => true],
  ['pages', 'Pages', r => !!r.page],
  ['found', 'Found', r => /^(Found|Added|Saved|Cited)/.test(r.verb)],
  ['asked', 'Asked', r => /^Asked/.test(r.verb)],
  ['esc', 'Escalated', r => r.kind === 'escalated' || /^Escalat/.test(r.verb)],
  ['prob', 'Problems', r => r.kind === 'escalated' || r.tag === 'fail' || /fail|error|problem|stopped/i.test(`${r.verb} ${r.object} ${r.descriptor || ''}`)],
  ['steps', 'Steps', r => r.tag === 'phase' || /^(Ran|Ticked|Created)/.test(r.verb)]];
function logBar() {
  const f = state.logf || {kind: 'all', q: '', page: null};
  return `<div class=logbar><div class=lf role=group aria-label="Show">${LOG_KINDS.map(([k, lab]) =>
      `<button type=button data-lk="${k}" aria-pressed=${f.kind === k}>${lab}</button>`).join('')}</div>` +
    `<button type=button class=lpg${f.page ? '' : ' hidden'} title="Show every page">${f.page ? pgw(f.page) : ''} <span aria-hidden=true>×</span></button>` +
    `<input class=lq type=search placeholder=Find aria-label="Find in the log" value="${esc(f.q)}"><span class=lc></span></div>`;
}
function drawLog() {
  const body = $('#tabbody'), log = $('.log', body);
  if (!log) return;
  const f = state.logf || {kind: 'all', q: '', page: null};
  const test = (LOG_KINDS.find(k => k[0] === f.kind) || LOG_KINDS[0])[2];
  const q = f.q.trim().toLowerCase();
  const rows = (state.logRows || []).filter(r => test(r) && (!f.page || r.page === f.page) &&
    (!q || `${r.verb} ${r.object} ${r.descriptor || ''} ${r.who || ''} ${r.page ? `p. ${r.page}` : ''}`.toLowerCase().includes(q)));
  const html = rows.slice(0, 300).map(r => logRow(r) + (state.openRow === r.i ? detailRow(r) : '')).join('') || '<li class=lnone>Nothing matches.</li>';
  if (log.dataset.k !== html) {
    log.dataset.k = html;
    log.innerHTML = html;
    if (log.dataset.run !== state.sel) { log.dataset.run = state.sel; state.seenLog = new Set(); }
    motionArrive(log, '.lr', e => e.dataset.i, state.seenLog);
  }
  const lc = $('.lc', body);
  if (lc) lc.textContent = rows.length === (state.logRows || []).length ? '' : `${rows.length} of ${state.logRows.length}`;
  syncSel();
}
$('#tabbody').addEventListener('click', e => {
  const go = e.target.closest('[data-goto]');
  if (go) return setTab(go.dataset.goto);
  const f = state.logf;
  if (!f) return;
  const kb = e.target.closest('.logbar [data-lk]');
  if (kb) {
    f.kind = kb.dataset.lk;
    kb.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === kb)));
    return drawLog();
  }
  if (e.target.closest('.lpg')) { f.page = null; e.target.closest('.lpg').hidden = true; return drawLog(); }
});
/* heat pick: a cell selects its page and its row's measure; the filter it makes is one chip, Esc or the chip clears it */
const FOUND_ROW = m => m === 'quotes' || !!GROUP_LABEL[m];             // a records cell filters the log to its page; a kind's row opens Found
function heatPick(page, m) {
  state.hsel = {page, m};
  if (FOUND_ROW(m)) {
    state.fndPick = {page, type: GROUP_LABEL[m] ? m : 'all', quoted: m === 'quotes'};
    return setTab('found');
  }
  if (state.tab !== 'run') { state.pendingPage = page; return setTab('run'); }
  state.logf.page = page;
  const b = $('#tabbody .lpg');
  if (b) { b.hidden = false; b.firstChild.textContent = `${pgw(page)} `; }
  drawLog();
  showText('run', page);
}
function syncSel() {                                // the picked cell stays marked while its filter stands
  const s = state.hsel;
  const ok = s && (state.tab === 'run' ? state.logf && state.logf.page === s.page && !FOUND_ROW(s.m) : state.tab === 'found' && state.fnd.page === s.page);
  if (!ok) state.hsel = null;
  document.querySelectorAll('#tabbody .hm .r i[data-sel]').forEach(c => c.removeAttribute('data-sel'));
  if (state.hsel && state.tab !== 'found') {
    const c = $(`#tabbody .hm .r[data-row="${state.hsel.m}"] i[data-p="${state.hsel.page}"]`);
    if (c) c.setAttribute('data-sel', '');
  }
}
function clearPick() {                              // Esc: the page filter off, on the tab it is on
  if (state.tab === 'run' && state.logf && state.logf.page) {
    state.logf.page = null;
    const b = $('#tabbody .lpg');
    if (b) b.hidden = true;
    drawLog();
    return true;
  }
  if (state.tab === 'found' && state.fnd.page) {
    const f = state.fnd;
    f.page = null; f.quoted = false;
    if (f.fromPick) { f.type = 'all'; f.fromPick = false; }
    renderFound(state.foundView);
    return true;
  }
  return false;
}
$('#tabbody').addEventListener('click', e => {
  const cell = e.target.closest('.hm .r i[data-p]');
  if (cell && state.tab === 'found' && cell.closest('.fheat')) { const p = Number(cell.dataset.p); state.fnd.page = state.fnd.page === p ? null : p; return renderFound(state.foundView); }
  if (cell && cell.parentNode.dataset.row !== 'triage') heatPick(Number(cell.dataset.p), cell.parentNode.dataset.row);
});
$('#tabbody').addEventListener('input', e => {
  if (e.target.matches('.logbar .lq') && state.logf) { state.logf.q = e.target.value; drawLog(); }
});

function bindTip(root, rowsOf, textOf) {      // the hover card over a heatmap cell
  root.addEventListener('mouseover', e => {
    const c = e.target.closest('.hm .r i[data-p]');
    const hm = e.target.closest('.hm');
    if (!hm) return;
    const tip = $('.tip', hm);
    if (!tip) return;
    if (!c) { tip.hidden = true; return; }
    const h = rowsOf().find(r => r.page === Number(c.dataset.p));
    if (!h) return;
    tip.innerHTML = textOf(h);
    tip.hidden = false;
    const box = hm.getBoundingClientRect(), cb = c.getBoundingClientRect(), row = $('.r', hm).getBoundingClientRect();
    tip.style.left = `${cb.left + cb.width / 2 - box.left}px`;
    tip.style.top = `${row.top - box.top}px`;
  });
  root.addEventListener('mouseleave', () => { const t = $('.tip', root); if (t) t.hidden = true; });
}
bindTip($('#tabbody'), () => state.heatRows || [], tipText);
bindTip($('#cfgpanel'), () => (state.cfg ? cfgRows(state.cfg) : []), h => cfgTip(h));

function logRow(r) {
  const where = r.page ? `p. ${r.page} ` : '';
  const more = r.more ? ` +${r.more}` : '';
  const cls = r.tag === 'phase' ? 'ph' : r.kind === 'escalated' ? 'esc' : r.tag === 'fail' ? 'fail' : '';
  const open = state.openRow === r.i;
  const icon = r.schema ? `<span class=ic>${entityIcon(r.schema)}</span>` : ic(r.kind === 'escalated' ? 'Escalated' : r.verb);
  const tw = r.open ? `<span class="tw${open ? ' on' : ''}" aria-hidden=true></span>` : '<span class=tw0></span>';
  return `<li class="lr ${cls}${r.open ? ' op' : ''}" data-i="${r.i}"${r.open ? ` role=button tabindex=0 aria-expanded=${open}` : ''}>${tw}<time>${esc(dayTime(r.at))}</time>${icon}<span class=v>${esc(r.verb)}${r.count > 1 ? ` <span class=n>×${r.count}</span>` : ''}</span>` +
    `<span class=o>${esc(where)}${esc(r.object)}${esc(more)}${r.descriptor ? ` <i>${esc(r.descriptor)}</i>` : ''}</span>` +
    (r.who ? `<span class=who>${esc(r.who)}</span>` : '') + '</li>';
}

// ---- Found, Questions, Calls (as they were) --------------------------------------------------------------------
const entityIcon = schema => `<span class=ei>${(window.ENTITY_ICONS || {})[schema] || (window.ENTITY_ICONS || {})._default || ''}</span>`;

const SCHEMA_WORD = {PublicBody: 'Public body', UnknownLink: 'Link', Employment: 'Employment', Membership: 'Membership'};
const schemaWord = s => SCHEMA_WORD[s] || String(s || '').replace(/([a-z])([A-Z])/g, '$1 $2');
function markQuote(quote, thing) {            // the quote with the value marked
  const q = String(quote || ''), t = String(thing || '');
  const i = t ? q.toLowerCase().indexOf(t.toLowerCase()) : -1;
  return i < 0 ? esc(q) : `${esc(q.slice(0, i))}<mark>${esc(q.slice(i, i + t.length))}</mark>${esc(q.slice(i + t.length))}`;
}
const pageBtn = p => p ? `<button type=button class=pgo data-pg="${p}" title="Show this page in the Run tab">${pgw(p)}</button>` : '';

// ---- a log row unfolded: its records, its model call, its problems, what the step did --------------------------------
function callHTML(c) {
  if (!c) return '';
  const bits = [['Call', `#${c.n ?? ''}`], ['Model', c.model], ['Took', c.ms == null ? '' : `${c.ms} ms`],
    ['Tokens', c.tokens ?? ''], ['Result', c.ok === false ? `error${c.why ? ': ' + c.why : ''}` : 'ok'],
    ['Prompt', c.prompt_chars ? `${c.prompt_chars} characters` : ''], ['Answer', c.answer_chars != null ? `${c.answer_chars} characters` : '']]
    .filter(b => b[1] !== '' && b[1] !== '#');
  return `<dl class=kvl>${bits.map(([k, v]) => `<dt>${k}</dt><dd>${k === 'Model' ? (modelMark(v) || esc(modelLabel(v))) : esc(v)}</dd>`).join('')}</dl>` +
    (c.answer ? `<pre class=raw>${esc(c.answer)}</pre>` : '');
}
function recsHTML(d) {
  const more = d.recs_total > d.recs.length ? `<div class=mre>${d.recs_total - d.recs.length} more</div>` : '';
  return d.recs.map(r => `<div class="rr${r.state === 'cited' ? '' : ' prop'}"><span class=rnm>${entityIcon(r.schema)}${esc(r.thing)}</span>${pageBtn(r.page)}` +
    (r.quote ? `<span class=rq>“${markQuote(r.quote, r.thing)}”</span>` : '') + '</div>').join('') + more;
}
function detailRow(r) {
  const d = (state.rowCache || {})[`${state.sel}/${r.i}`];
  let h = '';
  if (d) {
    h = (d.did ? `<p>${esc(d.did)}</p>` : '') + (d.problems && d.problems.length ? `<ul class=probs>${d.problems.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '') +
      (d.detail && !(d.problems && d.problems.length) ? `<p>${esc(d.detail)}</p>` : '') + callHTML(d.call) + (d.recs && d.recs.length ? recsHTML(d) : '');
  }
  return `<li class=ld data-for="${r.i}">${h}</li>`;
}
async function openRow(i) {
  if (state.openRow === i) { state.openRow = null; return drawLog(); }
  state.openRow = i;
  drawLog();
  const key = `${state.sel}/${i}`;
  state.rowCache = state.rowCache || {};
  try {
    const v = await api(`/api/view/${encodeURIComponent(state.sel)}?tab=run&row=${i}`);
    state.rowCache[key] = v.detail || {};
  } catch (e) {
    state.rowCache[key] = {detail: e.message};
  }
  if (state.openRow === i) drawLog();
}
function gotoPage(p) { state.pendingPage = Number(p); setTab('run'); }
$('#tabbody').addEventListener('click', e => {
  const pg = e.target.closest('[data-pg]');
  if (pg) { e.stopPropagation(); return gotoPage(pg.dataset.pg); }
  const row = e.target.closest('.lr.op');
  if (row && !e.target.closest('.hm')) return openRow(Number(row.dataset.i));
  if (state.tab === 'found' && foundClick(e, state.fnd, () => renderFound(state.foundView), foundPick)) return;
  if (e.target.closest('.fbar .lpg')) return clearPick();
  if (e.target.closest('.fbar .fexp')) { state.fnd.exp = !state.fnd.exp; return renderFound(state.foundView); }
  const xf = e.target.closest('[data-xfmt]'), xb = e.target.closest('[data-xby]');
  if (xf) { state.fnd.fmt = xf.dataset.xfmt; return renderFound(state.foundView); }
  if (xb) { state.fnd.by = xb.dataset.xby; return renderFound(state.foundView); }
  if (e.target.closest('.fexp-go')) return runExport();
  const rb = e.target.closest('.rawbar [data-rs]');
  if (rb) { state.rawf.sec = rb.dataset.rs; state.rawf.open = null; rb.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === rb))); return drawRaw(); }
  const rr = e.target.closest('.rw');
  if (rr) { state.rawf.open = state.rawf.open === rr.dataset.k ? null : rr.dataset.k; return openRaw(rr.dataset.k); }
});
$('#tabbody').addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.lr.op, .fr, .rw, [data-pick]')) { e.preventDefault(); e.target.click(); }
});
$('#tabbody').addEventListener('change', e => {
  if (e.target.matches('.fbar .fdid')) { state.fnd.did = e.target.value; state.fnd.sel = null; renderFound(state.foundView); }
});
$('#tabbody').addEventListener('input', e => {
  if (e.target.matches('.fbar .fq')) { state.fnd.q = e.target.value; drawFound(); }
  if (e.target.matches('.rawbar .rq2')) { state.rawf.q = e.target.value; drawRaw(); }
});

// ---- Found: one row per entity, its records unfold --------------------------------------------------------------------
state.fnd = {type: 'all', q: '', open: null};
state.seenQ = {run: '', seen: new Set()};
function renderChanges(v) {
  // one line a change; the questions it asked are the Questions tab's (a click goes there), the ids stay in the title
  const html = (v.changes || []).map(c => c.kind === 'asked'
    ? `<button type=button class="lr lchg" data-goto=questions title="${esc((c.ids || []).join(' '))}">${ic('Asked')}<span>${esc(c.label)}</span></button>`
    : `<div class="lr lchg" title="${esc((c.ids || []).join(' '))}">${ic('Changed')}<span>${esc(c.label)}</span></div>`).join('') ||
    '<div class=lnone>No changes recorded.</div>';
  const body = $('#tabbody');
  if (body.dataset.k !== html) { body.dataset.k = html; body.dataset.run = ''; body.innerHTML = html; }
}
// Found (the 24 Nov sketch): one chip row (kinds, the picked page, Find, Export); the pages as one Records row and, for the selected
// thing, a Named row; the things on the left; on the right the selected thing's card and trace, or with none selected the chosen
// kind's rich view (the page box's drawings for the whole run). A picked page narrows all of it and shows its facts above.
const FC_ICON = {all: 'records', people: 'person', bodies: 'et-org', places: 'places', events: 'et-event', ties: 'links', posts: 'et-post'};
function renderFound(v) {
  if (v.changes) return renderChanges(v);
  const body = $('#tabbody');
  const key = `${state.sel}/found`;
  if (body.dataset.run !== key) {
    body.dataset.run = key;
    state.fnd = {type: 'all', q: '', sel: null, did: '', page: null, quoted: false, fromPick: false, exp: false, fmt: 'jsonl', by: 'none'};
    if (state.fndPick) { Object.assign(state.fnd, state.fndPick, {fromPick: true}); state.fndPick = null; }   /* heat pick */
    body.innerHTML = '<div class="fbar logbar"></div><div class="hm fheat tipped"></div><div class=pv-box-host></div>' +
      '<div class="fgrid fnd2"><div class=fl><div class=flist></div></div><div class=ft></div></div>';
  }
  state.foundView = v;
  if (v.heat && v.heat.length) state.heatRows = v.heat;
  const f = state.fnd;
  const done = !['running', 'starting', 'stopping'].includes((state.byId[state.sel] || {}).status);
  const bar = foundChips(v, f, f.page ? (v.entities || []).filter(e => e.pages.includes(f.page)) : null) +
    `${f.page ? `<button type=button class=lpg title="Show every page">${pgw(f.page)} <span aria-hidden=true>×</span></button>` : ''}` +
    `<input class=fq type=search placeholder=Find aria-label="Find a thing" value="${esc(f.q)}">` +
    (done ? `<button type=button class="ib fexp" title="Export" aria-label="Export" aria-expanded=${f.exp}>${ic('download')}</button>` : '') +
    (done && f.exp ? expPop(f) : '');
  const fb = $('.fbar', body);
  if (fb.dataset.k !== bar.replace(/value="[^"]*"/, '') ) {
    const q = $('.fq', fb), had = q && document.activeElement === q;
    fb.dataset.k = bar.replace(/value="[^"]*"/, '');
    fb.innerHTML = bar;
    if (had) { const n = $('.fq', fb); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }
  }
  pvStrip(null);                                         // the unit (parts or pages)
  drawFoundHeat();
  drawFound();
  drawPageBox();
  drawFoundSide();
}
// Found's shared parts, for the run view and for Sources (one definition): the entity-type chips and the one compact filter on what a
// run did, the list of the things, and the right side (the selected thing's card, or the kind's rich view)
function foundChips(v, f, onPage) {
  const types = v.types || [], dids = v.dids || [];
  if (f.type !== 'all' && !types.some(t => t.key === f.type)) f.type = 'all';
  if (f.did && !dids.some(d => d.key === f.did)) f.did = '';
  const cnt = (k, n) => onPage ? onPage.filter(e => k === 'all' || e.group === k).length : n;
  return `<div class=fchips role=group aria-label="Kind">` +
    [['all', 'All', cnt('all', v.entity_count || 0)], ...types.map(t => [t.key, t.label, cnt(t.key, t.count)])].filter(([k, , n]) => k === 'all' || n || f.type === k).map(([k, lab, n]) =>
      `<button type=button class=fc data-ft="${k}" aria-pressed=${f.type === k}>${ic(FC_ICON[k] || 'records')}${lab} <b>${n}</b></button>`).join('') + '</div>' +
    (dids.length > 1 ? `<select class=fdid aria-label="Did"><option value="">Did</option>${dids.map(d => `<option value="${esc(d.key)}"${f.did === d.key ? ' selected' : ''}>${esc(d.label)}</option>`).join('')}</select>` : '');
}
const DID_WORD = {merge: 'Merged', apart: 'Kept apart', alias: 'Alias', rename: 'Renamed', retype: 'Retyped', prop: 'Changed', date: 'Dated', order: 'Ordered', bind: 'Bound',
  none: 'No match', link: 'Placed', tie: 'Tied', stake: 'Held', payment: 'Paid', accept: 'Accepted', reject: 'Rejected', leave: 'Left', ask: 'Asked', answer: 'Answered'};
function foundList(v, f) {
  const q = f.q.trim().toLowerCase();
  const list = (v.entities || []).filter(e => (f.type === 'all' || e.group === f.type) && (!f.page || e.pages.includes(f.page)) &&
    (!f.did || (e.did || []).some(d => d.kind === f.did)) && (!q || `${e.thing} ${schemaWord(e.schema)}`.toLowerCase().includes(q)))
    .sort((a, b) => b.pages.length - a.pages.length || a.thing.localeCompare(b.thing));
  const unit = n => n === 1 ? 'page' : 'pages';
  const note = e => e.did ? [...new Set(e.did.map(d => DID_WORD[d.kind] || d.verb))].slice(0, 2).join(' · ') : `${e.pages.length} ${unit(e.pages.length)}`;
  return list.slice(0, 500).map(e => `<div class="fr${f.sel === e.key ? ' sel' : ''}${e.cited ? '' : ' prop'}" data-k="${esc(e.key)}" role=button tabindex=0 aria-pressed=${f.sel === e.key} title="${esc(schemaWord(e.schema))}">` +
    `<span class=ic>${entityIcon(e.schema)}</span><b>${esc(e.thing)}</b><small>${esc(note(e))}</small></div>`).join('') +
    (list.length > 500 ? `<div class=mre>${list.length - 500} more: narrow the list</div>` : '') || '<div class=lnone>Nothing matches.</div>';
}
function foundSideHTML(v, f, d) {                        // d: the rich data (undefined while it loads, null if none)
  const e = (v.entities || []).find(x => x.key === f.sel);
  if (e) return entityCard(e, d);
  if (f.type === 'all') return '';
  return d ? PageView.rich(d, f.type) || '<div class=lnone>Nothing to draw.</div>' : '<span class=spin aria-label=Loading role=status></span>';
}
// the clicks both Found views share: a thing, a name in a card, a kind chip
function foundClick(e, f, redraw, pick) {
  const fr = e.target.closest('.fr[data-k]');
  if (fr) { f.sel = f.sel === fr.dataset.k ? null : fr.dataset.k; redraw(); return true; }
  const pk = e.target.closest('[data-pick]');
  if (pk) { e.preventDefault(); pick(pk.dataset.pick, pk.dataset.schema); return true; }
  const fb = e.target.closest('.fbar [data-ft]');
  if (fb) { f.type = fb.dataset.ft; f.sel = null; redraw(); return true; }
  return false;
}
function foundPickIn(v, f, name, schema) {              // a name from a rich view or a trace: the thing in the list, if there
  const n = String(name || '').toLowerCase();
  const e = (v.entities || []).find(x => x.thing.toLowerCase() === n && (!schema || x.schema === schema)) || (v.entities || []).find(x => x.thing.toLowerCase() === n);
  if (e) f.sel = f.sel === e.key ? null : e.key;
  return !!e;
}
// the export choices: the current filter (kind, page), as one file or split by kind or page
function expPop(f) {
  const seg = (name, opts, cur) => `<div class=seg role=radiogroup aria-label="${name}">${opts.map(([k, w]) => `<button type=button role=radio data-x${name.toLowerCase()}="${k}" aria-checked=${cur === k}>${w}</button>`).join('')}</div>`;
  return `<div class=fexp-pop role=dialog aria-label=Export><div class=f><span>Format</span>${seg('Fmt', [['jsonl', 'JSONL'], ['json', 'JSON']], f.fmt)}</div>` +
    `<div class=f><span>Files</span>${seg('By', [['none', 'One'], ['group', 'By kind'], ['page', 'By page']], f.by)}</div>` +
    `<button type=button class="btn primary fexp-go">${ic('download')}Export</button></div>`;
}
async function runExport() {
  const f = state.fnd, id = state.sel;
  try {
    const out = await api(`/api/export/${encodeURIComponent(id)}`, {format: f.fmt, group: f.type === 'all' ? '' : f.type, page: f.page || 0, by: f.by});
    f.exp = false;
    renderFound(state.foundView);
    notify('info', `${out.count} ${out.count === 1 ? 'record' : 'records'} exported to ${out.files.length === 1 ? out.files[0].split('/').pop() : out.files.length + ' files'} in the run's exports folder`);
  } catch (e) {
    notify('error', `Export: ${e.message}`);
  }
}
// the pages: one Records row, and the selected thing's Named row (where it is named)
function drawFoundHeat() {
  const hm = $('#tabbody .fheat'), v = state.foundView, rows = (v && v.heat) || [];
  if (!hm) return;
  if (!rows.length) { hm.innerHTML = ''; hm.hidden = true; return; }
  hm.hidden = false;
  const e = foundSel();
  state.namedPages = e ? new Set(e.pages) : null;
  HEAT_LABEL.named = e ? shortName(e.thing) : '';
  const ms = e ? ['records', 'named'] : ['records'];
  const k = `${state.sel}/${JSON.stringify(rows.map(r => [r.page, r.status, r.records]))}/${e ? e.key : ''}/${state.fnd.page || ''}`;
  if (hm.dataset.k === k) return;
  hm.dataset.k = k;
  hm.innerHTML = heatGrid(rows, ms) + '<div class=tip hidden></div>';
  hm.insertAdjacentHTML('afterend', '');
  applyCss(hm);
  const lg = legend([...heatKeys(rows), ...(e ? ['named'] : [])]);
  const old = hm.nextElementSibling && hm.nextElementSibling.classList.contains('lg') ? hm.nextElementSibling : null;
  if (old) old.outerHTML = lg; else hm.insertAdjacentHTML('afterend', lg);
  if (state.fnd.page) { const c = $(`#tabbody .fheat .r[data-row=records] i[data-p="${state.fnd.page}"]`); if (c) c.setAttribute('data-sel', ''); }
}
const shortName = n => { const w = String(n || '').split(/\s+/).filter(Boolean); return w.length > 1 ? w[w.length - 1] : (w[0] || ''); };
const foundSel = () => { const v = state.foundView, k = state.fnd.sel; return k && v ? (v.entities || []).find(e => e.key === k) || null : null; };
function foundPick(name, schema) {
  if (foundPickIn(state.foundView, state.fnd, name, schema)) renderFound(state.foundView);
}
// the run's rich data (all kinds; one page when a page is picked), kept per run and page
state.rich = {};
async function loadRich() {
  const id = state.sel, page = state.fnd.page || 0, k = `${id}|${page}`;
  const c = state.rich[k];
  if (c && (c.d || c.busy)) return c.d;
  state.rich[k] = {busy: true};
  let d = null;
  try { d = await api(`/api/pageview?run=${encodeURIComponent(id)}&all=1${page ? `&page=${page}` : ''}`); } catch (e) { d = null; }
  state.rich[k] = {d};
  if (state.sel === id && state.tab === 'found') drawFoundSide();
  return d;
}
const richNow = () => (state.rich[`${state.sel}|${state.fnd.page || 0}`] || {}).d;
// the right side: the selected thing's card and trace; with none selected, the chosen kind's rich view
function drawFoundSide() {
  const host = $('#tabbody .ft'), v = state.foundView, f = state.fnd;
  if (!host || !v) return;
  const d = richNow();
  if (d === undefined) loadRich();
  const html = foundSideHTML(v, f, d);
  const grid = $('#tabbody .fnd2');
  if (grid) grid.classList.toggle('one', !html);
  if (host.dataset.k !== html) { host.dataset.k = html; host.innerHTML = html; applyCss(host); }
}
// the card (the sketch): the name, its kind and pages, what is known of it, then its trace: every record that names it, page by page
function entityCard(e, d) {
  const items = (d && d[e.group]) || [], me = Array.isArray(items) ? items.find(x => (x.name || x.title) === e.thing) : null;
  const n = e.thing.toLowerCase(), pg = p => `<button type=button class=pg data-pg="${p}">${esc(pgw(p))}</button>`;
  const kv = [['Kind', esc(schemaWord(e.schema)), ''], ['Pages', e.pages.slice(0, 12).map(pg).join(' ') + (e.pages.length > 12 ? ` +${e.pages.length - 12}` : ''), ''],
    me && me.h ? ['H-ID', `<code>${esc(me.h)}</code>`, ''] : null, me && me.id ? ['Record', `<code>${esc(me.id)}</code>`, ''] : null].filter(Boolean);
  const tr = [];
  for (const r of e.recs) {
    const w = r.did;                                     // what a run did to it: its verb, the other thing, the words, why
    tr.push(w ? {page: r.page, icon: w.kind, k: w.verb, t: w.other || '', d: w.text || '', q: r.quote, why: w.why, flow: w.flow, did: true}
      : {page: r.page, icon: e.schema, k: schemaWord(e.schema), t: '', d: '', q: r.quote, cited: r.state === 'cited'});
  }
  for (const t of (e.did ? [] : (d && d.ties) || [])) {
    const a = (t.from || {}).name || '', b = (t.to || {}).name || '';
    if (a.toLowerCase() !== n && b.toLowerCase() !== n) continue;
    const other = a.toLowerCase() === n ? t.to : t.from;
    for (const p of t.pages || []) tr.push({page: p, icon: 'links', k: t.kind || schemaWord(t.schema), t: other, d: [t.start, t.end].filter(Boolean).join(' to ') || t.date || '', q: t.quote, cited: t.cited, tie: true});
  }
  for (const ev of (e.did ? [] : (d && d.events) || [])) {
    if (!(ev.who || []).some(w => String(w.name).toLowerCase() === n) || ev.title === e.thing) continue;
    for (const p of ev.pages || []) tr.push({page: p, icon: 'Event', k: 'Event', t: {name: ev.title, schema: 'Event'}, d: ev.date || '', q: ev.quote, cited: ev.cited});
  }
  tr.sort((x, y) => x.page - y.page);
  const seen = new Set(), rows = tr.filter(x => { const k = `${x.page}|${x.k}|${x.t && x.t.name}|${x.d}|${x.q}`; if (seen.has(k)) return false; seen.add(k); return true; });
  const li = rows.slice(0, 120).map(x => `<li><div class=m>${x.page ? pg(x.page) : ''}</div><div class=b>${x.tie ? ic('links') : `<span class=ic>${entityIcon(x.icon)}</span>`}</div><div><div class=h><b>${esc(x.k)}</b>` +
    (x.t && x.t.name ? `<a href="#" data-pick="${esc(x.t.name)}" data-schema="${esc(x.t.schema || '')}">${esc(x.t.name)}</a>` : '') + (x.d ? `<span class=d>${esc(x.d)}</span>` : '') +
    `<span class=grow></span>${x.flow ? `<span class=tag>${esc(x.flow)}</span>` : x.did ? '' : `<span class="tag${x.cited ? '' : ' p'}">${x.cited ? 'Cited' : 'Proposed'}</span>`}</div>` +
    `${x.why ? `<div class=why>${esc(x.why)}</div>` : ''}${x.q ? `<q>${markQuote(x.q, e.thing)}</q>` : ''}</div></li>`).join('');
  return `<div class=fhead><div><h3>${esc(e.thing)}</h3><div class=k>${entityIcon(e.schema)}${esc(schemaWord(e.schema))} · ${e.pages.length} ${e.pages.length === 1 ? 'page' : 'pages'}</div></div></div>` +
    `<div class=kv>${kv.map(([k, v2]) => `<span class=k>${k}</span><span>${v2}</span><span></span>`).join('')}</div>` +
    `<div class=sect>Trace <small>${rows.length}</small></div><ol class=tr>${li}</ol>`;
}
/* pageview: the strip (run tab, and Found while a page is picked) and the page box above the Found list. A page picked on the heatmap
   or the strip opens the box; its category chip is the type filter; the list below is hidden while the box is open. Esc or × closes. */
state.pv = {};
async function pvStrip(host) {
  const id = state.sel, rows = state.heatRows || [];
  const sig = rows.reduce((s, r) => s + r.records + (isDone(r.status) ? 1 : 0), 0);
  let c = state.pv.strip;
  if (!c || c.id !== id || (state.tab === 'run' && c.sig !== sig)) {
    let d = null;
    try { d = await api(`/api/pageview?run=${encodeURIComponent(id)}`); } catch (e) { d = null; }
    c = state.pv.strip = {id, sig, d};
  }
  if (id !== state.sel) return;
  const unit = c.d && c.d.unit || 'page';
  if (state.unit !== unit) {                              // a text source: Part 40, not p. 40, everywhere the unit shows
    state.unit = unit;
    state.viewKey = '';
    head();
    if (state.tab === 'found' && state.foundView) return renderFound(state.foundView);
  }
  if (!host || !host.isConnected) return;
  const page = state.tab === 'found' ? state.fnd.page : null, k = `${id}/${c.sig}/${page || ''}`;
  if (host.dataset.k === k) return;
  host.dataset.k = k;
  if (!c.d || !c.d.ok) { host.innerHTML = ''; return; }
  PageView.strip(host, c.d, {sel: page, legend: false, onPick: p => {
    if (state.tab === 'found') { state.fnd.page = p; renderFound(state.foundView); } else heatPick(p, 'records');
  }});
}
async function drawPageBox() {
  const body = $('#tabbody'), host = $('.pv-box-host', body), f = state.fnd;
  if (!host) return;
  if (!f.page) { body.classList.remove('pv-on'); host.innerHTML = ''; host.dataset.k = ''; PageView.close(); return; }
  const page = f.page, key = `${state.sel}/${page}`, cache = state.pv.pages = state.pv.pages || {};
  let c = cache[key];
  if (!c || Date.now() - c.t > 5000) {
    let d = null;
    try { d = await api(`/api/pageview?run=${encodeURIComponent(state.sel)}&page=${page}`); } catch (e) { d = null; }
    c = cache[key] = {t: Date.now(), d};
  }
  if (state.tab !== 'found' || state.fnd.page !== page || !host.isConnected) return;
  const ok = c.d && c.d.ok;
  body.classList.toggle('pv-on', !!ok);
  const k = `${key}/${f.type}/${JSON.stringify(c.d)}`;
  if (host.dataset.k === k) return;
  host.dataset.k = k;
  PageView.box(host, ok ? c.d : null, {cat: f.type || 'all', entities: false,   // the Found chips filter; the list below holds the entities
    onStep: p => { f.page = p; renderFound(state.foundView); },
    onClose: () => clearPick(),
    onCat: t => { f.type = t; f.fromPick = false; renderFound(state.foundView); }});
}
function drawFound() {
  const body = $('#tabbody'), box = $('.flist', body), v = state.foundView;
  if (!box || !v) return;
  const html = foundList(v, state.fnd);
  if (box.dataset.k !== html) { box.dataset.k = html; box.innerHTML = html; }
}

// ---- Raw: the files as they are (model calls, hydra calls, the driver's log, a session's transcript) --------------------
state.rawf = {sec: null, q: '', open: null};
const RAW_SECS = {calls: [['hydra', 'Hydra'], ['models', 'Model']], raw: [['driver', 'Driver log'], ['transcript', 'Session']]};
function renderRaw(v) {
  const body = $('#tabbody');
  const key = `${state.sel}/${state.tab}`;
  if (body.dataset.run !== key) {
    body.dataset.run = key;
    state.rawf = {sec: null, q: '', open: null};
    body.innerHTML = '<div class="rawbar logbar"></div><div class=rlist></div>';
  }
  state.rawView = v.raw || {};
  const have = (RAW_SECS[state.tab] || RAW_SECS.raw).filter(([k]) => (state.rawView[k] || []).length);
  const f = state.rawf;
  if (!have.some(([k]) => k === f.sec)) f.sec = have.length ? have[0][0] : null;
  const bar = `<div class=lf role=group aria-label="Show">${have.map(([k, lab]) => `<button type=button data-rs="${k}" aria-pressed=${f.sec === k}>${lab} <small>${state.rawView[k].length}</small></button>`).join('')}</div>` +
    `<input class=rq2 type=search placeholder=Find aria-label="Find in the raw files" value="${esc(f.q)}"><span class=lc></span>`;
  const rb = $('.rawbar', body), k = bar.replace(/value="[^"]*"/, '');
  if (rb.dataset.k !== k) {
    const had = $('.rq2', rb) && document.activeElement === $('.rq2', rb);
    rb.dataset.k = k;
    rb.innerHTML = bar;
    if (had) { const n = $('.rq2', rb); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }
  }
  drawRaw();
}
/* CZ335: Call notes. A call's note unfolded: what it did, its counts, hydra's lint lines, the records it touched */
const callClock = at => /^\d{4}-\d{2}-\d{2}T/.test(String(at || '')) ? String(at).slice(11, 19) : at;
const noteRec = (n, id) => {
  const x = (n.recs || {})[id];
  return `<button type=button class=cnr data-rid="${esc(id)}"${x ? ` data-nm="${esc(x.name)}" data-col="${esc(x.col || '')}" data-sch="${esc(x.schema || '')}"` : ''}>${esc(x ? x.name : id)}</button>`;
};
function noteHTML(n) {
  const counts = Object.entries(n.counts || {}).filter(([, v]) => v !== null && v !== undefined);
  const lint = (n.lint || []).filter(x => x && x.say);
  const shown = new Set(lint.map(x => x.id));
  const refs = (n.refs || []).filter(id => !shown.has(id));
  return `<p class=cdid>${esc(n.did)}</p>` +
    (counts.length ? `<div class=cchips>${counts.map(([k, v]) => `<span class=cchip>${esc(k)} <b>${esc(v)}</b></span>`).join('')}</div>` : '') +
    (lint.length ? `<ul class=clint>${lint.map(x => `<li><span class=crule>${esc(x.rule || '')}</span>${x.id ? noteRec(n, x.id) : ''}<span>${esc(x.say)}</span></li>`).join('')}</ul>` : '') +
    (refs.length || n.refs_more ? `<div class=crefs>${refs.map(id => noteRec(n, id)).join('')}${n.refs_more ? `<span class=cmore>+${esc(n.refs_more)}</span>` : ''}</div>` : '');
}
async function openNoteRecord(b) {                                        // the record opens as the Collections page's Selected card
  const arch = (state.rawView || {}).archive, col = b.dataset.col;
  if (!arch || !col) return;
  state.page = null;
  showPage('collections');
  state.col.archive = arch;
  await loadCollections();
  await selectCollection(col);
  etSelect({id: b.dataset.rid, name: b.dataset.nm || b.dataset.rid, schema: b.dataset.sch, collection: col, archive: arch});
}
$('#tabbody').addEventListener('click', e => {
  const b = e.target.closest('.cnr[data-rid]');
  if (b) { e.stopPropagation(); openNoteRecord(b); }
});
function rawRowHTML(sec, r, i, open, cache) {
  const k = `${sec}/${i}`;
  const on = open === k;
  const tw = `<span class="tw${on ? ' on' : ''}" aria-hidden=true></span>`;
  if (sec === 'models') {
    const more = on ? `<div class=rwd>${cache[k] ? callHTML(cache[k]) : ''}</div>` : '';
    return `<div class="rw${r.ok === false ? ' bad' : ''}" data-k="${k}" role=button tabindex=0 aria-expanded=${on}>${tw}<time>${esc(dayTime(r.at))}</time><span class=rv>#${r.n}</span>` +
      `<span class=ro>${esc(r.label || '')}</span><span class=rm>${modelMark(r.model) || esc(modelLabel(r.model))}</span><span class=rwt>${secs(r.ms)}</span><span class=rwt>${r.ok === false ? 'error' : r.answer_chars != null ? `${kChars(r.answer_chars)} chars` : ''}</span></div>${more}`;
  }
  if (sec === 'hydra' && r.note && r.note.did) {                      // a call with a note: what it did, not its arguments
    const more = on ? `<div class=rwd>${noteHTML(r.note)}</div>` : '';
    return `<div class="rw${r.ok === false ? ' bad' : ''}" data-k="${k}" role=button tabindex=0 aria-expanded=${on}>${tw}<time title="${esc(longWhen(r.at))}">${esc(dayTime(callClock(r.at)))}</time>` +
      (on ? `<span class=rv>${esc(r.tool)}</span>` : `<span class="rv cdo" title="${esc(r.tool)}">${esc(r.note.did)}</span>`) + `<span class=ro></span><span class=rwt>${secs(r.ms)}</span><span class=rwt>${r.ok === false ? 'error' : 'ok'}</span></div>${more}`;
  }
  if (sec === 'hydra') {
    const more = on ? `<div class=rwd><pre class=raw>${esc(r.args || '')}${r.text ? '\n' + esc(r.text) : ''}</pre></div>` : '';
    return `<div class="rw${r.ok === false ? ' bad' : ''}" data-k="${k}" role=button tabindex=0 aria-expanded=${on}>${tw}<time title="${esc(longWhen(r.at))}">${esc(dayTime(callClock(r.at)))}</time><span class=rv>${esc(r.tool)}</span>` +
      `<span class=ro>${esc(r.args || '')}</span><span class=rwt>${secs(r.ms)}</span><span class=rwt>${r.ok === false ? 'error' : 'ok'}</span></div>${more}`;
  }
  if (sec === 'transcript') {
    const more = on ? `<div class=rwd><pre class=raw>${esc(r.text)}</pre></div>` : '';
    return `<div class="rw" data-k="${k}" role=button tabindex=0 aria-expanded=${on}>${tw}<span class=rv>${esc(r.type)}</span><span class=ro>${esc((r.text || '').split('\n')[0])}</span></div>${more}`;
  }
  return `<div class=rline><span class=ro>${esc(r)}</span></div>`;
}
function drawRaw() {
  const body = $('#tabbody'), box = $('.rlist', body), v = state.rawView, f = state.rawf;
  if (!box || !v) return;
  if (!f.sec) { box.dataset.k = ''; box.innerHTML = '<div class=lnone>Nothing to show yet.</div>'; return; }
  const q = f.q.trim().toLowerCase();
  const text = r => typeof r === 'string' ? r : Object.values(r).join(' ');
  const rows = (v[f.sec] || []).map((r, i) => [r, i]).filter(([r]) => !q || text(r).toLowerCase().includes(q));
  const cache = state.rawCache || {};
  let at = null;                                                          // Calls: rows grouped under the task step they ran in
  const grouped = f.sec === 'hydra' && rows.some(([r]) => r.step);
  const html = rows.slice(-400).map(([r, i]) => {
    const head = grouped && (r.step || null) !== at && (at = r.step || null) ? `<div class=cgh title="${esc(r.step)}">${esc(r.step)}</div>` : '';
    return head + rawRowHTML(f.sec, r, i, f.open, cache);
  }).join('') || '<div class=lnone>Nothing matches.</div>';
  if (box.dataset.k !== html) { const y = body.scrollTop; box.dataset.k = html; box.innerHTML = html; body.scrollTop = y; }
  const lc = $('.rawbar .lc', body);
  if (lc) lc.textContent = rows.length === (v[f.sec] || []).length ? '' : `${rows.length} of ${v[f.sec].length}`;
}
async function openRaw(k) {
  drawRaw();
  const [sec, i] = k.split('/');
  if (sec !== 'models' || state.rawf.open !== k) return;
  const m = (state.rawView.models || [])[Number(i)];
  if (!m) return;
  state.rawCache = state.rawCache || {};
  try {
    const v = await api(`/api/view/${encodeURIComponent(state.sel)}?tab=raw&n=${m.n}`);
    state.rawCache[k] = v.call;
  } catch (e) {
    state.rawCache[k] = {why: e.message, ok: false, n: m.n};
  }
  drawRaw();
}

// ---- Questions: each with its evidence and the buttons hydra offers; one click answers it (answering.py) -----------------
// A click shows the answer at once and goes to hydra under its lock; the next open question is selected. An answered one
// keeps its answer, who gave it and when, with an Undo (hydra's history keeps both steps).
state.q = {sel: null, local: {}, reveal: false, run: '', pos: new Map(), more: {}};
const QKIND = {wikidata: 'Wikidata', 'same-name': 'Short name'};
const qKind = q => QKIND[q.kind] || (q.kind ? title(q.kind) : 'Question');
// the order the questions were first seen in this run: an answered one stays where it was (the manager lists open ones first)
function qRows(v) {
  if (state.q.run !== v.id) state.q = {...state.q, run: v.id, sel: null, local: {}, pos: new Map(), more: {}};
  const rows = (v.rows || []).map(q => state.q.local[q.id] ? {...q, ...state.q.local[q.id]} : q);
  for (const q of rows) if (!state.q.pos.has(q.id)) state.q.pos.set(q.id, state.q.pos.size);
  return rows.sort((a, b) => state.q.pos.get(a.id) - state.q.pos.get(b.id));
}
const qDone = q => q.status !== 'open';
// the choices on screen: three candidates at a time (hydra's order), then None of these and Check for more
const Q_TOP = 3;
function qShown(q) {
  const chs = q.choices || [], none = chs.find(c => c.none), cands = chs.filter(c => !c.none);
  if (cands.length <= Q_TOP) return {cands, none, more: false, all: chs};
  const at = Math.min(state.q.more[q.id] || 0, Math.floor((cands.length - 1) / Q_TOP) * Q_TOP);
  return {cands: cands.slice(at, at + Q_TOP), none, more: at + Q_TOP < cands.length, all: chs};
}
const qPassage = p => {
  if (!p || !p.text) return '';
  const i = p.mark ? p.text.toLowerCase().indexOf(p.mark.toLowerCase()) : -1;
  const body = i < 0 ? esc(p.text) : `${esc(p.text.slice(0, i))}<b>${esc(p.text.slice(i, i + p.mark.length))}</b>${esc(p.text.slice(i + p.mark.length))}`;
  return `<q title="${esc(p.text)}">${body}</q>` + (p.page ? `<span class=qpg>${esc(pgw(p.page))}</span>` : '');
};
function questionsTab(v) {
  const rows = qRows(v);
  if (!rows.length) return '';
  const sel = rows.some(q => q.id === state.q.sel) ? state.q.sel : null;
  const card = q => {
    const open = !qDone(q);
    const sh = qShown(q);
    const longer = sh.cands.some(c => c.label.length > 26);
    const a = q.answer || {};
    const by = [a.by, a.at ? longWhen(a.at) : ''].filter(Boolean).join(' · ');
    const body = open
      ? `<div class="qa${longer ? ' list' : ''}">${sh.cands.map((c, i) => `<button type=button class=btn data-c="${esc(c.choice)}" data-l="${esc(c.label)}">${i < 9 ? `<kbd>${i + 1}</kbd>` : ''}${esc(c.label)}</button>`).join('')}</div>` +
        (sh.none || sh.more || q.group === 'asked' ? '<div class="qa qr">' +
          (sh.none ? `<button type=button class=btn data-c="${esc(sh.none.choice)}" data-l="${esc(sh.none.label)}" title=0><kbd>0</kbd>${esc(sh.none.label)}</button>` : '') +
          (sh.more ? '<button type=button class=btn data-more=1 title=M><kbd>M</kbd>Check for more</button>' : '') +
          (q.group === 'asked' ? '<button type=button class=x data-c=dismiss data-l=Dismissed>Dismiss</button>' : '') + '</div>' : '')
      : `<div class=qd><b>${esc([a.choice, a.text].filter(Boolean).join(' — ') || 'Answered')}</b>${by ? `<span>${esc(by)}</span>` : ''}</div>`;
    return `<article class="qc${open ? '' : ' done'}${q.id === sel ? ' sel' : ''}" data-q="${esc(q.id)}" title="${esc(q.id)}">` +
      `<span class=qi>${ic(open ? 'Asked' : 'ok')}</span>` +
      `<div class=qh><b>${esc(qKind(q))}</b>${q.page && !(q.passage && q.passage.page) ? `<span>${pgw(q.page)}</span>` : ''}<span class=grow></span>` +
      (open ? '' : `<button type=button class=undo title=Undo aria-label=Undo>${ic('undo')}</button>`) + '</div>' +
      `<p class=qq title="${esc(q.question)}">${esc(q.question)}</p>` +
      qPassage(q.passage) +
      (open && q.suggestion && q.group === 'asked' && !q.passage ? `<div class=qs>${esc(q.suggestion)}</div>` : '') + body + '</article>';
  };
  return `<div class=ql>${rows.map(card).join('')}</div>`;
}
// the next question to select after one is answered: the open one below it, else the open one above
function qNext(rows, id) {
  const i = rows.findIndex(q => q.id === id);
  const open = (a, b) => rows.slice(a, b).filter(q => !qDone(q));
  return (open(i + 1)[0] || open(0, i).pop() || {}).id || null;
}
async function qWait() {
  for (let n = 0; n < 50 && state.busy.view; n++) await new Promise(r => setTimeout(r, 100));
}
const qRedraw = sv => { if (sv) drawSources(); else { state.viewKey = ''; render(); } };
async function answerQ(id, choice, label, sv) {
  const rows = qRows(sv || state.view);
  const was = rows.find(q => q.id === id);
  if (!was) return;
  const run = sv ? was.run : state.sel;
  state.q.local[id] = choice === 'clear' ? {status: 'open', answer: null}
    : {status: choice === 'dismiss' ? 'dismissed' : 'answered', answer: {choice: label, text: null, by: 'you', at: new Date().toISOString()}};
  state.q.sel = choice === 'clear' ? id : qNext(rows, id);
  state.q.reveal = true;
  qRedraw(sv);
  try {
    await api(`/api/answer/${encodeURIComponent(run)}`, {question: id, choice});
    if (sv) await srcViewLoad(state.src.sel, 'questions', true);
    else { await qWait(); if (run === state.sel) await pollView(); }
  } catch (e) {
    notify('error', `Answer: ${e.message}`);
  }
  delete state.q.local[id];
  if (sv ? state.page === 'sources' : run === state.sel && state.tab === 'questions') qRedraw(sv);
}
function qMore(id, sv) {
  const q = qRows(sv || state.view).find(x => x.id === id);
  if (!q || !qShown(q).more) return;
  state.q.more[id] = (state.q.more[id] || 0) + Q_TOP;
  state.q.sel = id;
  qRedraw(sv);
}
// a click in the questions, in the run view or (sv: the source's questions) in Sources
function qClick(e, sv) {
  const c = e.target.closest('.qc');
  if (!c) return false;
  const id = c.dataset.q, b = e.target.closest('button[data-c]');
  if (e.target.closest('.undo')) answerQ(id, 'clear', '', sv);
  else if (e.target.closest('[data-more]')) qMore(id, sv);
  else if (b) answerQ(id, b.dataset.c, b.dataset.l, sv);
  else { state.q.sel = state.q.sel === id ? null : id; qRedraw(sv); }
  return true;
}
$('#tabbody').addEventListener('click', e => {
  if (state.tab === 'questions') qClick(e);
});
document.addEventListener('keydown', e => {                    // ↑ ↓ move between questions; 1–3 the candidates shown, 0 or N none of these, M check for more
  const onSrc = state.page === 'sources' && state.src.tab === 'questions', sv = onSrc ? srcQView() : null;
  if (onSrc ? !sv : state.tab !== 'questions' || state.page || !state.view || state.view.tab !== 'questions') return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest('input, textarea, select, dialog, [contenteditable]')) return;
  const rows = qRows(sv || state.view);
  const i = rows.findIndex(q => q.id === state.q.sel);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!rows.length) return;
    e.preventDefault();
    const j = e.key === 'ArrowDown' ? Math.min(rows.length - 1, i + 1) : i < 0 ? rows.length - 1 : Math.max(0, i - 1);
    state.q.sel = rows[j].id;
    state.q.reveal = true;
    qRedraw(sv);
  } else if (/^[0-9mMnN]$/.test(e.key) && i >= 0 && !qDone(rows[i])) {
    const sh = qShown(rows[i]), k = e.key.toLowerCase();
    if (k === 'm') { if (sh.more) { e.preventDefault(); qMore(rows[i].id, sv); } return; }
    const c = k === '0' || k === 'n' ? sh.none : sh.cands[Number(k) - 1];
    if (c) { e.preventDefault(); answerQ(rows[i].id, c.choice, c.label, sv); }
  }
});


// ---- Report: targets, pages, passes, calls by tool, slowest, cost; the written report below --------------------------
function writtenReport(v) {
  const out = (v.blocks || []).map(b => {
    switch (b.type) {
      case 'h1': case 'h2': case 'h3': return `<${b.type}>${esc(b.text)}</${b.type}>`;
      case 'li': return `<div class="li${b.indent ? ' i2' : ''}">${esc(b.text)}</div>`;
      case 'gap': return '<div class=gap></div>';
      case 'table': {
        const [first, ...rest] = b.rows;
        return `<table><thead><tr>${first.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>` +
          rest.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('') + '</tbody></table>';
      }
      default: return `<p>${esc(b.text)}</p>`;
    }
  });
  return `<div class=report>${out.join('')}</div>`;
}
function reportTab(v) {
  const m = v.measures;
  if (!m) return writtenReport(v);
  const met = m.targets.filter(t => t.ok).length;
  const val = x => x == null ? '—' : String(x);
  const chips = `<div class=tchips>${m.targets.map(t => `<span class="tc${t.ok ? '' : ' miss'}" title="Bar ${esc(t.bar)}">${ic(t.ok ? 'ok' : 'miss')}${esc(t.label)} <b>${esc(val(t.value))}</b></span>`).join('')}</div>`;
  const tot = m.passes.reduce((a, x) => a + x.seconds, 0) || 1;
  let acc = 0;
  const labs = m.passes.map(p => { const left = acc / tot * 100; acc += p.seconds; return {...p, left}; }).filter(p => p.seconds / tot >= .06);
  const pbar = `<div class=pbar>${m.passes.map(p => `<i class="${p.key === 'CT' ? 'k' : ''}" data-css="flex:${p.seconds} 1 0;min-width:3px" title="${esc(p.label)} ${Math.round(p.seconds)} s"></i>`).join('')}</div>` +
    `<div class=plab>${labs.map(p => `<span data-css="${p.left > 85 ? 'left:auto;right:0;text-align:right' : 'left:' + p.left.toFixed(1) + '%'}">${esc(p.label)}<b>${secText(p.seconds)}</b></span>`).join('')}</div>`;
  const top = (m.tools[0] || {}).seconds || 1;
  const tb = `<div class="tb h"><span>Tool</span><span>Seconds</span><span class=v></span><span class=c>Calls</span></div>` +
    `<div class=tb>${m.tools.map(t => `<span class=t>${esc(t.tool)}</span><span class=bar><i data-w="${(t.seconds / top * 100).toFixed(1)}"></i></span><span class=v>${Math.round(t.seconds)} s</span><span class=c>${t.calls}</span>`).join('')}</div>`;
  const sl = `<div class=sl>${m.slowest.map(c => `<time>${esc(dayTime(c.at))}</time><span class=t>${esc(c.tool)}</span><span class=v>${c.seconds} s</span>`).join('')}</div>`;
  const cost = `<div class=cost>${m.cost.map(c => `<span class=k>${esc(c.label)}</span>${meter(c.used, c.cap)}<span class=v>${usd(c.usd)}</span><span class=n>${c.used} of ${c.cap ?? '∞'} calls</span>`).join('')}</div>`;
  const heat = v.heat && v.heat.length ? `<div class=sect>Pages</div><div class=hm>${heatGrid(v.heat, heatMeasures(v.heat).filter(m => m !== 'asked'))}</div>${legend(heatKeys(v.heat))}` : '';   // the run's pages at a glance, as drawn (apollo-ui.html Report)
  const wall = m.wall_seconds != null ? m.wall_seconds : tot;
  const body = `${m.targets.length ? `<div class=sect>Targets <small>${met} of ${m.targets.length} met</small></div>${chips}` : ''}${heat}` +
    `<div class=rgrid><div><div class=sect>Passes <small>${elapsed(wall)}</small></div>${pbar}` +
    `<div class=sect>Calls <small>by tool, top ${m.tools.length} of ${m.tools_total}</small></div>${tb}</div>` +
    `<div>${m.slowest.length ? `<div class=sect>Slowest</div>${sl}` : ''}${showCost() ? `<div class=sect>Cost <small>${usd(m.usd)}</small></div>${cost}` : ''}</div></div>`;
  return body + (v.blocks && v.blocks.length ? `<div class=sect>Written report</div>${writtenReport(v)}` : '');
}

// ---- the bar: New run, Sources, Collections ------------------------------------------------------------------------
const title = s => s.slice(0, 1).toUpperCase() + s.slice(1).replace(/[-_]/g, ' ');
function drawBar() {
  $('#who').innerHTML = `${ic('seal')}Unsigned`;
  layout();
}
// A view of its own (Sources, Collections) takes the page; Esc or Home leaves it.
function showPage(p) {
  closeCfg();
  state.page = state.page === p ? null : p;
  select(null);
  layout();
  if (state.page === 'sources') openSources();
  if (state.page === 'collections') openCollections();
  if (state.page === 'history') openHistory();
  if (state.page === 'settings') openSettings();
}
$('#nrun').onclick = () => { if (state.cfg) closeCfg(); else { state.page = null; openCfg('full'); } };
$('#nsrc').onclick = () => showPage('sources');
$('#ncol').onclick = () => showPage('collections');
$('#nhis').onclick = () => showPage('history');
async function loadConfigs() {
  try {
    state.configs = await api('/api/configs');
    setError('configs', null);
    drawBar();
    if (state.page === 'collections' && !state.col.archive) openCollections();     // the Draft's path arrives with the configs
  } catch (e) {
    setError('configs', `Recipes: ${e.message}`);
  }
  if (window.apolloPhase) window.apolloPhase("sources");   /* launch */
}

// ---- Configure: one recipe; a row of stage buttons, the chosen one's model, budget and prompt; Start --------------------
Object.assign(SV, {
  analyse: '<path d="M3 3v18h18"/><path d="M20 18v3M16 16v5M12 13v8M8 16v5M3 11c6 0 5-5 9-5s3 3 9 3"/>',
  read: '<path d="M3 19a9 9 0 0 1 9 0a9 9 0 0 1 9 0"/><path d="M3 6a9 9 0 0 1 9 0a9 9 0 0 1 9 0"/><path d="M3 6v13M12 6v13M21 6v13"/>',
  roster: '<path d="M9 6h11M9 12h11M9 18h11M5 6v.01M5 12v.01M5 18v.01"/>',
  supervise: '<circle cx=12 cy=12 r=2 /><path d="M21 12c-2.4 4-5.4 6-9 6s-6.6-2-9-6c2.4-4 5.4-6 9-6s6.6 2 9 6"/>',
  review: '<circle cx=12 cy=12 r=9 /><path d="M12 17v.01M12 13.5a1.5 1.5 0 0 1 1-1.5a2.6 2.6 0 1 0-3-4"/>',
  stubs: '<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z"/><path d="M12 11v6M9 14h6"/>',
  places: '<circle cx=12 cy=11 r=3 /><path d="M17.657 16.657l-4.243 4.243a2 2 0 0 1-2.827 0l-4.244-4.243a8 8 0 1 1 11.314 0z"/>',
  enrich: '<path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2-2a2 2 0 0 1-2-2a2 2 0 0 1-2 2zM16 6a2 2 0 0 1 2 2a2 2 0 0 1 2-2a2 2 0 0 1-2-2a2 2 0 0 1-2 2zM9 18a6 6 0 0 1 6-6a6 6 0 0 1-6-6a6 6 0 0 1-6 6a6 6 0 0 1 6 6z"/>',
  viewprep: '<path d="M4 4h6v8H4zM14 4h6v4h-6zM14 12h6v8h-6zM4 16h6v4H4z"/>',
  closeout: '<path d="M5 5a5 5 0 0 1 7 0a5 5 0 0 0 7 0v9a5 5 0 0 1-7 0a5 5 0 0 0-7 0zM5 21v-7"/>',
  home: '<path d="M5 12H3l9-9l9 9h-2"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/><path d="M9 21v-6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v6"/>',
  // CZ318: the flows' icons, from Hydra's shipped set (template/.hydra/resources/icons: network, timeline, wiki-w, coins)
  assemble: '<rect x=16 y=16 width=6 height=6 rx=1 /><rect x=2 y=16 width=6 height=6 rx=1 /><rect x=9 y=2 width=6 height=6 rx=1 /><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/><path d="M12 12V8"/>',
  timeline: '<path d="M4 16l6 -7l5 5l5 -6"/><path d="M15 14m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M10 9m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M4 16m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M20 8m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>',
  mark: '<path d="M3 19h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4"/><path d="M12.5 5.5l4 4"/><path d="M4.5 13.5l4 4"/><path d="M21 15v4h-8l4 -4l4 0"/>',
  fit: '<path d="M12 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M4 6l8 0"/><path d="M16 6l4 0"/><path d="M6 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M4 12l2 0"/><path d="M10 12l10 0"/><path d="M15 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M4 18l11 0"/><path d="M19 18l1 0"/>',
  wikidata: '<text x=11.75 y=19 text-anchor=middle fill=currentColor stroke=none font-family="\'Linux Libertine\',\'Hoefler Text\',Georgia,\'Times New Roman\',serif" font-size=20>W</text>',
  finance: '<path d="M13.744 17.736a6 6 0 1 1-7.48-7.48"/><path d="M15 6h1v4"/><path d="m6.134 14.768.866-.5 2 3.464"/><circle cx=16 cy=8 r=6 />',
  wiki: '<path d="M5 4m0 1a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z"/><path d="M9 4m0 1a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z"/><path d="M5 8h4"/><path d="M9 16h4"/><path d="M13.803 4.56l2.184 -.53c.562 -.135 1.133 .19 1.282 .732l3.695 13.418a1.02 1.02 0 0 1 -.634 1.219l-.133 .041l-2.184 .53c-.562 .135 -1.133 -.19 -1.282 -.732l-3.695 -13.418a1.02 1.02 0 0 1 .634 -1.219l.133 -.041z"/><path d="M14 9l4 -1"/><path d="M16 16l3.923 -.98"/>'
});
Object.assign(SV, {      // Tabler Icons (MIT): the log's other verbs, the Tools and the finding levels
  Amended: '<path d="M4 20h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4"/><path d="M13.5 6.5l4 4"/>',
  Changed: '<path d="M7 10h14l-4 -4"/><path d="M17 14h-14l4 4"/>',
  Checked: '<path d="M7 12l5 5l10 -10"/><path d="M2 12l5 5m5 -5l5 -5"/>',
  Created: '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M9 12h6"/><path d="M12 9v6"/>',
  Failed: '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M10 10l4 4m0 -4l-4 4"/>',
  Placed: '<path d="M9 11a3 3 0 1 0 6 0a3 3 0 0 0 -6 0"/><path d="M17.657 16.657l-4.243 4.243a2 2 0 0 1 -2.827 0l-4.244 -4.243a8 8 0 1 1 11.314 0"/>',
  Skipped: '<path d="M4 5v14l12 -7z"/><path d="M20 5l0 14"/>',
  Ticked: '<path d="M9 11l3 3l8 -8"/><path d="M20 12v6a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2h9"/>',
  Said: '<path d="M8 9h8"/><path d="M8 13h6"/><path d="M18 4a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-5l-5 3v-3h-2a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3h12"/>',
  Called: '<path d="M8 9l3 3l-3 3"/><path d="M13 15l3 0"/><path d="M3 6a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2z"/>',
  Stopped: '<path d="M5 7a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2z"/>',
  ocr: '<path d="M4 7v-1a2 2 0 0 1 2 -2h2"/><path d="M4 17v1a2 2 0 0 0 2 2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v1"/><path d="M16 20h2a2 2 0 0 0 2 -2v-1"/><path d="M5 12l14 0"/>',
  text: '<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2"/><path d="M9 9l1 0"/><path d="M9 13l6 0"/><path d="M9 17l6 0"/>',
  check: '<path d="M3.5 5.5l1.5 1.5l2.5 -2.5"/><path d="M3.5 11.5l1.5 1.5l2.5 -2.5"/><path d="M3.5 17.5l1.5 1.5l2.5 -2.5"/><path d="M11 6l9 0"/><path d="M11 12l9 0"/><path d="M11 18l9 0"/>',
  warn: '<path d="M12 9v4"/><path d="M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0z"/><path d="M12 16h.01"/>',
  info: '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 9h.01"/><path d="M11 12h1v4h1"/>'
});
const STAGE_ICON = {ocr: 'ocr', text: 'text', catalogue: 'catalogue', check: 'check', analyse: 'analyse', read: 'read', escalate: 'Escalated', roster: 'roster', lag: 'lag', rate: 'rate', supervise: 'supervise',
                    review: 'review', stubs: 'stubs', places: 'places', enrich: 'enrich', 'view-prep': 'viewprep', 'close-out': 'closeout',
                    assemble: 'assemble', refine: 'review', timeline: 'timeline', wikidata: 'wikidata', finance: 'finance', mark: 'mark', fit: 'fit'};
const MODELS = [['Haiku', 'claude-haiku-5-5'], ['Sonnet', 'claude-sonnet-5-5'], ['Opus', 'claude-opus-5-5']];   // bronze, silver, gold
const LIMIT_TIPS = {
  parallel: 'How many pages the reader works on at the same time. More is faster, and puts more calls in flight at once.',
  calls: 'The most model calls one page may use. When the source is profiled, a page’s cap scales with its words, between a floor and a ceiling.',
  seconds: 'The most time one page may take, in seconds, before it is cut off. Scales with the page’s words like the calls.',
  escalations: 'How many times a hard page may be handed to Sonnet in the whole run.',
  flush: 'Pages between saves of the archive. A crash loses at most this many pages of work.',
  ties: 'How many pairs of names the ties pass checks in one model call.',
  min_words: 'Pages with fewer words than this are thin and skipped.',
  redacted: 'Pages with a larger share of redacted lines than this are skipped.'
};
HEAT.triage = h => h.needs_ocr ? 'ocr' : ({thin: 'thin', redacted: 'redacted', skip: 'skip'}[h.status] || 'todo');
HEAT_LABEL.triage = 'Pages';
const TRIAGE_WORD = {todo: 'to do', thin: 'thin', redacted: 'redacted', skip: 'skipped', ocr: 'needs OCR'};

// First paint: the whole Configure layout at once. The last answer for this recipe (or any recipe) is kept in the browser
// and drawn as the shape; a first-ever open uses this fixed one. Values fill in, same sizes, when the server answers.
const SHELL_FLOWS = {
  analyse: [['Read the file', 'code'], ['Text layer', 'code'], ['Words and redaction', 'code'], ['Triage', 'code']],
  read: [['Preflight', 'code'], ['Setup', 'code'], ['Triage', 'code'], ['Names', 'haiku'], ['Roster', 'sonnet'], ['Records and ties', 'haiku'],
         ['Hard pages', 'sonnet'], ['Lag check', 'haiku'], ['Identity', 'haiku'], ['Close', 'code'], ['Rate', 'sonnet']]
};
const SHELL_LIMITS = [['parallel', 'Pages at once'], ['calls', 'Calls a page'], ['seconds', 'Seconds a page'], ['escalations', 'Escalations'],
                      ['flush', 'Save every'], ['ties', 'Ties batch'], ['min_words', 'Min words'], ['redacted', 'Redacted']];
function shellDetail(name) {
  const blank = d => {                           // another recipe's shape: its numbers are not this one's
    d.stages.forEach(s => { if (s.kind === 'flow') s.budget = null; });
    d.limits.forEach(l => { l.value = null; });
    d.run = {...d.run, pages: null};
    return d;
  };
  try {
    const own = localStorage.getItem('apollo.cfg.' + name), any = localStorage.getItem('apollo.cfg._last');
    if (own) return withTools(JSON.parse(own));
    if (any) return withTools(blank(JSON.parse(any)));
  } catch (e) { /* no cache: the fixed shape */ }
  const stage = (key, label, kind, extra = {}) => ({key, label, kind, about: '', role: null, model: kind === 'analyse' ? null : '-', budget: null, on: true, toggle: false, prompts: [], ...extra});
  const flow = k => SHELL_FLOWS[k].map(([label, who]) => ({label, who, note: '', per: 'once', role: who === 'code' ? undefined : who}));
  return withTools({name, flows: {analyse: flow('analyse'), read: flow('read')}, run: {source: null, pages: null, runner: 'Claude Code'}, agents: [],
          stages: [stage('analyse', 'Analyse', 'analyse'), stage('read', 'Basic', 'flow', {role: 'haiku'}), ...['Stubs', 'Places', 'Enrich'].map(l => stage(l.toLowerCase(), l, 'workflow'))],
          limits: SHELL_LIMITS.map(([key, label]) => ({key, label, value: null})), prompts: [], authorized: [], lag: false});
}
function keepShape(name, d) {
  try {
    const s = JSON.stringify({...d, prompts: []});
    localStorage.setItem('apollo.cfg.' + name, s);
    localStorage.setItem('apollo.cfg._last', s);
  } catch (e) { /* a full or blocked store: next open draws the fixed shape */ }
}
async function openCfg(name, source) {
  const shape = shellDetail(name);
  const p = shape.run.pages;
  state.page = null;
  state.cfg = {name, source: source || shape.run.source || null, detail: shape, shell: true, from: p ? String(p[0]) : '', to: p ? String(p[1]) : '',
               limits: store.get('apollo-limits') === '1', dry: false, flow: 'read', sel: 'read', step: null, models: {}, on: {}, an: null, analysing: false, prompts: {}, pk: {}, archive: 'draft',
               cap: '', capSet: false};
  state.rail.cfg.page = 0;
  for (const s of shape.stages) {
    if (s.model && s.model !== '-' && !(mkey(s) in state.cfg.models)) state.cfg.models[mkey(s)] = s.model;
    state.cfg.on[s.key] = s.on;
  }
  state.cfg.on.lag = false;
  state.cfg.on.after = store.get('apollo-after') !== '0';                 // Assemble after a Basic run: on unless turned off (owner)
  layout();
  drawCfg();
  await loadCfg(source);
}
function closeCfg() {
  if (!state.cfg) return;
  state.cfg = null;
  layout();
}
const stageOf = c => c.detail && (c.detail.stages.find(s => s.key === c.sel) || (ENGINE_FLOWS.includes(c.sel) ? {key: c.sel, kind: 'engine'} : undefined));   // an engine flow has no stage of its own: Dry and Run only
const mkey = st => st.role || st.key;
async function loadCfg(source) {
  const c = state.cfg;
  if (!c) return;
  try {
    const d = await api(`/api/recipe/${encodeURIComponent(c.name)}${source ? '?source=' + encodeURIComponent(source) : ''}`);
    if (state.cfg !== c) return;
    c.detail = withTools(d);
    c.shell = false;
    keepShape(c.name, d);
    c.source = d.run.source;
    c.from = d.run.pages ? d.run.pages[0] : '';
    c.to = d.run.pages ? Math.min(d.run.pages[1], d.run.pages[0] + 10) : '';     // a first run reads 11 pages; All pages widens it
    c.all = false;
    c.an = null;
    state.rail.cfg.page = 0;
    for (const s of d.stages) {
      if (s.model && !(mkey(s) in c.models)) c.models[mkey(s)] = s.model;
      c.on[s.key] = s.on;
    }
    c.on.lag = !!c.on.lag;
    if (c.on.after === undefined) c.on.after = store.get('apollo-after') !== '0';
    c.apFrom = c.apTo = '';                            // Assemble's pages: empty is every page read so far
    setError('cfg', null);
    drawCfg();
    loadStagePrompts();
    scheduleReady(c, true);
  } catch (e) {
    setError('cfg', `Configure: ${e.message}`);
  }
}
async function loadStagePrompts() {
  const c = state.cfg, s = c && c.detail && curStep(c);
  if (!s) return;
  for (const n of stepPrompts(c, s)) {
    if (n in c.prompts) continue;
    try { c.prompts[n] = (await api(`/api/prompt/${encodeURIComponent(n)}`)).text; } catch (e) { c.prompts[n] = ''; setError('cfg', `Prompt: ${e.message}`); }
  }
  if (state.cfg === c) drawStage();
}
function promptHTML(text) {
  return text.split('\n').map(l => /^#{1,3} /.test(l) ? `<span class=h>${esc(l)}</span>` : esc(l).replace(/\{[a-z_]+\}/g, m => `<span class=v>${m}</span>`));
}
const PROMPT_LABEL = {A: 'Names', C: 'Records', T: 'Ties', I: 'Identity', L: 'Lag', roster: 'Roster', rate: 'Rate', workflow: 'Brief'};

function cfgRows(c) {
  const a = parseInt(c.from, 10), z = parseInt(c.to, 10);
  if (c.shell && !(a > 0 && z >= a)) return Array.from({length: 40}, (_, i) => ({page: i + 1, status: 'todo'}));
  if (!(a > 0 && z >= a && z - a < 3000)) return [];
  const out = [];
  for (let p = a; p <= z; p++) out.push((c.an && c.an.get(p)) || {page: p, status: 'todo'});
  return out;
}
function cfgLegend(rows, analysed) {
  const kinds = [['todo', 'To do'], ['thin', 'Thin'], ['redacted', 'Redacted'], ['skip', 'Skip'], ['ocr', 'Needs OCR']];
  const n = k => rows.filter(r => HEAT.triage(r) === k).length;
  return `<div class=lg>${kinds.map(([k, w]) => `<span><i class=${k}></i>${w}${analysed ? ` <b>${n(k)}</b>` : ''}</span>`).join('')}</div>`;
}
function drawHeat() {
  const c = state.cfg, box = $('#cfgheat');
  if (!c || !box) return;
  const rows = cfgRows(c);
  if (!rows.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="hm tipped">${heatGrid(rows, ['triage'])}<div class=tip hidden></div></div>${c.an ? cfgLegend(rows, true) : ''}`;   // a legend once the pages are known
  applyCss(box);
}
function cfgTip(h) {
  const k = HEAT.triage(h);
  const small = h.words == null ? '' : `<small>${h.words} words · ${Math.round((h.redacted_share || 0) * 100)}% redacted</small>`;
  return `<b>p. ${h.page}</b> · ${TRIAGE_WORD[k]}${small}`;
}
function limitHTML(l) {
  const v = l.value == null ? '—' : l.key === 'redacted' ? Math.round(l.value * 100) + '%' : l.value;
  return `<div class=f><span class=lh>${esc(l.label)}<button type=button class=q aria-describedby="tt-${l.key}" aria-label="About ${esc(l.label)}">?</button></span>` +
    `<input class=n value="${esc(v)}" readonly aria-label="${esc(l.label)}"><span class=tt id="tt-${l.key}" role=tooltip>${esc(LIMIT_TIPS[l.key] || '')}</span></div>`;
}
const TOOL_KEYS = ['analyse', 'ocr', 'text', 'catalogue'];
const TOOL_VERB = {analyse: 'Analyse', ocr: 'Read text', text: 'Show text', catalogue: 'Catalogue'};
const TOOL_BUSY = {analyse: 'Analysing', ocr: 'Reading', text: 'Showing', catalogue: 'Cataloguing'};
const TOOL_STAGES = [
  {key: 'ocr', label: 'OCR', about: 'Reads the words on the chosen pages from their picture, for scanned pages that have no text of their own.'},
  {key: 'text', label: 'Text', about: 'Shows the text each chosen page already carries, the text layer the document came with.'}
].map(t => ({...t, kind: 'tool', role: null, model: null, budget: null, on: true, toggle: false, prompts: []}));
const AGENT_STAGES = [
  {key: 'catalogue', label: 'Catalogue', about: 'An agent reads the chosen pages and writes the source’s library catalogue record, each field citing its page.'}
].map(t => ({...t, kind: 'agent', role: null, model: null, budget: null, on: true, toggle: false, prompts: []}));
const TOOL_FLOWS = {
  ocr: [['Pages', 'Draws each chosen page as a picture.'], ['Words', 'Recognises the words on every picture.'], ['Save', 'Keeps the text in Apollo’s own folder, never beside the source.']],
  text: [['Pages', 'Takes each chosen page from the document.'], ['Text layer', 'Reads the text the page already carries, line by line.']]
};
const withTools = d => {                          // the Tools group: Analyse (from the manager) and the four tools this page adds
  d.stages = d.stages.filter(s => s.key !== 'build' && s.key !== 'check');
  for (const t of TOOL_STAGES.concat(AGENT_STAGES)) if (!d.stages.some(s => s.key === t.key)) d.stages = d.stages.concat(t);
  d.flows = d.flows || {};
  for (const [k, steps] of Object.entries(TOOL_FLOWS)) d.flows[k] = d.flows[k] || steps.map(([label, note]) => ({label, who: 'code', per: 'once', note}));
  return d;
};
const isTool = st => !!st && (st.kind === 'analyse' || st.kind === 'tool' || st.kind === 'agent');
// The flows, grouped by the domain they work in (owner: the real runs, not the tools; Analyse, OCR, Text and Catalogue run by
// themselves when a Read starts). The manager sends the list (configs.flows); this is its shape until it answers.
const ENGINE_FLOWS = ['mark', 'fit', 'assemble', 'refine', 'timeline', 'places', 'stubs', 'enrich', 'finance', 'wikidata', 'wiki'];      // apollo.py ENGINE_FLOWS: driven by pageflow/engine.py
const FLOW_DEFS = [
  {key: 'read', name: 'Basic', domain: 'Records', needs: 'source', grades: {haiku: {default: 4, max: 12}, sonnet: {default: 1, max: 4}}},
  {key: 'mark', name: 'Mark', domain: 'Records', needs: 'archive', after: 'read', grades: {haiku: {default: 1, max: 1}, sonnet: {default: 0, max: 1}, opus: {default: 0, max: 1}}},
  {key: 'fit', name: 'Fit', domain: 'Records', needs: 'archive', after: 'mark', grades: {haiku: {default: 0, max: 1}, sonnet: {default: 1, max: 1}, opus: {default: 0, max: 1}}},
  {key: 'assemble', name: 'Assemble', domain: 'Graph', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 1}}},
  {key: 'refine', name: 'Refine', domain: 'Graph', needs: 'archive', after: 'assemble', grades: {haiku: {default: 0, max: 1}, sonnet: {default: 0, max: 1}, opus: {default: 1, max: 1}}},
  {key: 'timeline', name: 'Timeline', domain: 'Time', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'places', name: 'Places', domain: 'Geo', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'stubs', name: 'Stubs', domain: 'Profiles', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'enrich', name: 'Enrich', domain: 'Profiles', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'wikidata', name: 'Wikidata', domain: 'Profiles', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'finance', name: 'Finance', domain: 'Finance', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}},
  {key: 'wiki', name: 'Curate', domain: 'Curate', needs: 'archive', after: 'read', grades: {sonnet: {default: 1, max: 4}}}];
const flowDefs = () => (state.configs.flows && state.configs.flows.length ? state.configs.flows : FLOW_DEFS);
const flowDef = k => flowDefs().find(f => f.key === k) || FLOW_DEFS[0];
// A flow waits for the finished run of its `after` flow on the chosen archive; an archive that already holds records counts as done.
function flowReady(f, c) {
  if (!f.after) return true;
  const p = archivePath(c);
  if (f.after !== 'mark' && p && p === draftPath() && ((state.configs.draft || {}).records || 0) > 0) return true;     // Fit waits for a Mark run, whatever the draft holds
  return state.runs.some(r => r.archive === p && flowKey(r) === f.after && r.status === 'done');
}
function stageButtons(d, c) {
  return flowDefs().map(f => {
    const ok = flowReady(f, c), need = ok ? '' : ` disabled title="Needs ${esc(flowDef(f.after).name)} first"`;
    return `<button type=button class=sb data-flow="${esc(f.key)}" aria-pressed=${f.key === c.flow}${need}><span class=bi>${ic(f.key)}</span><span class=bl>${esc(f.name)}</span></button>`;
  }).join('');
}
// Agents: how many of each grade the run sets off (bronze Haiku, silver Sonnet, gold Opus), only the grades the flow uses
const GRADE_WORD = {haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'};
const GRADES = ['haiku', 'sonnet', 'opus'];             // the rank, bronze → silver → gold: every list of grades runs in this order (owner)
function agentCounts(c) {
  const g = flowDef(c.flow).grades || {}, mine = (c.agents = c.agents || {})[c.flow] = (c.agents || {})[c.flow] || {};
  for (const [k, v] of Object.entries(g)) if (mine[k] == null) mine[k] = v.default;
  return mine;
}
function agentsHTML(c) {
  const g = flowDef(c.flow).grades || {}, n = agentCounts(c);
  const keys = GRADES.filter(k => g[k]);                // the manager's map arrives in key order (haiku, opus, sonnet)
  if (!keys.length) return '';
  return `<div class=f><span>Agents</span><span class=grades>${keys.map(k => `<label class=gr1 title="${GRADE_WORD[k]}">${modelMark(k)}` +
    `<input class=n data-grade="${k}" value="${esc(n[k])}" inputmode=numeric aria-label="${GRADE_WORD[k]} agents"></label>`).join('')}</span></div>`;
}
const defaultCap = n => state.set && state.set.limits && state.set.limits.run_usd != null ? state.set.limits.run_usd : n > 11 ?Math.max(1, Math.min(5, Math.ceil(n * 0.045 / 0.5) * 0.5)) : 1;
const cfgPages = c => { const a = parseInt(c.from, 10), z = parseInt(c.to, 10); return a > 0 && z >= a ? z - a + 1 : 0; };
const capText = c => c.capSet ? c.cap : String(defaultCap(cfgPages(c)));
const draftPath = () => (state.configs.draft && state.configs.draft.path) || '';
function archivePath(c) { return c.archive === 'draft' ? draftPath() : c.archive === 'new' ? '' : c.archive; }
function archiveOptions(c) {
  const seen = new Set([draftPath()]);
  const rs = state.runs.filter(r => r.archive && !seen.has(r.archive) && seen.add(r.archive));
  const opt = (v, w) => `<option value="${esc(v)}"${v === c.archive ? ' selected' : ''}>${esc(w)}</option>`;
  return opt('draft', 'Draft') + (c.flow === 'read' ? opt('new', 'New') : '') +
    rs.map(r => opt(r.archive, [runName(r), r.source_title].filter(Boolean).join(' · '))).join('');
}
// Answer questions: the session answers its own questions. On by default for Assemble, off for Wikidata; each flow keeps its own choice.
const answerOn = c => { const v = store.get('apollo-answer-' + c.flow); return v == null ? c.flow === 'assemble' : v === '1'; };
const answerSwitch = c => `<div class="f sw"><span>Answer questions</span><button type=button class="sw2${answerOn(c) ? ' on' : ''}" role=switch aria-checked=${answerOn(c)} aria-label="Answer questions" data-answer></button></div>`;
function drawCfg() {
  const c = state.cfg, box = $('#cfgpanel');
  if (!c) return;
  const d = c.detail;
  const head = `<div class=cfgh><h2>Configure</h2></div>`;
  if (!d) {                                      // first paint: what is known already (the sources), the rest fills in when it arrives
    const known = state.configs.sources || [];
    box.innerHTML = head + (known.length ? `<div class=cbody><div class=tqf><label class=f><span>Source</span><select class=w disabled aria-label=Source>${known.map(s => `<option>${esc(s.title)}</option>`).join('')}</select></label></div></div>` : '');
    return;
  }
  const srcs = state.configs.sources;
  const dis = c.shell ? ' disabled' : '';
  const read = c.flow === 'read';
  const srcSel = `<label class=f><span>Source</span><select class=w id=cfgsrc${dis}>${srcs.map(s => `<option value="${esc(s.name)}"${s.name === c.source ? ' selected' : ''}>${esc(s.title)}</option>`).join('')}</select></label>`;
  const asm = c.flow === 'assemble';
  const eng = ENGINE_FLOWS.includes(c.flow);           // the engine flows read after a source's Basic run: its source and pages
  const src = read ? srcSel + `
    <div class=f><span>Pages</span><span class=pp2><input id=cfgfrom value="${esc(c.from)}" inputmode=numeric aria-label="From page"${c.all || c.shell ? ' disabled' : ''}> – <input id=cfgto value="${esc(c.to)}" inputmode=numeric aria-label="To page"${c.all || c.shell ? ' disabled' : ''}><label class=allp><input type=checkbox id=cfgall${c.all ? ' checked' : ''}${dis}> All pages</label></span></div>`
    : eng ? srcSel + `<div class=f><span>Pages</span><span class=pp2 title="Empty: every page read so far"><input id=cfgafrom value="${esc(c.apFrom || '')}" inputmode=numeric placeholder=first aria-label="From page"${dis}> – <input id=cfgato value="${esc(c.apTo || '')}" inputmode=numeric placeholder=last aria-label="To page"${dis}></span></div>` : '';
  const ans = asm || c.flow === 'wikidata' ? answerSwitch(c) : '';
  const run = `<div class=tqf>${src}
    <label class=f><span>Archive</span><select class=s12 id=cfgarch aria-label=Archive>${archiveOptions(c)}</select></label>
    ${agentsHTML(c)}${ans}
    ${eng ? "" : `<label class=f><span>Runner</span><select class=s9 disabled aria-label=Runner><option>${esc(d.run.runner)}</option></select></label>`}</div>`;
  const capf = read ? `<div class=f><span class=lh>Cost cap<button type=button class=q aria-describedby=tt-cap aria-label="About the cost cap">?</button></span><span class=pp2>$<input class=n id=cfgcap value="${esc(capText(c))}" inputmode=decimal aria-label="Cost cap in dollars"${dis}></span><span class=tt id=tt-cap role=tooltip>The most this run may spend, in dollars. It finishes the page in hand, then closes and writes the report.</span></div>` : '';
  // Limits only for a Read with Limits on; the readers at once are the Agents field now
  const lim = read && c.limits !== false ? `<div class=sect>Limits</div><div class=lim>${capf}${d.limits.filter(l => l.key !== 'parallel').map(limitHTML).join('')}</div>` : '';
  const tools = `<details class=tools><summary>Allowed</summary><div class=chips>${d.authorized.map(t => `<span>${esc(t)}</span>`).join('')}</div></details>`;
  // two columns: everything on the left; the rail (Flow, Text) on the right, beside it all
  // the flow first (the first choice), Run at the end of its row; then what the flow needs
  box.innerHTML = `${head}<div class=cbody><div class=cfgtop><div class=cfgleft>` +
    `<div class="stgrow first"><div class=sgs>${stageButtons(d, c)}</div><div class=go id=cfggo></div></div>` +
    `${run}${read ? '<div class=rdyrow><button type=button id=cfgready class="rdy idle"></button><div id=cfgheat></div></div>' : ''}<div id=cfgrdd></div>` +
    `<div id=cfgstage class=sp></div>${lim}${tools}</div><aside class=rail id=cfgrail data-ctx=cfg aria-label=Rail></aside></div></div>`;
  drawHeat();
  drawReady();
  drawStage();
}
// Ready: is this source, with these pages (and the archive picked, for a workflow), ready for a run. A go-signal in the theme's colours.
function drawReady() {
  const c = state.cfg, b = $('#cfgready');
  if (!c || !b) return;
  const r = c.ready || {};
  const busy = r.state === 'checking';
  const tip = busy ? 'Checking' : r.sentence || 'Check: source, pages, Hydra, archive';
  b.className = `rdy ${busy ? 'busy' : r.level || 'idle'}`;
  b.title = tip;
  b.setAttribute('aria-label', tip);
  b.disabled = !!c.shell;
  const mark = busy ? 'spin' : r.level || 'idle';
  if (b.dataset.m !== mark) { const had = b.dataset.m; b.dataset.m = mark; b.innerHTML = busy ? '<span class=spin aria-hidden=true></span>' : ic('check'); if (had && !busy) motionPulse(b); }
  const box = $('#cfgrdd');
  if (box) box.innerHTML = c.showReady && r.items ? `<div class=rdd><p>${esc(r.sentence)}</p>${r.items.map(i => `<div class="tfind lv-${esc(i.level)}">${ic(LEVEL_ICON[i.level] || 'info')}<span>${esc(i.text)}</span></div>`).join('')}</div>` : '';
}
// Ready is a check of what changes readiness: the source, the pages, the archive, the flow and the run definition (models, switches,
// limits). It runs only when pressed and keeps its result until one of those changes; then it goes back to idle and does not re-run.
const readyKey = c => JSON.stringify([c.source, c.from, c.to, c.archive, c.flow, c.models, c.on, ((c.detail && c.detail.limits) || []).map(l => l.value), c.capSet ? c.cap : '']);
function scheduleReady(c) {
  if (!c.ready || c.ready.state === 'checking' || c.ready.key === readyKey(c)) return;
  c.rseq = (c.rseq || 0) + 1;
  c.ready = null;
  c.showReady = false;
  drawReady();
}
async function runReady(c, fromClick) {
  const key = readyKey(c), seq = c.rseq = (c.rseq || 0) + 1;
  c.ready = {...(c.ready || {}), state: 'checking'};
  drawReady();
  const pages = toolPages(c), arch = c.flow !== 'read' ? archivePath(c) : '';
  try {
    const got = await api(`/api/ready?source=${encodeURIComponent(c.source)}&recipe=${encodeURIComponent(c.name)}` + (pages ? `&pages=${pages}` : '') + (arch ? `&archive=${encodeURIComponent(arch)}` : ''));
    if (state.cfg !== c || c.rseq !== seq) return;
    c.ready = {...got, state: 'done', key};
    if (got.level === 'ready') c.showReady = false; else if (fromClick) c.showReady = true;
  } catch (e) {
    if (state.cfg !== c || c.rseq !== seq) return;
    c.ready = {key, state: 'done', level: 'not-ready', sentence: `Could not check: ${e.message}`, items: [{level: 'error', text: e.message}]};
  }
  drawReady();
}
const STEP_ON = {hard: 'escalate', lag: 'lag', rate: 'rate'};                 // the steps that can be switched off, and the setting each flips
const sk = s => s.key || s.label;
function flowSteps(c) {
  let steps = (c.detail.flows || {})[c.flow] || [];
  if (c.flow !== 'read') steps = steps.filter(s => !(s.who === 'session' && !s.branch));   // the session is the runner, not a step
  return steps;
}
const curStep = c => flowSteps(c).find(s => sk(s) === c.step);
const engineGrade = c => [...GRADES].reverse().find(g => agentCounts(c)[g]) || 'sonnet';   // pageflow engine: the strongest grade given an agent
const stepPrompts = (c, s) => s.prompts && s.prompts.length ? s.prompts : c.flow === 'read' ? [] : ['workflow'];
function drawStage() {
  const c = state.cfg, box = $('#cfgstage');
  if (!c || !box || !c.detail) return;
  const st = stageOf(c);
  document.querySelectorAll('#cfgpanel .sb').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.flow === c.flow)));
  box.hidden = false;
  if (st.kind === 'tool' || st.kind === 'agent') aboutEnsure(c, st.key);   /* tool args */
  if (st.kind === 'tool') {
    const old = $('.tres', box), top = old ? old.scrollTop : 0;
    box.innerHTML = toolPanel(c, st);
    const neu = $('.tres', box);
    if (neu) neu.scrollTop = top;
    drawRail('cfg');
    drawGo(c, st);
    loadTool(c, st);
    return;
  }
  if (st.kind === 'agent') { box.innerHTML = agentPanel(c, st); if (c.source) catLoad(c.source); }
  else if (st.kind === 'analyse') box.innerHTML = `<p class=sab>${esc(st.about)}</p>`;
  else box.innerHTML = stepPanel(c);
  box.hidden = !box.innerHTML;
  drawRail('cfg');
  drawGo(c, st);
}
// One step's settings: its model, its budget, an on/off switch where it can be off, and its prompt.
function stepPanel(c) {
  const s = curStep(c);
  if (!s) return '';
  const key = s.role || c.flow;
  const cur = c.models[key];
  const model = s.who === 'engine' ? `<span>${modelMark(engineGrade(c))}${GRADE_WORD[engineGrade(c)]}</span>` : s.who === 'code' ? '<span class=none>None</span>'
    : `<div class=seg role=radiogroup aria-label=Model>${MODELS.map(([w, id]) => `<button type=button role=radio data-model="${id}" data-mrole="${esc(key)}" aria-checked=${cur === id} title="${esc(modelLabel(id))}">${modelMark(id)}<span>${w}</span></button>`).join('')}</div>`;
  const onKey = STEP_ON[s.key];
  const sw = s.switch && onKey ? `<div class="f sw"><span>On</span><button type=button class="sw2${c.on[onKey] ? ' on' : ''}" role=switch aria-checked=${!!c.on[onKey]} aria-label="${esc(s.label)} on" data-switch="${onKey}"></button></div>` : '';
  const names = stepPrompts(c, s), pk = names.includes(c.pk[sk(s)]) ? c.pk[sk(s)] : names[0];
  let prompt = '';
  if (names.length) {
    const hl = promptHTML(c.prompts[pk] || '');
    prompt = `<details class=pd open><summary>Prompt</summary>${names.length > 1 ? `<div class=pt>${names.map(n =>
      `<button type=button data-prompt="${esc(n)}" aria-pressed=${n === pk} title="${esc(n)}.md">${esc(PROMPT_LABEL[n] || n)}</button>`).join('')}</div>` : ''}` +
      `<div class=ed><div class=ln>${hl.map((_, i) => i + 1).join('\n')}</div><pre>${hl.join('\n')}</pre></div></details>`;
  }
  return `<div class=tqf><div class=f><span>Model</span>${model}</div><div class=f><span>Budget</span><span class=bd>${esc(s.budget || '—')}</span></div>${sw}</div>${prompt}`;
}
function drawGo(c, st) {
  const box = $('#cfggo');
  if (box) box.innerHTML = `${isTool(st) ? '' : `<label><input type=checkbox id=cfgdry${c.dry ? ' checked' : ''}> Dry</label>`}${isTool(st) || c.flow !== 'read' ? '' : `<label><input type=checkbox id=cfglimits${c.limits !== false ? ' checked' : ''}> Limits</label>`}${runButton(c, st)}`;
}
function runButton(c, st, label = isTool(st) ? 'Run Tool' : 'Run') {
  if (st.kind === 'agent') {
    const cc = state.cat[c.source];
    if (catRunning(cc)) return `<button id=cfgstart class="btn run busy toolrun" type=button disabled aria-busy=true><span class=spin aria-hidden=true></span>Running</button>`;
    const off = cc && cc.available === false;
    return `<button id=cfgstart class="btn run toolrun" type=button${c.shell || !c.source || off ? ' disabled' : ''}>${ic('play')}${label}</button>`;
  }
  if (st.kind === 'tool') {
    const rec = toolRec(c, st), ok = toolReady(c, st);
    if (rec && rec.status === 'running') {
      const of = rec.total ? ` ${rec.done || 0} of ${rec.total}` : '';
      return `<button id=cfgstart class="btn run busy toolrun" type=button disabled aria-busy=true><span class=spin aria-hidden=true></span>Running${of}</button>`;
    }
    const tip = (st.key === 'ocr' || st.key === 'text') && !srcIsPdf(c)
      ? `<span class=qwrap><button type=button class=q aria-describedby=tt-ocr aria-label="Why ${TOOL_VERB[st.key]} is off">?</button><span class=tt id=tt-ocr role=tooltip>${TOOL_VERB[st.key]} reads the pages of a PDF; this source is not one.</span></span>`
      : st.key === 'ocr' && rec && rec.ocr_ready === false
      ? `<span class=qwrap><button type=button class=q aria-describedby=tt-ocr aria-label="About OCR">?</button><span class=tt id=tt-ocr role=tooltip>OCR is not installed on this machine. Run install-ocr-venv.sh from the Archive Workflow recipes.</span></span>` : '';
    return `${tip}<button id=cfgstart class="btn run toolrun" type=button${ok ? '' : ' disabled'}>${ic('play')}${label}</button>`;
  }
  if (c.starting) return `<button id=cfgstart class="btn run busy" type=button disabled aria-busy=true><span class=spin aria-hidden=true></span>Setting up run…</button>`;
  return `<button id=cfgstart class="btn run${st.kind === 'analyse' ? ' toolrun' : ''}" type=button${c.analysing || c.shell ? ' disabled' : ''}>${ic('play')}${label}</button>`;
}
$('#cfgpanel').addEventListener('click', e => {
  const c = state.cfg;
  if (!c || !c.detail || c.shell) return;
  const t = e.target;
  const fl = t.closest('.sb[data-flow]');
  if (fl) {
    c.flow = c.sel = fl.dataset.flow;
    c.step = null;
    if (c.flow !== 'read' && c.archive === 'new') c.archive = 'draft';
    drawCfg();
    scheduleReady(c);
    return;
  }
  const cell = t.closest('#cfgheat .r i[data-p]');
  if (cell) { showText('cfg', Number(cell.dataset.p)); return; }
  const mb = t.closest('[data-model]');
  if (mb) { c.models[mb.dataset.mrole] = mb.dataset.model; drawStage(); scheduleReady(c); return; }
  const ab = t.closest('[data-answer]');
  if (ab) { const on = !answerOn(c); store.set('apollo-answer-' + c.flow, on ? '1' : '0'); ab.classList.toggle('on', on); ab.setAttribute('aria-checked', String(on)); return; }
  const swb = t.closest('[data-switch]');
  if (swb) { const k = swb.dataset.switch; c.on[k] = !c.on[k]; if (k === 'after') store.set('apollo-' + k, c.on[k] ? '1' : '0'); drawStage(); scheduleReady(c); return; }
  const pb = t.closest('[data-prompt]');
  if (pb) { c.pk[c.step] = pb.dataset.prompt; drawStage(); return; }
  const fb = t.closest('[data-step]');                 // a step of the Flow tab: its settings open under the buttons
  if (fb) { c.step = c.step === fb.dataset.step ? null : fb.dataset.step; c.sel = c.flow; drawStage(); loadStagePrompts(); return; }
  if (t.closest('#cfgstart')) startFromCfg();
  if (t.closest('#cfgready')) {
    const r = c.ready || {};
    if (r.state === 'checking') return;
    if (r.level && r.level !== 'ready' && c.showReady) { c.showReady = false; drawReady(); return; }   // open details close again
    if (r.level && r.level !== 'ready') { c.showReady = true; drawReady(); }
    runReady(c, true);
  }
});
$('#cfgpanel').addEventListener('change', e => {
  const c = state.cfg;
  if (!c) return;
  if (e.target.id === 'cfgsrc') { c.source = e.target.value; c.total = 0; c.all = false; state.rail.cfg.page = 0; loadCfg(c.source); }
  else if (e.target.id === 'cfglang') c.lang = e.target.value;
  else if (e.target.id === 'cfgdry') c.dry = e.target.checked;
  else if (e.target.id === 'cfglimits') { c.limits = e.target.checked; store.set('apollo-limits', c.limits ? '1' : '0'); drawCfg(); }  // limits
  else if (e.target.id === 'cfgarch') { c.archive = e.target.value; $('#cfgpanel .sgs').innerHTML = stageButtons(c.detail, c); scheduleReady(c); }
  else if (e.target.id === 'cfgall') allPages(c, e.target.checked);
});
function syncCap(c) { const i = $('#cfgcap'); if (i && !c.capSet) i.value = capText(c); }
// All pages: the whole document (pages, or parts for a text source); off goes back to the source's own range
async function allPages(c, on) {
  c.all = on;
  if (!on) {
    const p = c.detail.run.pages;
    [c.from, c.to] = p ? [String(p[0]), String(p[1])] : ['', ''];
    drawCfg();
    return;
  }
  try {
    if (!c.total) {
      const res = await api(`/api/analyse?source=${encodeURIComponent(c.source)}&recipe=${encodeURIComponent(c.name)}`);
      c.total = res.total || 0;
    }
    if (state.cfg !== c || !c.all) return;
    if (c.total) [c.from, c.to] = ['1', String(c.total)];
    drawCfg();
    scheduleReady(c);
    runAnalyse();
  } catch (err) {
    setError('cfg', `All pages: ${err.message}`);
  }
}
$('#cfgpanel').addEventListener('input', e => {
  const c = state.cfg;
  if (!c) return;
  if (e.target.id === 'cfgfrom') { c.from = e.target.value.trim(); drawHeat(); syncCap(c); scheduleReady(c); }
  else if (e.target.id === 'cfgto') { c.to = e.target.value.trim(); drawHeat(); syncCap(c); scheduleReady(c); }
  else if (e.target.id === 'cfgafrom' || e.target.id === 'cfgato') { c[e.target.id === 'cfgafrom' ? 'apFrom' : 'apTo'] = e.target.value.trim(); scheduleReady(c); }
  else if (e.target.id === 'cfgcap') { c.cap = e.target.value.trim(); c.capSet = c.cap !== ''; }
  else if (e.target.dataset.grade) {                 // Agents: a whole number, at most the flow's ceiling
    const g = e.target.dataset.grade, max = ((flowDef(c.flow).grades || {})[g] || {}).max || 12;
    agentCounts(c)[g] = Math.max(0, Math.min(max, parseInt(e.target.value, 10) || 0));
  }
});
// ---- the tools: OCR, Text. Each runs in its own manager process; its result stays until the tool runs again ----
const toolKey = (c, st) => `${st.key}|${st.key === 'ocr' || st.key === 'text' ? c.source || '' : c.archive || ''}`;
const toolRec = (c, st) => state.tools[toolKey(c, st)];
const toolArgs = (c, st) => st.key === 'ocr' || st.key === 'text' ? `tool=${st.key}&source=${encodeURIComponent(c.source || '')}` : `tool=${st.key}&archive=${encodeURIComponent(c.archive || '')}`;
const srcInfo = c => (state.configs.sources || []).find(s => s.name === c.source);
const srcIsPdf = c => { const s = srcInfo(c); return !s || !s.file || /.pdf$/i.test(s.file); };      // OCR and Text read a PDF's pages
function toolReady(c, st) {
  if (c.shell || !c.detail) return false;
  if ((st.key === 'ocr' || st.key === 'text') && !srcIsPdf(c)) return false;
  const rec = toolRec(c, st);
  if (st.key === 'ocr') return !!c.source && !(rec && rec.ocr_ready === false);
  if (st.key === 'text') return !!c.source;
  return false;
}
function toolPages(c) {
  const a = parseInt(c.from, 10), z = parseInt(c.to, 10);
  return a > 0 && z >= a ? `${a}-${z}` : '';
}
async function loadTool(c, st) {                  // the last result for this source or archive, once per key; running ones are watched
  const key = toolKey(c, st);
  if (key in state.tools || state.toolAsked[key] || c.shell || !key.split('|')[1]) return;
  state.toolAsked[key] = true;
  try {
    state.tools[key] = await api(`/api/tool?${toolArgs(c, st)}`);
  } catch (e) {
    delete state.toolAsked[key];
    return;
  }
  delete state.toolAsked[key];
  if (state.cfg === c && c.sel === st.key) drawStage();
  watchTool(c, st);
}
function watchTool(c, st) {
  const key = toolKey(c, st), rec = state.tools[key];
  if (!rec || rec.status !== 'running' || state.toolWatch[key]) return;
  const args = toolArgs(c, st);
  state.toolWatch[key] = setInterval(async () => {
    try {
      const got = await api(`/api/tool?${args}`);
      state.tools[key] = got;
      if (got.status !== 'running') { clearInterval(state.toolWatch[key]); delete state.toolWatch[key]; }
      const cur = state.cfg && stageOf(state.cfg);
      if (cur && toolKey(state.cfg, cur) === key) { if (cur.kind === 'tool') drawStage(); else drawGo(state.cfg, cur); }
    } catch (e) { clearInterval(state.toolWatch[key]); delete state.toolWatch[key]; }
  }, 1000);
}
async function runTool(c, st) {
  const key = toolKey(c, st);
  const body = {tool: st.key, source: c.source, archive: c.archive, args: toolVals(c, st.key)};   /* tool args */
  try {
    await api('/api/tool', body);
    state.tools[key] = {...(state.tools[key] || {}), status: 'running', done: 0, total: null, pages: undefined, items: undefined};
    drawGo(c, st);
    state.tools[key] = await api(`/api/tool?${toolArgs(c, st)}`);
    setError('cfg', null);
  } catch (e) {
    setError('cfg', `${st.label}: ${e.message}`);
  }
  if (state.cfg === c) drawStage();
  watchTool(c, st);
}
const LEVEL_ICON = {error: 'Failed', warning: 'warn', info: 'info'};
function toolPanel(c, st) {
  return `${argPanel(c, st)}${toolResult(toolRec(c, st), st)}`;   /* tool args */
}
function toolResult(rec, st) {
  if (!rec || rec.status === 'none' || rec.ok === false) return '';
  const fail = rec.status === 'failed' ? `<p class=err>${esc(rec.error || 'It did not finish.')}</p>` : '';
  if (st.key === 'ocr' || st.key === 'text') {
    const pages = (rec.pages || []).map(p => `<section class=tpg><h4>p. ${p.page}<small>${p.lines} ${p.lines === 1 ? 'line' : 'lines'}</small></h4><pre class=raw>${esc(p.text)}</pre></section>`).join('');
    const det = st.key === 'ocr' && rec.detected && rec.detected.name ? `<p class=tdet>Detected: ${esc(rec.detected.name)}</p>` : '';
    return fail + det + (pages ? `<div class=tres>${pages}</div>` : '');
  }
  return fail;
}
async function runAnalyse() {
  const c = state.cfg;
  if (!c || !c.detail || c.shell || c.analysing) return;
  c.analysing = true;
  drawStage();
  try {
    const q = `source=${encodeURIComponent(c.source)}&recipe=${encodeURIComponent(c.name)}` + (c.from && c.to ? `&pages=${c.from}-${c.to}` : '');
    const res = await api(`/api/analyse?${q}`);
    if (state.cfg !== c) return;
    c.an = new Map((res.pages || []).map(p => [p.page, p]));
    if (res.total) c.total = res.total;
    setError('cfg', null);
    drawHeat();
    animateCells([...document.querySelectorAll('#cfgheat .r i')], true);
  } catch (e) {
    setError('cfg', `Analyse: ${e.message}`);
  } finally {
    c.analysing = false;
    if (state.cfg === c) drawStage();
  }
}
async function startFromCfg() {
  const c = state.cfg, st = c && c.detail && stageOf(c);
  if (!st) return;
  if (st.kind === 'analyse') return runAnalyse();
  if (st.kind === 'tool') return runTool(c, st);
  if (st.kind === 'agent') { if (c.source) { state.cat[c.source] = {...(state.cat[c.source] || {ok: true, available: true}), job: {status: 'running'}}; catStart(c.source); drawGo(c, st); } return; }
  if (c.starting) return;
  // (owner, CZ313) Run starts on the click: no "are you sure" box
  const body = {dry: c.dry, flow: c.flow, agents: {...agentCounts(c)}};
  if (c.flow !== 'read') {
    if (!archivePath(c)) { setError('cfg', 'Start: no archive to work in yet.'); return; }
    Object.assign(body, {archive: c.archive === 'draft' ? 'draft' : c.archive, model: c.models[c.flow]});
    if (c.flow === 'wikidata' && answerOn(c)) body.answer = true;
    if (ENGINE_FLOWS.includes(c.flow)) {
      if (c.source) body.source = c.source;
      if (c.apFrom && c.apTo) body.pages = `${c.apFrom}-${c.apTo}`;
      else if (c.apFrom || c.apTo) { setError('cfg', 'Start: pages are a first and a last page, or both empty.'); return; }
      if (c.flow === 'assemble' && !answerOn(c)) body.no_answer = true;
    }
  } else {
    const first = {};
    for (const s of c.detail.stages) if (s.role && !(s.role in first)) first[s.role] = s.model;
    const models = Object.entries(first).filter(([r, m]) => c.models[r] && c.models[r] !== m).map(([r]) => `${r}=${c.models[r]}`).join(',');
    const without = [c.on.rate ? '' : 'rate', c.on.escalate ? '' : 'escalate'].filter(Boolean).join(',');
    const cap = parseFloat(capText(c));
    if (!(cap > 0)) { setError('cfg', 'Start: the cost cap is a number of dollars.'); return; }
    Object.assign(body, {source: c.source, pages: c.from && c.to ? `${c.from}-${c.to}` : '', budget: String(cap), lag: !!c.on.lag, after: c.on.after ? 'assemble' : '', models, without,
                         archive: c.archive === 'draft' ? 'draft' : c.archive === 'new' ? 'new' : c.archive});
    if (c.limits === false) body.no_limits = true;  // limits
  }
  c.starting = true;
  const swap = () => { const b = $('#cfgstart'); if (b) b.outerHTML = runButton(c, st); };
  swap();                                        // pressed, and "Setting up run…" until the run's own view is up
  try {
    const out = await api('/api/start', body);
    setError('cfg', null);
    await pollRuns();
    closeCfg();
    select(out.id);
  } catch (e) {
    setError('cfg', `Start: ${e.message}`);
  } finally {
    c.starting = false;
    swap();
  }
}

// ---- dialogs ---------------------------------------------------------------------------------------------------
function confirmBox(msg, ok) {
  const d = $('#confirmdlg');
  $('#confirmmsg').textContent = msg;
  $('#confirmok').textContent = ok;
  d.returnValue = '';
  d.showModal();
  return new Promise(res => d.addEventListener('close', () => res(d.returnValue === 'yes'), {once: true}));
}

$('#stop').onclick = async () => {
  const s = state.byId[state.sel];
  if (!s || !(await confirmBox(`Stop ${s.name || s.id}?`, 'Stop'))) return;
  try {
    await api(`/api/stop/${encodeURIComponent(s.id)}`, {});
    setError('action', null);
    pollRuns();
  } catch (e) {
    setError('action', `Stop: ${e.message}`);
  }
};

// Run again: clear this run and start it fresh with the same settings
$('#rerun').onclick = async () => {
  const s = state.byId[state.sel];
  if (!s) return;
  const b = $('#rerun');
  if (!(await confirmBox(`Run ${s.name || s.id} again from the start? What it found so far is deleted, then it runs with the same settings${s.dry ? '' : ' (not dry: this costs money)'}.`, 'Run again'))) return;
  b.disabled = true;
  b.textContent = 'Setting up run…';
  try {
    const out = await api(`/api/rerun/${encodeURIComponent(s.id)}`, {});
    setError('action', null);
    await pollRuns();
    select(out.id);
  } catch (e) {
    setError('action', `Run again: ${e.message}`);
  } finally {
    b.textContent = 'Run again';
  }
};

// Resume: carry a stopped page-flow run on from its saved state; nothing is cleared
$('#resume').onclick = async () => {
  const s = state.byId[state.sel];
  if (!s) return;                                           // (owner, CZ313) resumes on the click
  const b = $('#resume');
  b.disabled = true;
  try {
    await api(`/api/resume/${encodeURIComponent(s.id)}`, {});
    setError('action', null);
    await pollRuns();
  } catch (e) {
    setError('action', `Resume: ${e.message}`);
  } finally {
    b.disabled = false;
  }
};

// Clear: a run that has run loses its archive and everything it found, for good; a run that never ran just leaves the list
$('#clear').onclick = async () => {
  const s = state.byId[state.sel];
  if (!s) return;
  const name = s.name || s.id;
  const recs = (s.cells || []).reduce((n, c) => n + (c[2] || 0), 0);
  const ran = (s.calls && s.calls.hydra) || (s.progress && s.progress.pages_read) || recs;
  const msg = ran
    ? `Clear ${name}? This permanently deletes its archive and everything it found${recs ? ` (${recs} records)` : ''}, with its log and report. This cannot be undone.`
    : `Remove ${name} from the list? It has not run, so no data is lost.`;
  if (!(await confirmBox(msg, ran ? 'Clear for good' : 'Remove'))) return;
  try {
    await api(`/api/clear/${encodeURIComponent(s.id)}`, {});
    setError('action', null);
    select(null);
    await pollRuns();
  } catch (e) {
    setError('action', `Clear: ${e.message}`);
  }
};

$('#folder').onclick = async () => {
  const s = state.byId[state.sel];
  if (!s) return;
  const p = s.run_folder_host || s.run_folder;
  const box = $('#path'), inp = $('#pathtext');
  box.hidden = false;
  inp.value = p;
  inp.focus();
  inp.select();
  let ok = false;
  try { await navigator.clipboard.writeText(p); ok = true; } catch (_) {
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
  }
  const b = $('#folder');
  b.textContent = ok ? 'Copied' : 'Folder';
  setTimeout(() => { b.textContent = 'Folder'; }, 1500);
};

$('#quit').onclick = async () => {
  if (!(await confirmBox('Quit Apollo? Runs keep going.', 'Quit'))) return;
  try { await api('/api/quit', {}); } catch (_) { /* gone already */ }
  state.gone = true;
  document.body.classList.add('gone');
};

// ---- keys, start-up ----
// ---- theme, mode, settings panel -------------------------------------------------------------------------------
const THEMES = [  // from hydra's themes; dark: false for the light-only ones; sw: bg, accent, link
  {id: 'dune', name: 'Dune', dark: true, sw: ['#f6ede7', '#c87a4f', '#1b5e52']},
  {id: 'shore', name: 'Shore', dark: true, sw: ['#fbf8f5', '#c87a4f', '#0f4a44']},
  {id: 'tide', name: 'Tide', dark: true, sw: ['#f3f6f8', '#c87a4f', '#1f5d57']},
  {id: 'tan', name: 'Tan', dark: false, sw: ['#f7f1e5', '#8a4a12', '#8a4a12']},
  {id: 'light', name: 'Light', dark: false, sw: ['#ffffff', '#3366cc', '#3366cc']},
];
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* no storage */ } },
};
const prefs = {theme: 'dune', mode: 'light', cost: false, line: true};  // mode: light | dark | system; line: the scroll line
const sysDark = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : {matches: false};
function applyLook() {
  const t = THEMES.find(x => x.id === prefs.theme) || THEMES[0];
  const dark = t.dark && (prefs.mode === 'dark' || (prefs.mode === 'system' && sysDark.matches));
  const r = document.documentElement;
  r.dataset.theme = t.id;
  r.dataset.mode = dark ? 'dark' : 'light';
  drawSettings();
  postLook(dark);
}
// the window's title bar takes the bar's colours (Windows 11); the bar's own computed colours, as #rrggbb
const hex2 = c => { const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || ''); return m ? '#' + [m[1], m[2], m[3]].map(x => Number(x).toString(16).padStart(2, '0')).join('') : null; };
let lastLook = '';
function postLook(dark) {
  const bar = document.querySelector('header.bar');
  if (!bar) return;
  const cs = getComputedStyle(bar);
  const css = v => { const p = document.createElement('i'); p.style.color = `var(${v})`; document.body.appendChild(p); const c = hex2(getComputedStyle(p).color); p.remove(); return c; };
  const look = {caption: hex2(cs.backgroundColor), text: hex2(cs.color), dark: !!dark, bg: css('--bg'), ink: css('--ink'), primary: css('--link'), accent: css('--accent')};   // the colours also repaint the window icon
  const key = JSON.stringify(look);
  if (!look.caption || !look.text || key === lastLook) return;
  lastLook = key;
  api('/api/look', look).catch(() => { lastLook = ''; });
}
function setCost(on) {
  prefs.cost = on;
  store.set('apollo-cost', on ? '1' : '0');
  document.body.classList.toggle('nocost', !on);
  drawSettings();
  $('#stats').dataset.k = '';
  state.viewKey = '';
  state.runsKey = '';
  head();
  pollRuns();
  pollView();
}
function setMode(m) { prefs.mode = m; store.set('apollo-mode', m); applyLook(); }
function setTheme(t) { prefs.theme = t; store.set('apollo-theme', t); applyLook(); }
$('#mode').onclick = () => setMode(document.documentElement.dataset.mode === 'dark' ? 'light' : 'dark');
if (sysDark.addEventListener) sysDark.addEventListener('change', () => { if (prefs.mode === 'system') applyLook(); });

// The cog opens the Settings page (settings.js, CZ330): theme, mode, scroll line and cost are its Appearance section.
function drawSettings() { if (state.page === 'settings' && window.drawSettingsPage) drawSettingsPage(); }
function setLine(on) { prefs.line = on; store.set('apollo-line', on ? '1' : '0'); document.body.classList.toggle('noline', !on); drawSettings(); }
$('#cog').onclick = () => showPage('settings');

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (dismissToasts()) return;
  if (state.agentOpen) { state.agentOpen = null; agentRedraw(); return; }
  if (document.querySelector('dialog[open]')) return;       // the dialog closes itself
  if (!$('#path').hidden) { $('#path').hidden = true; return; }
  if (state.page === 'sources' && state.src.tab === 'questions' && state.q.sel) { state.q.sel = null; drawSources(); return; }
  if (state.page === 'sources' && state.src.tab === 'found' && state.src.fnd.sel) { state.src.fnd.sel = null; drawSources(); return; }
  if (state.openRow != null && state.tab === 'run') { state.openRow = null; drawLog(); return; }     // Esc folds the open row
  if (state.q.sel && state.tab === 'questions') { state.q.sel = null; state.viewKey = ''; render(); return; }   // Esc deselects the question
  if (state.fnd.exp && state.tab === 'found') { state.fnd.exp = false; renderFound(state.foundView); return; }
  if (state.fnd.sel && state.tab === 'found') { state.fnd.sel = null; renderFound(state.foundView); return; }
  if (clearPick()) return;                                   // Esc clears the page filter a heatmap cell made
  if (state.rawf.open && (state.tab === 'raw' || state.tab === 'calls')) { state.rawf.open = null; drawRaw(); return; }
  if (state.cfg && state.cfg.step && !$('#cfgstage').hidden) { state.cfg.step = null; drawStage(); return; }   // Esc deselects the step first
  if (state.cfg) { closeCfg(); return; }
  if (state.page === 'sources' && state.src.wiki.open) { toggleWiki(false); return; }
  if (window.etEsc && etEsc()) return;                       // entity trace: Esc deselects the entity, then the collection
  if (state.page === 'settings' && window.setEsc && setEsc()) return;     // Settings: Esc closes the key field, then deselects the row
  if (state.page === 'sources' && state.src.sel) { selectSource(state.src.sel); return; }
  if (state.page) { state.page = null; layout(); return; }
  select(null);
});

function boot() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const th = hash.get('theme') || store.get('apollo-theme');
  prefs.theme = THEMES.some(x => x.id === th) ? th : 'dune';
  const mode = hash.get('mode') || store.get('apollo-mode');
  prefs.mode = ['light', 'dark', 'system'].includes(mode) ? mode : 'light';
  prefs.cost = store.get('apollo-cost') === '1';
  document.body.classList.toggle('nocost', !prefs.cost);
  prefs.line = store.get('apollo-line') !== '0';
  document.body.classList.toggle('noline', !prefs.line);
  applyLook();
  drawBar();
  setTab(hash.get('tab') || 'run');
  loadConfigs().then(async () => {
    if (!hash.get('configure')) return;
    await openCfg(hash.get('configure'), hash.get('source'));
    if (hash.get('stage') && state.cfg) { state.cfg.sel = hash.get('stage'); drawStage(); }
    if (hash.get('flow') && state.cfg) { state.cfg.flow = state.cfg.sel = hash.get('flow'); drawCfg(); }
    if (hash.get('step') && state.cfg) { state.cfg.step = hash.get('step'); drawStage(); loadStagePrompts(); }
    if (hash.get('rail') && state.cfg) { state.rail.cfg.tab = hash.get('rail'); drawRail('cfg'); }
    if (hash.has('analyse')) runAnalyse();
  });
  if (hash.get('view')) showPage(hash.get('view'));
  pollRuns().then(() => {
    if (hash.get('run')) select(hash.get('run'));
    if (hash.get('rail') && hash.get('run')) { state.rail.run.tab = hash.get('rail'); }
  });
  // poll only as fast as there is something to watch: 1 s while a run is live, 10 s when idle, 30 s when hidden
  const busy = () => (state.runs || []).some(r => r.status === 'running' || r.status === 'starting' || r.status === 'stopping');
  const every = (fn, fast) => {
    const tick = async () => { try { await fn(); } catch (_) {} setTimeout(tick, document.hidden ? 30000 : busy() ? fast : 10000); };
    setTimeout(tick, fast);
  };
  every(pollRuns, 1000);
  every(pollView, 1500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollRuns(); pollView(); } });
}
document.addEventListener('DOMContentLoaded', boot);

$('#home').onclick = () => { $('#path').hidden = true; closeCfg(); state.page = null; layout(); };

// ---- limits: the Calls label in a run's head switches its run-level caps (live: the driver; finished: the display) ----
$('#stats').addEventListener('click', async e => {
  const b = e.target.closest('button.limsw'), s = state.byId[state.sel];
  if (!b || !s) return;
  const on = !limitsOn(s);
  const live = s.alive && s.kind === 'pageflow';
  limOv[s.id] = on;
  head();
  if (!live) return;
  try { await api(`/api/limits/${encodeURIComponent(s.id)}`, {limits: on}); }
  catch (_) { delete limOv[s.id]; head(); }
});
