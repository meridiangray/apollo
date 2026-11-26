// The entity Trace of the right rails (Sources and Collections): a list of entities, and the Selected card of the one picked, which
// ends with that entity's compact Trace. One line per step: the category's icon in its accent, a one-word past-tense verb (red when it
// takes something away), the object cut with an ellipsis, a one-word descriptor and the time. Steps of one category within 3 s are one
// line with a count (the manager folds them). A filter row of the categories the entity has; a click on a line shows its quotes.
// The steps are read by the manager (entitytrace.py); a person is "you", an agent its role, never a name.
'use strict';
state.et = {sel: null, trace: {}, filt: '', open: {}, lists: {}, src: {}};
const ET_CATS = [
  ['org', 'Organization', 'et-org'], ['kin', 'Family', 'et-kin'], ['post', 'Position', 'et-post'], ['geo', 'Geographic', 'et-geo'],
  ['event', 'Events', 'et-event'], ['hold', 'Holdings', 'et-hold'], ['id', 'Identity', 'et-id'], ['src', 'Sources', 'et-src'], ['rev', 'Review', 'et-rev']];
const ET_AWAY = new Set(['Removed', 'Unplaced', 'Dropped', 'Deleted', 'Purged', 'Reset', 'Separated', 'Dismissed']);
Object.assign(SV, {
  'et-org': '<path d="M3 21h18"/><path d="M9 8h1M9 12h1M9 16h1M14 8h1M14 12h1M14 16h1"/><path d="M5 21v-16a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16"/>',
  'et-kin': '<path d="M10 3h4v4h-4z"/><path d="M3 17h4v4h-4z"/><path d="M17 17h4v4h-4z"/><path d="M7 17l5-4l5 4"/><path d="M12 7v6"/>',
  'et-post': '<path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-14a2 2 0 0 1-2-2v-14a2 2 0 0 1 2-2"/><path d="M9 14a3 3 0 1 0 6 0a3 3 0 1 0-6 0"/><path d="M10 6h4"/>',
  'et-geo': '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx=12 cy=10 r=3 />',
  'et-event': '<path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-12"/><path d="M16 3v4M8 3v4M4 11h16"/><path d="M8 15h2v2h-2z"/>',
  'et-hold': '<path d="M13.744 17.736a6 6 0 1 1-7.48-7.48"/><path d="M15 6h1v4"/><path d="m6.134 14.768.866-.5 2 3.464"/><circle cx=16 cy=8 r=6 />',
  'et-id': '<path d="M18.9 7a8 8 0 0 1 1.1 5v1a6 6 0 0 0 .8 3"/><path d="M8 11a4 4 0 0 1 8 0v1a10 10 0 0 0 2 6"/><path d="M12 11v2a14 14 0 0 0 2.5 8"/><path d="M8 15a18 18 0 0 0 1.8 6"/><path d="M4.9 19a22 22 0 0 1-.9-7v-1a8 8 0 0 1 12-6.95"/>',
  'et-src': '<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21h-10a2 2 0 0 1-2-2v-14a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2"/><path d="M12 17v-6"/><path d="M9.5 14.5l2.5 2.5l2.5-2.5"/>',
  'et-rev': '<circle cx=12 cy=12 r=9 /><path d="M12 17v.01"/><path d="M12 13.5a1.5 1.5 0 0 1 1-1.5a2.6 2.6 0 1 0-3-4"/>'
});
const etKey = e => `${e.archive}|${e.id || ''}|${e.name}`;
const etHm = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false}); };
const etDay = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('en-GB', {weekday: 'short', day: 'numeric', month: 'short'}); };

function etLine(l) {
  const c = ET_CATS.find(x => x[0] === l.cat);
  const quoted = l.steps.some(s => s.quote);
  const x = quoted || l.n > 1;
  const who = l.who && l.who !== 'you' ? `<span class=tr-who title="${esc(l.who)}">${esc(l.who)}</span>` : '';
  const open = !!state.et.open[l.at + l.verb + l.obj];
  const tip = `${l.tip || l.obj} · ${l.who === 'you' ? 'you' : l.who} · ${new Date(l.at).toLocaleString('en-GB')}`;
  const head = `<div class="tr-row${ET_AWAY.has(l.verb) ? ' r' : ''}${x ? ' x' : ''}" style="--c:var(--tc-${l.cat})" title="${esc(tip)}"${x ? ` data-tk="${esc(l.at + l.verb + l.obj)}" role=button tabindex=0 aria-expanded=${open}` : ''}>` +
    `<span class=tr-ic>${ic(c ? c[2] : 'calls')}</span><b class=tr-v>${esc(l.verb)}</b>${l.n > 1 ? `<span class=tr-n>×${l.n}</span>` : ''}` +
    `<span class=tr-o><span>${esc(l.obj)}</span></span>${l.desc ? `<span class=tr-d>${esc(l.desc)}</span>` : ''}${who}<time>${esc(etHm(l.at))}</time></div>`;
  const body = open ? `<ul class=tr-q>${l.steps.filter(s => s.quote || l.n > 1).map(s =>
    `<li>${l.n > 1 ? `<b>${esc(s.verb)}</b> <span>${esc(s.obj)}</span>` : ''}${s.quote ? `<q>${esc(s.quote)}</q>` : ''}</li>`).join('')}</ul>` : '';
  return `<li>${head}${body}</li>`;
}
function etTrace(t) {
  if (!t) return `<div class=trace><h4>Trace</h4><span class=spin aria-label=Loading role=status></span></div>`;
  if (!t.lines.length) return '';
  const filt = state.et.filt;
  const chips = ET_CATS.filter(x => t.cats.includes(x[0]));
  const rows = t.lines.filter(l => !filt || l.cat === filt);
  let out = '', day = '';
  for (const l of rows) {
    const d = etDay(l.at);
    if (d !== day) { out += `<li class=tr-day>${esc(d)}</li>`; day = d; }
    out += etLine(l);
  }
  const bar = chips.length > 1 ? `<div class=tr-filter role=group aria-label=Show><button type=button data-tf="" aria-pressed=${!filt}>All</button>${chips.map(([k, w, i]) =>
    `<button type=button data-tf="${k}" style="--c:var(--tc-${k})" aria-pressed=${filt === k} title="${w}" aria-label="${w}">${ic(i)}</button>`).join('')}</div>` : '';
  return `<div class=trace><h4>Trace</h4>${bar}<ol class=etr>${out}</ol></div>`;
}
// the Selected card: the entity's icon and name, then only the facts it has; it ends with the Trace
function etCard() {
  const e = state.et.sel;
  if (!e) return '';
  const t = state.et.trace[etKey(e)];
  const info = (t && t.entity) || {};
  const rows = [];
  const add = (k, v) => { if (v) rows.push(`<tr><th scope=row>${k}</th><td>${v}</td></tr>`); };
  add('Type', esc(schemaWord(e.schema || info.schema)));
  add('Record', e.id ? `<code>${esc(e.id)}</code>` : '');
  add('Collection', esc(info.collection_title || e.collTitle || e.collection || ''));
  add('Source', esc(e.source || ''));                        // set only in a Complex Collection, which spans several
  return `<h3 class=rh>Selected</h3><div class=selcard><table class=infobox><caption><span class=selname>${entityIcon(e.schema || info.schema)}<span>${esc(e.name)}</span></span></caption>${rows.join('')}</table>${etTrace(t)}</div>`;
}
function etList(surface, ents, archive, coll) {
  state.et.lists[surface] = {ents, archive, coll};
  if (!ents.length) return '';
  const sel = state.et.sel ? etKey(state.et.sel) : '';
  return `<ul class=elist>${ents.map((e, i) => {
    const on = sel && etKey({...e, archive}) === sel;
    return `<li><button type=button class="erow${on ? ' on' : ''}" data-es="${surface}" data-ei="${i}" aria-pressed=${!!on}>${entityIcon(e.schema)}<span class=en>${esc(e.name)}</span><span class=et>${esc(schemaWord(e.schema))}</span></button></li>`;
  }).join('')}</ul>`;
}
function etRedraw() {
  if (state.page === 'collections' && window.drawCollections) drawCollections();
  else if (state.page === 'sources' && window.drawSources) drawSources();
}
async function etSelect(ent) {
  const cur = state.et.sel;
  if (cur && etKey(cur) === etKey(ent)) { state.et.sel = null; etRedraw(); return; }
  state.et.sel = ent;
  state.et.filt = '';
  etRedraw();
  const key = etKey(ent);
  if (state.et.trace[key]) return;
  const q = ent.id ? `record=${encodeURIComponent(ent.id)}` : `name=${encodeURIComponent(ent.name)}&schema=${encodeURIComponent(ent.schema || '')}`;
  try {
    const got = await api(`/api/entitytrace?archive=${encodeURIComponent(ent.archive)}&${q}`);
    state.et.trace[key] = got;
  } catch (e) {
    notify('error', `Trace: ${e.message}`);
    if (state.et.sel && etKey(state.et.sel) === key) state.et.sel = null;
  }
  etRedraw();
}
// a source's entities: the found rows of its runs and the archive's records that cite it
async function etSourceLoad(slug, title) {
  const arch = draftPath();
  if (!arch || !slug) return;
  const s = state.et.src[slug] = state.et.src[slug] || {ents: [], at: 0};
  if (Date.now() - s.at < 4000) return;
  s.at = Date.now();
  try {
    const got = await api(`/api/entitytrace?archive=${encodeURIComponent(arch)}&source=${encodeURIComponent(slug)}&title=${encodeURIComponent(title || '')}`);
    s.ents = got.entities || [];
    s.lanes = got.lanes || {};                                 // {run id: {page: n}}: what each run after Read touched per page (Sources' lanes)
  } catch (e) {
    s.at = 0;
    notify('error', `Trace: ${e.message}`);
  }
  if (state.page === 'sources') etRedraw();
}
const etSourceRail = r => {
  const s = state.et.src[r.slug];
  return etCard() + (s ? etList('src', s.ents, draftPath(), null) : '');
};
// Esc: the entity first, then the collection
function etEsc() {
  if (state.page !== 'sources' && state.page !== 'collections') return false;
  if (state.et.sel) { state.et.sel = null; etRedraw(); return true; }
  if (window.colEsc && colEsc()) return true;                // the picked collections
  if (state.page === 'collections' && state.col.sel) { state.col.sel = null; etRedraw(); return true; }
  return false;
}
document.addEventListener('click', e => {
  const row = e.target.closest('.erow[data-ei]');
  if (row) {
    const l = state.et.lists[row.dataset.es], ent = l && l.ents[+row.dataset.ei];
    const cx = l && l.coll && l.coll.complex;                  // a Complex Collection's thing: its own collection and its source
    const own = cx && ent ? l.coll.members.find(m => m.name === ent.collection) : null;
    if (ent) etSelect({id: ent.id || null, name: ent.name, schema: ent.schema, collection: ent.collection || (l.coll && l.coll.name) || null,
      collTitle: own ? own.title : l.coll && !cx ? l.coll.title : null, source: cx ? ent.source || (own ? colSource(own) : null) : null, archive: l.archive});
    return;
  }
  const f = e.target.closest('[data-tf]');
  if (f) { state.et.filt = f.dataset.tf; etRedraw(); return; }
  const line = e.target.closest('.tr-row.x[data-tk]');
  if (line) { const k = line.dataset.tk; state.et.open[k] = !state.et.open[k]; etRedraw(); }
});
document.addEventListener('keydown', e => {
  const line = e.target.closest && e.target.closest('.tr-row.x[data-tk]');
  if (line && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); const k = line.dataset.tk; state.et.open[k] = !state.et.open[k]; etRedraw(); }
});
