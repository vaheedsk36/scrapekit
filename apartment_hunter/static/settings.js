/* Shared settings-modal logic. Hits /api/settings and /api/provider/models.
   Behaviour identical to the per-page copies it replaces. */
(function(){
  const $ = (id) => document.getElementById(id);
  const overlay = $("settingsOverlay"), statusEl = $("setStatus");
  function open(){ overlay.classList.remove("hidden"); refreshStatus(); restoreLocal(); window.lucide && lucide.createIcons(); }
  function close(){ overlay.classList.add("hidden"); }
  $("openSettings").addEventListener("click", open);
  $("closeSettings").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  function setStatus(msg, kind){
    statusEl.className = "text-xs " + (kind==="err"?"text-rose-600 dark:text-rose-400":kind==="ok"?"text-emerald-600 dark:text-emerald-400":"text-zinc-500 dark:text-zinc-400");
    statusEl.textContent = msg;
  }
  function restoreLocal(){
    const p = localStorage.getItem("ah_provider"); if (p) $("setProvider").value = p;
    const m = localStorage.getItem("ah_model");
    if (m){ const sel=$("setModel"); sel.innerHTML=`<option value="${m}">${m}</option>`; sel.value=m; }
  }
  async function refreshStatus(){
    try { const s = await (await fetch("/api/settings")).json();
      if (s.has_key) setStatus(`Active: ${s.provider}${s.model?" · "+s.model:""} (key from ${s.source})`, "ok");
      else setStatus("No key set yet — pick a provider and paste your key.", "");
    } catch { setStatus("", ""); }
  }
  $("loadModels").addEventListener("click", async () => {
    const provider=$("setProvider").value, api_key=$("setKey").value.trim();
    setStatus("Loading models…","");
    try {
      const r=await fetch("/api/provider/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider,api_key})});
      const d=await r.json();
      if(!r.ok||d.error){ setStatus("Could not load models: "+(d.error||r.status),"err"); return; }
      $("setModel").innerHTML=d.models.map(m=>`<option value="${m}">${m}</option>`).join("")||'<option value="">No models</option>';
      setStatus(`Loaded ${d.models.length} model(s). Pick one and Save.`,"ok");
    } catch(e){ setStatus("Request failed: "+e,"err"); }
  });
  $("saveSettings").addEventListener("click", async () => {
    const provider=$("setProvider").value, model=$("setModel").value, api_key=$("setKey").value.trim();
    if(!model){ setStatus("Choose a model first (Load models).","err"); return; }
    try {
      const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider,model,api_key})});
      const s=await r.json();
      localStorage.setItem("ah_provider",provider); localStorage.setItem("ah_model",model); $("setKey").value="";
      if(s.has_key) setStatus(`Saved. Using ${s.provider} · ${s.model}.`,"ok");
      else setStatus("Saved provider/model, but no key set — paste your key and Save again.","err");
    } catch(e){ setStatus("Save failed: "+e,"err"); }
  });
  fetch("/api/settings").then(r=>r.json()).then(s=>{ if(!s.has_key){ const t=document.getElementById("statusTxt"); if(t)t.textContent="Set API key →"; }}).catch(()=>{});
})();
