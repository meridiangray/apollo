// The Data tab of a run: a few saved queries and a free SELECT on the DuckDB log (state/apollo.duckdb, runlog.py). The manager
// refuses anything but SELECT and WITH, and opens the log read-only. The saved queries are about the run in hand.
'use strict';
state.data = {sql: '', res: null, err: '', busy: false, run: null, active: null};

const sqlq = s => `'${String(s).replace(/'/g, "''")}'`;
const DATA_QUERIES = [
  ['Slowest pages', id => `SELECT page, seconds, calls, records, status\nFROM pages\nWHERE run_id = ${sqlq(id)}\nORDER BY seconds DESC\nLIMIT 20`],
  ['Calls by model', id => `SELECT model, count(*) AS calls, round(avg(ms)) AS avg_ms, sum(tokens) AS tokens,\n       count(*) FILTER (WHERE NOT ok) AS failed\nFROM model_calls\nWHERE run_id = ${sqlq(id)}\nGROUP BY model\nORDER BY calls DESC`],
  ['Records by type', id => `SELECT schema, count(DISTINCT thing) AS records,\n       count(DISTINCT thing) FILTER (WHERE state = 'cited') AS cited\nFROM found\nWHERE run_id = ${sqlq(id)}\nGROUP BY schema\nORDER BY records DESC`],
  ['Failed calls', id => `SELECT 'hydra' AS kind, clock, tool AS what, ms, error\nFROM hydra_calls WHERE run_id = ${sqlq(id)} AND NOT ok\nUNION ALL\nSELECT 'model', clock, label, ms, why\nFROM model_calls WHERE run_id = ${sqlq(id)} AND NOT ok\nORDER BY clock`],
  ['Tables', () => `SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns\nFROM information_schema.columns\nWHERE table_schema = 'main'\nGROUP BY table_name\nORDER BY table_name`]
];

function dataHTML() {
  const d = state.data;
  const bar = DATA_QUERIES.map(([lab], i) => `<button type=button data-dq="${i}" aria-pressed=${d.active === i}>${esc(lab)}</button>`).join('');
  return `<div class=dtop><div class=lf role=group aria-label="Saved queries">${bar}</div></div>` +
    `<textarea class=dsql rows=7 spellcheck=false aria-label=Query>${esc(d.sql)}</textarea>` +
    `<div class=dgo><button type=button class="btn primary" data-dgo${d.busy ? ' disabled' : ''}>${d.busy ? '<span class=spin aria-hidden=true></span>' : ''}Run</button></div>` +
    `<div class=dres></div>`;
}
function resultHTML(d) {
  if (d.err) return `<p class=derr>${esc(d.err)}</p>`;
  const r = d.res;
  if (!r) return '';
  const num = v => typeof v === 'number';
  const head = r.columns.map((c, i) => `<th${r.rows.length && num(r.rows[0][i]) ? ' class=num' : ''}>${esc(c)}</th>`).join('');
  const rows = r.rows.map(row => `<tr>${row.map(v => `<td${num(v) ? ' class=num' : ''}>${v == null ? '' : esc(num(v) && !Number.isInteger(v) ? String(Math.round(v * 1e4) / 1e4) : String(v))}</td>`).join('')}</tr>`).join('');
  const more = r.truncated ? `<p class=dmore>First ${r.rows.length} rows</p>` : '';
  return `<div class=dwrap><table class=dtab><thead><tr>${head}</tr></thead><tbody>${rows || `<tr><td class=lnone colspan=${r.columns.length || 1}>No rows</td></tr>`}</tbody></table></div>${more}`;
}
function renderData() {
  const body = $('#tabbody'), d = state.data;
  if (body.dataset.run !== `${state.sel}/data`) {
    body.dataset.run = `${state.sel}/data`;
    if (d.run !== state.sel) Object.assign(d, {sql: '', res: null, err: '', active: null, run: state.sel});
    body.innerHTML = dataHTML();
  }
  const res = $('.dres', body), html = resultHTML(d);
  if (res.dataset.k !== html) { res.dataset.k = html; res.innerHTML = html; }
}
async function runData() {
  const d = state.data, ta = $('#tabbody .dsql');
  if (ta) d.sql = ta.value;
  if (!d.sql.trim() || d.busy) return;
  d.busy = true;
  $('#tabbody').dataset.run = '';
  renderData();
  try {
    d.res = await api('/api/logdb', {sql: d.sql});
    d.err = '';
  } catch (e) {
    d.res = null;
    d.err = e.message;
  }
  d.busy = false;
  $('#tabbody').dataset.run = '';
  if (state.tab === 'data') renderData();
}
document.addEventListener('click', e => {
  if (state.tab !== 'data') return;
  const b = e.target.closest('[data-dq]');
  if (b) {
    const i = Number(b.dataset.dq), d = state.data;
    d.active = i;
    d.sql = DATA_QUERIES[i][1](state.sel);
    const ta = $('#tabbody .dsql');
    if (ta) ta.value = d.sql;
    runData();
    return;
  }
  if (e.target.closest('[data-dgo]')) runData();
});
document.addEventListener('keydown', e => {
  if (state.tab === 'data' && e.target.matches('.dsql') && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runData(); }
});
document.addEventListener('input', e => {
  if (e.target.matches('#tabbody .dsql')) { state.data.sql = e.target.value; state.data.active = null; }
});
