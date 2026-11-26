// The Sources view, laid out like Runs (owner): a left rail lists the sources (type filters on top, the drop bay at the foot),
// each entry like a Runs entry (title, format and pages, the flow lights of what has run on it, its read pages); the main area is the
// selected source: its title with Pages, Brief and Remove, the model and Catalogue, its whole page range as one Records row (what
// runs have read, shaded by records), the things found in it, and on the right its card (picture, facts, catalogue) and the
// selected thing's card. Built-in sources are read only.
'use strict';
const LAYER_WORD = {full: 'full', partial: 'partial', none: 'none'};
state.src = {data: null, up: [], open: {}, draft: {}, gen: {}, sel: null, hideWiki: store.get('apollo-hidewiki') === '1', off: new Set((store.get('apollo-srctypes') || '').split(',').filter(Boolean)), wiki: {open: false, lang: 'en', q: '', busy: false}};
const bytesText = n => n == null ? '' : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
Object.assign(SV, {wikipedia: '<g transform="translate(-.5 -.25)"><path d="M3 4.984h2"/><path d="M8 4.984h2.5"/><path d="M14.5 4.984h2.5"/><path d="M22 4.984h-2"/><path d="M4 4.984l5.455 14.516l6.545 -14.516"/><path d="M9 4.984l6 14.516l6 -14.516"/></g>'});
const LANG_WORD = {en: 'English', ar: 'Arabic', fr: 'French', de: 'German', es: 'Spanish', it: 'Italian', ru: 'Russian', fa: 'Persian', tr: 'Turkish', ur: 'Urdu', he: 'Hebrew', zh: 'Chinese', ja: 'Japanese', pt: 'Portuguese'};
const SRC_TYPES = [['document', 'Document', 'text'], ['photo', 'Photo', 'ty-photo'], ['video', 'Video', 'ty-video'], ['audio', 'Audio', 'ty-audio'], ['web', 'Web', 'ty-web']];
Object.assign(SV, {
  'ty-photo': '<path d="M15 8h.01"/><path d="M3 6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3h-12a3 3 0 0 1-3-3v-12"/><path d="M3 16l5-5c.928-.893 2.072-.893 3 0l5 5"/><path d="M14 14l1-1c.928-.893 2.072-.893 3 0l3 3"/>',
  'ty-video': '<path d="M15 10l4.553-2.276a1 1 0 0 1 1.447.894v6.764a1 1 0 0 1-1.447.894l-4.553-2.276v-4"/><path d="M3 8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2z"/>',
  'ty-audio': '<g transform="translate(1 0)"><path d="M3 17a3 3 0 1 0 6 0a3 3 0 0 0-6 0"/><path d="M13 17a3 3 0 1 0 6 0a3 3 0 0 0-6 0"/><path d="M9 17v-13h10v13"/><path d="M9 8h10"/></g>',
  'ty-web': '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0-18 0"/><path d="M3.6 9h16.8"/><path d="M3.6 15h16.8"/><path d="M11.5 3a17 17 0 0 0 0 18"/><path d="M12.5 3a17 17 0 0 1 0 18"/>'
});
const IMG_EXT = ['jpg', 'jpeg', 'png', 'tif', 'tiff', 'webp', 'gif'], VID_EXT = ['mp4', 'mov', 'webm', 'mkv'], AUD_EXT = ['mp3', 'wav', 'm4a', 'flac'];
const ACCEPT = '.pdf,.txt,.md,.html,.htm,.docx,' + [...IMG_EXT, ...VID_EXT, ...AUD_EXT].map(e => '.' + e).join(',');
// a source's type from its format: pdf, docx, txt and md are a document; html, Wikipedia and any fetched page are web (the manager says so as `type`)
const srcType = r => {
  if (r.type) return r.type;
  const f = String(r.format || '').toLowerCase();
  return IMG_EXT.includes(f) ? 'photo' : VID_EXT.includes(f) ? 'video' : AUD_EXT.includes(f) ? 'audio' : f === 'html' || f === 'htm' || r.url || isWiki(r) ? 'web' : 'document';
};
const typeIcon = r => (SRC_TYPES.find(t => t[0] === srcType(r)) || SRC_TYPES[0])[2];
const plural = (n, w) => n === 1 ? w : `${w}s`;
const dateOnly = s => String(s || '').slice(0, 10);
const isWiki = r => /wikipedia/i.test(r.publisher || '') || /(^|\.)wikipedia\.org(\/|$)/i.test(String(r.url || '').replace(/^[a-z]+:\/\//i, ''));
const srcRows = () => { const d = state.src.data || {dropped: [], builtin: []}; return d.dropped.concat(d.builtin.map(b => ({...b, builtin: true}))); };
const srcSel = () => srcRows().find(r => r.slug === state.src.sel);

async function openSources() {
  drawSources();
  await refreshSources();
}
async function refreshSources() {
  try {
    state.src.data = await api('/api/sources');
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
  }
  if (state.src.data && state.src.sel && !srcRows().some(r => r.slug === state.src.sel)) state.src.sel = null;
  if (state.src.sel) { const r = srcSel(); if (!r || r.kind !== 'media') catLoad(state.src.sel); etSourceLoad(state.src.sel, r && r.title); }
  if (state.page === 'sources') drawSources();
}
function sourceRow(r) {
  const total = r.page_count ? `${r.page_count} ${plural(r.page_count, 'page')}` : '';
  const layer = r.text_layer ? `<span class="badge lay-${esc(r.text_layer)}">${esc(LAYER_WORD[r.text_layer] || r.text_layer)}</span>` : '';
  const pg = r.pages ? `${r.pages.from}-${r.pages.to}` : '';
  const open = !!state.src.open[r.slug];
  const sel = state.src.sel === r.slug;
  return `<li class="srow${sel ? ' sel' : ''}" data-slug="${esc(r.slug)}" data-flip-id="${esc(r.slug)}" aria-current=${sel}>
    <div class=sr1><input class=stitle value="${esc(r.title)}" title="${esc(r.title)}" aria-label=Title data-f=title>
      <span class=meta1>${catMark(r)}<span class=chip>${esc(String(r.format || '').toUpperCase())}</span>${total ? `<span>${esc(total)}</span>` : ''}${layer}<span class=dim>${esc(bytesText(r.bytes))}</span></span></div>
    ${r.kind === 'media' ? `<div class=sr2><span class=grow></span><button type=button class="btn danger" data-act=remove>Remove</button></div>` : `<div class=sr2>
      <label class=f><span>Pages</span><input class=spages value="${esc(pg)}" inputmode=numeric data-f=pages aria-label=Pages></label>
      <span class=grow></span>
      <button type=button class=btn data-act=brief aria-expanded=${open}>Brief</button>
      <button type=button class="btn danger" data-act=remove>Remove</button>
    </div>`}
    ${open && r.kind !== 'media' ? `<div class=sbrief><textarea data-f=brief aria-label=Brief rows=7>${esc((r.slug in state.src.draft ? state.src.draft[r.slug] : (r.brief || '')).trim())}</textarea><div class=sbtn>` +
      `<button type=button class=ib data-act=generate title="Generate brief" aria-label="Generate brief"${state.src.gen[r.slug] ? ' disabled aria-busy=true' : ''}>${state.src.gen[r.slug] ? '<span class=spin aria-hidden=true></span>' : ic('sparkle')}</button>` +
      `<button type=button class=ib data-act=default title="Default brief" aria-label="Default brief">${ic('reset')}</button></div></div>` : ''}
  </li>`;
}
// a source that ships with the page flow: the same list, a small muted mark, nothing to edit
function builtinRow(r) {
  const sel = state.src.sel === r.slug;
  const total = r.page_count ? `${r.page_count} ${plural(r.page_count, 'page')}` : '';
  return `<li class="srow ro${sel ? ' sel' : ''}" data-slug="${esc(r.slug)}" data-flip-id="${esc(r.slug)}" aria-current=${sel}>
    <div class=sr1><span class="stitle ro" title="${esc(r.title)}">${esc(r.title)}</span>
      <span class=meta1>${catMark(r)}<span class=bin>Built in</span>${r.format ? `<span class=chip>${esc(String(r.format).toUpperCase())}</span>` : ''}${total ? `<span>${esc(total)}</span>` : ''}</span></div>
  </li>`;
}

// ---- the portrait card: the drop zone and the Wikipedia button, then the selected source's infobox ----------------------------
function wikiForm() {
  const w = state.src.wiki;
  if (!w.open) return '';
  return `<form class=wikif id=wikif autocomplete=off><label class=f><span>Language</span><input class=wl name=lang value="${esc(w.lang)}" maxlength=8 aria-label=Language></label>` +
    `<label class=f><span>Article</span><input class=wq name=q value="${esc(w.q)}" placeholder="Title or address" aria-label="Article title or address"></label>` +
    `<button type=submit class="btn primary"${w.busy ? ' disabled aria-busy=true' : ''}>${w.busy ? '<span class=spin aria-hidden=true></span>' : ''}Add</button></form>`;
}
function infoRows(r) {
  const rows = [];
  const live = !r.builtin && r.kind !== 'media';                                   // a dropped source's facts are edited in place
  const add = (k, v, f) => {
    if (f && live) rows.push(`<tr><th scope=row>${k}</th><td class=inl data-edit=src data-f="${f}" data-ek="${esc(r.slug)}:${f}" data-v="${esc(v || '')}" tabindex=0>${esc(v || '')}</td></tr>`);
    else if (v !== null && v !== undefined && v !== '') rows.push(`<tr><th scope=row>${k}</th><td>${esc(v)}</td></tr>`);
  };
  add('Format', String(r.format || '').toUpperCase());
  add('Pages', r.page_count);
  if (r.builtin) add('Range', typeof r.pages === 'string' ? r.pages : '');
  if (r.text_layer) add('Text', LAYER_WORD[r.text_layer] || r.text_layer);
  if (r.text_layer && r.text_layer !== 'full') add('Scanned', r.scanned);
  add('Publisher', r.publisher, 'publisher');
  add('Language', r.language, 'language');
  add('Address', r.url, 'url');
  add('Retrieved', dateOnly(r.retrieved), 'retrieved');
  add('Size', bytesText(r.bytes));
  add('Added', dateOnly(r.added));
  return rows.join('');
}
function infobox(r) {
  const media = r.kind === 'media';
  const pic = r.format === 'pdf' || srcType(r) === 'photo' ? `<img class=thumb src="/api/source/thumb?slug=${encodeURIComponent(r.slug)}&v=${esc(String(r.bytes || ''))}" alt="" width=420>`
    : `<span class=glyph aria-hidden=true>${ic(typeIcon(r))}</span>`;
  return `<table class=infobox><caption${r.builtin || media ? '' : ` class=inl data-edit=src data-f=title data-ek="${esc(r.slug)}:title" data-v="${esc(r.title)}" tabindex=0`}>${esc(r.title)}</caption><tr><td colspan=2 class=img>${pic}</td></tr>${infoRows(r)}${media ? '' : catAcceptedRows(r.slug)}</table>` +
    `${media ? '' : catPending(r.slug)}`;
}
function sourceCard(r) {
  const media = r.kind === 'media';
  const pic = r.format === 'pdf' || srcType(r) === 'photo' ? `<img class=thumb src="/api/source/thumb?slug=${encodeURIComponent(r.slug)}&v=${esc(String(r.bytes || ''))}" alt="" width=420>`
    : `<span class=glyph aria-hidden=true>${ic(typeIcon(r))}</span>`;
  const acc = media ? '' : catAcceptedRows(r.slug), pend = media ? '' : catPending(r.slug);
  const portrait = `<aside class="side portrait srportrait"><table class=infobox><caption>${esc(r.title)}</caption><tr><td colspan=2 class=img>${pic}</td></tr>${infoRows(r)}</table></aside>`;
  return `<div class=srcard><div class=srkv>${acc ? `<table class=infobox><tbody>${acc}</tbody></table>` : ''}${pend}</div>${portrait}</div>`;
}
// (owner) a source has tabs like a run: its catalogue, what was found in it, its pages, the runs on it
const SRC_TABS = [['catalogue', 'Catalogue'], ['found', 'Found'], ['pages', 'Pages'], ['runs', 'Runs']];
state.src.tab = SRC_TABS.some(t => t[0] === store.get('apollo-srctab')) ? store.get('apollo-srctab') : 'catalogue';
state.src.ftype = '';
function sourceTabs(r, ents, runs) {
  const n = {found: ents.length, runs: runs.length};
  return `<nav class=tabs role=tablist>${SRC_TABS.map(([k, w]) => `<button type=button role=tab data-stab=${k} aria-selected=${state.src.tab === k}>${w}${n[k] ? ` <small>${n[k]}</small>` : ''}</button>`).join('')}</nav>`;
}
function sourceFound(ents) {
  const kinds = [...new Set(ents.map(e => e.schema))];
  if (state.src.ftype && !kinds.includes(state.src.ftype)) state.src.ftype = '';
  const shown = state.src.ftype ? ents.filter(e => e.schema === state.src.ftype) : ents;
  const chips = kinds.length > 1 ? `<div class=fchips role=group aria-label=Kind><button type=button class=fc data-sft="" aria-pressed=${!state.src.ftype}>All <b>${ents.length}</b></button>` +
    kinds.map(k => `<button type=button class=fc data-sft="${esc(k)}" aria-pressed=${state.src.ftype === k}>${entityIcon(k)}${esc(schemaWord(k))} <b>${ents.filter(e => e.schema === k).length}</b></button>`).join('') + '</div>' : '';
  // the chips head the list's column: when they wrap, the Selected card beside them stays where it is (as in Collections)
  return `<div class=srcart><div class=srcmain>${chips}${etList('src', shown, draftPath(), null)}</div><aside class="side portrait" id=portrait aria-label=Selected>${etCard()}</aside></div>`;
}
function sourceRuns(runs) {
  return runs.map(x => `<div class=fr data-run="${esc(x.id)}" role=button tabindex=0><span class=ic>${ic(flowKey(x))}</span><b>${esc(runName(x))}</b>` +
    `<small><i class="dot ${dotOf(x)}"></i> ${esc(x.status)} · ${esc(longWhen(x.started))}</small></div>`).join('') || '';
}
state.src.page = null;
state.src.ptext = {};
function sourcePagesTab(r) {
  const p = state.src.page, t = p ? state.src.ptext[`${r.slug}|${p}`] : null;
  const text = p ? `<div class=sect>${esc(pgw(p))}</div>${t == null ? '<span class=spin aria-label=Loading role=status></span>' : `<div class=ptext>${esc(t)}</div>`}` : '';
  return sourcePages(r) + text;
}
async function sourcePageText(slug, p) {
  const k = `${slug}|${p}`;
  if (k in state.src.ptext) return;
  state.src.ptext[k] = null;
  try { const got = await api(`/api/pagetext?source=${encodeURIComponent(slug)}&page=${p}`); state.src.ptext[k] = got.text || got.page_text || ''; }
  catch (e) { state.src.ptext[k] = e.message; }
  if (state.page === 'sources') drawSources();
}
function portraitHTML(d) {
  const r = srcSel();
  return r ? infobox(r) + etSourceRail(r) : '';
}
// the drop bay: the bottom of the main column, always in view; it lights while files are dragged over the window
function bayHTML() {
  return `${wikiForm()}<div class=drop id=drop><span>Drop files here</span><div class=dbtns><button type=button class=btn id=pickfiles>Choose files</button>` +
    `<button type=button class="ib wikib" id=wikib title=Wikipedia aria-label=Wikipedia aria-expanded=${state.src.wiki.open}>${ic('wikipedia')}</button></div>` +
    `<input type=file id=filein multiple hidden accept="${ACCEPT}"></div>`;
}
function filterRow(rows) {
  const have = new Set(rows.map(srcType));
  return `<div class=srcfilter role=group aria-label=Show><button type=button class="ib hidewiki" id=hidewiki aria-pressed=${state.src.hideWiki} title="Hide Wikipedia" aria-label="Hide Wikipedia">${ic('wikipedia')}</button>` +
    SRC_TYPES.map(([k, w, i]) => `<button type=button class="ib stype" data-type="${k}" aria-pressed=${!state.src.off.has(k)} title="${w}" aria-label="${w}"${have.has(k) ? '' : ' disabled'}>${ic(i)}</button>`).join('') + '</div>';
}
// a rail entry: the source as a Runs entry (its runs are the runs grouped under it in the Runs rail)
function sourceItem(r) {
  const g = (state.groups || []).find(x => x.key === 'src:' + r.slug) || {runs: []};
  const sel = state.src.sel === r.slug;
  const meta = [String(r.format || '').toUpperCase(), r.page_count ? `${r.page_count} ${plural(r.page_count, 'page')}` : '', r.builtin ? 'Built in' : ''].filter(Boolean).join(' · ');
  return `<li data-src-pick="${esc(r.slug)}"${sel ? ' class=sel' : ''}><span class=sic aria-hidden=true>${ic(typeIcon(r))}</span><b title="${esc(r.title)}">${esc(r.title)}</b>` +
    `<span class=cm>${catMark(r)}</span><small>${esc(meta)}</small>${lightsHTML(g)}` +
    miniRows(g) + '</li>';
}
// the source's whole page range, one Records row: the pages runs have read shaded by what they found, the rest to do
function sourcePages(r) {
  const n = r.page_count || 0;
  if (!n || n > 3000 || r.kind === 'media') return '';
  const g = (state.groups || []).find(x => x.key === 'src:' + r.slug) || {runs: []};
  const read = new Map();
  for (const run of g.runs.filter(x => flowKey(x) === 'read').reverse()) for (const c of run.cells || []) read.set(c.page, c);   // newest wins
  const rows = Array.from({length: n}, (_, i) => ({...(read.get(i + 1) || {page: i + 1, status: 'todo', records: 0})}));
  return `<div class="hm srcheat">${heatGrid(rows, ['records', ...srcLanes(r, g, rows)])}</div>${read.size ? legend(heatKeys(rows)) : ''}`;
}
// a lane per flow after Read that has a finished run on this source (its newest), in Apollo's flow order: each page shaded by
// what that run touched there (the records it wrote, the questions it put), as the manager counts them (entitytrace.py lanes).
// A run on the Draft that names no source counts when it touched this source's pages.
// the flows in Apollo's order (runview.FLOWS); an old Links run reads as Wikidata through flowKey (CZ318)
const SRC_LANES = [['assemble', 'Assemble'], ['refine', 'Refine'], ['timeline', 'Timeline'], ['places', 'Places'], ['stubs', 'Stubs'], ['enrich', 'Enrich'],
                   ['wikidata', 'Wikidata'], ['finance', 'Finance'], ['wiki', 'Curate']];
function srcLanes(r, g, rows) {
  const lanes = (state.et.src[r.slug] || {}).lanes || {}, out = [];
  const ours = x => lanes[x.id] && (g.runs.includes(x) || Object.keys(lanes[x.id]).length);
  for (const [k, w] of SRC_LANES) {
    const run = (state.runs || []).find(x => flowKey(x) === k && x.status === 'done' && ours(x));
    if (!run) continue;
    const m = 'lane-' + k, per = lanes[run.id];
    for (const h of rows) h[m] = per[h.page] || 0;
    HEAT_LABEL[m] = w;
    HEAT_N[m] = h => h[m] || 0;
    HEAT[m] = h => { const c = cellRec(h.status, h[m]); return c === 'z' ? 'zg' : c; };
    out.push(m);
  }
  return out;
}
function sourceHead(r) {
  const pg = r.pages && typeof r.pages === 'object' ? `${r.pages.from}-${r.pages.to}` : '';
  const open = !!state.src.open[r.slug];
  // one row beside the title (owner): the page range, Brief, the model and Catalogue, Remove last
  const pages = r.builtin || r.kind === 'media' ? '' : `<input class=spages value="${esc(pg)}" inputmode=numeric data-f=pages aria-label=Pages title=Pages placeholder=Pages>` +
    `<button type=button class=btn data-act=brief aria-expanded=${open}>Brief</button>`;
  const ctl = pages + modelPill() + catAction() + (r.builtin ? '' : `<button type=button class="btn danger" data-act=remove>Remove</button>`);
  const brief = open && !r.builtin && r.kind !== 'media' ? `<div class=sbrief><textarea data-f=brief aria-label=Brief rows=7>${esc((r.slug in state.src.draft ? state.src.draft[r.slug] : (r.brief || '')).trim())}</textarea><div class=sbtn>` +
    `<button type=button class=ib data-act=generate title="Generate brief" aria-label="Generate brief"${state.src.gen[r.slug] ? ' disabled aria-busy=true' : ''}>${state.src.gen[r.slug] ? '<span class=spin aria-hidden=true></span>' : ic('sparkle')}</button>` +
    `<button type=button class=ib data-act=default title="Default brief" aria-label="Default brief">${ic('reset')}</button></div></div>` : '';
  const ttl = r.builtin || r.kind === 'media' ? '' : ` class=inl data-edit=src data-f=title data-ek="${esc(r.slug)}:title" data-v="${esc(r.title)}" tabindex=0`;
  return `<div class="srow srhead" data-slug="${esc(r.slug)}"><div class=dhead><h2 title="${esc(r.title)}"${ttl}>${esc(r.title)}</h2><div class=actions>${ctl}</div></div>${brief}</div>`;
}
function drawSources() {
  const box = $('#srcpanel');
  if (!box) return;
  const d = state.src.data || {dropped: [], builtin: []};
  const hide = state.src.hideWiki;
  const adding = state.src.up.map(u => `<li class=adding><span class=spin aria-hidden=true></span><b>${esc(u)}</b></li>`).join('');
  const off = state.src.off, shown = r => !(hide && isWiki(r)) && !off.has(srcType(r));
  const rows = srcRows().filter(shown);
  if (!state.src.sel && rows.length) { state.src.sel = rows[0].slug; const f = rows[0]; if (f.kind !== 'media') catLoad(f.slug); etSourceLoad(f.slug, f.title); }
  const r = srcSel();
  const rail = `<h2>Sources <small>${rows.length || ''}</small></h2>${filterRow(srcRows())}<div class=scroll><ol class="rl srl">${adding}${rows.map(sourceItem).join('')}</ol></div><div class=bay id=bay>${bayHTML()}</div>`;
  const ents = r ? (state.et.src[r.slug] || {}).ents || [] : [];
  const runs = r ? ((state.groups || []).find(x => x.key === 'src:' + r.slug) || {runs: []}).runs : [];
  const tab = state.src.tab;
  const body = !r ? '' : tab === 'found' ? sourceFound(ents) : tab === 'pages' ? sourcePagesTab(r) : tab === 'runs' ? sourceRuns(runs) : sourceCard(r);
  const main = r ? `${sourceHead(r)}${sourceTabs(r, ents, runs)}<div class=stab>${body}</div>` : '';
  const html = `<section class="panel xrail">${rail}</section><section class="panel xmain">${main}</section>`;
  if (box.dataset.k === html) return;
  const keep = document.activeElement && box.contains(document.activeElement) && document.activeElement.matches('input,textarea') ? document.activeElement : null;
  if (keep && (keep.dataset.dirty || keep.closest('#wikif'))) return;      // never redraw under a field being typed in
  const ys = [...box.querySelectorAll('.xrail .scroll, .xmain')].map(x => x.scrollTop);
  box.dataset.k = html;
  box.innerHTML = html;
  [...box.querySelectorAll('.xrail .scroll, .xmain')].forEach((x, i) => { x.scrollTop = ys[i] || 0; });
  applyCss(box);
  modelFit();
}

async function uploadFiles(list) {
  const files = [...list];
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  state.src.up = files.map(f => f.name);
  drawSources();
  try {
    const r = await fetch('/api/sources/upload', {method: 'POST', body: fd, headers: {'X-Apollo-Token': TOKEN}});
    let j = null;
    try { j = await r.json(); } catch (_) { /* no JSON */ }
    if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
    const bad = (j.results || []).filter(x => !x.ok).map(x => `${x.name}: ${x.why}`);
    for (const b of bad) notify('error', b);
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
  }
  state.src.up = [];
  await refreshSources();
  loadConfigs();
}
async function saveSource(slug, fields) {
  try {
    await api(`/api/source/${encodeURIComponent(slug)}`, fields);
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
  }
  await refreshSources();
  loadConfigs();
}
const srcOf = el => { const li = el.closest('.srow'); return li && li.dataset.slug; };
$('#srcpanel').addEventListener('click', async e => {
  const st = e.target.closest('[data-stab]');
  if (st) { state.src.tab = st.dataset.stab; store.set('apollo-srctab', state.src.tab); drawSources(); return; }
  const sft = e.target.closest('[data-sft]');
  if (sft) { state.src.ftype = sft.dataset.sft; drawSources(); return; }
  const cell = e.target.closest('.srcheat .r i[data-p]');
  if (cell && state.src.sel) { const p = Number(cell.dataset.p); state.src.page = state.src.page === p ? null : p; if (state.src.page) sourcePageText(state.src.sel, p); drawSources(); return; }
  const rn = e.target.closest('.stab [data-run]');
  if (rn) { state.page = null; select(rn.dataset.run); layout(); return; }
  const lt = e.target.closest('.srl .lt[data-id]');           // a lit flow: that run, in Runs
  if (lt) { state.page = null; select(lt.dataset.id); layout(); return; }
  const pick = e.target.closest('[data-src-pick]');
  if (pick) { selectSource(pick.dataset.srcPick); return; }
  if (e.target.closest('#pickfiles')) { $('#filein').click(); return; }
  if (e.target.closest('#wikib')) { toggleWiki(); return; }
  if (e.target.closest('#hidewiki')) { toggleHideWiki(); return; }
  const ty = e.target.closest('.stype');
  if (ty) { toggleType(ty.dataset.type); return; }
  const cb = e.target.closest('[data-cat]');
  if (cb && state.src.sel) {
    const a = cb.dataset.cat, slug = state.src.sel;
    if (a === 'start') catStart(slug); else catAct(slug, a);
    return;
  }
  const b = e.target.closest('[data-act]');
  const slug = b && srcOf(b);
  if (!slug) {
    const li = e.target.closest('.srow[data-slug]');       // a click on a source (not on its fields) selects it
    if (li && !e.target.closest('input,select,textarea,button')) selectSource(li.dataset.slug);
    return;
  }
  const row = state.src.data.dropped.find(r => r.slug === slug);
  if (b.dataset.act === 'brief') { state.src.open[slug] = !state.src.open[slug]; state.src.data && drawSources(); }
  else if (b.dataset.act === 'default') { delete state.src.draft[slug]; await saveSource(slug, {brief: ''}); }
  else if (b.dataset.act === 'generate') { generateBrief(slug); }
  else if (b.dataset.act === 'remove') {
    if (!(await confirmBox(`Remove ${row ? row.title : slug}? Its copy in Apollo is deleted; the original is not touched.`, 'Remove'))) return;
    try {
      await api(`/api/source/${encodeURIComponent(slug)}/remove`, {});
    } catch (err) {
      notify('error', `Remove: ${err.message}`);
    }
    await refreshSources();
    loadConfigs();
  }
});
$('#srcpanel').addEventListener('input', e => {
  if (!e.target.dataset.f) return;
  e.target.dataset.dirty = '1';
  if (e.target.dataset.f === 'brief') state.src.draft[srcOf(e.target)] = e.target.value;
});
// Generate: a model drafts a brief from the source's first pages and catalogue record; it goes into the box, and is saved only when the owner leaves the box
async function generateBrief(slug) {
  if (state.src.gen[slug]) return;
  state.src.gen[slug] = true;
  drawSources();
  try {
    const got = await api('/api/brief/generate', {source: slug, model: prefs.agent});
    if (got && got.brief) state.src.draft[slug] = got.brief;
  } catch (e) {
    notify('error', `Brief: ${e.message}`);
  }
  delete state.src.gen[slug];
  drawSources();
}
$('#srcpanel').addEventListener('change', e => {
  if (e.target.id === 'filein') { uploadFiles(e.target.files); e.target.value = ''; return; }
  const f = e.target.dataset.f, slug = srcOf(e.target);
  if (!f || !slug) return;
  delete e.target.dataset.dirty;
  if (f === 'brief') delete state.src.draft[slug];
  saveSource(slug, {[f]: e.target.value});
});
$('#srcpanel').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('input[data-f]')) e.target.blur();
});
// a dropped file anywhere on this view is taken in; anywhere else it is ignored, so the window never navigates to it
for (const ev of ['dragenter', 'dragover']) document.addEventListener(ev, e => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
  e.preventDefault();
  if (state.page === 'sources') $('#srcpanel').classList.add('dragging');
});
document.addEventListener('dragleave', e => { if (!e.relatedTarget) $('#srcpanel').classList.remove('dragging'); });
document.addEventListener('drop', e => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
  e.preventDefault();
  $('#srcpanel').classList.remove('dragging');
  if (state.page === 'sources') uploadFiles(e.dataTransfer.files);
});
function selectSource(slug) {
  if (state.src.sel === slug) return;
  state.src.sel = slug;
  state.src.page = null;
  state.src.ftype = '';
  state.et.sel = null;
  if (state.src.sel) { const r = srcSel(); if (!r || r.kind !== 'media') catLoad(state.src.sel); etSourceLoad(state.src.sel, r && r.title); }
  drawSources();
}
function toggleWiki(open) {
  const w = state.src.wiki;
  w.open = open === undefined ? !w.open : open;
  drawSources();
  if (w.open) { const q = $('#wikif .wq'); if (q) q.focus(); }
}
// Wikipedia: one article, fetched only when Add is pressed. The manager does it with hydra's own text extraction.
$('#srcpanel').addEventListener('input', e => {
  if (e.target.closest('#wikif')) state.src.wiki[e.target.name === 'lang' ? 'lang' : 'q'] = e.target.value;
});
$('#srcpanel').addEventListener('submit', async e => {
  if (!e.target.matches('#wikif')) return;
  e.preventDefault();
  const w = state.src.wiki;
  if (w.busy) return;
  if (!w.q.trim()) { notify('warn', 'Give an article title or its address.'); return; }
  w.busy = true;
  document.activeElement && document.activeElement.blur();
  drawSources();
  try {
    const before = new Set(((state.src.data || {}).dropped || []).map(r => r.slug));
    await api('/api/wikipedia', {lang: w.lang.trim() || 'en', query: w.q.trim()});
    w.q = '';
    w.open = false;
    await refreshSources();
    const added = state.src.data.dropped.find(r => !before.has(r.slug));
    if (added) state.src.sel = added.slug;
    loadConfigs();
  } catch (err) {
    notify('error', err.message);
  }
  w.busy = false;
  drawSources();
});
// a picture that is not there leaves the card without one
$('#srcpanel').addEventListener('error', e => { if (e.target.matches && e.target.matches('img.thumb')) e.target.closest('td').innerHTML = `<span class=glyph aria-hidden=true>${ic('text')}</span>`; }, true);
// a source type shown or hidden; the choice is kept
function toggleType(k) {
  const off = state.src.off;
  if (off.has(k)) off.delete(k); else off.add(k);
  store.set('apollo-srctypes', [...off].join(','));
  const sel = srcSel();
  if (sel && off.has(srcType(sel))) state.src.sel = null;
  drawSources();
}
// Hide Wikipedia: the sources whose publisher is Wikipedia, or whose address is wikipedia.org, leave the list; the choice is kept
function toggleHideWiki() {
  state.src.hideWiki = !state.src.hideWiki;
  store.set('apollo-hidewiki', state.src.hideWiki ? '1' : '0');
  const sel = srcSel();
  if (state.src.hideWiki && sel && isWiki(sel)) state.src.sel = null;
  drawSources();
}

// ---- live edit: one click on a value edits it; Enter or blur saves, Esc leaves it as it was ------------------------------------
// A short value gets an input; a long one (several lines, over 60 characters, or a free-text field) a full-width textarea that grows to
// show the whole value: Enter makes a new line, Ctrl or Cmd+Enter or leaving the box saves, Esc cancels.
const LONG_EDIT = 60;
function startEdit(td) {
  if (td.querySelector('.ined')) return;
  const was = td.dataset.v || '';
  const long = !!td.dataset.long || was.includes('\n') || was.length > LONG_EDIT;
  const ta = document.createElement(long ? 'textarea' : 'input');
  ta.className = 'ined';
  if (long) ta.rows = 1;
  ta.value = was;
  ta.setAttribute('aria-label', td.closest('tr') ? (td.closest('tr').querySelector('th') || td).textContent.trim() : 'Value');
  ta.dataset.dirty = '1';
  let done = false;
  const rj = td.querySelector(':scope > .rjm');               // a waiting value's own minus (several values under one label): kept through the edit
  const fit = () => { if (long) { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; } };
  const leave = save => {
    if (done) return;
    done = true;
    const v = long ? ta.value.split('\n').map(l => l.replace(/\s+/g, ' ').trim()).join('\n').trim() : ta.value.replace(/\s+/g, ' ').trim();
    td.classList.remove('editing');
    const val = save && v !== was ? v : was;
    td.textContent = td.dataset.edit === 'cat' ? catDisp(td.dataset.key, td.dataset.kind, val) : val;
    if (rj) td.prepend(rj);
    if (save && v !== was) commitEdit(td, v);
  };
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (!long || e.ctrlKey || e.metaKey)) { e.preventDefault(); leave(true); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); leave(false); }
  });
  ta.addEventListener('blur', () => leave(true));
  ta.addEventListener('input', fit);
  td.classList.add('editing');
  td.replaceChildren(...(rj ? [rj, ta] : [ta]));
  fit();
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}
async function commitEdit(td, v) {
  const slug = state.src.sel;
  if (!slug) return;
  try {
    if (td.dataset.edit === 'cat') {
      await api('/api/catalogue/edit', {source: slug, index: Number(td.dataset.i), value: v, pending: td.dataset.pend === '1'});
      await catLoad(slug, true);
    } else {
      await api(`/api/source/${encodeURIComponent(slug)}`, {[td.dataset.f]: v});
      await refreshSources();
    }
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
    if (td.dataset.edit === 'cat') await catLoad(slug, true); else await refreshSources();
  }
}
// a waiting record: the minus beside a label rejects that field (again: restores it); Accept keeps the rest
async function rejectField(slug, i, btn) {
  const c = state.cat[slug], it = c && c.pending && c.pending.record[i];
  if (!it) return;
  const el = btn && (btn.closest('.cline') && btn.closest('td') && btn.closest('td').querySelectorAll('.cline').length > 1 ? btn.closest('.cline') : btn.closest('tr'));
  if (el && window.gsap && !REDUCED) await new Promise(done => gsap.to(el, {opacity: 0, height: 0, duration: .28, ease: 'power2.in', onComplete: done}));
  else if (el) el.remove();
  try {
    await api('/api/catalogue/mark', {source: slug, index: i, rejected: !it.rejected});
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
  }
  await catLoad(slug, true);
}
// an accepted value's minus removes it at once (an empty value removes a field)
async function removeField(slug, i) {
  try {
    await api('/api/catalogue/edit', {source: slug, index: i, value: '', pending: false});
  } catch (e) {
    notify('error', `Sources: ${e.message}`);
  }
  await catLoad(slug, true);
}
// "+ Field": the button turns into a field picker and a value box; Enter adds, Esc cancels
function openAdd(btn) {
  const slug = state.src.sel;
  if (!slug) return;
  const opts = JSON.parse(btn.dataset.opts), pend = btn.dataset.add === 'pend';
  const form = document.createElement('form');
  form.className = 'catform';
  form.innerHTML = `<select class=ined aria-label=Field>${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select><input class=ined aria-label=Value autocomplete=off>`;
  const [sel, inp] = form.children;
  let done = false;
  const close = () => { if (!done) { done = true; form.replaceWith(btn); } };
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const v = inp.value.replace(/\s+/g, ' ').trim();
    if (!v) { inp.focus(); return; }
    const [key, kind] = sel.value.split('|');
    try {
      await api('/api/catalogue/add', {source: slug, key, kind, value: v, pending: pend});
    } catch (err) {
      notify('error', `Sources: ${err.message}`);      // the form stays, to correct the text
      inp.focus();
      return;
    }
    done = true;
    await catLoad(slug, true);
  });
  form.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } });
  form.addEventListener('focusout', () => setTimeout(() => { if (!form.contains(document.activeElement)) close(); }, 0));
  btn.replaceWith(form);
  inp.focus();
}
$('#srcpanel').addEventListener('click', e => {
  const del = e.target.closest('[data-del]');
  if (del && state.src.sel) { removeField(state.src.sel, Number(del.dataset.del)); return; }
  const ad = e.target.closest('[data-add]');
  if (ad) { openAdd(ad); return; }
  const rj = e.target.closest('[data-rej]');
  if (rj && state.src.sel) { rejectField(state.src.sel, Number(rj.dataset.rej), rj); return; }
  const td = e.target.closest('[data-edit]');
  if (td && !e.target.closest('.ined')) startEdit(td);
});
$('#srcpanel').addEventListener('keydown', e => {
  const td = e.target.matches && e.target.matches('[data-edit]') ? e.target : null;
  if (td && e.key === 'Enter') { e.preventDefault(); startEdit(td); }
});
