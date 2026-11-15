/* Live search: any event not in events.json is sent to the agent server (agent/server.py).
   The server's public URL is read from the bucket's live.json (or ?api=...). */
(function(){
  const SUPA='https://dexgswmmjewpcwkzocax.supabase.co/storage/v1/object/public/narrative-runs/';
  const qp=new URLSearchParams(location.search).get('api');
  let API=qp||null;
  if(!API) fetch(SUPA+'live.json',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{if(j&&j.api)API=j.api}).catch(()=>{});
  const slugify=q=>q.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60);
  const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const css=document.createElement('style');css.textContent=`
  .live{margin-top:14px;font-size:14px;color:var(--mute);line-height:1.5}
  .live b{color:var(--ink,inherit)}
  .live .bar{height:4px;border-radius:4px;background:rgba(127,127,127,.18);margin:8px 0 4px;overflow:hidden;max-width:420px}
  .live .bar i{display:block;height:100%;background:#d24a2c;transition:width .6s}
  .live .dots::after{content:'';animation:ld 1.4s steps(4) infinite}
  @keyframes ld{0%{content:''}25%{content:'.'}50%{content:'..'}75%{content:'...'}}
  .live .stop{margin-top:10px;border:1px solid var(--line);background:none;color:var(--mute);border-radius:99px;padding:5px 14px;font-size:12.5px;cursor:pointer;font-family:var(--sans)}
  .live .stop:hover{color:var(--ink);border-color:var(--mute)}`;
  document.head.appendChild(css);

  function addTopic(slug,q){
    let tp=TOPICS.find(t=>t.slug===slug);
    if(!tp){tp={slug,title:q.replace(/\b\w/g,c=>c.toUpperCase()),query:q,path:SUPA+slug+'/',sample:false,live:true};
      tp.keys=[q].map(k=>k.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim());TOPICS.push(tp);try{renderHome()}catch(e){}}
    return tp;
  }
  const fmt=s=>Math.floor(s/60)+':'+String(s%60).padStart(2,'0');
  let polling=null,runId=0,ctl=null,curSlug=null;
  // Cancel: stop waiting right away, and ask the server to stop the agents (older servers just ignore it)
  window.liveCancel=function(){
    const slug=curSlug; runId++; clearInterval(polling); polling=null; if(ctl){try{ctl.abort()}catch(e){}} ctl=null; curSlug=null;
    const herr=document.getElementById('herr'); if(herr) herr.innerHTML='';
    if(API&&slug) fetch(API+'/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug})}).catch(()=>{});
  };
  document.addEventListener('click',e=>{if(e.target.closest('[data-live-stop]'))liveCancel()});
  window.liveRun=async function(q){
    const herr=document.getElementById('herr'),slug=slugify(q); if(!slug) return false;
    if(curSlug) liveCancel();                       // a new search replaces one that's still running
    const id=++runId; curSlug=slug;
    // already analyzed by someone? open it straight away
    try{const r=await fetch(SUPA+slug+'/clusters.json',{method:'HEAD',cache:'no-store'});if(r.ok){location.hash=addTopic(slug,q).slug;return true}}catch(e){}
    if(!API){herr.innerHTML=`<b>Oops!</b> We don’t have data on “${esc(q)}” yet, and live search is offline right now. Try one of these:`;return true}
    const show=s=>{
      if(id!==runId) return;                        // cancelled or replaced
      if(s.state==='cancelled'){clearInterval(polling);curSlug=null;herr.innerHTML='';return}
      if(s.state==='done'){clearInterval(polling);curSlug=null;herr.innerHTML='';location.hash=addTopic(slug,q).slug;return}
      if(s.state==='failed'||s.state==='busy'||s.state==='unknown'){clearInterval(polling);
        curSlug=null;herr.innerHTML=`<div class="live"><b>Couldn’t finish “${esc(q)}”.</b> ${esc(s.error||'Try a more specific event.')}</div>`;return}
      const pct=Math.max(6,Math.round(100*(s.step_n||0)/(s.steps||6)));
      herr.innerHTML=`<div class="live"><b>Agents are investigating “${esc(q)}”</b> · ${fmt(s.elapsed||0)}<div class="bar"><i style="width:${pct}%"></i></div><span class="dots">${esc(s.step||'Starting')}</span><br><span style="font-size:12px">Agents are reading Reddit live: usually about 6 minutes. Explore the other events meanwhile, this page will open it when ready.</span><br><button type="button" class="stop" data-live-stop>Cancel search</button></div>`;
    };
    try{
      show({state:'running',step:'Starting agents',step_n:0,steps:6,elapsed:0});   // show Cancel immediately
      ctl=new AbortController();
      const r=await fetch(API+'/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q}),signal:ctl.signal});
      const s=await r.json(); if(id!==runId) return true; show(s); if(s.state!=='running')return true;
      if(s.slug) curSlug=s.slug;
      clearInterval(polling);
      polling=setInterval(async()=>{if(id!==runId)return;try{show(await (await fetch(API+'/api/status?slug='+encodeURIComponent(s.slug||slug))).json())}catch(e){}},3000);
    }catch(e){if(id!==runId||e.name==='AbortError')return true;curSlug=null;herr.innerHTML=`<b>Oops!</b> Live search is offline right now. Try one of these:`}
    return true;
  };
})();
