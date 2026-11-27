// Settings (CZ330): the cog's page. The sections on the left, the chosen one on the right, like Sources and Collections.
// Every change applies at once (no Save, no confirm) and is kept once, where its owner keeps it (settings.py says where).
// A key's value is typed into a field that is gone as soon as it is sent: the page only ever learns whether a key is set.
'use strict';
state.set = null;                                                   // /api/settings: models, flows, limits, folders, network
state.setUi = {sec: 'models', keyEdit: null, tests: {}};
const SET_SECTIONS = [['models', 'Models'], ['defaults', 'Defaults'], ['limits', 'Limits'], ['network', 'Network'],
  ['folders', 'Folders'], ['appearance', 'Appearance']];
const PROVIDER_WORD = {builtin: 'Built-in', anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google',
  'openai-compatible': 'Compatible', local: 'Local'};
Object.assign(SV, {
  key: '<path d="M16.555 3.843l3.602 3.602a2.877 2.877 0 0 1 0 4.069l-2.643 2.643a2.877 2.877 0 0 1 -4.069 0l-.301 -.301l-6.558 6.558a2 2 0 0 1 -1.239 .578l-.175 .008h-1.172a1 1 0 0 1 -.993 -.883l-.007 -.117v-1.172a2 2 0 0 1 .467 -1.284l.119 -.13l.414 -.414h2v-2h2v-2l2.144 -2.144l-.301 -.301a2.877 2.877 0 0 1 0 -4.069l2.643 -2.643a2.877 2.877 0 0 1 4.069 0z"/><path d="M15 9h.01"/>',
  trash: '<path d="M4 7l16 0"/><path d="M10 11l0 6"/><path d="M14 11l0 6"/><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12"/><path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3"/>',
  unkey: '<path d="M10 10l-7.5 7.5v3.5h3.5l1 -1v-2h2v-2h2l1.5 -1.5"/><path d="M3 3l18 18"/><path d="M14.5 6.5a3.5 3.5 0 0 1 3 3"/><path d="M11.6 6.5a6 6 0 0 1 6.9 6.9"/>',
  plus: SV.plus || '<path d="M12 5l0 14"/><path d="M5 12l14 0"/>'
});
{
  const want = new URLSearchParams(location.hash.slice(1)).get('sec');
  if (SET_SECTIONS.some(s => s[0] === want)) state.setUi.sec = want;
}

async function loadSettings() {
  try {
    state.set = await api('/api/settings');
    setError('set', null);
  } catch (e) {
    setError('set', `Settings: ${e.message}`);
  }
  drawSettings();
}
function openSettings() { drawSettingsPage(); loadSettings(); }
async function setSend(body) {
  try {
    const got = await api('/api/settings', body);
    if (got && got.models) state.set = got;
    return got;
  } catch (e) {
    notify('error', e.message);
    loadSettings();                                                // the page shows what is kept, not what was typed
    return null;
  } finally {
    drawSettings();
  }
}

// ---- the sections ---------------------------------------------------------------------------------------------------
const ibtn = (icon, title, data, extra = '') => `<button type=button class="ib sib" title="${esc(title)}" aria-label="${esc(title)}" ${data}${extra}>${ic(icon)}</button>`;
const sw = (on, label, data, dis = false) => `<button type=button class="sw2${on ? ' on' : ''}" role=switch aria-checked=${!!on} aria-label="${esc(label)}" ${data}${dis ? ' disabled' : ''}></button>`;

function modelRow(m, d) {
  const id = esc(m.id), built = m.provider === 'builtin', edit = state.setUi.keyEdit === m.id, t = state.setUi.tests[m.id];
  const inp = (f, w = '') => `<input class="inp${w}" data-k="${id}:${f}" data-f="${f}" value="${esc(m[f])}" aria-label="${esc(m.label)} ${f}"${built && f !== 'label' ? ' disabled' : ''}>`;
  const provs = (d.providers || []).map(p => `<option value="${esc(p)}"${p === m.provider ? ' selected' : ''}>${esc(PROVIDER_WORD[p] || p)}</option>`).join('');
  const mark = !m.key ? '' : `<span class="kset${m.set ? ' on' : ''}">${m.set ? 'Set' : 'Not set'}</span>`;
  const store = d.can_store ? '' : ` disabled title="Set ${esc(m.key)} in the environment"`;
  const keyBtns = !m.key || built ? '' : edit ? '' :
    ibtn('key', m.set ? 'Replace key' : 'Set key', `data-keyset="${id}"`, store) + (m.set && d.can_store ? ibtn('unkey', 'Remove key', `data-keyrm="${id}"`) : '');
  const keyCell = edit
    ? `<input class="inp kval" type=password autocomplete=new-password spellcheck=false data-k="${id}:value" data-keyval="${id}" aria-label="${esc(m.key)}">`
    : `<span class=kcell>${inp('key', ' kname')}${mark}</span>`;
  const res = !t ? '' : t.busy ? '<span class=spin aria-hidden=true></span>' : t.ok
    ? `<span class=tok>${ic('ok')}${esc(secs(t.ms))}</span>` : `<span class=tbad title="${esc(t.error)}">${esc(t.error)}</span>`;
  return `<tr data-mid="${id}"><td>${inp('label')}</td><td><select class=inp data-k="${id}:provider" data-f=provider aria-label="${esc(m.label)} provider">${provs}</select></td>` +
    `<td>${inp('model')}</td><td>${inp('endpoint', ' wide')}</td><td>${keyCell}</td>` +
    `<td class=sacts>${keyBtns}${built ? '' : ibtn('calls', 'Test', `data-test="${id}"`, t && t.busy ? ' disabled' : '')}${ibtn('trash', 'Remove model', `data-mrm="${id}"`)}</td><td class=sres>${res}</td></tr>`;
}
function secModels(d) {
  const rows = (d.models || []).map(m => modelRow(m, d)).join('');
  return `<table class="ctable stable"><thead><tr><th>Label</th><th>Provider</th><th>Model</th><th>Endpoint</th><th>Key</th><th></th><th></th></tr></thead>` +
    `<tbody>${rows}</tbody></table><div class=sadd>${ibtn('plus', 'Add model', 'data-madd')}</div>`;
}
function secDefaults(d) {
  const grades = GRADES;
  const cell = (f, g) => {
    const lim = f.grades[g];
    if (!lim) return '<td></td>';
    return `<td><input class="inp n" inputmode=numeric data-k="ag:${esc(f.key)}.${g}" data-agent="${esc(f.key)}.${g}" value="${lim.default}" aria-label="${esc(f.label)} ${GRADE_WORD[g]}" title="${lim.low}–${lim.max}"></td>`;
  };
  const rows = (d.flows || []).map(f => `<tr><td>${esc(f.label)}</td>${grades.map(g => cell(f, g)).join('')}</tr>`).join('');
  const tool = (typeof AGENTS !== 'undefined' ? AGENTS : []).map(([k, w, id]) =>
    `<button type=button role=radio aria-checked=${k === prefs.agent} data-tool="${k}">${modelMark(id)}${w}</button>`).join('');
  return `<h3>Agents</h3><table class="ctable stable sag"><thead><tr><th>Flow</th>${grades.map(g => `<th>${modelMark(g)}${GRADE_WORD[g]}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>` +
    `<h3>Tool agent</h3><div class=seg role=radiogroup aria-label="Tool agent">${tool}</div>`;
}
function secLimits(d) {
  const l = d.limits || {};
  const f = (key, word, v, unit, ph = '') => `<label class=sf><span>${word}</span><span class=pp2><i class=u>${unit === '$' ? '$' : ''}</i>` +
    `<input class="inp n" inputmode=decimal data-k="lim:${key}" data-set="limits.${key}" value="${v == null ? '' : esc(v)}"${ph ? ` placeholder="${ph}"` : ''} aria-label="${word}">${unit === 's' ? 's' : ''}</span></label>`;
  return `<div class=sgrid>${f('run_usd', 'Run cost', l.run_usd, '$', 'By pages')}${f('chat_usd', 'Chat cost', l.chat_usd, '$')}${f('chat_secs', 'Chat time', l.chat_secs, 's')}${f('page_reach', 'Page reach', d.page_reach, '', '1')}</div>`;
}
function secNetwork(d) {
  const n = d.network || {};
  return `<div class=sgrid><div class=sf><span>Basemap</span>${sw(n.basemap, 'Basemap', 'data-net=basemap', n.basemap == null)}</div></div>`;
}
function secFolders(d) {
  const f = d.folders || {};
  const row = (k, word, env) => {
    const x = f[k] || {};
    return `<label class="sf wide"><span>${word}</span><input class="inp path" data-k="fold:${k}" data-set="folders.${k}" value="${esc(x.path || '')}" aria-label="${word}"` +
      `${x.env ? ` readonly title="Set by ${env}"` : ''}></label>`;
  };
  return `<div class=sgrid>${row('archives', 'Archives', 'APOLLO_ARCHIVES')}${row('sources', 'Sources', 'APOLLO_SOURCES')}</div>`;
}
function secAppearance() {
  const t = THEMES.find(x => x.id === prefs.theme) || THEMES[0];
  const sws = THEMES.map(x => `<button type=button class=swatch data-theme="${x.id}" aria-pressed=${x.id === prefs.theme}><i data-css="--sw-bg:${x.sw[0]};--sw-accent:${x.sw[1]};--sw-link:${x.sw[2]}"></i>${esc(x.name)}</button>`).join('');
  const modes = [['light', 'Light'], ['dark', 'Dark'], ['system', 'System']].map(([id, w]) =>
    `<button type=button role=radio data-mode="${id}" aria-checked=${id === prefs.mode}${id === 'dark' && !t.dark ? ' disabled' : ''}>${w}</button>`).join('');
  return `<h3>Theme</h3><div class="swatches sws">${sws}</div><h3>Mode</h3><div class=seg role=radiogroup aria-label=Mode>${modes}</div>` +
    `<div class=sgrid><div class=sf><span>Scroll line</span>${sw(prefs.line, 'Scroll line', 'data-pref=line')}</div>` +
    `<div class=sf><span>Show cost</span>${sw(prefs.cost, 'Show cost', 'data-pref=cost')}</div></div>`;
}
const SEC_DRAW = {models: secModels, defaults: secDefaults, limits: secLimits, network: secNetwork, folders: secFolders, appearance: secAppearance};

function drawSettingsPage() {
  const box = $('#setpanel');
  if (!box) return;
  const ui = state.setUi, d = state.set;
  const list = SET_SECTIONS.map(([k, w]) => `<li data-sec="${k}" tabindex=0 class="${k === ui.sec ? 'sel' : ''}" aria-current=${k === ui.sec}><b>${w}</b></li>`).join('');
  const word = (SET_SECTIONS.find(s => s[0] === ui.sec) || SET_SECTIONS[0])[1];
  const body = ui.sec === 'appearance' ? secAppearance() : d ? SEC_DRAW[ui.sec](d) : '';
  const html = `<section class="panel xrail"><h2>Settings</h2><ol class="rl setl">${list}</ol></section>` +
    `<section class="panel xmain"><div class=cfgh><h2>${word}</h2></div><div class="cbody sbody" data-sec="${ui.sec}">${body}</div></section>`;
  if (box.dataset.k === html) return;
  const a = document.activeElement, focusKey = a && box.contains(a) ? a.dataset.k || (a.dataset.sec && 'sec:' + a.dataset.sec) : null;
  const caret = a && a.selectionStart != null ? [a.selectionStart, a.selectionEnd] : null;
  const typed = focusKey && a.matches('input') ? a.value : null;     // what is being typed survives a redraw
  const y = (box.querySelector('.xmain') || {}).scrollTop || 0;
  box.dataset.k = html;
  box.innerHTML = html;
  applyCss(box);
  const main = box.querySelector('.xmain');
  if (main) main.scrollTop = y;
  const back = focusKey && (focusKey.startsWith('sec:') ? box.querySelector(`[data-sec="${focusKey.slice(4)}"]`) : box.querySelector(`[data-k="${CSS.escape(focusKey)}"]`));
  if (back) { if (typed != null && back.matches('input')) back.value = typed; back.focus(); if (caret && back.setSelectionRange) try { back.setSelectionRange(...caret); } catch (_) { /* not a text field */ } }
  const kv = box.querySelector('[data-keyval]');
  if (kv && !focusKey) kv.focus();
}

// ---- changes ----------------------------------------------------------------------------------------------------------
const modelRows = () => (state.set.models || []).map(({set, ...r}) => r);
function saveModels(rows) {
  state.set.models = rows.map(r => ({...r, set: (state.set.models.find(m => m.key === r.key) || {}).set || false}));
  drawSettings();
  return setSend({op: 'models', rows});
}
function newModelId(rows) {
  let n = 1;
  while (rows.some(r => r.id === `model-${n}`)) n++;
  return `model-${n}`;
}
async function sendKey(input) {
  const id = input.dataset.keyval, m = (state.set.models || []).find(x => x.id === id), value = input.value;
  input.value = '';
  state.setUi.keyEdit = null;
  if (!m || !value.trim()) { drawSettings(); return; }
  await setSend({op: 'key', name: m.key, value});
}
async function testModel(id) {
  state.setUi.tests[id] = {busy: true};
  drawSettings();
  try {
    const got = await api('/api/settings', {op: 'test', id});
    state.setUi.tests[id] = got.test || {ok: false, error: 'No answer'};
  } catch (e) {
    state.setUi.tests[id] = {ok: false, error: e.message};
  }
  drawSettings();
}
function setValue(el) {
  const v = el.value.trim();
  const key = el.dataset.set;
  const num = key.startsWith('limits.');
  if (num && v !== '' && !Number.isFinite(Number(v))) { notify('error', `${el.getAttribute('aria-label')}: a number`); return; }
  setSend({op: 'set', key, value: v === '' ? null : num ? Number(v) : v});
}

$('#setpanel').addEventListener('click', e => {
  const t = e.target.closest('button,[data-sec]');
  if (!t || t.disabled) return;
  const ui = state.setUi;
  if (t.dataset.sec) { ui.sec = t.dataset.sec; ui.keyEdit = null; drawSettingsPage(); if (!state.set) loadSettings(); return; }
  if (t.dataset.keyset) { ui.keyEdit = t.dataset.keyset; drawSettingsPage(); return; }
  if (t.dataset.keyrm != null) { const m = state.set.models.find(x => x.id === t.dataset.keyrm); if (m) setSend({op: 'key', name: m.key, remove: true}); return; }
  if (t.dataset.test) { testModel(t.dataset.test); return; }
  if (t.dataset.mrm) { saveModels(modelRows().filter(r => r.id !== t.dataset.mrm)); return; }
  if (t.dataset.madd != null) {
    const rows = modelRows(), id = newModelId(rows);
    saveModels([...rows, {id, label: 'New model', provider: 'anthropic', model: '', key: 'ANTHROPIC_API_KEY', endpoint: ''}]);
    return;
  }
  if (t.dataset.tool) { prefs.agent = t.dataset.tool; store.set('apollo-agent', prefs.agent); if (window.agentRedraw) agentRedraw(); drawSettings(); return; }
  if (t.dataset.net) { setSend({op: 'network', name: t.dataset.net, on: t.getAttribute('aria-checked') !== 'true'}); return; }
  if (t.dataset.theme) { setTheme(t.dataset.theme); return; }
  if (t.dataset.mode) { setMode(t.dataset.mode); return; }
  if (t.dataset.pref === 'line') { setLine(!prefs.line); return; }
  if (t.dataset.pref === 'cost') { setCost(!prefs.cost); return; }
});
$('#setpanel').addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.f) {
    const id = el.closest('tr').dataset.mid;
    saveModels(modelRows().map(r => r.id === id ? {...r, [el.dataset.f]: el.value.trim()} : r));
    return;
  }
  if (el.dataset.agent) {
    const v = el.value.trim();
    setSend({op: 'set', key: `agents.${el.dataset.agent}`, value: v === '' ? null : parseInt(v, 10)});
    return;
  }
  if (el.dataset.set && !el.readOnly) setValue(el);
});
$('#setpanel').addEventListener('keydown', e => {
  const el = e.target;
  if (e.key === 'Enter' && el.dataset.keyval) { e.preventDefault(); sendKey(el); return; }
  if (e.key === 'Enter' && el.dataset.sec) { el.click(); return; }
  if (e.key === 'Enter' && el.matches('input.inp')) el.blur();          // Enter applies, as leaving the field does
});
// Esc: the key field first, then a test result; false lets the page close
function setEsc() {
  const ui = state.setUi;
  if (ui.keyEdit) { ui.keyEdit = null; drawSettingsPage(); return true; }
  if (Object.keys(ui.tests).length) { ui.tests = {}; drawSettingsPage(); return true; }
  return false;
}
loadSettings();                                                      // New run's cost cap reads the Run cost at once
