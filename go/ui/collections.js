// The Collections view, laid out like Runs (owner): a left rail with the archive and its collections grouped under their containing
// source (each source styled like a Runs entry; a Complex Collection, holding collections of several sources, will sit there as a
// source of its own); the main area is the selected collection: its kinds, its things, and on the right its card (records, kinds,
// sources, the run that added it, when) and the selected thing's card with its Trace (entitytrace.js). Draft first, then the runs'
// archives. Apollo builds an archive itself when it is stale: a small spinner by the select says so.
'use strict';
state.col = {archive: '', data: null, sel: null, open: {}, busy: false, type: ''};
const when = longWhen;                                 // app.js: "November 25, 2026 6:36 PM"

function colArchives() {
  const seen = new Set();
  const out = [];
  const d = draftPath();
  if (d) { seen.add(d); out.push([d, 'Draft']); }
  for (const r of state.runs) if (r.archive && !seen.has(r.archive) && r.kind === 'pageflow') { seen.add(r.archive); out.push([r.archive, [runName(r), r.source_title].filter(Boolean).join(' · ')]); }
  return out;
}
function openCollections() {
  if (!state.col.archive) state.col.archive = draftPath();
  drawCollections();
  loadCollections();
}
async function loadCollections() {
  const c = state.col, arch = c.archive;
  if (!arch || c.loading) return;
  c.loading = true;
  try {
    const got = await api(`/api/collections?archive=${encodeURIComponent(arch)}`);
    if (c.archive === arch) {
      c.data = got;
      setError('col', null);
      if (!c.sel && !c.picked && got.collections.length) { c.picked = true; selectCollection(got.collections[0].name, false); }
    }
  } catch (e) {
    if (c.archive === arch) { c.data = null; setError('col', `Collections: ${e.message}`); }
  }
  c.loading = false;
  if (state.page === 'collections') drawCollections();
}
function typeCells(types) {
  return types.map(t => `<span class=ty title="${esc(schemaWord(t.schema))}">${entityIcon(t.schema)}<b>${t.n}</b></span>`).join('');
}
function colInfoRows(r) {
  const rows = [];
  const add = (k, v) => { if (v) rows.push(`<tr><th scope=row>${k}</th><td>${v}</td></tr>`); };
  add('Records', r.count);
  add('Types', `<div class=tys>${typeCells(r.types)}</div>`);
  add('Sources', r.sources.map(s => `<div>${esc(s)}</div>`).join(''));
  add('Run', r.imported ? `<span class=dim>imported from ${esc(r.imported)}</span>` : r.run ? `<button type=button class=pgo data-run="${esc(r.run.id)}" title="${esc([r.run.flow, r.run.source].filter(Boolean).join(' · ') || r.run.id)}">${esc([r.run.flow, r.run.source].filter(Boolean).join(' · ') || r.run.id)}</button>` : '');
  add('Added', esc(when(r.added)));
  return rows.join('');
}
// the rail holds the collection's own card, and the Selected entity's card with its Trace while one is picked (owner: the rail
// is for the collection, not a list of what it holds)
function colRail(r) {
  return `<table class=infobox><caption>${esc(r.title)}</caption>${colInfoRows(r)}</table>${etCard()}`;
}
// under the table: the selected collection's entities, with a filter by type (one type, or all)
function colEntities(r) {
  const c = state.col, o = c.open[r.name];
  if (o && !o.records) return '<div class=colents><span class=spin aria-hidden=true></span></div>';
  const all = o && o.records ? o.records : [];
  const types = r.types.filter(t => t.n);
  if (c.type && !types.some(t => t.schema === c.type)) c.type = '';
  const ents = c.type ? all.filter(e => e.schema === c.type) : all;
  const chips = types.length > 1 ? `<div class=colfilter role=group aria-label=Types><button type=button class=fc data-ct="" aria-pressed=${!c.type}>All <b>${r.count}</b></button>` +
    types.map(t => `<button type=button class=fc data-ct="${esc(t.schema)}" aria-pressed=${c.type === t.schema} title="${esc(schemaWord(t.schema))}">${entityIcon(t.schema)}<span>${esc(schemaWord(t.schema))}</span> <b>${t.n}</b></button>`).join('') + '</div>' : '';
  const more = o && o.records && o.records.length < o.total ? `<button type=button class=btn data-more="${esc(r.name)}">Show more</button>` : '';
  return `<div class=colents>${chips}${etList('col', ents, c.archive, r)}${more}</div>`;
}
// the source a collection belongs to: the run that added it says; else the first source it cites
const colSource = r => {                       // the source a collection belongs to: its name starts with the source's, else the run that added it, else the first it cites
  const s = (state.configs.sources || []).filter(x => x.name && (r.name === x.name || r.name.startsWith(x.name + '-'))).sort((a, b) => b.name.length - a.name.length)[0];
  return (s && s.title) || (r.run && r.run.source) || (r.sources || [])[0] || 'Other';
};
function colGroups(list) {
  const out = new Map();
  for (const r of list) { const k = colSource(r); if (!out.has(k)) out.set(k, []); out.get(k).push(r); }
  return [...out];
}
function drawCollections() {
  const box = $('#colpanel');
  if (!box) return;
  const c = state.col, d = c.data;
  const opts = colArchives().map(([v, w]) => `<option value="${esc(v)}"${v === c.archive ? ' selected' : ''}>${esc(w)}</option>`).join('');
  let list = '', main = '';
  if (d && !d.collections.length) list = '<p class=none>No collections yet.</p>';
  else if (d) {
    list = `<ol class="rl crl">${colGroups(d.collections).map(([src, cs]) => {
      const g = (state.groups || []).find(x => x.title === src);
      return `<li class=cgrp><span class=sic aria-hidden=true>${ic('text')}</span><b title="${esc(src)}">${esc(src)}</b><small>${cs.length} ${cs.length === 1 ? 'collection' : 'collections'}</small>${g ? lightsHTML(g) : ''}</li>` +
        cs.map(r => `<li class="csub${r.name === c.sel ? ' sel' : ''}" data-coll="${esc(r.name)}" tabindex=0 aria-current=${r.name === c.sel}>` +
          `<b title="${esc(r.title)}">${esc(r.title)}</b><time>${r.count}</time></li>`).join('');
    }).join('')}</ol>`;
    const sel = d.collections.find(r => r.name === c.sel);
    if (sel) main = `<div class=dhead><h2 title="${esc(sel.title)}">${esc(sel.title)}</h2></div>` +
      `<div class=srcart><div class=srcmain>${colEntities(sel)}</div><aside class="side portrait" id=colrail aria-label=Collection>${colRail(sel)}</aside></div>`;
  }
  const rail = `<h2>Collections <small>${d ? d.collections.length || '' : ''}</small></h2><div class=colbar><select id=colarch aria-label=Archive>${opts}</select>${d && d.building ? '<span class=spin aria-label=Building role=status></span>' : ''}</div><div class=scroll>${list}</div>`;
  const html = `<section class="panel xrail">${rail}</section><section class="panel xmain">${main}</section>`;
  if (box.dataset.k === html) return;
  const ys = box.dataset.arch === c.archive ? [...box.querySelectorAll('.xrail .scroll, .xmain')].map(x => x.scrollTop) : [];
  box.dataset.k = html;
  box.innerHTML = html;
  [...box.querySelectorAll('.xrail .scroll, .xmain')].forEach((x, i) => { x.scrollTop = ys[i] || 0; });   // a click never jumps to the top
  applyCss(box);
  box.dataset.arch = c.archive;
}
async function selectCollection(name, more) {
  const c = state.col;
  if (c.sel === name && !more && c.open[name]) return;          // the rail keeps a selection
  if (c.sel !== name) { c.sel = name; c.type = ''; if (state.et.sel && state.et.sel.collection !== name) state.et.sel = null; }
  if (!c.open[name]) c.open[name] = {records: null, total: 0};
  drawCollections();
  const cur = c.open[name], offset = cur.records && more ? cur.records.length : 0;
  if (cur.records && !more) return;
  try {
    const got = await api(`/api/collections?archive=${encodeURIComponent(c.archive)}&collection=${encodeURIComponent(name)}&offset=${offset}`);
    cur.records = (cur.records || []).concat(got.records);
    cur.total = got.total;
    setError('col', null);
  } catch (e) {
    delete c.open[name];
    setError('col', `Collections: ${e.message}`);
  }
  drawCollections();
}
$('#colpanel').addEventListener('click', e => {
  const ct = e.target.closest('[data-ct]');
  if (ct) { state.col.type = state.col.type === ct.dataset.ct ? '' : ct.dataset.ct; drawCollections(); return; }
  const more = e.target.closest('[data-more]');
  if (more) { selectCollection(more.dataset.more, true); return; }
  const run = e.target.closest('[data-run]');
  if (run) { state.page = null; select(run.dataset.run); layout(); return; }
  const row = e.target.closest('[data-coll]');
  if (row) selectCollection(row.dataset.coll, false);
});
$('#colpanel').addEventListener('keydown', e => {
  const row = e.target.closest('[data-coll]');
  if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); selectCollection(row.dataset.coll, false); }
});
$('#colpanel').addEventListener('change', e => {
  if (e.target.id !== 'colarch') return;
  state.col.archive = e.target.value;
  state.col.data = null;
  state.col.sel = null;
  state.col.picked = false;
  state.col.open = {};
  state.et.sel = null;
  drawCollections();
  loadCollections();
});
// the table follows its archive: a build in the background, a run still writing
setInterval(() => { if (state.page === 'collections' && !document.hidden) loadCollections(); }, 3000);
