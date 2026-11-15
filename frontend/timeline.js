// r/eaction — "How it evolved" panel. Reads <topic>/timeline.json (written by the timeline-analyst agent).
// Optional: if a topic has no timeline.json, nothing is shown.
(function(){
  const css=`
  .tl-arc{font-family:var(--serif);font-size:14px;line-height:1.4;margin:6px 0 12px}
  .tl-rail{position:relative;margin:4px 0 6px 6px;padding-left:16px;border-left:2px solid var(--line,rgba(127,127,127,.3))}
  .tl-ph{position:relative;margin:0 0 14px}
  .tl-ph:before{content:"";position:absolute;left:-23px;top:3px;width:10px;height:10px;border-radius:50%;background:var(--accent,#e2572b)}
  .tl-name{font-weight:600;font-size:13px}
  .tl-when{font-size:10.5px;color:var(--faint);margin-top:1px}
  .tl-st{font-size:11px;color:var(--mute);margin-top:3px}
  .tl-sum{font-size:12px;margin-top:3px;line-height:1.4}
  .tl-tp{position:relative;margin:-6px 0 12px;padding:6px 8px;border-radius:6px;background:rgba(127,127,127,.09);font-size:11.5px;line-height:1.35}
  .tl-tp b{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent,#e2572b)}
  .tl-tp q{display:block;font-style:italic;margin:2px 0}
  .tl-tp span{color:var(--mute)}
  .tl-spread{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:11.5px;margin-top:4px}
  .tl-spread i{font-style:normal;color:var(--faint)}
  .tl-cav{font-size:10.5px;color:var(--faint);margin-top:8px;line-height:1.4}`;
  const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
  const e=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const short=s=>e(String(s||'').replace(/\s*UTC.*$/,'').replace(/\s\d{1,2}:\d{2}.*$/,''));
  window.timelineBlock=function(t){
    if(!t||!t.phases||!t.phases.length) return '';
    const tps=t.turning_points||[];
    let rail='';
    t.phases.forEach((p,i)=>{
      rail+=`<div class="tl-ph"><div class="tl-name">${e(p.name)}</div>
        <div class="tl-when">${short(p.from)} → ${short(p.to)}${p.communities&&p.communities.length?' · '+p.communities.slice(0,3).map(c=>'r/'+e(String(c).replace(/^r\//,''))).join(', '):''}</div>
        ${p.stance?`<div class="tl-st">${e(p.stance)}</div>`:''}
        ${p.summary?`<div class="tl-sum">${e(p.summary)}</div>`:''}</div>`;
      const tp=tps[i];
      if(tp&&i<t.phases.length-1) rail+=`<div class="tl-tp"><b>Turning point · ${short(tp.when)}</b>
        ${tp.likely_trigger?`<q>${e(tp.likely_trigger)}</q>`:''}<span>${e(tp.effect)}</span></div>`;
    });
    const spread=(t.spread||[]).slice(0,7).map(s=>'r/'+e(String(s.subreddit).replace(/^r\//,''))).join(' <i>→</i> ');
    return `<div class="ph">How it evolved</div>
      ${t.arc?`<div class="tl-arc">${e(t.arc)}</div>`:''}
      <div class="tl-rail">${rail}</div>
      ${spread?`<div class="ph">How it spread</div><div class="tl-spread">${spread}</div>`:''}
      ${(t.caveats||[]).length?`<div class="tl-cav">${e(t.caveats[0])}</div>`:''}`;
  };
})();

// ---------------------------------------------------------------- stream timeline (bottom of the map)
// Width of the stream = how many comments were posted at that time; colors = narratives (same as the dots).
// Starts thin with the first posts and widens as the conversation grows. Phases + turning points from timeline.json.
(function(){
  const css=`
  .left{grid-template-rows:auto 1fr auto !important}
  .tls{border-top:1px solid rgba(127,127,127,.18);padding:6px 22px 8px;position:relative}
  .tls-h{display:flex;align-items:center;gap:10px;font-size:9.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint)}
  .tls-h button{all:unset;cursor:pointer;color:var(--mute);font-size:11px;letter-spacing:0;text-transform:none}
  .tls svg{display:block;width:100%;height:132px;overflow:visible}
  .tls .ax{font-size:10px;fill:var(--faint)}
  .tls .phl{font-size:10.5px;fill:var(--mute)}
  .tls .tp{cursor:help}
  .tls.min svg{display:none}
  .tls-h .sp{flex:1}
  .tls-h .pl{all:unset;cursor:pointer;font-size:11px;letter-spacing:0;text-transform:none;color:var(--ink,inherit);border:1px solid rgba(127,127,127,.35);border-radius:999px;padding:2px 10px}
  .tls-h input[type=range]{flex:0 1 260px;accent-color:#d24a2c;cursor:pointer}
  .tls-h .day{font-size:11px;letter-spacing:0;text-transform:none;color:var(--mute);min-width:150px;font-variant-numeric:tabular-nums}
  .tls svg{cursor:ew-resize}`;
  const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
  let box=null,cur=null;
  const MON={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};
  function parseWhen(s,year){ // "Nov 8 22:25 UTC", "Oct 21 2026, 17:29 UTC", "Nov 08 2026 22:25 UTC"
    const m=String(s||'').match(/([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?,?\s*(\d{1,2}):(\d{2})?/);
    if(!m) return null; const mo=MON[m[1].toLowerCase()]; if(mo==null) return null;
    return Date.UTC(+(m[3]||year),mo,+m[2],+(m[4]||0),+(m[5]||0))/1000;
  }
  function ensureBox(){
    if(box) return box;
    const stage=document.getElementById('stage'); if(!stage) return null;
    box=document.createElement('div');box.className='tls';box.id='tls';
    stage.after(box);
    box.addEventListener('click',e=>{if(e.target.closest('[data-tls-toggle]')){box.classList.toggle('min');draw();window.dispatchEvent(new Event('resize'))}});
    window.addEventListener('resize',()=>{clearTimeout(box._t);box._t=setTimeout(draw,120)});
    return box;
  }
  function draw(){
    if(!box) return; const tp=cur;
    if(!tp||!tp.stream||!tp.stream.length||!tp.model){box.style.display='none';return}
    box.style.display='';
    const min=box.classList.contains('min');
    const head=`<div class="tls-h"><span>Over time · width = comments posted · color = narrative</span><button data-tls-toggle>${min?'Show ▴':'Hide ▾'}</button></div>`;
    if(min){box.innerHTML=head;return}
    const W=Math.max(300,box.clientWidth-44),H=132,top=26,bot=18,ih=H-top-bot;
    const ts=tp.stream.map(r=>r[0]).sort((a,b)=>a-b);
    const t0=ts[Math.floor(ts.length*0.005)],t1=ts[Math.ceil(ts.length*0.995)-1]; // trim extreme outliers
    const NB=Math.min(90,Math.max(30,Math.round(W/14)));const span=Math.max(1,t1-t0),bw=span/NB;
    const idx=new Map((typeof NARRS!=='undefined'?NARRS:[]).map((n,i)=>[String(n.srcId),i]));
    const K=idx.size+1; // last = off-topic
    const bins=Array.from({length:NB},()=>new Array(K).fill(0));
    tp.stream.forEach(([t,n,noise])=>{if(t<t0||t>t1)return;const b=Math.min(NB-1,Math.floor((t-t0)/bw));
      const k=(noise||n==null||!idx.has(String(n)))?K-1:idx.get(String(n));bins[b][k]++});
    // light smoothing so single-thread bursts read as a river, not spikes
    const sm=bins.map((row,i)=>row.map((v,k)=>((bins[i-1]?.[k]||0)+2*v+(bins[i+1]?.[k]||0))/4));
    const tot=sm.map(r=>r.reduce((a,b)=>a+b,0)),mx=Math.max(1,...tot);
    const X=i=>22+(i+.5)/NB*W, Y=v=>top+ih/2+v/mx*ih/2;
    let base=sm.map((r,i)=>-tot[i]/2),paths='';
    const off=(typeof T!=='undefined'&&T.off)||'rgba(150,150,150,.45)';
    const order=[...Array(K).keys()].sort((a,b)=>(a===K-1)-(b===K-1)); // narratives first, off-topic last
    order.forEach(k=>{
      const lo=base.map(v=>v),hi=base.map((v,i)=>v+sm[i][k]);
      let d='M'+X(0)+','+Y(lo[0]);for(let i=1;i<NB;i++)d+='L'+X(i)+','+Y(lo[i]);
      for(let i=NB-1;i>=0;i--)d+='L'+X(i)+','+Y(hi[i]);d+='Z';
      const col=k<K-1?(NARRS[k].color):off;
      paths+=`<path d="${d}" fill="${col}" fill-opacity="${k<K-1?.85:.35}"><title>${k<K-1?NARRS[k].label:'Off-topic'}</title></path>`;
      base=hi;
    });
    const fmtD=t=>new Date(t*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
    let ax='';for(let j=0;j<=4;j++){const t=t0+span*j/4;ax+=`<text class="ax" x="${22+W*j/4}" y="${H-3}" text-anchor="${j?j<4?'middle':'end':'start'}">${fmtD(t)}</text>`}
    const yr=new Date(t1*1000).getUTCFullYear(),tl=tp.timeline||{},xt=t=>22+Math.min(1,Math.max(0,(t-t0)/span))*W;
    let ph='';(tl.phases||[]).forEach((p,i)=>{const a=parseWhen(p.from,yr);if(a==null)return;const x=xt(a);
      ph+=`<line x1="${x}" x2="${x}" y1="${top-4}" y2="${H-bot}" stroke="currentColor" stroke-opacity=".18" stroke-dasharray="3 3"/>
           <text class="phl" x="${x+4}" y="${top-8}">${(i+1)+'. '+(n=>n.length>30?n.slice(0,29)+'…':n)(String(p.name).replace(/[<&]/g,''))}<title>${String(p.name).replace(/[<&]/g,'')}</title></text>`});
    let tps='';(tl.turning_points||[]).forEach(tp2=>{const a=parseWhen(tp2.when,yr);if(a==null)return;const x=xt(a);
      tps+=`<g class="tp"><line x1="${x}" x2="${x}" y1="${top}" y2="${H-bot}" stroke="#d24a2c" stroke-width="1.5"/>
        <circle cx="${x}" cy="${top}" r="4.5" fill="#d24a2c"/><title>Turning point · ${String(tp2.when).replace(/[<&]/g,'')}\n“${String(tp2.likely_trigger||'').replace(/[<&]/g,'')}”\n${String(tp2.effect||'').replace(/[<&]/g,'')}</title></g>`});
    box.innerHTML=head+`<svg viewBox="0 0 ${W+44} ${H}" preserveAspectRatio="none" style="color:var(--mute)">${paths}${ph}${tps}${ax}</svg>`;
      }

  window.renderStream=function(tp){ensureBox();cur=tp;draw();window.dispatchEvent(new Event('resize'))};
})();
