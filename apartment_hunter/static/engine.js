/* Generic front-end engine, driven by a per-page `window.TOOL` config object.
   Behaviour is a direct generalisation of the original price-tracker.html JS.

   window.TOOL shape:
   {
     endpoint,          // SSE endpoint, e.g. "/api/track"
     event,             // SSE item event name, e.g. "offer"
     itemKey,           // key on each item event holding the object, e.g. "offer"
     positiveStatus,    // "deal" or "match" — the positive row status / filter
     emptyMsg,          // status text when a search returns nothing
     filename,          // export basename (no extension), e.g. "prices"
     jsonMode,          // "spread" -> {...item, deal_score, deal, verdict}; else built from exportCols
     chips: [{ el, options, multi, selected }],       // chip groups to wire up
     params: () => ({ ... }),                          // query object from the form
     columns: [{ cell(item, result, index) -> "<td>…</td>" }],   // one per table column
     summary: [{ value(results) -> string|number }],  // aligned with the template stat cells
     exportCols: [{ name, get(result) }],              // CSV columns (and JSON when jsonMode !== "spread")
     rowClass: (result) -> "extra classes"             // optional extra <tr> classes
   }
*/
const $ = (id) => document.getElementById(id);
let results = [], currency = "₹", filterMode = "all", sort = { key:"score", dir:-1 };
const chipGetters = {};

const esc = (x) => String(x ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money = (n) => (n===null||n===undefined||n==="") ? "—" : currency + Number(n).toLocaleString();
const amountText = (a) => (a===null||a===undefined||a==="") ? "—" : (typeof a==="number" ? currency + Number(a).toLocaleString() : String(a));
const icons = () => window.lucide && lucide.createIcons();
const scoreColor = (s)=> s>=75?"bg-emerald-500":s>=50?"bg-amber-500":"bg-zinc-400";

/* ---- chip groups (string or {val,label} options) ---- */
function chipGroup(el, options, {multi=true, selected=[]}={}){
  el.innerHTML = options.map(o=>{
    const val = typeof o==="object"?o.val:o, label = typeof o==="object"?o.label:o;
    return `<button type="button" data-val="${esc(val)}" class="chip ${selected.includes(val)?"chip-on":""}">${esc(label)}</button>`;
  }).join("");
  el.addEventListener("click", e=>{
    const b=e.target.closest("button"); if(!b)return;
    if(!multi) el.querySelectorAll("button").forEach(x=>x.classList.remove("chip-on"));
    b.classList.toggle("chip-on");
  });
  return () => [...el.querySelectorAll("button.chip-on")].map(b=>b.dataset.val);
}
function chip(id){ return chipGetters[id] ? chipGetters[id]() : []; }

/* ---- currency ---- */
function currencyFromCountry(){ const o=$("country") && $("country").selectedOptions[0]; return (o && o.dataset.cur) || ""; }

/* ---- status + terminal ---- */
function setStatus(kind, txt){
  const map = { busy:"bg-indigo-500 pulse", ok:"bg-emerald-500", err:"bg-rose-500", "":"bg-zinc-400" };
  $("led").className = "w-2 h-2 rounded-full " + (map[kind]||map[""]);
  $("statusTxt").textContent = txt;
  $("prog").className = "prog" + (kind==="busy" ? " on" : "");
  $("termState").textContent = kind==="busy"?"running":kind==="err"?"error":kind==="ok"?"done":"idle";
  $("termState").className = "ml-auto text-[11px] font-mono " + (kind==="busy"?"text-indigo-400":kind==="err"?"text-rose-400":kind==="ok"?"text-emerald-400":"text-zinc-500");
}
const TCOL = { info:"text-sky-400", ok:"text-emerald-400", warn:"text-amber-400", error:"text-rose-400" };
const TSYM = { info:"›", ok:"✓", warn:"!", error:"✕" };
function term(level, msg){
  const box=$("term"); if(box.dataset.fresh!=="1"){ box.innerHTML=""; box.dataset.fresh="1"; }
  const t=new Date().toLocaleTimeString(), el=document.createElement("div"); el.className="flex gap-2";
  el.innerHTML=`<span class="text-zinc-600 shrink-0">${t}</span><span class="${TCOL[level]||"text-zinc-400"} shrink-0">${TSYM[level]||"·"}</span><span class="text-zinc-300 break-words">${esc(msg)}</span>`;
  box.appendChild(el); box.scrollTop=box.scrollHeight;
}

/* ---- platforms ---- */
function renderPlatforms(items){
  const box=$("platforms");
  box.innerHTML = (items&&items.length) ? items.map(p=>
    `<span class="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 dark:border-zinc-700 pl-3 pr-1.5 py-1 text-sm">${esc(p.name)}<span class="inline-flex items-center justify-center min-w-5 h-5 px-1 rounded-full bg-indigo-600 text-white text-[11px] font-semibold tabnum">${p.count}</span></span>`
  ).join("") : '<span class="text-sm text-zinc-400">None yet.</span>';
}

/* ---- shared cell builders (usable from TOOL.columns) ---- */
function titleLink(o, head){
  const hasUrl=o.url&&o.url!=="#";
  return hasUrl ? `<a href="${esc(o.url)}" target="_blank" rel="noopener" class="font-medium text-indigo-600 dark:text-indigo-400 hover:underline">${esc(head)}</a>`
                : `<span class="font-medium">${esc(head)}</span>`;
}
function actionLink(o, label){
  const hasUrl=o.url&&o.url!=="#";
  return hasUrl ? `<a href="${esc(o.url)}" target="_blank" rel="noopener" class="inline-flex items-center gap-1 text-indigo-600 dark:text-indigo-400 hover:underline text-sm whitespace-nowrap">${label}<i data-lucide="external-link" class="w-3.5 h-3.5"></i></a>` : "";
}
function tdLink(o, label){ return `<td class="td">${actionLink(o, label)}</td>`; }
function tdThumb(o){
  return `<td class="td"><div class="w-14 h-14 rounded-md bg-zinc-100 dark:bg-zinc-800 overflow-hidden flex items-center justify-center shrink-0 relative"><i data-lucide="image" class="w-4 h-4 text-zinc-400"></i>${o.image?`<img src="${esc(o.image)}" class="absolute inset-0 w-full h-full object-contain" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`:""}</div></td>`;
}
function tdTitle(o, head, sub, extra){
  return `<td class="td max-w-[${extra||300}px]">${titleLink(o, head)}<span class="block text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">${esc(sub)}</span></td>`;
}
function tdScore(r){
  return `<td class="td"><div class="flex items-center gap-2"><span class="fitbar"><i class="block h-full ${scoreColor(r.score)}" style="width:${Math.max(0,Math.min(100,r.score))}%"></i></span><span class="tabnum text-xs font-semibold w-6 text-right">${r.score}</span></div></td>`;
}
function tdBadge(r){ return `<td class="td"><span class="badge b-${r.status}">${esc(r.verdict||r.status)}</span></td>`; }
function tdMuted(v){ return `<td class="td text-zinc-500 dark:text-zinc-400 whitespace-nowrap">${esc(v||"")}</td>`; }

/* ---- results ---- */
function view(){
  const k=sort.key;
  let v = filterMode===TOOL.positiveStatus ? results.filter(r=>r[TOOL.positiveStatus]) : results.slice();
  v.sort((a,b)=>{
    const av = k==="score" ? a.score : (a[TOOL.itemKey][k] ?? Infinity);
    const bv = k==="score" ? b.score : (b[TOOL.itemKey][k] ?? Infinity);
    return (av<bv?-1:av>bv?1:0)*(k==="price"?1:sort.dir);
  });
  if(k==="price") v.sort((a,b)=>((a[TOOL.itemKey].price??Infinity)-(b[TOOL.itemKey].price??Infinity))*sort.dir);
  return v;
}
function render(){
  const v=view(), tb=$("rows"), n=TOOL.columns.length;
  if(!results.length) tb.innerHTML=`<tr><td colspan="${n}" class="td text-center text-zinc-400 py-12">No results yet.</td></tr>`;
  else if(!v.length) tb.innerHTML=`<tr><td colspan="${n}" class="td text-center text-zinc-400 py-12">Nothing matches this filter.</td></tr>`;
  else tb.innerHTML = v.map((r,i)=>{
    const o=r[TOOL.itemKey];
    const posCls = r.status===TOOL.positiveStatus ? "tr-"+TOOL.positiveStatus : "";
    const extra = TOOL.rowClass ? TOOL.rowClass(r) : "";
    return `<tr class="${posCls} ${extra} hover:bg-zinc-50 dark:hover:bg-zinc-800/40">${TOOL.columns.map(c=>c.cell(o,r,i)).join("")}</tr>`;
  }).join("");
  TOOL.summary.forEach((s,i)=>{ const el=$("s"+i); if(el) el.textContent = s.value(results); });
  $("count").textContent=results.length?`Showing ${v.length} of ${results.length}`:"";
  const has=results.length>0; $("expCsv").disabled=!has; $("expJson").disabled=!has;
  document.querySelectorAll(".ar").forEach(a=>a.textContent="");
  const ar=document.querySelector(`.ar[data-for="${sort.key}"]`); if(ar) ar.textContent=sort.dir===-1?"▼":"▲";
  icons();
}
document.querySelectorAll("th[data-sort]").forEach(th=>th.addEventListener("click",()=>{ const k=th.dataset.sort; sort=sort.key===k?{key:k,dir:-sort.dir}:{key:k,dir:-1}; render(); }));
document.querySelectorAll("#filter button").forEach(b=>b.addEventListener("click",()=>{
  document.querySelectorAll("#filter button").forEach(x=>x.className="px-3.5 py-1.5 font-medium text-zinc-500 dark:text-zinc-400"+(x.previousElementSibling?" border-l border-zinc-300 dark:border-zinc-700":""));
  b.className="px-3.5 py-1.5 font-medium bg-indigo-50 dark:bg-indigo-500/15 text-indigo-700 dark:text-indigo-300"+(b.previousElementSibling?" border-l border-zinc-300 dark:border-zinc-700":"");
  filterMode=b.dataset.f; render();
}));
$("country") && $("country").addEventListener("change",e=>{
  const c=e.target.selectedOptions[0].dataset.cur;
  if(c){ const p=$("curPrefix"); if(p)p.textContent=c; currency=c; }
});

/* ---- run ---- */
$("form").addEventListener("submit",(e)=>{
  e.preventDefault();
  results=[]; currency = ($("curPrefix") && $("curPrefix").textContent) || currencyFromCountry();
  $("term").dataset.fresh="0"; $("term").innerHTML=""; renderPlatforms([]);
  $("rows").innerHTML=`<tr><td colspan="${TOOL.columns.length}" class="td text-center text-zinc-400 py-12">Searching…</td></tr>`;
  TOOL.summary.forEach((s,i)=>{ const el=$("s"+i); if(el) el.textContent="—"; });
  $("go").disabled=true; setStatus("busy","Searching");
  const qs=new URLSearchParams(TOOL.params());
  const es=new EventSource(TOOL.endpoint+"?"+qs.toString());
  es.addEventListener("log",ev=>{const d=JSON.parse(ev.data);term(d.level,d.msg);});
  es.addEventListener("sources",ev=>renderPlatforms(JSON.parse(ev.data).items));
  es.addEventListener(TOOL.event,ev=>{results.push(JSON.parse(ev.data));render();});
  es.addEventListener("done",ev=>{const d=JSON.parse(ev.data);render();
    if(!results.length){ setStatus("err", TOOL.emptyMsg); }
    else if(TOOL.positiveStatus==="deal"){ const n=d.deals; setStatus("ok",`${n} deal${n===1?"":"s"} of ${d.processed}`); }
    else { const n=d.matched; setStatus("ok",`${n} match${n===1?"":"es"} of ${d.processed}`); }
    $("go").disabled=false; es.close();});
  es.onerror=()=>{term("error","Connection closed");setStatus("err","Interrupted");$("go").disabled=false;es.close();};
});

/* ---- export ---- */
function download(name,text,type){const b=new Blob([text],{type}),u=URL.createObjectURL(b),a=document.createElement("a");a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(u);}
$("expJson").addEventListener("click",()=>{
  const rows=view(); if(!rows.length) return;
  let data;
  if(TOOL.jsonMode==="spread") data=rows.map(r=>({...r[TOOL.itemKey], deal_score:r.score, deal:r.deal, verdict:r.verdict}));
  else data=rows.map(r=>{ const o={}; TOOL.exportCols.forEach(c=>{ o[c.name]=c.get(r); }); return o; });
  download(TOOL.filename+".json", JSON.stringify(data,null,2), "application/json");
});
$("expCsv").addEventListener("click",()=>{
  const rows=view(); if(!rows.length) return;
  const cols=TOOL.exportCols, cell=v=>`"${String(v??"").replace(/"/g,'""')}"`, lines=[cols.map(c=>c.name).join(",")];
  for(const r of rows) lines.push(cols.map(c=>cell(c.get(r))).join(","));
  download(TOOL.filename+".csv", lines.join("\r\n"), "text/csv;charset=utf-8");
});

/* ---- init ---- */
(TOOL.chips||[]).forEach(c=>{ chipGetters[c.el]=chipGroup($(c.el), c.options, {multi:c.multi, selected:c.selected||[]}); });
currency = currencyFromCountry() || currency;
icons();
