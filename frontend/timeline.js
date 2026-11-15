// r/eaction — "How it evolved" panel. Reads <topic>/timeline.json (written by the timeline-analyst agent).
// Optional: if a topic has no timeline.json, nothing is shown.
// Kept deliberately plain: phases as a dated list, turning points as the headline that set them off,
// placed in date order. Model scores like "(+0.39)" and the stance percentage lines are left out.
(function(){
  const css=`
  .tl-arc{font-family:var(--serif);font-size:14px;line-height:1.45;margin:6px 0 14px}
  .tl-rail{position:relative;margin:4px 0 6px 4px;padding-left:16px;border-left:1px solid var(--line)}
  .tl-ph{position:relative;margin:0 0 16px}
  .tl-ph:before{content:"";position:absolute;left:-20px;top:4px;width:7px;height:7px;border-radius:50%;background:var(--ink)}
  .tl-name{font-family:var(--serif);font-weight:600;font-size:14px}
  .tl-when{font-size:11px;color:var(--faint);margin-top:2px}
  .tl-sum{font-family:var(--serif);font-size:13px;margin-top:5px;line-height:1.45;color:var(--mute)}
  .tl-tp{position:relative;margin:-6px 0 16px;padding:8px 10px 9px;border-radius:8px;background:var(--raise);font-family:var(--serif);font-size:14px;line-height:1.45;color:var(--ink)}
  .tl-tp .m{display:block;margin-bottom:3px}
  .tl-tp .d,.tl-tp .s,.tl-when{font-family:var(--sans)}
  [class^=tl-],[class^=tl-] *,[class^=tlv],[class^=tlv] *{font-style:normal}
  .tl-tp:before{content:"";position:absolute;left:-20px;top:13px;width:5px;height:5px;border-radius:50%;border:1px solid var(--faint);background:var(--panel)}
  .tl-tp .d{font-size:11px;color:var(--faint);margin-right:6px}
  .tl-tp .s{font-size:11px;color:var(--faint);white-space:nowrap}
  .tl-spread{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:12px;margin-top:4px;color:var(--mute)}
  .tl-spread i{font-style:normal;color:var(--faint)}
  .tl-cav{font-size:10.5px;color:var(--faint);margin-top:10px;line-height:1.4}`;
  const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
  const e=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const MON=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
  // "Oct 16 2026, 17:29 UTC" -> {m:9,d:16,h:17.5}
  const parts=s=>{const m=String(s||'').match(/([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+\d{4})?,?\s*(?:(\d{1,2}):(\d{2}))?/);
    if(!m) return null;const mo=MON.indexOf(m[1].toLowerCase());if(mo<0) return null;return {m:mo,d:+m[2],h:(+m[3]||0)+(+m[4]||0)/60}};
  const key=s=>{const p=parts(s);return p?p.m*40+p.d+p.h/24:1e9};
  const day=s=>{const p=parts(s);return p?MON[p.m][0].toUpperCase()+MON[p.m].slice(1)+' '+p.d:String(s||'').replace(/\s*UTC.*$/,'')};
  const range=(a,b)=>{const p=parts(a),q=parts(b);if(!b) return day(a);if(p&&q&&p.m===q.m) return p.d===q.d?day(a):day(a)+'–'+q.d;return day(a)+' – '+day(b)};
  const subs=l=>(l||[]).slice(0,3).map(c=>'r/'+String(c).replace(/^r\//,'')).join(', ');
  // strip model-ish scoring like "(+0.39)", "sharply (+0.44)"
  const clean=s=>String(s||'').replace(/\s*\([+-]?\d*\.\d+\)/g,'').replace(/\s{2,}/g,' ').trim();
  window.timelineBlock=function(t){
    if(!t||!t.phases||!t.phases.length) return '';
    const items=[...t.phases.map((p,i)=>({k:key(p.from),o:0,i,p})),
                 ...(t.turning_points||[]).filter(tp=>tp&&(tp.likely_trigger||tp.effect)).map((tp,i)=>({k:key(tp.when),o:1,i,tp}))]
      .sort((a,b)=>a.k-b.k||a.o-b.o||a.i-b.i);
    let rail='';
    items.forEach(it=>{
      if(it.p){const p=it.p;
        rail+=`<div class="tl-ph"><div class="tl-name">${e(p.name)}</div>
          <div class="tl-when">${e(range(p.from,p.to))}${p.communities&&p.communities.length?' · '+e(subs(p.communities)):''}</div>
          ${p.summary?`<div class="tl-sum">${e(clean(p.summary))}</div>`:''}</div>`;
      }else{const tp=it.tp;
        let trig=clean(tp.likely_trigger||tp.effect),sub='';
        const m=trig.match(/\s*\((r\/[A-Za-z0-9_]+)\)\s*$/);if(m){sub=m[1];trig=trig.slice(0,m.index)}
        trig=trig.replace(/^["“']+|["”']+$/g,'');
        rail+=`<div class="tl-tp" title="${e(clean(tp.effect))}"><span class="m"><span class="d">${e(day(tp.when))}</span>${sub?`<span class="s">${e(sub)}</span>`:''}</span>“${e(trig)}”</div>`;
      }
    });
    const spread=(t.spread||[]).slice(0,7).map(s=>'r/'+e(String(s.subreddit).replace(/^r\//,''))).join(' <i>→</i> ');
    return `<div class="ph">How it evolved</div>
      ${t.arc?`<div class="tl-arc">${e(clean(t.arc))}</div>`:''}
      <div class="tl-rail">${rail}</div>
      ${spread?`<div class="ph">How it spread</div><div class="tl-spread">${spread}</div>`:''}
      ${(t.caveats||[]).length?`<div class="tl-cav">${e(clean(t.caveats[0]))}</div>`:''}`;
  };
})();

// ---------------------------------------------------------------- full-screen timeline view
// A "Timeline" button at the bottom right of the map swaps the whole graphic for a stream chart:
// width = how many comments were posted at that time, colors = narratives (same as the dots).
// Phases and turning points come from timeline.json. Hover the chart to read any moment.
(function(){
  const css=`
  .tlbtn{position:absolute;right:18px;bottom:16px;z-index:9;display:none;align-items:center;gap:7px;border:1px solid var(--line);
    background:var(--panel);color:var(--ink);border-radius:99px;padding:7px 15px 7px 12px;font-size:12.5px;cursor:pointer;
    box-shadow:0 4px 14px rgba(0,0,0,.12);transition:background .15s,border-color .15s}
  .tlbtn:hover{background:var(--raise);border-color:var(--mute)}
  .tlbtn svg{flex:none}
  .tlv{position:absolute;inset:0;z-index:6;background:var(--bg);opacity:0;pointer-events:none;transition:opacity .35s ease;
    display:flex;flex-direction:column;padding:18px 26px 64px}
  .tlv.on{opacity:1;pointer-events:auto}
  .tlv-h{font-size:9.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
  .tlv-arc{font-family:var(--serif);font-size:16px;line-height:1.35;color:var(--ink);max-width:820px;margin-bottom:6px}
  .tlv-c{position:relative;flex:1;min-height:0}
  .tlv svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
  .tlv .ax{font-size:11px;fill:var(--faint)}
  .tlv .phl{font-family:var(--serif);font-size:13px;fill:var(--mute);font-weight:600}
  .tlv .phd{font-size:10.5px;fill:var(--faint)}
  .tlv .tpl{font-size:10.5px;fill:#d24a2c;font-weight:600}
  .tlv .tp{cursor:help}
  .tlv path.lay{transition:opacity .15s}
  .tlv-tip{position:absolute;z-index:2;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:9px 12px;font-size:12px;line-height:1.45;box-shadow:0 10px 28px rgba(0,0,0,.2);min-width:190px;display:none}
  .tlv-tip b{font-weight:600}
  .tlv-tip .r{display:flex;align-items:center;gap:7px;color:var(--mute)}
  .tlv-tip .r i{width:8px;height:8px;border-radius:50%;flex:none}
  .tlv-tip .r span:last-child{margin-left:auto;font-variant-numeric:tabular-nums;padding-left:10px}`;
  const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
  const E=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const MON={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};
  function parseWhen(s,year){ // "Nov 8 22:25 UTC", "Oct 21 2026, 17:29 UTC", "Nov 08 2026 22:25 UTC", or ISO
    if(typeof s==='number') return s>1e12?s/1000:s;
    const iso=Date.parse(s); if(/^\d{4}-\d{2}-\d{2}/.test(String(s))&&!isNaN(iso)) return iso/1000;
    const m=String(s||'').match(/([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?,?\s*(?:(\d{1,2}):(\d{2}))?/);
    if(!m) return null; const mo=MON[m[1].toLowerCase()]; if(mo==null) return null;
    return Date.UTC(+(m[3]||year),mo,+m[2],+(m[4]||0),+(m[5]||0))/1000;
  }
  let btn=null,view=null,cur=null,on=false,geo=null;
  const ICON_T='<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 17c3 0 4-6 7-6s4 4 7 4 3-3 4-3"/><path d="M3 21h18"/></svg>';
  const ICON_M='<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="7" cy="8" r="3"/><circle cx="17" cy="7" r="2.5"/><circle cx="12" cy="17" r="3.5"/><path d="M9.5 9.5l1.5 4.5M14.8 8.5l-1.8 5.5"/></svg>';
  function ensure(){
    if(btn) return true;
    const stage=document.getElementById('stage'); if(!stage) return false;
    view=document.createElement('div');view.className='tlv';stage.appendChild(view);
    btn=document.createElement('button');btn.className='tlbtn';btn.type='button';stage.appendChild(btn);
    btn.onclick=()=>setOn(!on);
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&on)setOn(false)});
    window.addEventListener('resize',()=>{clearTimeout(view._t);view._t=setTimeout(()=>on&&draw(),120)});
    view.addEventListener('mousemove',hover);view.addEventListener('mouseleave',()=>{const t=view.querySelector('.tlv-tip'),g=view.querySelector('#tlv-g');if(t)t.style.display='none';if(g)g.style.display='none'});
    return true;
  }
  function setOn(v){
    on=!!v&&!!(cur&&cur.stream&&cur.stream.length);
    view.classList.toggle('on',on);
    btn.innerHTML=on?ICON_M+'Back to map':ICON_T+'Timeline';
    if(on){draw();reveal()}
  }
  function bins(tp,NB){
    const ts=tp.stream.map(r=>r[0]).sort((a,b)=>a-b);
    const t0=ts[Math.floor(ts.length*0.005)],t1=ts[Math.ceil(ts.length*0.995)-1]; // trim extreme outliers
    const span=Math.max(1,t1-t0),bw=span/NB;
    const idx=new Map((typeof NARRS!=='undefined'?NARRS:[]).map((n,i)=>[String(n.srcId),i]));
    const K=idx.size+1; // last = off-topic
    const raw=Array.from({length:NB},()=>new Array(K).fill(0));
    tp.stream.forEach(([t,n,noise])=>{if(t<t0||t>t1)return;const b=Math.min(NB-1,Math.floor((t-t0)/bw));
      const k=(noise||n==null||!idx.has(String(n)))?K-1:idx.get(String(n));raw[b][k]++});
    // light smoothing so single-thread bursts read as a river, not spikes
    const sm=raw.map((row,i)=>row.map((v,k)=>((raw[i-1]?.[k]||0)+2*v+(raw[i+1]?.[k]||0))/4));
    return {t0,t1,span,bw,K,raw,sm};
  }
  function draw(){
    const tp=cur; if(!view||!tp||!tp.stream||!tp.stream.length||!tp.model) return;
    const tl=tp.timeline||{};
    view.innerHTML=`<div class="tlv-h">Over time · width = comments posted · color = narrative</div>
      ${tl.arc?`<div class="tlv-arc">${E(tl.arc)}</div>`:''}<div class="tlv-c"><svg></svg><div class="tlv-tip"></div></div>`;
    const box=view.querySelector('.tlv-c'),W=box.clientWidth,H=box.clientHeight; if(W<100||H<120) return;
    const L=8,R=8,top=46,bot=26,iw=W-L-R,ih=H-top-bot;
    const NB=Math.min(160,Math.max(36,Math.round(iw/9)));
    const b=bins(tp,NB),{t0,t1,span,K,sm}=b;
    const tot=sm.map(r=>r.reduce((a,c)=>a+c,0)),mx=Math.max(1,...tot);
    const X=i=>L+(i+.5)/NB*iw, Y=v=>top+ih/2+v/mx*ih/2*.96;
    const off=(typeof T!=='undefined'&&T.off)||'rgba(150,150,150,.45)';
    const order=[...Array(K).keys()].sort((a,c)=>(a===K-1)-(c===K-1)); // narratives first, off-topic last
    // smooth curve through points (Catmull-Rom → cubic Bézier)
    const curve=pts=>{let d='';for(let i=0;i<pts.length;i++){const p=pts[i];if(!i){d+='M'+p[0]+','+p[1];continue}
      const p0=pts[i-2]||pts[i-1],p1=pts[i-1],p3=pts[i+1]||p;
      d+=`C${p1[0]+(p[0]-p0[0])/6},${p1[1]+(p[1]-p0[1])/6} ${p[0]-(p3[0]-p1[0])/6},${p[1]-(p3[1]-p1[1])/6} ${p[0]},${p[1]}`}return d};
    let base=tot.map(v=>-v/2),paths='';
    order.forEach(k=>{
      const lo=base.slice(),hi=base.map((v,i)=>v+sm[i][k]);
      const up=lo.map((v,i)=>[X(i),Y(v)]),dn=hi.map((v,i)=>[X(i),Y(v)]).reverse();
      const d=curve(up)+'L'+dn[0][0]+','+dn[0][1]+curve(dn).replace(/^M[^C]*/,'')+'Z';
      const isN=k<K-1,col=isN?NARRS[k].color:off;
      paths+=`<path class="lay" data-k="${isN?k:-1}" d="${d}" fill="${col}" fill-opacity="${isN?.88:.3}"></path>`;
      base=hi;
    });
    const fmtD=t=>new Date(t*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
    const fmtDT=t=>new Date(t*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZone:'UTC'})+' UTC';
    // ticks on round UTC times: whole days, or 1/3/6/12-hour steps for short events
    let ax='';const maxT=Math.max(2,Math.floor(iw/110));
    const steps=[3600,3*3600,6*3600,12*3600,86400,2*86400,3*86400,7*86400,14*86400,30*86400];
    const step=steps.find(st=>span/st<=maxT)||steps[steps.length-1];
    const fmtH=t=>new Date(t*1000).toLocaleString('en-US',{hour:'numeric',timeZone:'UTC'});
    for(let t=Math.ceil(t0/step)*step;t<=t1;t+=step){const x=L+(t-t0)/span*iw;
      const lab=step<86400?(new Date(t*1000).getUTCHours()===0?fmtD(t):fmtH(t)):fmtD(t);
      const anc=x-L<30?'start':L+iw-x<30?'end':'middle';
      ax+=`<line x1="${x}" x2="${x}" y1="${H-bot}" y2="${H-bot+4}" stroke="var(--faint)"/><text class="ax" x="${x}" y="${H-6}" text-anchor="${anc}">${lab}</text>`}
    ax+=`<line x1="${L}" x2="${L+iw}" y1="${H-bot}" y2="${H-bot}" stroke="var(--line)"/>`;
    const yr=new Date(t1*1000).getUTCFullYear(),xt=t=>L+Math.min(1,Math.max(0,(t-t0)/span))*iw;
    // phases: soft alternating bands with a numbered name on top
    let ph='';const phs=(tl.phases||[]).map(p=>({p,a:parseWhen(p.from,yr),z:parseWhen(p.to,yr)})).filter(o=>o.a!=null);
    phs.forEach((o,i)=>{const x0=xt(o.a),x1=o.z!=null?xt(o.z):(phs[i+1]?xt(phs[i+1].a):L+iw),w=Math.max(0,x1-x0);
      if(i%2===0) ph+=`<rect x="${x0}" y="${top-34}" width="${w}" height="${ih+34}" fill="currentColor" fill-opacity=".035"/>`;
      ph+=`<line x1="${x0}" x2="${x0}" y1="${top-34}" y2="${H-bot}" stroke="currentColor" stroke-opacity=".16" stroke-dasharray="3 4"/>`;
      const nm=String(o.p.name||''),max=Math.max(4,Math.floor((w-14)/7));
      ph+=`<text class="phl" x="${x0+7}" y="${top-18}">${E((i+1)+'. '+(nm.length>max?nm.slice(0,max-1)+'…':nm))}<title>${E(nm)}</title></text>`;
      if(w>110) ph+=`<text class="phd" x="${x0+7}" y="${top-4}">${E(fmtD(o.a))}${o.z!=null?' – '+E(fmtD(o.z)):''}</text>`;
    });
    let tps='';(tl.turning_points||[]).forEach((t2,i)=>{const a=parseWhen(t2.when,yr);if(a==null)return;const x=xt(a);
      tps+=`<g class="tp"><line x1="${x}" x2="${x}" y1="${top}" y2="${H-bot}" stroke="#d24a2c" stroke-width="1.5" stroke-opacity=".85"/>
        <circle cx="${x}" cy="${top}" r="5" fill="#d24a2c"/><circle cx="${x}" cy="${top}" r="9" fill="#d24a2c" fill-opacity=".18"/>
        <title>Turning point · ${E(t2.when)}\n“${E(t2.likely_trigger||'')}”\n${E(t2.effect||'')}</title></g>`});
    const svg=view.querySelector('svg');
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.style.color='var(--mute)';
    // the chart sits under a mask whose soft left-to-right edge reveal() sweeps across
    const defs=`<defs><linearGradient id="tlv-fade" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
      <mask id="tlv-m" maskUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><rect id="tlv-mr" x="0" y="0" width="${W}" height="${H}" fill="#fff"/>
      <rect id="tlv-me" x="${W}" y="0" width="0" height="${H}" fill="url(#tlv-fade)"/></mask></defs>`;
    svg.innerHTML=defs+`<g mask="url(#tlv-m)">`+ph+paths+tps+`</g>`+ax+`<line id="tlv-g" x1="0" x2="0" y1="${top}" y2="${H-bot}" stroke="var(--ink)" stroke-opacity=".5" stroke-width="1" style="display:none;pointer-events:none"/>`;
    geo={L,iw,NB,b,W,H,top,bot,fmtDT,X};
  }
  // sweep the chart in from left to right (soft edge, ease-out)
  let rv=0;
  function reveal(){
    const svg=view&&view.querySelector('svg'); if(!svg||!geo) return;
    const W=geo.W,H=geo.H,edge=Math.min(220,W*.22),dur=2000,delay=200,t0=performance.now()+delay,id=++rv;
    const set=x=>{const r=svg.querySelector('#tlv-mr'),e=svg.querySelector('#tlv-me');if(!r||!e)return;
      r.setAttribute('width',Math.max(0,x));e.setAttribute('x',Math.max(0,x));e.setAttribute('width',edge)};
    set(-edge);
    const step=now=>{if(id!==rv)return;const p=Math.min(1,Math.max(0,(now-t0)/dur)),k=p<.5?2*p*p:1-Math.pow(-2*p+2,2)/2;
      set(-edge+(W+edge*2)*k);if(p<1)requestAnimationFrame(step)};
    requestAnimationFrame(step);
  }
  function hover(e){
    if(!on||!geo) return;
    const box=view.querySelector('.tlv-c'),r=box.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
    const tip=view.querySelector('.tlv-tip'),g=view.querySelector('#tlv-g');
    if(x<geo.L||x>geo.L+geo.iw||y<0||y>geo.H){tip.style.display='none';g.style.display='none';return}
    const i=Math.max(0,Math.min(geo.NB-1,Math.floor((x-geo.L)/geo.iw*geo.NB))),row=geo.b.raw[i],n=row.reduce((a,c)=>a+c,0);
    const gx=geo.X(i);g.setAttribute('x1',gx);g.setAttribute('x2',gx);g.style.display='';
    const a=geo.b.t0+i*geo.b.bw,z=a+geo.b.bw;
    const rows=row.map((v,k)=>({v,k})).filter(o=>o.v>0).sort((p,q)=>q.v-p.v).slice(0,5).map(o=>{
      const isN=o.k<geo.b.K-1,col=isN?NARRS[o.k].color:'var(--faint)',lab=isN?(NARRS[o.k].short||NARRS[o.k].label):'Off-topic';
      return `<div class="r"><i style="background:${col}"></i><span>${E(lab)}</span><span>${o.v}</span></div>`}).join('');
    tip.innerHTML=`<b>${E(geo.fmtDT(a))}</b><div style="color:var(--faint);font-size:11px;margin-bottom:4px">${n.toLocaleString()} comment${n===1?'':'s'} in the next ${Math.max(1,Math.round(geo.b.bw/3600))}h</div>${rows||'<div class="r">Quiet</div>'}`;
    tip.style.display='block';
    const tw=tip.offsetWidth,th=tip.offsetHeight;
    tip.style.left=Math.min(r.width-tw-4,Math.max(4,x+14))+'px';tip.style.top=Math.min(r.height-th-4,Math.max(4,y-th/2))+'px';
    // emphasise the layer under the cursor
    const k=e.target.closest&&e.target.closest('path.lay')?+e.target.closest('path.lay').dataset.k:null;
    view.querySelectorAll('path.lay').forEach(p=>p.style.opacity=k==null||+p.dataset.k===k?1:.35);
  }
  window.renderStream=function(tp){
    if(!ensure()) return;
    cur=tp;
    const has=!!(tp&&tp.stream&&tp.stream.length&&tp.model);
    btn.style.display=has?'inline-flex':'none';
    setOn(has&&on);
  };
})();
