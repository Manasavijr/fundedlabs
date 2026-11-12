// FundedLabs client: the design prototype's interactions, wired to the FastAPI backend.
// Every number shown comes from an /api/* call; nothing is generated in the browser.

const API = process.env.NEXT_PUBLIC_API || '';

export default function init(){
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const sleep = ms => new Promise(r => setTimeout(r, RM ? 0 : ms));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const D = s => { const [y,m,d] = String(s).slice(0,10).split('-').map(Number); return new Date(Date.UTC(y, m-1, d||1)); };
const mi = s => { const d = D(s); return d.getUTCFullYear()*12 + d.getUTCMonth(); };
const now0 = new Date(); const TODAY = now0.getFullYear()*12 + now0.getMonth();
const TODAY_ISO = `${now0.getFullYear()}-${String(now0.getMonth()+1).padStart(2,'0')}-${String(now0.getDate()).padStart(2,'0')}`;
const fmtM = m => MON[m%12] + ' ' + Math.floor(m/12);
const shortM = m => MON[m%12] + " '" + String(Math.floor(m/12)).slice(2);
const money = n => { n = Number(n)||0; return n >= 1e9 ? '$' + (n/1e9).toFixed(2) + 'B' : n >= 1e6 ? '$' + (n/1e6).toFixed(n >= 1e7 ? 1 : 2) + 'M' : '$' + Math.round(n/1e3) + 'K'; };
const title = s => String(s ?? '').toLowerCase().replace(/(^|[\s\-/(])([a-z])/g, (m,a,b) => a + b.toUpperCase()).replace(/\b(Of|And|For|In|The|At|To)\b/g, w => w.toLowerCase());
const person = s => { s = String(s ?? ''); if(s.includes(',')){ const [l, f] = s.split(','); s = f.trim() + ' ' + l.trim(); } return s === s.toUpperCase() ? title(s) : s; };
const initials = n => person(n).split(/\s+/).filter(Boolean).map(w => w[0]).slice(0,2).join('').toUpperCase();
const inst = n => { const t = title(n); return t.length > 34 ? t.replace('University of California', 'UC').replace('University', 'U.') : t; };

async function api(path, opts){
  const r = await fetch(API + '/api' + path, opts);
  if(!r.ok){ let msg = r.status + ''; try{ msg = (await r.json()).detail || msg; }catch(e){} throw new Error(msg); }
  return r.json();
}
const qs = o => '?' + Object.entries(o).filter(([,v]) => v !== null && v !== undefined && v !== '').map(([k,v]) => k + '=' + encodeURIComponent(v)).join('&');
// Show the statement the API ran with its parameters filled in (display only).
function inline(sql, params){ return String(sql).replace(/%\((\w+)\)s/g, (m,k) => { const v = params?.[k]; return v === null || v === undefined ? 'NULL' : typeof v === 'number' ? String(v) : `'${String(v).replace(/'/g,"''")}'`; }).replace(/%%/g, '%'); }

const STATE_NAME = {AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',CO:'Colorado',CT:'Connecticut',DE:'Delaware',DC:'District of Columbia',FL:'Florida',GA:'Georgia',HI:'Hawaii',ID:'Idaho',IL:'Illinois',IN:'Indiana',IA:'Iowa',KS:'Kansas',KY:'Kentucky',LA:'Louisiana',ME:'Maine',MD:'Maryland',MA:'Massachusetts',MI:'Michigan',MN:'Minnesota',MS:'Mississippi',MO:'Missouri',MT:'Montana',NE:'Nebraska',NV:'Nevada',NH:'New Hampshire',NJ:'New Jersey',NM:'New Mexico',NY:'New York',NC:'North Carolina',ND:'North Dakota',OH:'Ohio',OK:'Oklahoma',OR:'Oregon',PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',SD:'South Dakota',TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',VA:'Virginia',WA:'Washington',WV:'West Virginia',WI:'Wisconsin',WY:'Wyoming'};
const CODE_OF = Object.fromEntries(Object.entries(STATE_NAME).map(([k,v]) => [v,k]));

/* ---------- state ---------- */
const S = { tab:'explore', q:'', agency:'ALL', state:null, insts:new Set(), page:0, per:8,
  list:[], totals:{active_awards:0, active_total:0}, leaders:[], sql:null, ms:null,
  heat:{}, outreach:[], home:null, chips:[] };

/* ---------- tabs ---------- */
const tabs = $$('.tab');
function moveInd(){ const a = tabs.find(t => t.getAttribute('aria-selected') === 'true'); const ind = $('#ind'); if(!a) return; ind.style.left = a.offsetLeft + 10 + 'px'; ind.style.width = (a.offsetWidth - 20) + 'px'; }
const ORDER = ['explore','outreach','pipe','ask'];
function setTab(name, push){
  if(!ORDER.includes(name)) name = 'explore';
  const dir = ORDER.indexOf(name) >= ORDER.indexOf(S.tab) ? 'enter-r' : 'enter-l';
  S.tab = name; document.body.dataset.tab = name;
  tabs.forEach(t => t.setAttribute('aria-selected', t.dataset.tab === name ? 'true' : 'false'));
  $$('.view').forEach(v => { const on = v.dataset.view === name; v.hidden = !on; if(on){ v.classList.remove('enter','enter-r','enter-l'); void v.offsetWidth; v.classList.add(dir); } });
  moveInd();
  requestAnimationFrame(() => $$('.seg').forEach(setSegThumb));
  if(name === 'outreach'){ loadOutreach(); }
  if(name === 'explore') requestAnimationFrame(() => { sizeMap(); renderRows(); });
  if(name === 'pipe') loadPipeline();
  if(push){ try{ history.replaceState(null,'','#'+name); }catch(e){} }
}
tabs.forEach(t => t.addEventListener('click', () => setTab(t.dataset.tab, true)));
addEventListener('resize', () => { moveInd(); $$('.seg').forEach(setSegThumb); sizeMap(); });

function setSegThumb(seg){ const b = $('button[aria-pressed="true"]', seg); const th = $('.thumb', seg); if(!b||!th) return; th.style.left = b.offsetLeft + 'px'; th.style.width = b.offsetWidth + 'px'; }
function segInit(seg, onPick){
  $$('button', seg).forEach(b => b.addEventListener('click', () => {
    $$('button', seg).forEach(x => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    setSegThumb(seg); onPick(b.dataset.v);
  }));
  requestAnimationFrame(() => setSegThumb(seg));
}

$('#theme').addEventListener('click', () => {
  const r = document.documentElement; const cur = r.getAttribute('data-theme');
  const dark = cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  r.setAttribute('data-theme', dark ? 'light' : 'dark');
  try{ localStorage.setItem('fl-theme', dark ? 'light' : 'dark'); }catch(e){}
});
try{ const t = localStorage.getItem('fl-theme'); if(t) document.documentElement.setAttribute('data-theme', t); }catch(e){}

function tween(el, to, fmt){
  const from = Number(el.dataset.v || 0); el.dataset.v = to;
  if(RM){ el.textContent = fmt(to); return; }
  const t0 = performance.now(), d = 600;
  (function step(now){ const k = Math.min(1,(now-t0)/d), e = 1-Math.pow(1-k,3); el.textContent = fmt(from + (to-from)*e); if(k<1) requestAnimationFrame(step); })(t0);
}

/* ---------- toast ---------- */
let toastT;
function showToast(kind, sql){
  const t = $('#toast'), code = $('#toastCode'); $('.tag', t).textContent = kind; clearTimeout(toastT); t.classList.add('show'); code.textContent = '';
  let i = 0; const step = () => { i += 4; code.textContent = sql.slice(0,i); if(i < sql.length) setTimeout(step, RM ? 0 : 10); }; step();
  toastT = setTimeout(() => t.classList.remove('show'), 5200);
}
function fail(e){ showToast('ERROR', String(e.message || e) + '  (is the API running on port 8000?)'); }

/* ---------- explore: filters ---------- */
const uc = $('#uniChips');
async function loadChips(){
  try{
    const r = S.state ? await api(`/states/${S.state}/institutions` + qs({limit:12})) : await api('/institutions/top' + qs({limit:12}));
    S.chips = r.rows;
  }catch(e){ S.chips = []; }
  uc.innerHTML = '';
  S.chips.forEach(c => { const b = document.createElement('button'); b.className = 'chip'; b.textContent = inst(c.name); b.title = `${title(c.name)} · ${c.active_awards} active awards`;
    b.setAttribute('aria-pressed', S.insts.has(c.inst_id) ? 'true' : 'false');
    b.addEventListener('click', () => { S.insts.has(c.inst_id) ? S.insts.delete(c.inst_id) : S.insts.add(c.inst_id); b.setAttribute('aria-pressed', S.insts.has(c.inst_id) ? 'true' : 'false'); refresh(true); });
    uc.appendChild(b); });
}
segInit($('#agencySeg'), v => { S.agency = v; refresh(true); loadHeat(); });
let qTimer; $('#q').addEventListener('input', e => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = e.target.value.trim(); refresh(true); }, 250); });

/* ---------- explore: rows ---------- */
const rowsEl = $('#rows'); const rowMap = new Map();
const RING = 2*Math.PI*15;
function rowState(a){
  const s = mi(a.start_date), e = mi(a.end_date);
  const left = Math.max(0, e - TODAY + 1), tot = e - s + 1;
  const lbl = left <= 0 ? '0' : left < 12 ? String(left) : (left/12).toFixed(left < 60 ? 1 : 0);
  const unit = left < 12 ? 'mo' : 'yr';
  const ys = Math.floor(s/12), ye = Math.floor(e/12), cy = Math.floor(TODAY/12), cells = [];
  for(let y = ys; y <= ye && cells.length < 12; y++) cells.push(y < cy ? 'past' : y === cy ? 'cur' : 'fut');
  return {pct: Math.min(1, left/Math.max(1,tot)), lbl, unit, cells, ys, soon: left < 12};
}
function rowHTML(a){
  const r = rowState(a);
  return `<span class="ring" title="Share of the award still ahead"><svg viewBox="0 0 36 36" aria-hidden="true"><circle class="rt" cx="18" cy="18" r="15"></circle><circle class="rf" cx="18" cy="18" r="15" stroke-dasharray="${RING}" stroke-dashoffset="${RING*(1-r.pct)}"></circle></svg><b class="rl">${r.lbl}<small>${r.unit}</small></b></span>
  <span class="rmain"><span class="t" title="${esc(a.title)}">${esc(a.title)}</span><span class="who">${esc(a.pi_name ? person(a.pi_name) : 'No PI listed')} · ${esc(inst(a.institution))}</span></span>
  <span class="yrs" aria-label="Fiscal years ${r.ys} to ${r.ys + r.cells.length - 1}">${r.cells.map((c,i) => `<i class="${c}" title="FY${r.ys+i}"></i>`).join('')}</span>
  <span class="ramt"><span class="amt">${money(a.total_amount)}</span><span class="ag ${a.agency}">${a.agency}</span></span>`;
}
const SHELL = matchMedia('(min-width:760px) and (min-height:500px)');
function perPage(){ if(!SHELL.matches) return 50; const h = rowsEl.clientHeight; return h ? Math.max(1, Math.floor(h / 59)) : 8; }

let exploreTok = 0;
async function refresh(reset){
  if(reset) S.page = 0;
  const tok = ++exploreTok, per = perPage();
  S.per = per;
  const p = { state:S.state, inst:[...S.insts].join(','), agency:S.agency === 'ALL' ? null : S.agency, q:S.q || null, limit:per, offset:S.page*per };
  let r;
  try{ r = await api('/explore' + qs(p)); }catch(e){ fail(e); return; }
  if(tok !== exploreTok) return;
  S.list = r.awards.rows; S.totals = r.totals.rows[0] || S.totals; S.leaders = r.leaders.rows; S.sql = r.awards; S.ms = r.awards.ms;
  renderRows(reset); renderLeaders(); renderSQL(r.awards);
  const n = Number(S.totals.active_awards)||0;
  (reset ? (el,v,f) => { el.dataset.v = v; el.textContent = f(v); } : tween)($('#cAwards'), n, v => Math.round(v).toLocaleString());
  $('#stCount').textContent = n.toLocaleString();
  tween($('#cMoney'), Number(S.totals.active_total)||0, v => money(v));
  feedFromList();
}
function renderRows(soft){
  const total = Number(S.totals.active_awards)||0, per = S.per, pages = Math.max(1, Math.ceil(total / per));
  const list = S.list;
  $('#pgTxt').textContent = total ? `${(S.page*per + 1).toLocaleString()}–${(S.page*per + list.length).toLocaleString()} of ${total.toLocaleString()}` : '0';
  $('#pgPrev').disabled = S.page === 0; $('#pgNext').disabled = S.page >= pages - 1;
  $('#pager').hidden = pages < 2 && total < 1;
  rowMap.forEach(el => el.remove()); rowMap.clear();
  let emp = $('.empty', rowsEl); if(emp) emp.remove();
  list.forEach(a => { const el = document.createElement('button'); el.className = 'row'; el.innerHTML = rowHTML(a);
    el.addEventListener('click', () => a.pi_id ? openDrawer(a.pi_id) : openAward(a.award_id));
    el.classList.toggle('soon', mi(a.end_date) - TODAY < 12); rowMap.set(a.award_id, el); rowsEl.appendChild(el); });
  if(!list.length){ emp = document.createElement('div'); emp.className = 'empty'; emp.textContent = 'No active awards match these filters. Clear a filter or pick another state.'; rowsEl.appendChild(emp); }
  if(RM) return;
  list.forEach((a,i) => { const el = rowMap.get(a.award_id); el.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'none'}],{duration:280,delay:i*24,easing:'cubic-bezier(.2,.8,.2,1)',fill:'backwards'}); });
}
function flipPage(d){ const pages = Math.ceil((Number(S.totals.active_awards)||0) / S.per); const p = Math.max(0, Math.min(pages - 1, S.page + d)); if(p === S.page) return; S.page = p; refresh(false); }
$('#pgPrev').addEventListener('click', () => flipPage(-1));
$('#pgNext').addEventListener('click', () => flipPage(1));
let wheelT = 0; rowsEl.addEventListener('wheel', e => { if(!SHELL.matches) return; e.preventDefault(); const now = Date.now(); if(now - wheelT < 450 || Math.abs(e.deltaY) < 8) return; wheelT = now; flipPage(e.deltaY > 0 ? 1 : -1); }, {passive:false});
let rzT; addEventListener('resize', () => { clearTimeout(rzT); rzT = setTimeout(() => { if(perPage() !== S.per && S.tab === 'explore') refresh(true); fitKids($('#suggest')); }, 200); });
function fitKids(box){ if(!box) return; const kids = Array.from(box.children); kids.forEach(k => k.hidden = false); if(!SHELL.matches) return; const H = box.clientHeight; kids.forEach(k => { if(k.offsetTop + k.offsetHeight > H + 1) k.hidden = true; }); }

function renderLeaders(){
  const top = S.leaders, max = top.length ? Number(top[0].active_award_total) : 1, box = $('#leaders');
  if(!top.length){ box.innerHTML = '<p class="note" style="margin:0">Nobody matches these filters.</p>'; return; }
  box.innerHTML = top.map(l => `<button class="lead" data-pi="${l.inv_id}"><span class="r"><span>${esc(person(l.full_name))} <span class="note">· ${esc(inst(l.institution))}</span></span><span class="mono">${money(l.active_award_total)}</span></span><span class="b"><i style="width:0"></i></span></button>`).join('');
  $$('.lead', box).forEach((b,i) => { b.addEventListener('click', () => openDrawer(Number(b.dataset.pi))); requestAnimationFrame(() => requestAnimationFrame(() => { $('i', b).style.width = (Number(top[i].active_award_total)/max*100) + '%'; })); });
}
function hlSQL(s){ return esc(s).replace(/'[^']*'/g, m => `<span class="str">${m}</span>`).replace(/\b(SELECT|FROM|JOIN|LEFT JOIN|ON|AND|OR|WHERE|ORDER BY|DESC|LIMIT|OFFSET|IN|GROUP BY|SUM|COUNT|MAX|MIN|AS|MATCH|AGAINST|BETWEEN|INTERVAL|MONTH|LIKE|CONCAT|EXISTS|NOT|COALESCE|DISTINCT|INSERT INTO|VALUES|UPDATE|SET|DELETE)\b/g, '<span class="kw">$1</span>').replace(/(?<![\w'])(\d+)(?![\w'])/g, '<span class="num">$1</span>'); }
let lastSQL = '';
function renderSQL(res){
  const s = inline(res.sql, res.params);
  const box = $('#sqlbox'); box.innerHTML = hlSQL(s);
  if(lastSQL && lastSQL !== s) { box.classList.remove('flashall'); void box.offsetWidth; }
  lastSQL = s;
  $('#access').textContent = `${res.ms} ms`;
  $('#sqlrows').textContent = `${res.rows.length} rows on this page · measured ${res.ms} ms in MySQL`;
}

/* ---------- drawer: professor profile (Q3, Q6, award_funding) ---------- */
const drawer = $('#drawer'), scrim = $('#scrim'); let lastFocus = null;
async function openDrawer(id){
  lastFocus = document.activeElement;
  drawer.innerHTML = `<div class="dcol"><div class="dhead"><div class="avatar">…</div><div><h2 id="dName">Loading</h2></div></div><div class="shimmer"></div></div>`;
  drawer.classList.add('open'); scrim.classList.add('open'); drawer.setAttribute('aria-hidden','false');
  let r; try{ r = await api(`/investigators/${id}`); }catch(e){ closeDrawer(); fail(e); return; }
  const v = r.investigator, awards = r.awards.rows, act = awards.filter(a => a.active);
  const led = act.filter(a => a.role === 'PI');
  const through = act.length ? Math.max(...act.map(a => mi(a.end_date))) : null;
  const fyRows = r.funding_by_year.rows, fy = {}; const y0 = Math.max(2016, Math.floor(TODAY/12) - 9);
  for(let y = y0; y <= Math.floor(TODAY/12); y++) fy[y] = 0;
  fyRows.forEach(x => { if(fy[x.fiscal_year] !== undefined) fy[x.fiscal_year] = Number(x.amount)||0; });
  const maxF = Math.max(1, ...Object.values(fy));
  const coL = r.coinvestigators.rows.slice(0, 8), pubs = r.publications.rows;
  const inO = S.outreach.some(o => o.inv_id === v.inv_id);
  const dept = (awards.find(a => a.program) || {}).program;
  drawer.innerHTML = `<div class="dcol">
   <div class="dhead"><div class="avatar">${initials(v.full_name)}</div><div><h2 id="dName">${esc(person(v.full_name))}</h2><p>${esc(inst(v.institution || ''))}${dept ? ' · ' + esc(title(dept)) : ''}</p></div>
   <button class="iconbtn dclose" id="dClose" aria-label="Close"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"></path></svg></button></div>
   <div class="stats"><div class="stat"><b id="sF">$0</b><span>active as PI</span></div><div class="stat"><b id="sA">0</b><span>active awards</span></div><div class="stat"><b>${through ? shortM(through) : 'None'}</b><span>funded through</span></div></div>
   <button class="cta ${inO?'done':''}" id="addO">${inO ? 'In your outreach list' : 'Add to outreach'}</button>
   <div class="sec"><h3>Funding by fiscal year <small>award_funding · as PI</small></h3>
     <div class="fy">${Object.entries(fy).map(([y,val]) => `<div class="${Number(y)===Math.floor(TODAY/12)?'cur':''}" title="FY${y}: ${money(val)}"><i data-h="${val/maxF*100}"></i></div>`).join('')}</div>
     <div class="fyl">${Object.keys(fy).map(y => `<span>${String(y).slice(2)}</span>`).join('')}</div></div>
   <div class="sec"><h3>Awards <small>${awards.length} total</small></h3>${awards.slice(0,6).map(a => `<div class="pub"><span class="y">${String(a.end_date).slice(0,4)}</span><span>${esc(a.title)}<small>${a.agency} · ${a.role === 'PI' ? 'PI' : 'Co-PI'} · ${money(a.total_amount)} · ${a.active ? 'active' : 'ended'}</small></span></div>`).join('')}</div>
   </div><div class="dcol">
   <div class="sec"><h3>Co-leads awards with <small>Q6 self-join</small></h3>
     ${coL.length ? `<svg id="orbit" viewBox="0 0 320 200" width="100%" aria-hidden="true"></svg><div class="colist">${coL.map(c => `<button data-pi="${c.inv_id}">${esc(person(c.full_name))} · ${c.shared_awards}</button>`).join('')}</div>` : '<p class="note">No co-investigators on record.</p>'}</div>
   <div class="sec"><h3>Publications from these awards <small>Q3 · last 3 years</small></h3>
     ${pubs.length ? pubs.slice(0,8).map(p => `<div class="pub"><span class="y">${p.pub_year ?? ''}</span><span>${esc(p.title || 'Untitled')}<small>${esc(p.journal || '')}${p.pmid ? ' · PMID ' + p.pmid : ''}</small></span></div>`).join('') + (pubs.length > 8 ? `<p class="note">and ${pubs.length - 8} more</p>` : '') : '<p class="note">No linked publications in the last 3 years.</p>'}</div>
   <p class="note">Queries: ${r.publications.ms} ms (Q3), ${r.coinvestigators.ms} ms (Q6), ${r.awards.ms} ms (awards).</p></div>`;
  setTimeout(() => { const c = $('#dClose'); if(c) c.focus(); }, 50);
  $('#dClose').onclick = closeDrawer;
  tween($('#sF'), led.reduce((s,a) => s + (Number(a.total_amount)||0), 0), x => money(x));
  tween($('#sA'), act.length, x => String(Math.round(x)));
  setTimeout(() => $$('.fy i', drawer).forEach((el,i) => setTimeout(() => el.style.height = el.dataset.h + '%', i*35)), 200);
  $$('.colist button', drawer).forEach(b => b.onclick = () => openDrawer(Number(b.dataset.pi)));
  if(coL.length) drawOrbit(v.full_name, coL);
  const best = led.sort((a,b) => mi(b.end_date) - mi(a.end_date))[0] || act[0] || awards[0];
  $('#addO').onclick = e => addOutreach(v.inv_id, person(v.full_name), best ? best.award_id : null, e.currentTarget);
}
async function openAward(id){
  try{ const r = await api(`/awards/${id}`); const pi = r.people.rows.find(p => p.role === 'PI') || r.people.rows[0]; if(pi) openDrawer(pi.inv_id); else showToast('AWARD', r.award.title); }catch(e){ fail(e); }
}
function drawOrbit(me, coL){
  const svg = $('#orbit'), cx = 160, cy = 100, R = 72; let s = '';
  const pts = coL.map((c,i) => { const ang = -Math.PI/2 + i*(2*Math.PI/coL.length); return [c, cx + R*1.45*Math.cos(ang), cy + R*Math.sin(ang)]; });
  pts.forEach(([c,x,y]) => { const len = Math.hypot(x-cx,y-cy); s += `<line class="ln" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke-dasharray="${len}" stroke-dashoffset="${len}"><animate attributeName="stroke-dashoffset" from="${len}" to="0" dur="${RM?'0.01':'0.6'}s" fill="freeze"></animate></line>`; });
  pts.forEach(([c,x,y],i) => { s += `<g style="opacity:0;transition:opacity .4s ${0.25+i*0.1}s" class="og"><circle class="nd" cx="${x}" cy="${y}" r="20"></circle><text x="${x}" y="${y+4}" text-anchor="middle">${esc(initials(c.full_name))}</text></g>`; });
  s += `<circle class="nd me" cx="${cx}" cy="${cy}" r="26"></circle><text class="me" x="${cx}" y="${cy+4}" text-anchor="middle">${esc(initials(me))}</text>`;
  svg.innerHTML = s; requestAnimationFrame(() => requestAnimationFrame(() => $$('.og', svg).forEach(g => g.style.opacity = 1)));
}
function closeDrawer(){ drawer.classList.remove('open'); scrim.classList.remove('open'); drawer.setAttribute('aria-hidden','true'); if(lastFocus && lastFocus.focus) lastFocus.focus(); }
scrim.addEventListener('click', closeDrawer);
addEventListener('keydown', e => { if(e.key === 'Escape' && drawer.classList.contains('open')) closeDrawer(); });

/* ---------- outreach (Q8: INSERT, UPDATE, read) ---------- */
async function addOutreach(invId, name, awardId, btn){
  if(S.outreach.some(o => o.inv_id === invId)){ closeDrawer(); setTab('outreach', true); return; }
  let r; try{ r = await api('/outreach', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inv_id:invId, award_id:awardId, status:'to_contact'})}); }catch(e){ fail(e); return; }
  S.fresh = r.lastrowid;
  btn.classList.add('done'); btn.textContent = 'Added to outreach';
  const badge = $('#obadge'), a = btn.getBoundingClientRect(), b = badge.getBoundingClientRect();
  const f = document.createElement('div'); f.className = 'flyer'; f.textContent = '+ ' + name; document.body.appendChild(f);
  f.style.left = (a.left + a.width/2 - 60) + 'px'; f.style.top = a.top + 'px';
  const anim = f.animate([{transform:'translate(0,0) scale(1)',opacity:1},{transform:`translate(${b.left - a.left - a.width/2 + 60}px,${b.top - a.top}px) scale(.35)`,opacity:.2}],{duration:RM?1:750,easing:'cubic-bezier(.5,0,.2,1)'});
  anim.onfinish = () => { f.remove(); loadOutreach(); badge.classList.remove('bump'); void badge.offsetWidth; badge.classList.add('bump'); };
  showToast('INSERT', inline(r.sql, r.params) + `;  -- ${r.ms} ms, outreach_id ${r.lastrowid}`);
}
const COLS = [['to_contact','To contact'],['emailed','Emailed'],['replied','Replied'],['meeting','Meeting'],['offer','Offer'],['declined','Declined']];
const STEPS = COLS.slice(0,5);
const NEXT = {to_contact:'Send the first email', emailed:'Follow up', replied:'Book a meeting', meeting:'Send a thank-you note', offer:'Reply to the offer', declined:'Closed'};
async function loadOutreach(moved){
  try{ const r = await api('/outreach'); S.outreach = r.rows; S.outreachMs = r.ms; }catch(e){ fail(e); return; }
  renderBoard(moved);
}
function renderBoard(moved){
  const O = S.outreach, live = O.filter(o => o.status !== 'declined');
  const cnt = COLS.map(([k]) => O.filter(o => o.status === k).length);
  $('#funnel').innerHTML = COLS.map(([k,l],i) => `<span class="fs ${cnt[i] ? '' : 'zero'}"><i class="st${i}"></i><b>${cnt[i]}</b>${l}</span>`).join('');
  const fb = $('#fbar'); if(!fb.children.length) fb.innerHTML = COLS.map((c,i) => `<i class="st${i}"></i>`).join('');
  $$('i', fb).forEach((el,i) => el.style.flexGrow = cnt[i]);
  $('#board').innerHTML = O.map(o => trailHTML(o)).join('') || '<p class="empty">Nobody yet. Open a professor and press Add to outreach, or add someone below.</p>';
  const due = O.filter(o => Number(o.follow_up_due)).length;
  $('#dueTxt').textContent = due + (due===1 ? ' follow-up due' : ' follow-ups due');
  $('#obadge').textContent = live.filter(o => o.status !== 'offer').length;
  renderSuggest();
  $$('.trail', $('#board')).forEach(row => { const o = O.find(x => x.outreach_id == row.dataset.id);
    if(o.outreach_id === S.fresh || o.outreach_id === moved){ row.classList.add('pop'); S.fresh = null; }
    $$('.step', row).forEach(b => b.addEventListener('click', () => moveCard(o, b.dataset.st)));
    const x = $('.decl', row); if(x) x.addEventListener('click', () => moveCard(o, o.status === 'declined' ? 'to_contact' : 'declined'));
    $('.who', row).addEventListener('click', () => openDrawer(o.inv_id)); });
}
async function renderSuggest(){
  const box = $('#suggest');
  if(!S.home){ box.innerHTML = '<p class="note" style="margin:0">Loading…</p>'; return; }
  let r; try{ r = await api('/outreach/suggest' + qs({inst_id:S.home.inst_id})); }catch(e){ box.innerHTML = ''; return; }
  $('#sugNote').textContent = `${inst(S.home.name)} · active funding, longest runway first · Add runs an INSERT · ${r.ms} ms`;
  box.innerHTML = r.rows.map(p => { const e = mi(p.funded_to), s = mi(p.since), pct = Math.min(1, (e - TODAY + 1)/Math.max(1, e - s + 1));
    return `<div class="pc"><button class="who" type="button" data-pi="${p.inv_id}"><span class="av">${esc(initials(p.full_name))}</span><span class="nm"><b>${esc(person(p.full_name))}</b><small>${p.active_awards} active · ${money(p.active_total)}</small></span></button>
      <span class="fund"><span class="mring" style="--p:${pct}"></span>to ${shortM(e)}</span><button class="add" type="button" data-pi="${p.inv_id}" data-aw="${p.award_id}" data-nm="${esc(person(p.full_name))}">Add</button></div>`; }).join('') || '<p class="note" style="margin:0">Everyone funded here is already on your list.</p>';
  $$('.who', box).forEach(b => b.addEventListener('click', () => openDrawer(Number(b.dataset.pi))));
  $$('.add', box).forEach(b => b.addEventListener('click', () => addOutreach(Number(b.dataset.pi), b.dataset.nm, Number(b.dataset.aw), b)));
  requestAnimationFrame(() => fitKids(box));
}
function trailHTML(o){
  const cur = STEPS.findIndex(s => s[0] === o.status), dec = o.status === 'declined';
  const prog = dec ? 0 : cur/(STEPS.length-1)*100, due = Number(o.follow_up_due);
  const fu = o.follow_up_on ? shortM(mi(o.follow_up_on)).replace(" '", ' ') + ' ' + Number(String(o.follow_up_on).slice(8,10)) : '';
  return `<article class="trail ${dec ? 'dead' : ''}" data-id="${o.outreach_id}">
    <button class="who" type="button"><span class="av">${esc(initials(o.full_name))}</span><span class="nm"><b>${esc(person(o.full_name))}</b><small>${esc(inst(o.institution || ''))}${o.notes ? ' · ' + esc(o.notes) : ''}</small></span></button>
    <div class="steps" role="group" aria-label="Status for ${esc(person(o.full_name))}"><span class="line"><i style="width:${prog}%"></i></span>
      ${STEPS.map(([k,l],i) => `<button class="step ${!dec && i < cur ? 'done' : ''} ${!dec && i === cur ? 'cur' : ''}" type="button" data-st="${k}" aria-pressed="${!dec && i === cur}" aria-label="Mark as ${l}"><span class="dot"></span><span class="lb">${l}</span></button>`).join('')}
    </div>
    <div class="nxt">${dec ? '<span class="tagd">Declined</span>' : due ? `<span class="fu">Follow up was due ${MON[mi(o.follow_up_on)%12]} ${Number(String(o.follow_up_on).slice(8,10))}</span>` : `<span class="na">${NEXT[o.status]}${o.follow_up_on && o.status !== 'to_contact' ? ' by ' + MON[mi(o.follow_up_on)%12] + ' ' + Number(String(o.follow_up_on).slice(8,10)) : ''}</span>`}
      <span class="fund"><span class="mring" style="--p:${Math.min(1, (Number(o.active_awards)||0)/3)}"></span>${Number(o.active_awards) ? o.active_awards + ' active · ' + money(o.active_award_total) : 'no active awards'}</span></div>
    <button class="decl" type="button" aria-label="${dec ? 'Reopen' : 'Mark declined'}">${dec ? 'Reopen' : 'Declined'}</button>
  </article>`;
}
async function moveCard(o, st){
  if(!o || o.status === st) return;
  const body = {status:st};
  if(st === 'emailed'){ const f = new Date(); f.setDate(f.getDate() + 7); body.sent_on = TODAY_ISO; body.follow_up_on = f.toISOString().slice(0,10); }
  let r; try{ r = await api(`/outreach/${o.outreach_id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); }catch(e){ fail(e); return; }
  showToast('UPDATE', inline(r.sql, r.params) + `;  -- ${r.ms} ms, ${r.rowcount} row`);
  loadOutreach(o.outreach_id);
}

/* ---------- pipeline: real ingest_run / ingest_step ---------- */
const STAGES = [
  ['nih','Parse NIH','ExPORTER projects, links, publications','M5 4h14v16H5zM9 9h6M9 13h6M9 17h3'],
  ['nsf','Parse NSF','Award files into the same shape','M5 4h14v16H5zM9 9h6M9 13h6M9 17h3'],
  ['cle','Clean and merge','Yearly NIH rows become one award','M4 7h10M4 12h16M4 17h7M17 4v6M14 7h6'],
  ['mat','Match people','Name plus institution','M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3 2-5 5-5s5 2 5 5m-2-5c1-.6 2-1 3-1 3 0 5 2 5 5'],
  ['loa','Load','LOAD DATA, then ANALYZE','M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3']
];
const stageOf = step => { const k = step.slice(0,3); return k === 'wri' ? 'cle' : k; };
let PIPE = null, pipeTok = 0;
async function loadPipeline(runId){
  try{ PIPE = await api('/pipeline' + qs({run_id:runId})); }catch(e){ fail(e); return; }
  const seg = $('#runSeg');
  if(!runId){
    seg.innerHTML = '<span class="thumb"></span>' + PIPE.runs.rows.slice(0,3).map((r,i) => `<button data-v="${r.run_id}" aria-pressed="${i===0}">Run ${r.run_id} · ${esc(r.source)}</button>`).join('');
    segInit(seg, v => { pipeTok++; loadPipeline(Number(v)); });
  }
  renderStages();
  const run = PIPE.runs.rows.find(r => r.run_id === PIPE.run_id);
  $('#log').innerHTML = run ? `<div class="cm">ingest_run ${run.run_id}: ${esc(run.source)}, ${esc(run.status)}, started ${esc(run.started_at)}, ${run.seconds ?? '?'} s, ${run.steps} steps.</div><div class="cm">Press Replay run to stream its logged steps.</div>` : '<div class="cm">No runs logged yet. Run python ingest/pipeline.py load.</div>';
  $('#itab').innerHTML = PIPE.steps.rows.map((s,i) => `<tr id="it-${i}"><td>${esc(s.step)}</td><td><span class="ist done">done</span></td><td class="n">${s.rows_in == null ? '—' : Number(s.rows_in).toLocaleString()}</td><td class="n">${s.rows_out == null ? '—' : Number(s.rows_out).toLocaleString()}</td><td class="n">${s.duration_ms == null ? '—' : (s.duration_ms >= 1000 ? (s.duration_ms/1000).toFixed(1) + ' s' : s.duration_ms + ' ms')}</td></tr>`).join('');
  $('#itabNote').textContent = `Read back from ingest_step in ${PIPE.steps.ms} ms.`;
}
function renderStages(){
  const steps = PIPE ? PIPE.steps.rows : [];
  $('#stages').innerHTML = STAGES.map(([k,n,d,ic],i) => { const mine = steps.filter(s => stageOf(s.step) === k), out = mine.reduce((a,s) => a + (Number(s.rows_out)||0), 0);
    return `${i ? '<span class="conn" id="cn-'+i+'"><i></i><i></i><i></i></span>' : ''}<div class="stage" id="st-${i}"><span class="ic"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ic}"></path></svg></span><span class="sx"><b>${n}</b><small>${d}</small></span><span class="st">${mine.length ? mine.length + ' steps · ' + out.toLocaleString() + ' rows out' : 'not logged'}</span></div>`; }).join('');
}
$('#replay').addEventListener('click', async () => {
  if(!PIPE) return;
  const tok = ++pipeTok, ok = () => tok === pipeTok; renderStages(); const log = $('#log'); log.innerHTML = '';
  const run = PIPE.runs.rows.find(r => r.run_id === PIPE.run_id), steps = PIPE.steps.rows;
  log.insertAdjacentHTML('beforeend', `<div class="cm">-- ingest_run ${run.run_id} (${esc(run.source)}), replayed from the log</div>`);
  for(let i=0; i<STAGES.length && ok(); i++){
    const st = $('#st-'+i); st.classList.add('running'); if(i) $('#cn-'+i).classList.add('flow');
    for(const s of steps.filter(s => stageOf(s.step) === STAGES[i][0])){ await sleep(380); if(!ok()) return;
      log.insertAdjacentHTML('beforeend', `<div><span class="cm">[${esc(s.step)}]</span> ${s.rows_in == null ? '' : Number(s.rows_in).toLocaleString() + ' in → '}${s.rows_out == null ? '' : Number(s.rows_out).toLocaleString() + ' out'} · ${s.duration_ms ?? '?'} ms</div>`); log.scrollTop = log.scrollHeight; }
    await sleep(260); if(!ok()) return;
    st.classList.remove('running'); st.classList.add('done'); if(i) $('#cn-'+i).classList.remove('flow');
  }
  if(ok()){ log.insertAdjacentHTML('beforeend', `<div class="ok">run ${run.run_id} · status = ${esc(run.status)}</div>`); log.scrollTop = log.scrollHeight; }
});

/* ---------- ask: Q1-Q8 with SQL, rows, time and plan ---------- */
let QS = [];
function askParams(q){
  const p = {};
  if(q.params.includes('inst_id')) p.inst_id = S.home && S.home.inst_id;
  if(q.params.includes('inv_id')) p.inv_id = S.homePI && S.homePI.inv_id;
  return p;
}
function phrase(q){
  const u = S.home ? inst(S.home.name) : 'your university', pi = S.homePI ? person(S.homePI.full_name) : 'a professor';
  return {Q1:`Which awards are active at ${u}, ending latest first?`, Q2:`Who holds the most active funding at ${u}?`, Q3:`What has ${pi} published from their awards lately?`,
    Q4:'How has machine learning funding changed by year and agency?', Q5:'Which awards end in the next 12 months?', Q6:`Who does ${pi} co-lead awards with?`,
    Q7:'What are the 50 largest National Cancer Institute awards?', Q8:'Whom have I contacted, and who needs a follow-up?'}[q.id] || q.question;
}
async function loadAsk(){
  try{ QS = await api('/ask'); }catch(e){ return; }
  $('#sugg').innerHTML = QS.map((x,i) => `<button data-i="${i}" title="${esc(x.concepts)}">${x.id} · ${esc(phrase(x))}</button>`).join('');
  $$('#sugg button').forEach(b => b.addEventListener('click', () => ask(phrase(QS[b.dataset.i]), QS[b.dataset.i])));
  $('#askCtx').textContent = S.home ? `Using ${inst(S.home.name)}${S.homePI ? ' and ' + person(S.homePI.full_name) : ''} as the example university and professor.` : '';
}
$('#askForm').addEventListener('submit', e => { e.preventDefault(); const v = $('#askIn').value.trim(); if(!v) return; $('#askIn').value = '';
  const l = v.toLowerCase(); const id = /co-?lead|collab|together|co-?invest|co-?pi/.test(l) ? 'Q6' : /publi|paper/.test(l) ? 'Q3' : /largest|biggest|top.*award|cancer/.test(l) ? 'Q7' : /topic|trend|by year|over time|machine learning/.test(l) ? 'Q4' : /contact|follow|outreach/.test(l) ? 'Q8' : /end|ending|expir|months|soon/.test(l) ? 'Q5' : /most|who.*funding|rank|top.*(people|investigator|professor)/.test(l) ? 'Q2' : /active|award/.test(l) ? 'Q1' : null;
  ask(v, QS.find(q => q.id === id)); });
let askBusy = false;
async function ask(text, item, analyze){
  if(askBusy) return; askBusy = true; const th = $('#thread'); th.innerHTML = '';
  th.insertAdjacentHTML('beforeend', `<div class="bub">${esc(text)}</div>`);
  const ans = document.createElement('section'); ans.className = 'sql ans'; ans.innerHTML = `<div class="sql-head"><strong>${item ? item.id : 'No match'}</strong></div><div class="shimmer"></div>`; th.appendChild(ans);
  if(!item){ ans.innerHTML = `<div class="sql-head"><strong>No matching workload query</strong></div><pre class="code">This tab answers with the eight workload queries (Q1 to Q8). Try one of the questions below, or ask about active awards, top funding, publications, co-leads, topics, awards ending soon, the largest awards, or your outreach.</pre>`; askBusy = false; return; }
  let r; try{ r = await api(`/ask/${item.id}` + qs({...askParams(item), analyze: analyze ? 'true' : null})); }catch(e){ ans.innerHTML = `<div class="sql-head"><strong>Error</strong></div><pre class="code">${esc(e.message)}</pre>`; askBusy = false; return; }
  const sql = inline(r.sql, r.params);
  ans.innerHTML = `<div class="sql-head"><strong>${item.id} · SQL from the workload</strong><span class="tag">${esc(item.concepts)}</span></div><pre class="code"></pre><div class="guards"></div>`;
  const pre = $('pre', ans);
  for(let i=0; i<=sql.length; i+=6){ pre.innerHTML = hlSQL(sql.slice(0,i)); await sleep(8); }
  pre.innerHTML = hlSQL(sql);
  const g = $('.guards', ans);
  for(const t of [`${r.rows.length} rows`, `${r.ms} ms measured`, 'read-only query']){ g.insertAdjacentHTML('beforeend', `<span class="guard">${esc(t)}</span>`); await sleep(140); }
  const cols = r.rows.length ? Object.keys(r.rows[0]).filter(c => !/^(inv_id|award_id|pub_id|outreach_id|inst_id)$/.test(c)) : [];
  const fmt = (k,v) => v == null ? '' : /amount|total|funding/.test(k) && typeof v === 'number' ? money(v) : /name/.test(k) ? person(v) : String(v);
  const res = document.createElement('div'); res.className = 'res';
  res.innerHTML = `<table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${r.rows.slice(0,25).map((row,i) => `<tr style="animation-delay:${i*45}ms">${cols.map(c => `<td class="${typeof row[c] === 'number' ? 'n' : ''}">${esc(fmt(c,row[c]))}</td>`).join('')}</tr>`).join('') || `<tr><td>No rows.</td></tr>`}</tbody></table><div class="foot">${r.rows.length > 25 ? `Showing 25 of ${r.rows.length} rows. ` : ''}</div>`;
  ans.appendChild(res);
  const plan = document.createElement('div'); plan.className = 'sql';
  plan.innerHTML = `<div class="sql-head"><strong>${esc(r.plan_kind)}</strong><button class="run" type="button" id="anBtn" style="height:28px;padding:0 10px;font-size:12px">${analyze ? 'Show EXPLAIN' : 'Run EXPLAIN ANALYZE'}</button></div><pre class="code">${esc(r.plan)}</pre>`;
  ans.appendChild(plan);
  res.scrollIntoView({behavior: RM ? 'auto' : 'smooth', block:'nearest'});
  $('#anBtn').onclick = () => { askBusy = false; ask(text, item, !analyze); };
  askBusy = false;
}

/* ---------- live US map: real awards arc from NIH/NSF to their institution's state ---------- */
const mcv = $('#usmap'), mctx = mcv.getContext('2d');
const BB = {x:-60, y:8, w:1020, h:604};
let MAPD = null, STATES = [], NATION = null;
const MP = {w:0, h:0, s:1, ox:0, oy:0, events:[], rings:[], col:{}, hover:null, base:null, dpr:1};
const KNOWN = {'UNIVERSITY OF SOUTHERN CALIFORNIA':'USC','UNIVERSITY OF CALIFORNIA LOS ANGELES':'UCLA','CALIFORNIA INSTITUTE OF TECHNOLOGY':'Caltech','STANFORD UNIVERSITY':'Stanford','UNIVERSITY OF WASHINGTON':'U Washington','UNIVERSITY OF COLORADO':'CU Boulder','UNIVERSITY OF TEXAS AT AUSTIN':'UT Austin','UNIVERSITY OF WISCONSIN-MADISON':'Wisconsin','UNIVERSITY OF MICHIGAN AT ANN ARBOR':'Michigan','GEORGIA INSTITUTE OF TECHNOLOGY':'Georgia Tech','JOHNS HOPKINS UNIVERSITY':'Johns Hopkins','MASSACHUSETTS INSTITUTE OF TECHNOLOGY':'MIT'};
const posCache = new Map();
// A campus dot: known coordinates for a few universities, otherwise a stable point inside the state.
function campusPos(a){
  const key = a.inst_id; if(posCache.has(key)) return posCache.get(key);
  const k = KNOWN[String(a.institution).toUpperCase()]; let p = null;
  if(k && MAPD.campus[k]) p = MAPD.campus[k];
  const st = STATES.find(s => s.code === a.state);
  if(!p && st){ let seed = key * 9301 + 49297;
    const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
    for(let t = 0; t < 12 && !p; t++){ const x = st.c[0] + (rnd()-.5)*60, y = st.c[1] + (rnd()-.5)*40; mctx.setTransform(1,0,0,1,0,0); if(mctx.isPointInPath(st.p, x, y)) p = [x,y]; }
    if(!p) p = st.c; }
  posCache.set(key, p); return p;
}
function readCols(){ const cs = getComputedStyle(document.documentElement), g = n => cs.getPropertyValue(n).trim();
  MP.col = {land:g('--map-land'), line:g('--map-line'), dot:g('--map-dot'), heat:g('--accent'), flash:g('--ochre'), NIH:g('--arc-nih'), NSF:g('--arc-nsf'), campus:g('--campus'), label:g('--map-label'), hover:g('--ink')}; buildBase(); }
function sizeMap(){ if(!MAPD) return; const r = mcv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1); if(!r.width) return;
  mcv.width = r.width*dpr; mcv.height = r.height*dpr; MP.dpr = dpr; MP.w = r.width; MP.h = r.height;
  const I = mapInsets(r), aw = r.width - I.l - I.r, ah = r.height - I.t - I.b;
  MP.s = Math.min(aw/BB.w, ah/BB.h); MP.ox = I.l + (aw - BB.w*MP.s)/2 - BB.x*MP.s; MP.oy = I.t + (ah - BB.h*MP.s)/2 - BB.y*MP.s; buildBase(); }
function mapInsets(r){
  const I = {l:0, t:0, r:0, b:0}; if(!SHELL.matches) return I;
  const side = $('.xside').getBoundingClientRect(), head = $('.xhead').getBoundingClientRect();
  const closed = $('#v-explore').classList.contains('xclosed');
  I.l = 10; I.r = closed ? 10 : Math.max(0, r.right - side.left) + 6; I.t = Math.max(0, head.bottom - r.top) + 10; I.b = 70;
  return I;
}
const toScr = p => [MP.ox + p[0]*MP.s, MP.oy + p[1]*MP.s];
function buildBase(){
  if(!MP.w || !MAPD) return; const off = document.createElement('canvas'); off.width = mcv.width; off.height = mcv.height; const c = off.getContext('2d'), C = MP.col;
  c.setTransform(MP.dpr,0,0,MP.dpr,0,0); c.save(); c.translate(MP.ox, MP.oy); c.scale(MP.s, MP.s);
  c.fillStyle = C.land; STATES.forEach(st => c.fill(st.p));
  c.save(); c.clip(NATION); c.fillStyle = C.dot; for(let y=BB.y; y<BB.y+BB.h; y+=7) for(let x=BB.x + ((y/7)%2 ? 3.5 : 0); x<BB.x+BB.w; x+=7) c.fillRect(x, y, 1.3, 1.3); c.restore();
  c.strokeStyle = C.line; c.lineWidth = 1.2/MP.s; STATES.forEach(st => c.stroke(st.p));
  c.restore(); MP.base = off;
}
let HEAT = {}, HEAT_SHOW = {};
async function loadHeat(){
  try{ const r = await api('/states'); HEAT = {}; r.rows.forEach(s => { const v = S.agency === 'NIH' ? s.nih_awards : S.agency === 'NSF' ? s.nsf_awards : s.active_awards; HEAT[STATE_NAME[s.state]] = {n:Number(v)||0, total:Number(s.active_total)||0, all:s}; }); }catch(e){}
}
function bez(a, b, t, lift){ const mx = (a[0]+b[0])/2, my = (a[1]+b[1])/2 - lift; const u = 1-t; return [u*u*a[0] + 2*u*t*mx + t*t*b[0], u*u*a[1] + 2*u*t*my + t*t*b[1]]; }
function drawMap(now){
  const c = mctx, C = MP.col; if(!MP.base) return;
  c.setTransform(1,0,0,1,0,0); c.clearRect(0,0,mcv.width,mcv.height); c.drawImage(MP.base, 0, 0);
  c.setTransform(MP.dpr,0,0,MP.dpr,0,0);
  const vals = Object.values(HEAT).map(h => Math.sqrt(h.n)), tmax = Math.max(1, ...vals);
  STATES.forEach(st => { const tg = Math.sqrt((HEAT[st.name]||{}).n||0)/tmax, cur = HEAT_SHOW[st.name] ?? 0; HEAT_SHOW[st.name] = RM ? tg : cur + (tg - cur)*0.08; });
  c.save(); c.translate(MP.ox, MP.oy); c.scale(MP.s, MP.s);
  STATES.forEach(st => { const f = Math.max(0, 1 - (now - st.flash)/1600);
    const hv = HEAT_SHOW[st.name] || 0; if(hv > 0.004){ c.globalAlpha = 0.08 + 0.6*hv; c.fillStyle = C.heat; c.fill(st.p); }
    if(f > 0){ c.globalAlpha = 0.5*f; c.fillStyle = C.flash; c.fill(st.p); }
    if(MP.hover === st || S.state === st.code){ c.globalAlpha = 1; c.strokeStyle = C.hover; c.lineWidth = 2/MP.s; c.stroke(st.p); } });
  c.restore(); c.globalAlpha = 1;
  for(const e of MP.events){ const k = Math.min(1, (now - e.t0)/e.dur), a = toScr(e.from), b = toScr(e.to), lift = Math.hypot(b[0]-a[0], b[1]-a[1])*0.28;
    c.strokeStyle = e.color; c.lineWidth = e.ex ? 1.4 : 1; c.globalAlpha = (e.ex ? .4 : .16) * (k < 1 ? 1 : Math.max(0, 1 - (now - e.t0 - e.dur)/900));
    c.beginPath(); c.moveTo(a[0],a[1]); c.quadraticCurveTo((a[0]+b[0])/2, (a[1]+b[1])/2 - lift, b[0], b[1]); c.stroke();
    if(k < 1){ const N = 14; for(let i=0; i<N; i++){ const t1 = Math.max(0, k - (N-i)*0.012), t2 = Math.max(0, k - (N-i-1)*0.012); const p = bez(a,b,t1,lift), q = bez(a,b,t2,lift);
        c.globalAlpha = (i+1)/N * (e.ex ? 1 : .5); c.lineWidth = e.ex ? 2.8 : 1.6; c.beginPath(); c.moveTo(p[0],p[1]); c.lineTo(q[0],q[1]); c.stroke(); }
      const h = bez(a,b,k,lift); c.globalAlpha = 1; c.fillStyle = e.color; c.beginPath(); c.arc(h[0], h[1], e.ex ? 3.4 : 2.2, 0, 7); c.fill(); }
    if(k >= 1 && !e.landed){ e.landed = true; MP.rings.push({at:e.to, t0:now, ex:e.ex, color:e.color, name:e.name}); const st = STATES.find(s => s.code === e.state); if(st) st.flash = now; if(e.onLand) e.onLand(); } }
  c.globalAlpha = 1; MP.events = MP.events.filter(e => now - e.t0 < e.dur + 900);
  for(const r of MP.rings){ const k = (now - r.t0)/1500, p = toScr(r.at);
    c.strokeStyle = r.color; c.lineWidth = 1.6; c.globalAlpha = Math.max(0, 1-k) * (r.ex ? 1 : .5); c.beginPath(); c.arc(p[0], p[1], 3 + k*(r.ex ? 26 : 12), 0, 7); c.stroke();
    if(r.name){ c.globalAlpha = Math.max(0, Math.min(1, (1-k)*2)); c.fillStyle = C.label; c.font = '600 12px "DM Mono", ui-monospace, monospace'; c.textAlign = p[0] > MP.w*0.75 ? 'right' : 'left'; c.fillText(r.name, p[0] + (c.textAlign === 'right' ? -10 : 10), p[1] - 10); } }
  c.globalAlpha = 1; MP.rings = MP.rings.filter(r => now - r.t0 < 1500);
  const pulse = 1 + 0.22*Math.sin(now/380);
  posCache.forEach(p => { const q = toScr(p); c.fillStyle = C.campus; c.globalAlpha = .55; c.beginPath(); c.arc(q[0],q[1],2.4*pulse,0,7); c.fill(); });
  c.globalAlpha = 1;
  ['NIH','NSF'].forEach(k => { const p = toScr(MAPD.hub[k]); c.fillStyle = C[k]; c.beginPath(); c.arc(p[0],p[1],4.2,0,7); c.fill(); });
  const hp = toScr(MAPD.hub.NIH); c.fillStyle = C.label; c.font = '700 11px "DM Mono", ui-monospace, monospace'; const nr = hp[0] > MP.w*0.82; c.textAlign = nr ? 'right' : 'left'; c.fillText('NIH · NSF', hp[0] + (nr ? -6 : 9), hp[1] + 18);
}
const mtip = $('#mtip');
function stateAt(ev){ const r = mcv.getBoundingClientRect(), x = (ev.clientX - r.left - MP.ox)/MP.s, y = (ev.clientY - r.top - MP.oy)/MP.s; mctx.setTransform(1,0,0,1,0,0); return STATES.find(st => mctx.isPointInPath(st.p, x, y)) || null; }
mcv.addEventListener('pointermove', ev => {
  if(!MAPD) return; const st = stateAt(ev); MP.hover = st; mcv.style.cursor = st ? 'pointer' : 'default';
  if(!st){ mtip.classList.remove('show'); return; }
  const h = HEAT[st.name];
  mtip.innerHTML = `<b>${esc(st.name)}</b><br>${h ? h.all.active_awards.toLocaleString() + ' active awards · ' + money(h.all.active_total) + '<br><span class="mono">NIH ' + h.all.nih_awards + ' · NSF ' + h.all.nsf_awards + '</span>' : 'No active awards'}<br><span style="opacity:.75">${S.state === st.code ? 'Click to show all states' : 'Click to filter'}</span>`;
  const host = mcv.parentNode.getBoundingClientRect(); let lx = ev.clientX - host.left + 14; if(lx > host.width - 230) lx = ev.clientX - host.left - 230;
  mtip.style.left = lx + 'px'; mtip.style.top = (ev.clientY - host.top + 14) + 'px'; mtip.classList.add('show'); });
mcv.addEventListener('pointerleave', () => { MP.hover = null; mtip.classList.remove('show'); });
mcv.addEventListener('click', ev => { if(!MAPD) return; const st = stateAt(ev); if(!st) return;
  S.state = S.state === st.code ? null : st.code; S.insts.clear();
  if(S.state) showToast('FILTER', `... JOIN institution i ON i.inst_id = a.inst_id WHERE i.state = '${S.state}'`);
  loadChips(); refresh(true); setSide(true); });

/* live feed: most recently started awards */
const feed = $('#feed'); let FEED = [], feedI = 0;
function feedTick(){ $$('.ago', feed).forEach(el => { el.textContent = el.dataset.d; }); }
function pushFeed(a){
  if(feed.firstElementChild && feed.firstElementChild.dataset.id == a.award_id) return;
  const li = document.createElement('li');
  const days = Math.round((Date.now() - D(a.start_date).getTime())/864e5);
  li.innerHTML = `<span class="fa ${a.agency}">${a.agency}</span><span class="ft"><b>${esc(a.title)}</b><small>${esc(inst(a.institution))} · ${money(a.total_amount)}</small></span><span class="ago mono" data-d="${days <= 0 ? 'today' : days + 'd ago'}">${days <= 0 ? 'today' : 'started ' + days + 'd ago'}</span>`;
  li.dataset.id = a.award_id; li.addEventListener('click', () => openAward(a.award_id)); li.tabIndex = 0; li.setAttribute('role','button');
  li.addEventListener('keydown', ev => { if(ev.key === 'Enter') openAward(a.award_id); });
  feed.prepend(li); while(feed.children.length > 3) feed.lastElementChild.remove();
}
async function loadFeed(){ try{ FEED = (await api('/feed' + qs({limit:30}))).rows; }catch(e){ FEED = []; } }
function feedFromList(){ /* faint arcs come from the current result page */ }
let spawnN = 0, faintI = 0;
function spawn(now){
  spawnN++;
  if(spawnN % 2 === 1 && FEED.length){ const a = FEED[feedI++ % FEED.length], to = campusPos(a);
    MP.events.push({from:MAPD.hub[a.agency], to, t0:now, dur:1700, ex:true, color:MP.col[a.agency], name:inst(a.institution), state:a.state, onLand:() => pushFeed(a)}); }
  else if(S.list.length){ const a = S.list[faintI++ % S.list.length]; if(a.state) MP.events.push({from:MAPD.hub[a.agency], to:campusPos(a), t0:now, dur:1400, ex:false, color:MP.col[a.agency], state:a.state}); }
}
let lastSpawn = 0, lastFeedTick = 0;
function mapLoop(now){
  if(S.tab === 'explore' && !document.hidden){
    if(now - lastSpawn > 900){ lastSpawn = now; spawn(now); }
    if(now - lastFeedTick > 1000){ lastFeedTick = now; feedTick(); }
    drawMap(now);
  }
  requestAnimationFrame(mapLoop);
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => readCols());
new MutationObserver(() => readCols()).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});

/* awards panel slides in over the map */
const vx = $('#v-explore'); vx.classList.add('xclosed');
function setSide(open){ vx.classList.toggle('xclosed', !open); const tg = $('#sideToggle'); tg.setAttribute('aria-expanded', open); tg.setAttribute('aria-label', open ? 'Close awards panel' : 'Show active awards'); tg.title = open ? 'Close (Esc)' : ''; setTimeout(() => { sizeMap(); if(open && perPage() !== S.per) refresh(true); }, 460); }
$('#sideToggle').addEventListener('click', () => setSide(vx.classList.contains('xclosed')));
addEventListener('keydown', e => { if(e.key === 'Escape' && !drawer.classList.contains('open') && !dock.classList.contains('open') && !vx.classList.contains('xclosed')) setSide(false); });
const dock = $('.dock'); function setDock(on){ dock.classList.toggle('open', on); $('#sqlBtn').setAttribute('aria-expanded', on); if(on) $$('.seg', dock).forEach(setSegThumb); }
$('#sqlBtn').addEventListener('click', () => setDock(!dock.classList.contains('open')));
$('#dockClose').addEventListener('click', () => setDock(false));
addEventListener('keydown', e => { if(e.key === 'Escape' && dock.classList.contains('open')) setDock(false); });
segInit($('#dockSeg'), v => { $('#sqlpane').hidden = v !== 'sql'; $('#leaders').hidden = v !== 'lead'; $('#access').hidden = v !== 'sql'; });

/* ---------- boot ---------- */
(async () => {
  try{ const h = await api('/health'); $('#dbpill').textContent = `MySQL ${String(h.version).split('-')[0]} · ${Number(h.awards).toLocaleString()} awards`; }catch(e){ $('#dbpill').textContent = 'API offline'; fail(e); }
  try{
    MAPD = await (await fetch('/usdata.json')).json();
    STATES = MAPD.states.map(s => ({name:s.n, code:CODE_OF[s.n], p:new Path2D(s.d), c:s.c, flash:0}));
    NATION = new Path2D(MAPD.nation);
  }catch(e){}
  setTab((location.hash || '#explore').slice(1), false);
  await Promise.all([loadHeat(), loadFeed(), loadChips()]);
  try{ const u = await api('/search/institutions' + qs({q:'UNIVERSITY OF SOUTHERN CALIFORNIA'})); S.home = u.rows[0] || null;
    if(S.home){ const t = await api(`/institutions/${S.home.inst_id}`); S.homePI = t.investigators.rows[0] || null; } }catch(e){}
  sizeMap(); readCols(); refresh(true); loadOutreach(); loadAsk();
  FEED.slice(0,3).reverse().forEach(pushFeed);
  if(RM) drawMap(0); else requestAnimationFrame(mapLoop);
})();
}
