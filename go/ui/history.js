// Action History: everything the person or an agent did, newest first, read from the log (apollo.py history): runs started,
// stopped and cleared; tools; changes to the sources; toasts. A chip filters by kind; a row opens the run or the source it concerns.
'use strict';
state.his = {kind: '', data: null, loading: false};
const HIS_KINDS = [['', 'All'], ['run', 'Runs'], ['tool', 'Tools'], ['source', 'Sources'], ['toast', 'Toasts']];
const hisWhen = longWhen;                              // app.js: "November 25, 2026 6:36 PM"
const hisSecs = s => s == null ? '' : s < 1 ? '< 1 s' : s < 60 ? `${Math.round(s)} s` : elapsed(s);
const hisTitle = slug => { const s = (state.configs.sources || []).find(x => x.name === slug); return s ? s.title : slug; };

function openHistory() {
  drawHistory();
  loadHistory();
}
async function loadHistory() {
  const h = state.his, kind = h.kind;
  if (h.loading) return;
  h.loading = true;
  try {
    const got = await api(`/api/history${kind ? `?kind=${kind}` : ''}`);
    if (h.kind === kind) { h.data = got; setError('his', null); }
  } catch (e) {
    setError('his', `History: ${e.message}`);
  }
  h.loading = false;
  if (state.page === 'history') drawHistory();
}
function hisRow(r) {
  const run = r.run && state.byId[r.run] ? r.run : '';
  const target = run ? `data-run="${esc(run)}"` : r.source ? `data-source="${esc(r.source)}"` : '';
  const src = r.source ? `<span class=hissrc>${esc(hisTitle(r.source))}</span>${r.pages ? `<span class=dim> · Pages ${esc(r.pages)}</span>` : ''}` : '';
  const res = r.result ? `<span class="hisres r-${esc(String(r.result).replace(/\s+/g, '-'))}">${esc(r.result)}</span>` : '';
  const why = r.detail && r.kind !== 'toast' ? ` <span class=dim title="${esc(r.detail)}">${esc(String(r.detail).slice(0, 60))}</span>` : '';
  const what = r.kind === 'toast' ? `<span class=histoast>${esc(r.detail || '')}</span>` : esc(r.what);
  return `<tr class="hisrow${target ? ' jump' : ''}" ${target}${target ? ' tabindex=0' : ''}><td class=dim>${esc(hisWhen(r.at))}</td><td>${what}</td><td title="${esc(r.source ? hisTitle(r.source) + (r.pages ? ' · Pages ' + r.pages : '') : '')}">${src}</td>` +
    `<td>${r.model ? modelMark(r.model) : ''}</td><td>${res}${why}</td><td class="num dim">${esc(hisSecs(r.secs))}</td></tr>`;
}
function drawHistory() {
  const box = $('#hispanel');
  if (!box) return;
  const h = state.his, d = h.data;
  const chips = HIS_KINDS.map(([k, w]) => {
    const n = d ? (k ? d.counts[k] : d.total) : '';
    return `<button type=button class=hischip data-hk="${k}" aria-pressed=${h.kind === k}>${w}${n !== '' ? ` <small>${n}</small>` : ''}</button>`;
  }).join('');
  const head = `<div class=cfgh><h2>Action History</h2></div><div class=hischips role=group aria-label=Kind>${chips}</div>`;
  let body = '';
  if (d && !d.rows.length) body = '<p class=none>No actions yet.</p>';
  else if (d) {
    body = `<table class=ctable><thead><tr><th>When</th><th>What</th><th>Source</th><th>Model</th><th>Result</th><th class=num>Duration</th></tr></thead><tbody>${d.rows.map(hisRow).join('')}</tbody></table>`;
  }
  const html = `${head}<div class=cbody>${body}</div>`;
  if (box.dataset.k === html) return;
  box.dataset.k = html;
  box.innerHTML = html;
}
function hisOpen(row) {
  if (row.dataset.run) { state.page = null; select(row.dataset.run); layout(); return; }
  if (row.dataset.source) {
    state.src.sel = row.dataset.source;
    state.page = 'sources';
    layout();
    openSources();
  }
}
$('#hispanel').addEventListener('click', e => {
  const k = e.target.closest('[data-hk]');
  if (k) { state.his.kind = k.dataset.hk; state.his.data = null; drawHistory(); loadHistory(); return; }
  const row = e.target.closest('.hisrow[tabindex]');
  if (row) hisOpen(row);
});
$('#hispanel').addEventListener('keydown', e => {
  const row = e.target.closest('.hisrow[tabindex]');
  if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); hisOpen(row); }
});
setInterval(() => { if (state.page === 'history' && !document.hidden) loadHistory(); }, 3000);
