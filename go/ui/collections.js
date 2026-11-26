// The Collections view, laid out like Runs (owner): a left rail with the archive and its collections grouped under their containing
// source (each source styled like a Runs entry); the main area is the selected collection: its kinds, its things, and on the right
// its card (records, kinds, sources, the run that added it, when) and the selected thing's card with its Trace (entitytrace.js).
// Draft first, then the runs' archives. Apollo builds an archive itself when it is stale: a small spinner by the select says so.
// A Complex Collection (CZ306) is a hydra collection group (hydra.yaml `collection_groups`) whose collections sit under more than
// one source: it is a containing entry of its own, with the sources it spans and their runs' lights, its collections beneath.
// Drag a collection (or a source: all its collections) onto a Complex Collection to add it, onto a collection of another source to
// make a Complex Collection of both, or from a Complex Collection onto its own source to take it out.
'use strict';
state.col = {archive: '', data: null, sel: null, open: {}, busy: false, type: '', drag: null, fold: {}, foldFor: null, fresh: null};
const when = longWhen;                                 // app.js: "November 25, 2026 6:36 PM"
Object.assign(SV, {
  cplx: '<path d="M3 15m0 2a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M15 15m0 2a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M9 3m0 2a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M6 15v-1a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v1"/><path d="M12 9l0 3"/>',   // Tabler sitemap (hydra's icons/sitemap.svg): a collection of collections; no entity type uses it
  plus: '<path d="M12 5l0 14"/><path d="M5 12l14 0"/>',
  chev: '<path d="M6 9l6 6l6 -6"/>',
  pencil: '<path d="M4 20h4L18.5 9.5a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  'cplx-del': '<path d="M12 4l-8 4l8 4l8-4l-8-4"/><path d="M4 12l8 4l3-1.5"/><path d="M4 16l8 4l3-1.5"/><path d="M16 17h6"/>',
});
const GKEY = 'g:', SKEY = 's:';
// collapsed entries (a source's group, a Complex Collection) are kept per archive in localStorage
const foldName = () => 'apollo.col.fold.' + state.col.archive;
function foldLoad() {
  const c = state.col;
  if (c.foldFor === c.archive) return;
  c.foldFor = c.archive;
  try { c.fold = JSON.parse(localStorage.getItem(foldName()) || '{}') || {}; } catch (e) { c.fold = {}; }
}
function foldSet(key, on) {
  const c = state.col;
  foldLoad();
  if (on) c.fold[key] = 1; else delete c.fold[key];
  try { localStorage.setItem(foldName(), JSON.stringify(c.fold)); } catch (e) { /* the page still works */ }
  drawCollections();
}
const foldBtn = (key, open) => `<button type=button class=cfold data-fold="${esc(key)}" aria-expanded=${open} title="${open ? 'Collapse' : 'Expand'}" aria-label="${open ? 'Collapse' : 'Expand'}">${ic('chev')}</button>`;
                                     // a group's key in the rail; a collection name never holds a colon

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
      if (!c.sel && !c.picked && got.collections.length) { c.picked = true; selectCollection(colFirst(got), false); }
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
  if (r.types.filter(t => t.n).length < 2) add('Types', `<div class=tys>${typeCells(r.types)}</div>`);   // two kinds or more are the chips under the title: not shown twice
  add('Sources', r.sources.map(s => `<div>${esc(s)}</div>`).join(''));
  add('Run', r.imported ? `<span class=dim>imported from ${esc(r.imported)}</span>` : r.run ? `<button type=button class=pgo data-run="${esc(r.run.id)}" title="${esc([r.run.flow, r.run.source].filter(Boolean).join(' · ') || r.run.id)}">${esc([r.run.flow, r.run.source].filter(Boolean).join(' · ') || r.run.id)}</button>` : '');
  add('Added', esc(when(r.added)));
  return rows.join('');
}
// the rail holds the collection's own card, and the Selected entity's card with its Trace while one is picked (owner: the rail
// is for the collection, not a list of what it holds)
function colRail(r) {
  let cap = esc(r.title);
  if (r.complex) {                                  // a Complex Collection's title is edited in place; its buttons rename and ungroup
    const title = state.col.renaming ? `<input class=cgin id=cgin value="${esc(r.title)}" aria-label=Rename maxlength=200>` : `<span class=cgt data-gren title="Rename">${cap}</span>`;
    cap = `<div class=cgcap>${title}<span class=cgbtn><button type=button class=ib data-gren title=Rename aria-label=Rename>${ic('pencil')}</button>` +
      `<button type=button class=ib data-gdel title=Ungroup aria-label=Ungroup>${ic('cplx-del')}</button></span></div>`;
  }
  return `<table class=infobox><caption>${cap}</caption>${colInfoRows(r)}</table>${etCard()}`;
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
// a source's type glyph by its title (the Sources rail's icons); a source Apollo does not know is a document
const colGlyph = title => {
  const s = (state.configs.sources || []).find(x => x.title === title);
  return s ? typeIcon({format: String(s.file || '').split('.').pop(), publisher: s.publisher}) : 'text';
};
// the Complex Collections: hydra's collection groups whose collections sit under more than one source
function colComplex(d) {
  const by = new Map(d.collections.map(r => [r.name, r]));
  return (d.groups || []).map(g => {
    const members = g.collections.map(n => by.get(n)).filter(Boolean);
    return {...g, name: GKEY + g.name, group: g.name, complex: true, members, homes: [...new Set(members.map(colSource))]};
  }).filter(g => g.homes.length > 1);
}
// the runs of the sources a Complex Collection spans, in the Runs rail's order, for its lights
const colRuns = homes => {
  const keys = new Set((state.groups || []).filter(x => homes.includes(x.title)).map(x => x.key));
  return {runs: (state.runs || []).filter(r => keys.has(runSource(r).key))};
};
// the rail's first entry: the first Complex Collection, else the first collection of the first source
const colFirst = d => {
  const cx = colComplex(d);
  if (cx.length) return cx[0].name;
  return (colGroups(d.collections)[0] || [0, d.collections])[1][0].name;
};
function drawCollections() {
  const box = $('#colpanel');
  if (!box || state.col.drag) return;                  // a drag in progress keeps its rows
  const c = state.col, d = c.data;
  foldLoad();
  const opts = colArchives().map(([v, w]) => `<option value="${esc(v)}"${v === c.archive ? ' selected' : ''}>${esc(w)}</option>`).join('');
  let list = '', main = '';
  if (d && !d.collections.length && !c.fresh) list = '<p class=none>No collections yet.</p>';
  else if (d) {
    const cx = colComplex(d), held = new Set(cx.flatMap(g => g.collections));     // a collection shows once: under its Complex Collection
    const sub = r => `<li class="csub${r.name === c.sel ? ' sel' : ''}" data-coll="${esc(r.name)}" draggable=true tabindex=0 aria-current=${r.name === c.sel}>` +
      `<b title="${esc(r.title)}">${esc(r.title)}</b><time>${r.count}</time></li>`;
    const fr = c.fresh, fo = k => !c.fold[k];            // a new entry's title field open in place; fo: the entry is expanded
    const fresh = !fr ? '' : `<li class="cgrp cplx cnew" data-new=1 tabindex=0><span class=sic aria-hidden=true>${ic('cplx')}</span>` +
      (fr.editing ? `<input class=cgin id=cnewin value="${esc(fr.title)}" aria-label="New collection" maxlength=200>` : `<b>${fr.title ? esc(fr.title) : '<span class=dim>New collection</span>'}</b>`) +
      (fr.names.length ? `<time>${fr.names.length}</time>` : '') + '</li>';
    list = `<ol class="rl crl">${fresh}${cx.map(g =>
      `<li class="cgrp cplx${g.name === c.sel ? ' sel' : ''}" data-coll="${esc(g.name)}" tabindex=0 aria-current=${g.name === c.sel} aria-expanded=${fo(g.name)}><span class=sic aria-hidden=true>${ic('cplx')}</span>` +
      `<b title="${esc(g.title)}">${esc(g.title)}</b><time>${g.count}</time><small class=cspan>${g.homes.map(h => `<span title="${esc(h)}">${ic(colGlyph(h))}${g.title.includes(h) ? '' : `<span>${esc(h)}</span>`}</span>`).join('')}</small>` +
      `${lightsHTML(colRuns(g.homes))}${foldBtn(g.name, fo(g.name))}</li>` + (fo(g.name) ? g.members.map(sub).join('') : '')).join('')}` +
      `${colGroups(d.collections.filter(r => !held.has(r.name))).map(([src, cs]) => {
      const g = (state.groups || []).find(x => x.title === src);
      const sk = SKEY + src;
      return `<li class=cgrp data-csrc="${esc(src)}" draggable=true tabindex=0 aria-expanded=${fo(sk)}><span class=sic aria-hidden=true>${ic(colGlyph(src))}</span><b title="${esc(src)}">${esc(src)}</b><small>${cs.length} ${cs.length === 1 ? 'collection' : 'collections'}</small>${g ? lightsHTML(g) : ''}${foldBtn(sk, fo(sk))}</li>` +
        (fo(sk) ? cs.map(sub).join('') : '');
    }).join('')}</ol>`;
    const sel = c.sel && c.sel.startsWith(GKEY) ? cx.find(g => g.name === c.sel) : d.collections.find(r => r.name === c.sel);
    if (sel) main = `<div class=dhead><h2 title="${esc(sel.title)}">${esc(sel.title)}</h2></div>` +
      `<div class=srcart><div class=srcmain>${colEntities(sel)}</div><aside class="side portrait" id=colrail aria-label=Collection>${colRail(sel)}</aside></div>`;
  }
  const rail = `<h2>Collections <small>${d ? d.collections.length || '' : ''}</small></h2><div class=colbar><select id=colarch aria-label=Archive>${opts}</select><button type=button class=ib id=colnew title="New collection" aria-label="New collection">${ic('plus')}</button>${d && d.building ? '<span class=spin aria-label=Building role=status></span>' : ''}</div><div class=scroll>${list}</div>`;
  const html = `<section class="panel xrail">${rail}</section><section class="panel xmain">${main}</section>`;
  if (box.dataset.k === html) return;
  const ys = box.dataset.arch === c.archive ? [...box.querySelectorAll('.xrail .scroll, .xmain')].map(x => x.scrollTop) : [];
  box.dataset.k = html;
  box.innerHTML = html;
  [...box.querySelectorAll('.xrail .scroll, .xmain')].forEach((x, i) => { x.scrollTop = ys[i] || 0; });   // a click never jumps to the top
  applyCss(box);
  box.dataset.arch = c.archive;
  const ni = $('#cnewin');
  if (ni && document.activeElement !== ni) { ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); }
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
    const which = name.startsWith(GKEY) ? `group=${encodeURIComponent(name.slice(GKEY.length))}` : `collection=${encodeURIComponent(name)}`;
    const got = await api(`/api/collections?archive=${encodeURIComponent(c.archive)}&${which}&offset=${offset}`);
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
  const fb = e.target.closest('[data-fold]');
  if (fb) { foldSet(fb.dataset.fold, !state.col.fold[fb.dataset.fold]); return; }
  if (e.target.closest('#colnew')) { newStart(); return; }
  const ct = e.target.closest('[data-ct]');
  if (ct) { state.col.type = state.col.type === ct.dataset.ct ? '' : ct.dataset.ct; drawCollections(); return; }
  const more = e.target.closest('[data-more]');
  if (more) { selectCollection(more.dataset.more, true); return; }
  const run = e.target.closest('[data-run]');
  if (run) { state.page = null; select(run.dataset.run); layout(); return; }
  const lt = e.target.closest('.crl .lt[data-id]');            // a lit flow: that run, in Runs
  if (lt) { state.page = null; select(lt.dataset.id); layout(); return; }
  if (e.target.closest('[data-gren]')) { renameStart(); return; }
  if (e.target.closest('[data-gdel]')) { ungroup(); return; }
  const row = e.target.closest('[data-coll]');
  if (row) selectCollection(row.dataset.coll, false);
});
// a source's short form for a Complex Collection's name: the title before its first colon, without a leading article; when the
// whole name runs long, "History of the X" is X and any title is cut to four words, never ending on a joining word
const SHORT = /^(?:an?|the)\s+/i, LINK = new Set(['of', 'the', 'and', 'a', 'an', 'in', 'on', 'for', 'to', 'at', 'by', 'with']);
const shortTitle = (t, tight) => {
  let s = String(t || '').split(':')[0].replace(SHORT, '').trim();
  if (tight) {
    s = s.replace(/^(?:\w+\s+)?(?:history|story|life|biography|report|study|survey)\s+(?:of|on|about)\s+(?:the\s+|a\s+)?/i, '');
    const w = s.split(/\s+/).slice(0, 4);
    while (w.length > 1 && LINK.has(w[w.length - 1].toLowerCase())) w.pop();
    s = w.join(' ');
  }
  return s || String(t || '');
};
const complexTitle = homes => {
  const long = homes.map(h => shortTitle(h, false)).join(' · ');
  return long.length <= 48 ? long : homes.map(h => shortTitle(h, true)).join(' · ');
};
// rename in place: click the title (or the pencil), Enter saves, Esc cancels, empty text does nothing
function renameStart() {
  const c = state.col;
  if (!c.sel || !c.sel.startsWith(GKEY) || c.renaming) return;
  c.renaming = true;
  drawCollections();
  const i = $('#cgin');
  if (i) { i.focus(); i.select(); }
}
function renameEnd() {
  state.col.renaming = false;
  drawCollections();
}
async function groupEdit(body, key) {
  const c = state.col;
  try {
    await api('/api/collections/group/edit', {archive: c.archive, key: key || c.sel.slice(GKEY.length), ...body});
    setError('col', null);
    return true;
  } catch (e) {
    setError('col', `Complex Collection: ${e.message}`);
    return false;
  }
}
async function renameSave(text) {
  const c = state.col, title = text.split(/\s+/).filter(Boolean).join(' ');
  const cur = (c.data && (c.data.groups || []).find(g => GKEY + g.name === c.sel)) || {};
  if (!title || title === cur.title) { renameEnd(); return; }
  const ok = await groupEdit({title});
  c.renaming = false;
  c.loading = false;
  if (ok) await loadCollections();
  drawCollections();
}
// ungroup: only the grouping goes; the collections and their records stay under their sources
async function ungroup() {
  const c = state.col;
  if (!c.sel || !c.sel.startsWith(GKEY)) return;
  const was = c.sel;
  if (!await groupEdit({})) return;
  delete c.open[was];
  c.sel = null;
  c.picked = false;
  c.renaming = false;
  c.loading = false;
  await loadCollections();
}
// a new collection: an entry at the top of the rail, its title open in place; Enter keeps the title, Esc removes the entry. It is
// written (hydra's group, through the create route) on the drop that gives it enough collections: two or more, of two sources.
function newStart() {
  const c = state.col;
  if (!c.fresh) c.fresh = {title: '', names: [], editing: true};
  c.fresh.editing = true;
  c.drag = null;
  drawCollections();
  const i = $('#cnewin');
  if (i) i.focus();
}
function newEnd() {
  state.col.fresh = null;
  drawCollections();
}
const foldKeyOf = li => li.dataset.csrc ? SKEY + li.dataset.csrc : li.dataset.coll;
$('#colpanel').addEventListener('input', e => { if (e.target.id === 'cnewin' && state.col.fresh) state.col.fresh.title = e.target.value; });
$('#colpanel').addEventListener('keydown', e => {
  const c = state.col;
  if (e.target.id === 'cnewin' && c.fresh) {
    if (e.key === 'Enter') { e.preventDefault(); c.fresh.title = e.target.value.split(/\s+/).filter(Boolean).join(' '); c.fresh.editing = false; drawCollections(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); newEnd(); }
    return;
  }
  if (e.key === 'Escape' && c.fresh && e.target.closest && e.target.closest('#colpanel .xrail')) { e.stopPropagation(); newEnd(); return; }
  const li = e.target.closest && e.target.closest('#colpanel .crl li.cgrp');       // Left collapses, Right expands a focused entry
  if (li && e.target === li && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !li.dataset.new && foldKeyOf(li)) {
    const key = foldKeyOf(li);
    e.preventDefault();
    foldSet(key, e.key === 'ArrowLeft');
    const again = [...document.querySelectorAll('#colpanel .crl li.cgrp')].find(x => foldKeyOf(x) === key);
    if (again) again.focus();
  }
});
$('#colpanel').addEventListener('keydown', e => {
  if (e.target.id !== 'cgin') return;
  if (e.key === 'Enter') { e.preventDefault(); renameSave(e.target.value); }
  else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); renameEnd(); }
});
// Drag and drop in the rail. What dropping the dragged collections on a row does, or null when the row is no target:
//   on a Complex Collection: they join it; on a collection of another source: a Complex Collection of both;
//   from a Complex Collection onto its own source: they leave it (the group goes when one source is left).
function dropPlan(li) {
  const c = state.col, d = c.data, dr = c.drag;
  if (!d || !dr || !li) return null;
  const cx = colComplex(d), homeOf = n => colSource(d.collections.find(r => r.name === n) || {name: n, sources: []});
  const homes = new Set(dr.names.map(homeOf));
  if (li.dataset.new) {                                // the new entry takes what it is given; it is written once it holds two collections of two sources
    if (!c.fresh || dr.group) return null;
    const names = [...new Set([...c.fresh.names, ...dr.names])], hs = [...new Set(names.map(homeOf))];
    return {fresh: true, make: names.length > 1 && hs.length > 1 ? names : null, names, homes: hs};
  }
  if (li.classList.contains('scroll')) return dr.group ? {leave: cx.find(g => g.name === dr.group), names: dr.names} : null;   // the rail's blank part: out of the group
  const join = g => g && g.name !== dr.group && dr.names.some(n => !g.collections.includes(n)) ? {join: g, add: dr.names.filter(n => !g.collections.includes(n))} : null;
  if (li.dataset.coll && li.dataset.coll.startsWith(GKEY)) return join(cx.find(g => g.name === li.dataset.coll));
  const own = li.dataset.csrc, one = li.dataset.coll;
  const holder = one && cx.find(g => g.collections.includes(one));
  if (holder) return join(holder);
  const home = own || (one && homeOf(one));
  if (!home) return null;
  if (dr.group) return homes.size === 1 && homes.has(home) ? {leave: cx.find(g => g.name === dr.group), names: dr.names} : null;
  if (homes.has(home)) return null;
  const held = new Set(cx.flatMap(g => g.collections));
  const names = own ? d.collections.filter(r => colSource(r) === own && !held.has(r.name)).map(r => r.name) : [one];
  return names.length ? {make: [...dr.names, ...names], homes: [...homes, home]} : null;
}
const colMarks = on => document.querySelectorAll('#colpanel .crl li, #colpanel .xrail .scroll').forEach(li => li.classList.toggle('dt', on && !!dropPlan(li)));
async function dropApply(plan) {
  const c = state.col, d = c.data;
  let sel = null;
  if (plan.fresh && !plan.make) { c.fresh.names = plan.names; drawCollections(); return; }
  if (plan.make) {
    try {
      const got = await api('/api/collections/group', {archive: c.archive, title: (plan.fresh && c.fresh.title) || complexTitle(plan.homes), collections: plan.make});
      sel = GKEY + got.name;
      if (plan.fresh) c.fresh = null;
      setError('col', null);
    } catch (e) { setError('col', `Complex Collection: ${e.message}`); }
  } else if (plan.join) {
    if (await groupEdit({collections: [...plan.join.collections, ...plan.add]}, plan.join.group)) sel = plan.join.name;
  } else if (plan.leave) {
    const g = plan.leave, rest = g.collections.filter(n => !plan.names.includes(n));
    const homes = new Set(rest.map(n => colSource(d.collections.find(r => r.name === n) || {name: n, sources: []})));
    const keep = rest.length >= 2 && homes.size > 1;
    if (await groupEdit(keep ? {collections: rest} : {}, g.group)) sel = keep ? g.name : null;
    if (!keep && c.sel === g.name) { c.sel = null; c.picked = false; }
  }
  for (const k of Object.keys(c.open)) if (k.startsWith(GKEY)) delete c.open[k];
  c.loading = false;
  await loadCollections();
  if (sel) { c.sel = null; selectCollection(sel, false); } else drawCollections();
}
const colDragLi = e => e.target.closest && (e.target.closest('#colpanel .crl li') || e.target.closest('#colpanel .xrail .scroll'));
$('#colpanel').addEventListener('dragstart', e => {
  const li = colDragLi(e), c = state.col, d = c.data;
  if (!li || !d || li.classList.contains('cplx') || !(li.dataset.coll || li.dataset.csrc)) return;
  const cx = colComplex(d), held = new Set(cx.flatMap(g => g.collections));
  const names = li.dataset.csrc ? d.collections.filter(r => colSource(r) === li.dataset.csrc && !held.has(r.name)).map(r => r.name) : [li.dataset.coll];
  if (!names.length) return;
  const g = li.dataset.coll && cx.find(x => x.collections.includes(li.dataset.coll));
  c.drag = {names, group: g ? g.name : null};
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', names.join(','));
  colMarks(true);
});
$('#colpanel').addEventListener('dragover', e => {
  const li = colDragLi(e);
  if (li && li.classList.contains('dt')) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
});
$('#colpanel').addEventListener('drop', e => {
  const li = colDragLi(e), plan = li && li.classList.contains('dt') && dropPlan(li);
  if (!plan) return;
  e.preventDefault();
  colMarks(false);
  dropApply(plan);
});
$('#colpanel').addEventListener('dragend', () => {           // Esc, or a drop elsewhere, ends it too
  colMarks(false);
  state.col.drag = null;
  drawCollections();
});
$('#colpanel').addEventListener('keydown', e => {
  const row = e.target.closest('[data-coll]');
  if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); selectCollection(row.dataset.coll, false); }
});
$('#colpanel').addEventListener('change', e => {
  if (e.target.id !== 'colarch') return;
  state.col.archive = e.target.value;
  state.col.fresh = null;
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
