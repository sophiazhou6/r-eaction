// r/eaction — reads data/events.json and data/<slug>/{clusters,connectivity}.json (see README.md)
/* ============================================================
   Settings
   ============================================================ */
// Where the data lives. Override with ?data=some/folder/ in the URL.
const DATA=(new URLSearchParams(location.search).get('data')||'data/').replace(/\/?$/,'/');
// Narrative colors, in order (largest narrative gets the first color). Narratives past these are shown as "other".
const PALETTE=['#2a78d6','#eb6834','#1baf7a','#e8a400','#e87ba4','#7b5cf0','#e34948','#4fb3d9'];
// Pairs of subreddits share a line only if their commenter overlap is at least this fraction of the largest overlap.
const LINK_MIN_FRACTION=.15;

/* ============================================================
   Helpers
   ============================================================ */
function rng(seed){return function(){seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
function hashStr(s){let h=2166136261;for(const ch of String(s)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return h>>>0}
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const hex=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16));
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>n>=1000?(n/1000).toFixed(1)+'k':String(n);
const pct=v=>Math.round(v*100)+'%';

const THEMES={
  dark: {ink:'236,233,225',off:'#5d606b',none:'#3a3c44',mid:'#B4B2A9'},
  light:{ink:'28,27,25',  off:'#bdb9ae',none:'#d9d5cb',mid:'#a9a69d'},
};
let T=THEMES.dark;
function stanceColor(v){
  if(v==null) return T.none;
  const A=hex('#b5722e'),M=hex(T.mid),B=hex('#3a9ea5');
  let a,b,t; if(v<.5){a=A;b=M;t=v/.5}else{a=M;b=B;t=(v-.5)/.5}
  return `rgb(${a.map((x,i)=>Math.round(x+(b[i]-x)*t))})`;
}
function stanceWords(v){
  if(v==null) return 'no clear stance';
  if(v<.2) return 'strongly '+AXIS['0']; if(v<.4) return 'leans '+AXIS['0'];
  if(v<=.6) return 'mixed'; if(v<=.8) return 'leans '+AXIS['1']; return 'strongly '+AXIS['1'];
}

/* ============================================================
   Data model — built from <topic>/clusters.json + connectivity.json
   ============================================================ */
let AXIS={question:'','0':'','1':''},NARRS=[],SUBS=[],DOTS=[],overall=[],DIST={},DMAX=1,JAC={},SHAREDN={},LINKS=[],MAXJ=1;
const share=(s,k)=>s.n?s.dots.filter(d=>d.k===k).length/s.n:0;
const dist=(a,b)=>DIST[a.name+'|'+b.name]??1;
const sim=(a,b)=>1-dist(a,b);
const jac=(a,b)=>JAC[a.name+'|'+b.name]||0;
const sharedPeople=(a,b)=>SHAREDN[a.name+'|'+b.name]??Math.round(jac(a,b)*(a.authors+b.authors)/(1+jac(a,b)));
function jsd(p,q){let d=0;for(let i=0;i<p.length;i++){const m=(p[i]+q[i])/2;if(p[i])d+=.5*p[i]*Math.log2(p[i]/m);if(q[i])d+=.5*q[i]*Math.log2(q[i]/m)}return Math.sqrt(Math.max(0,d))}
function recolor(){DOTS.forEach(c=>{c.colN=c.k>=0?NARRS[c.k].color:T.off;c.colS=stanceColor(c.stance)})}

function buildModel(cl,co){
  co=co||{};
  const comments=(cl.comments||[]).filter(c=>c&&c.subreddit);
  // stance: accept 0..1 or -1..1
  const neg=comments.some(c=>typeof c.stance==='number'&&c.stance<0);
  AXIS={question:co.stance_axis?.question||'','0':co.stance_axis?.['0']||'against','1':co.stance_axis?.['1']||'for'};

  // narratives: keep the largest (as many as there are colors), in the order the file lists them
  const counts={};comments.forEach(c=>{const r=c.narrative??c.cluster;if(r!=null&&r!==-1&&!c.is_noise)counts[r]=(counts[r]||0)+1});
  let narrs=(cl.narratives&&cl.narratives.length)?cl.narratives:Object.keys(counts).map(id=>({id,label:'Narrative '+id}));
  const keep=new Set([...narrs].sort((a,b)=>(counts[b.id]||0)-(counts[a.id]||0)).slice(0,PALETTE.length));
  narrs=narrs.filter(n=>keep.has(n));
  const idx=new Map(narrs.map((n,i)=>[String(n.id),i]));
  NARRS=narrs.map((n,i)=>({id:i,srcId:n.id,label:n.label||('Narrative '+n.id),short:n.short_label||n.label||('Narrative '+n.id),summary:n.summary||'',color:PALETTE[i]}));

  // communities
  const groupNames={};(co.community_groups?.groups||[]).forEach(g=>groupNames[g.id]=g.label);
  const meta={};(co.communities||[]).forEach(c=>meta[c.subreddit]=c);
  const byName=new Map();SUBS=[];
  comments.forEach(c=>{if(!byName.has(c.subreddit)){const g=meta[c.subreddit]?.group;const s={name:c.subreddit,group:g==null?'':(groupNames[g]??String(g)),dots:[]};byName.set(c.subreddit,s);SUBS.push(s)}});

  // comments -> dots
  DOTS=comments.map((c,i)=>{
    const raw=c.narrative??c.cluster;
    const k=(c.is_noise||raw==null||raw===-1||!idx.has(String(raw)))?-1:idx.get(String(raw));
    let stance=typeof c.stance==='number'?c.stance:null; if(stance!=null&&neg)stance=(stance+1)/2;
    const score=c.score??0,replies=c.n_replies??0;
    const eng=(c.score!=null||c.n_replies!=null)?Math.max(0,score)+4*replies:(c.weight??1);
    const d={id:String(c.id??('c'+i)),post_id:c.post_id,sub:byName.get(c.subreddit),k,stance,score,replies,eng,
      body:c.body||'',claim:c.claim||null,emotion:c.emotion||'',author:c.author||'',noise:k<0,m:true};
    d.sub.dots.push(d);return d;
  });

  // place each dot inside its blob: every narrative gets its own direction (same in every blob),
  // with a random-looking but repeatable spread per comment
  const N=Math.max(NARRS.length,1);
  DOTS.forEach(d=>{
    const r=rng(hashStr(d.id)),g=()=>{let u=0,v=0;while(!u)u=r();while(!v)v=r();return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v)};
    let ox=0,oy=0;
    for(let t=0;t<10;t++){
      if(d.k<0){ox=g()*.2;oy=g()*.2}
      else{const a=-Math.PI/2+d.k*2*Math.PI/N;ox=Math.cos(a)*.52+g()*.19;oy=Math.sin(a)*.52+g()*.19}
      if(ox*ox+oy*oy<.92)break;
      if(t===9){const m=Math.hypot(ox,oy);ox*=.9/m;oy*=.9/m}
    }
    d.ox=ox;d.oy=oy;
  });
  // dot size: rank by engagement, so a few top comments stand out
  {const by=[...DOTS].sort((a,b)=>a.eng-b.eng);by.forEach((c,i)=>{const p=by.length>1?i/(by.length-1):0;c.r=1.1+8.5*Math.pow(p,4)})}
  DOTS.sort((a,b)=>b.r-a.r);

  SUBS.forEach(s=>{
    s.n=s.dots.length; s.R=7.4*Math.sqrt(s.n);
    s.authors=new Set(s.dots.map(d=>d.author).filter(Boolean)).size||Math.round(s.n*.7);
    const on=s.dots.filter(d=>d.stance!=null); s.mean=on.length?on.reduce((a,d)=>a+d.stance,0)/on.length:null;
    const nd=s.dots.filter(d=>d.k>=0); s.p=NARRS.map(n=>nd.length?nd.filter(d=>d.k===n.id).length/nd.length:0);
  });
  overall=NARRS.map(n=>DOTS.length?DOTS.filter(d=>d.k===n.id).length/DOTS.length:0);

  // distance = how different the story mixes are (js_distance from the pipeline, or computed here)
  const jd=co.js_distance; DIST={};DMAX=0;
  const fromJD=(a,b)=>{if(!jd)return null;
    if(Array.isArray(jd)){const e=jd.find(e=>(e.a===a&&e.b===b)||(e.a===b&&e.b===a));return e?(e.d??e.distance??e.value??null):null}
    return jd[a]?.[b]??jd[b]?.[a]??jd[a+'|'+b]??jd[b+'|'+a]??null};
  SUBS.forEach(a=>SUBS.forEach(b=>{if(a===b)return;let d=fromJD(a.name,b.name);if(typeof d!=='number')d=jsd(a.p,b.p);DIST[a.name+'|'+b.name]=d;DMAX=Math.max(DMAX,d)}));
  if(!DMAX)DMAX=1;

  // lines = shared commenters
  JAC={};SHAREDN={};
  (co.shared_commenters||[]).forEach(e=>{const a=e.a??e.source,b=e.b??e.target,j=e.jaccard??e.weight??0;
    if(!byName.has(a)||!byName.has(b))return;JAC[a+'|'+b]=JAC[b+'|'+a]=j;if(e.shared!=null)SHAREDN[a+'|'+b]=SHAREDN[b+'|'+a]=e.shared});
  MAXJ=Math.max(0,...Object.values(JAC))||1;
  LINKS=[];
  for(let i=0;i<SUBS.length;i++)for(let j=i+1;j<SUBS.length;j++){const v=jac(SUBS[i],SUBS[j]);if(v>0&&v>=MAXJ*LINK_MIN_FRACTION)LINKS.push({a:SUBS[i],b:SUBS[j],j:v,v:.26*v/MAXJ})}
  LINKS.sort((a,b)=>a.j-b.j);

  layout();
  return {AXIS,NARRS,SUBS,DOTS,overall,DIST,DMAX,JAC,SHAREDN,LINKS,MAXJ};
}
function setModel(m){({AXIS,NARRS,SUBS,DOTS,overall,DIST,DMAX,JAC,SHAREDN,LINKS,MAXJ}=m);recolor()}

/* ============================================================
   Layout: subreddits telling similar stories sit close; lines stay clear of other blobs
   ============================================================ */
const targetOf=(a,b)=>{const dn=dist(a,b)/DMAX,jn=1-dn;return{jn,t:a.R+b.R+30+400*Math.pow(dn,1.3)}};
function tick(alpha){
  for(let i=0;i<SUBS.length;i++)for(let j=i+1;j<SUBS.length;j++){
    const a=SUBS[i],b=SUBS[j];
    const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||.01,ux=dx/d,uy=dy/d;
    const {jn,t}=targetOf(a,b);
    let f=(d-t)*.008*(.25+jn);
    const minD=a.R+b.R+(Math.abs(uy)>.45?120:70); if(d<minD) f-=(minD-d)*.3;
    f*=alpha; a.vx+=ux*f;a.vy+=uy*f;b.vx-=ux*f;b.vy-=uy*f;
  }
  LINKS.forEach(L=>{const a=L.a,b=L.b;SUBS.forEach(c=>{
    if(c===a||c===b)return;const g=segGap(a,b,c);if(!g)return;
    const f=g.over*.35*alpha;c.vx+=g.nx*f;c.vy+=g.ny*f;a.vx-=g.nx*f*.5;a.vy-=g.ny*f*.5;b.vx-=g.nx*f*.5;b.vy-=g.ny*f*.5})});
  SUBS.forEach(s=>{s.vx-=s.x*.0007*alpha;s.vy-=s.y*.0007*alpha;s.vx*=.82;s.vy*=.82;s.x+=s.vx;s.y+=s.vy});
}
// how far blob c intrudes on the straight line a→b (null if clear)
function segGap(a,b,c){
  const abx=b.x-a.x,aby=b.y-a.y,len2=abx*abx+aby*aby||1;
  const t=Math.max(0,Math.min(1,((c.x-a.x)*abx+(c.y-a.y)*aby)/len2));
  const px=a.x+abx*t,py=a.y+aby*t,dx=c.x-px,dy=c.y-py,d=Math.hypot(dx,dy)||.01,need=c.R*1.1+34;
  return d<need?{over:need-d,nx:dx/d,ny:dy/d}:null;
}
function crossings(){let n=0;LINKS.forEach(L=>SUBS.forEach(c=>{if(c!==L.a&&c!==L.b&&segGap(L.a,L.b,c))n++}));return n}
function stress(){let e=crossings()*1e6;for(let i=0;i<SUBS.length;i++)for(let j=i+1;j<SUBS.length;j++){const a=SUBS[i],b=SUBS[j],{jn,t}=targetOf(a,b);e+=(.25+jn)*(Math.hypot(a.x-b.x,a.y-b.y)-t)**2}return e}
function layout(){
  // try several starting arrangements and keep the best (fewer tries for big events)
  const tries=SUBS.length>25?8:SUBS.length>15?16:48;
  let best=null,be=Infinity;
  for(let k=0;k<tries;k++){
    const r=rng(300+k);
    SUBS.forEach(s=>{const a=r()*Math.PI*2,d=200+r()*300;s.x=Math.cos(a)*d;s.y=Math.sin(a)*d;s.vx=s.vy=0});
    for(let i=0;i<260;i++)tick(1);
    for(let i=0,a=.4;i<300;i++,a*=.985)tick(a);
    const e=stress(); if(e<be){be=e;best=SUBS.map(s=>[s.x,s.y])}
  }
  if(best) SUBS.forEach((s,i)=>{s.x=best[i][0];s.y=best[i][1];s.vx=s.vy=0;s.bx=s.x;s.by=s.y});
}

/* ============================================================
   Canvas + view
   ============================================================ */
const stage=document.getElementById('stage'),cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let W=0,H=0;
function resize(){const dpr=window.devicePixelRatio||1;W=stage.clientWidth;H=stage.clientHeight;cv.width=W*dpr;cv.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0)}
// Reshape the layout to the window: wide window -> wider layout, tall window -> taller.
// Starts from the story-similarity layout, stretches it, then nudges blobs apart
// (and off lines they don't belong to) so nothing overlaps.
let lastAspect=0,aspectTimer=null;
function relaxStep(){
  for(let i=0;i<SUBS.length;i++)for(let j=i+1;j<SUBS.length;j++){
    const a=SUBS[i],b=SUBS[j],dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||.01,ux=dx/d,uy=dy/d;
    const minD=a.R+b.R+(Math.abs(uy)>.45?110:60); if(d>=minD) continue;
    const f=(minD-d)*.25; a.vx-=ux*f;a.vy-=uy*f;b.vx+=ux*f;b.vy+=uy*f;
  }
  LINKS.forEach(L=>{const a=L.a,b=L.b;SUBS.forEach(c=>{
    if(c===a||c===b)return;const g=segGap(a,b,c);if(!g)return;
    const f=g.over*.3;c.vx+=g.nx*f;c.vy+=g.ny*f;a.vx-=g.nx*f*.5;a.vy-=g.ny*f*.5;b.vx-=g.nx*f*.5;b.vy-=g.ny*f*.5})});
  SUBS.forEach(s=>{s.vx*=.6;s.vy*=.6;s.x+=s.vx;s.y+=s.vy});
}
function adaptAspect(){
  if(W<60||H<60||!SUBS.length) return;
  const BASE=SUBS.map(s=>[s.bx,s.by]);
  const target=(W-PAD.l-PAD.r)/(H-PAD.t-PAD.b);
  if(Math.abs(target-lastAspect)<.03) return; lastAspect=target;
  let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;
  SUBS.forEach((s,i)=>{const[x,y]=BASE[i];x0=Math.min(x0,x-s.R);x1=Math.max(x1,x+s.R);y0=Math.min(y0,y-s.R);y1=Math.max(y1,y+s.R+30)});
  const k=clamp(Math.sqrt(target/((x1-x0)/(y1-y0))),.55,2);
  SUBS.forEach((s,i)=>{s.x=BASE[i][0]*k;s.y=BASE[i][1]/k;s.vx=s.vy=0});
  for(let it=0;it<300;it++) relaxStep();
  // pushing blobs apart changes the shape again, so re-stretch until it matches the window
  for(let pass=0;pass<4;pass++){
    let a0=1e9,a1=-1e9,b0=1e9,b1=-1e9;
    SUBS.forEach(s=>{a0=Math.min(a0,s.x-s.R);a1=Math.max(a1,s.x+s.R);b0=Math.min(b0,s.y-s.R);b1=Math.max(b1,s.y+s.R+30)});
    const f=clamp(Math.sqrt(target/((a1-a0)/(b1-b0))),.8,1.25); if(Math.abs(f-1)<.02) break;
    const cx=(a0+a1)/2,cy=(b0+b1)/2;
    SUBS.forEach(s=>{s.x=cx+(s.x-cx)*f;s.y=cy+(s.y-cy)/f;s.vx=s.vy=0});
    for(let it=0;it<200;it++) relaxStep();
  }
  if(typeof fit==='function'&&!S.node&&!manual) fit(true);
}
new ResizeObserver(()=>{resize();clearTimeout(aspectTimer);aspectTimer=setTimeout(adaptAspect,120)}).observe(stage); resize();

const view={s:.5,cx:0,cy:0},tv={s:.5,cx:0,cy:0}; let zoom=0,manual=false;
const PAD={t:46,b:18,l:16,r:16};
const scx=()=>PAD.l+(W-PAD.l-PAD.r)/2, scy=()=>PAD.t+(H-PAD.t-PAD.b)/2;
const toS=(x,y)=>({x:scx()+(x-view.cx)*view.s,y:scy()+(y-view.cy)*view.s});
const toW=(x,y)=>({x:(x-scx())/view.s+view.cx,y:(y-scy())/view.s+view.cy});
function fit(snap){
  if(W<60||H<60||!SUBS.length) return;
  if(manual){const k=.3;view.s+=(tv.s-view.s)*k;view.cx+=(tv.cx-view.cx)*k;view.cy+=(tv.cy-view.cy)*k;zoom+=((S.node?1:0)-zoom)*.13;return}
  let ts,tcx,tcy;
  if(S.node){
    const n=S.node; tcx=n.x; tcy=n.y;
    ts=Math.min(5,(W-PAD.l-PAD.r)/(n.R*2*1.75),(H-PAD.t-PAD.b)/(n.R*2*1.3));
  }else{
    let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;
    SUBS.forEach(s=>{x0=Math.min(x0,s.x-s.R);x1=Math.max(x1,s.x+s.R);y0=Math.min(y0,s.y-s.R-4);y1=Math.max(y1,s.y+s.R+(S.hl!=null?44:30))});
    ts=Math.min(4,(W-PAD.l-PAD.r)/(x1-x0),(H-PAD.t-PAD.b)/(y1-y0)); tcx=(x0+x1)/2; tcy=(y0+y1)/2;
  }
  tv.s=ts;tv.cx=tcx;tv.cy=tcy;
  const k=snap?1:.13;
  view.s+=(ts-view.s)*k; view.cx+=(tcx-view.cx)*k; view.cy+=(tcy-view.cy)*k;
  zoom+=((S.node?1:0)-zoom)*k;
}

/* ============================================================
   State + drawing
   ============================================================ */
const S={mode:'narrative',hl:null,node:null,dot:null,q:''};
let hoverNode=null,hoverDot=null;
const ink=a=>`rgba(${T.ink},${a})`;
const FONT=(sz,w=400)=>`${w} ${sz}px "Helvetica Neue",Helvetica,Arial,sans-serif`;

// Find a spot for each subreddit name that doesn't sit on another blob or another name:
// below the blob first, then above, right, left.
let LP=new Map();
function placeLabels(s){
  LP=new Map(); const placed=[], lh=S.hl!=null?28:15;
  ctx.font=FONT(11,600);
  const hitsBlob=(rc,self)=>SUBS.some(c=>{if(c===self)return false;const q=toS(c.x,c.y),r=c.R*s*1.02;
    const nx=Math.max(rc.x,Math.min(q.x,rc.x+rc.w)),ny=Math.max(rc.y,Math.min(q.y,rc.y+rc.h));return Math.hypot(q.x-nx,q.y-ny)<r});
  const hitsLabel=rc=>placed.some(o=>rc.x<o.x+o.w&&rc.x+rc.w>o.x&&rc.y<o.y+o.h&&rc.y+rc.h>o.y);
  [...SUBS].sort((a,b)=>b.R-a.R).forEach(n=>{
    const q=toS(n.x,n.y),r=n.R*s,w=ctx.measureText('r/'+n.name).width+8;
    const cands=[
      {x:q.x,y:q.y+r+16,al:'center'},
      {x:q.x,y:q.y-r-lh+6,al:'center'},
      {x:q.x+r*.9+8,y:q.y+4,al:'left'},
      {x:q.x-r*.9-8,y:q.y+4,al:'right'},
    ];
    let best=null,bs=1e9;
    for(const c of cands){
      const rx=c.al==='center'?c.x-w/2:c.al==='left'?c.x-2:c.x-w+2;
      const rc={x:rx,y:c.y-12,w,h:lh};
      let sc=(hitsBlob(rc,n)?10:0)+(hitsLabel(rc)?10:0)+(rc.x<0||rc.x+rc.w>W||rc.y<PAD.t-10||rc.y+rc.h>H?5:0);
      if(sc<bs){bs=sc;best={...c,rc}} if(!sc)break;
    }
    placed.push(best.rc); LP.set(n,best);
  });
}
function draw(){
  if(W<60||H<60||document.body.classList.contains('at-home')) return;
  ctx.clearRect(0,0,W,H);
  const s=view.s,F=S.node,ov=1-zoom,hov=F?null:hoverNode;
  placeLabels(s);

  // straight lines, edge to edge (overview only)
  {
    const la=Math.max(ov,.18);
    ctx.lineCap='round';
    ctx.save();ctx.beginPath();ctx.rect(0,0,W,H);
    ctx.font=FONT(11,600);
    SUBS.forEach(n=>{const q=toS(n.x,n.y),r=n.R*s*1.08;ctx.moveTo(q.x+r,q.y);ctx.arc(q.x,q.y,r,0,Math.PI*2);
      if(ov>.02){const L=LP.get(n).rc;ctx.rect(L.x-4,L.y-2,L.w+8,L.h+4)}});
    ctx.clip('evenodd');
    LINKS.forEach(L=>{
      const pa=toS(L.a.x,L.a.y),pb=toS(L.b.x,L.b.y),dx=pb.x-pa.x,dy=pb.y-pa.y,d=Math.hypot(dx,dy);
      const ra=L.a.R*s*1.08,rb=L.b.R*s*1.08; if(d<=ra+rb+2) return;
      const ux=dx/d,uy=dy/d,hot=hov&&(L.a===hov||L.b===hov);
      ctx.strokeStyle=ink((hot?.6:hov?.08:.16+L.v*1.3)*la);
      ctx.lineWidth=1+L.v*16;
      ctx.beginPath();ctx.moveTo(pa.x+ux*ra,pa.y+uy*ra);ctx.lineTo(pb.x-ux*rb,pb.y-uy*rb);ctx.stroke();
    });
    ctx.restore();
  }

  // dots
  for(const c of DOTS){
    const n=c.sub,x=scx()+(n.x+c.ox*n.R-view.cx)*s,y=scy()+(n.y+c.oy*n.R-view.cy)*s;
    let a=.9;
    if(c.noise) a=.4;
    if(S.mode==='stance'&&c.stance==null) a=.5;
    if(S.hl!=null&&c.k!==S.hl) a=.07;
    if(S.q&&!c.m) a=.06;
    if(F){ if(n!==F) a*=1-.9*zoom }
    else if(hov&&n!==hov) a*=.4;
    ctx.globalAlpha=a; ctx.fillStyle=S.mode==='stance'?c.colS:c.colN;
    ctx.beginPath();ctx.arc(x,y,Math.max(.7,c.r*s),0,6.2832);ctx.fill();
  }
  ctx.globalAlpha=1;

  // ring around the hovered blob

  // ring on hovered / selected dot
  [hoverDot,S.dot].forEach((c,i)=>{ if(!c) return;
    const p=toS(c.sub.x+c.ox*c.sub.R,c.sub.y+c.oy*c.sub.R);
    ctx.strokeStyle=ink(i?.95:.6);ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(p.x,p.y,c.r*s+3.5,0,7);ctx.stroke();
  });

  // "% shared" labels for the hovered blob's lines
  if(hov){
    ctx.font=FONT(12.5);ctx.textAlign='center';ctx.textBaseline='middle';
    const pills=[];
    LINKS.filter(L=>L.a===hov||L.b===hov).sort((a,b)=>b.j-a.j).forEach(L=>{
      const pa=toS(L.a.x,L.a.y),pb=toS(L.b.x,L.b.y),d=Math.hypot(pb.x-pa.x,pb.y-pa.y);
      const ux=(pb.x-pa.x)/d,uy=(pb.y-pa.y)/d,ra=L.a.R*s,rb=L.b.R*s; if(d<ra+rb+40) return;
      const x0=pa.x+ux*ra,y0=pa.y+uy*ra,x1=pb.x-ux*rb,y1=pb.y-uy*rb;
      // put the label somewhere along the line that isn't on top of another blob
      const t=pct(L.j)+' shared',w=ctx.measureText(t).width+14;
      const clash=(cx,cy)=>{const rc={x:cx-w/2,y:cy-10,w,h:20};
        if(SUBS.some(n=>{const q=toS(n.x,n.y),nx=Math.max(rc.x,Math.min(q.x,rc.x+w)),ny=Math.max(rc.y,Math.min(q.y,rc.y+20));return Math.hypot(q.x-nx,q.y-ny)<n.R*s*1.05}))return true;
        return [...[...LP.values()].map(l=>l.rc),...pills].some(o=>rc.x<o.x+o.w&&rc.x+w>o.x&&rc.y<o.y+o.h&&rc.y+20>o.y)};
      let mx=null,my=null;
      for(const f of [.5,.4,.6,.3,.7,.22,.78,.15,.85]){const cx=x0+(x1-x0)*f,cy=y0+(y1-y0)*f;if(!clash(cx,cy)){mx=cx;my=cy;break}}
      if(mx==null) return;   // no clear spot: skip the pill (the line itself still shows)
      pills.push({x:mx-w/2,y:my-10,w,h:20});
      ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--bg');
      ctx.beginPath();ctx.roundRect(mx-w/2,my-10,w,20,10);ctx.fill();
      ctx.fillStyle=ink(.85);ctx.fillText(t,mx,my+1);
    });
  }

  // blob labels (overview)
  if(ov>.02){
    ctx.textBaseline='alphabetic';
    SUBS.forEach(n=>{
      const L=LP.get(n); ctx.textAlign=L.al;
      ctx.globalAlpha=ov*(hov?(n===hov?1:.35):1);
      ctx.font=FONT(11,n===hov?600:400);ctx.fillStyle=ink(n===hov?.9:.45);
      ctx.fillText('r/'+n.name,L.x,L.y);
      let t=null; if(S.hl!=null){t=pct(share(n,S.hl));ctx.fillStyle=SM()?ink(.75):NARRS[S.hl].color}
      if(t){ctx.font=FONT(10.5,600);ctx.fillText(t,L.x,L.y+14)}
    });
    ctx.globalAlpha=1;
  }

  // zoomed: label each narrative's direction around the blob
  if(F&&zoom>.05){
    const p=toS(F.x,F.y),r=F.R*s;
    ctx.globalAlpha=zoom;ctx.textBaseline='middle';
    NARRS.forEach(nr=>{
      const v=share(F,nr.id); if(v<.04) return;
      const a=-Math.PI/2+nr.id*2*Math.PI/NARRS.length,cx=Math.cos(a),cy=Math.sin(a);
      const x=p.x+cx*r*1.1,y=p.y+cy*r*1.1+(cy>.5?8:cy<-.5?-8:0);
      ctx.textAlign=cx>.3?'left':cx<-.3?'right':'center';
      const dim=S.hl!=null&&S.hl!==nr.id;
      ctx.globalAlpha=zoom*(dim?.3:1);
      ctx.font=FONT(12,600);ctx.fillStyle=SM()?ink(.75):nr.color;ctx.fillText(nr.short+'  '+pct(v),x,y);
    });
    ctx.globalAlpha=1;
  }
}

/* ============================================================
   Interaction
   ============================================================ */
function hit(px,py){
  const w=toW(px,py);
  if(S.node||view.s>1.3){
    let best=null,bd=1e9;
    for(const c of (S.node?S.node.dots:DOTS)){
      if(S.hl!=null&&c.k!==S.hl) continue; if(S.q&&!c.m) continue;
      const d=Math.hypot(c.sub.x+c.ox*c.sub.R-w.x,c.sub.y+c.oy*c.sub.R-w.y);
      if(d<c.r+4/view.s&&d<bd){bd=d;best=c}
    }
    if(best) return {dot:best};
  }
  for(const n of SUBS) if(Math.hypot(n.x-w.x,n.y-w.y)<n.R*1.1) return {node:n};
  return {};
}
const pos=e=>{const r=cv.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top}};
let drag=null;
function setManual(v){if(manual!==v){manual=v;renderTop()}}
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  if(!manual){tv.s=view.s;tv.cx=view.cx;tv.cy=view.cy}
  setManual(true);
  const p=pos(e),wx=(p.x-scx())/tv.s+tv.cx,wy=(p.y-scy())/tv.s+tv.cy;
  const ns=clamp(tv.s*Math.exp(-e.deltaY*.0018),.15,8);
  tv.s=ns; tv.cx=wx-(p.x-scx())/ns; tv.cy=wy-(p.y-scy())/ns;
},{passive:false});
cv.addEventListener('mousedown',e=>{const p=pos(e);drag={x:p.x,y:p.y,moved:false}});
window.addEventListener('mouseup',()=>{setTimeout(()=>drag=null,0);if(drag&&drag.moved)cv.style.cursor='default'});
cv.addEventListener('mousemove',e=>{
  const p=pos(e);
  if(drag&&(drag.moved||Math.hypot(p.x-drag.x,p.y-drag.y)>4)){
    if(!drag.moved){drag.moved=true;if(!manual){tv.s=view.s;tv.cx=view.cx;tv.cy=view.cy}setManual(true);hideTip();hoverDot=null}
    tv.cx-=(p.x-drag.x)/tv.s; tv.cy-=(p.y-drag.y)/tv.s; view.cx=tv.cx; view.cy=tv.cy;
    drag.x=p.x;drag.y=p.y;cv.style.cursor='grabbing';return;
  }
  const h=hit(p.x,p.y);
  hoverDot=h.dot||null; hoverNode=h.node||(h.dot&&!S.node?h.dot.sub:null);
  cv.style.cursor=(h.dot||(h.node&&h.node!==S.node))?'pointer':'default';
  if(h.dot) showTip(h.dot,p); else hideTip();
});
cv.addEventListener('mouseleave',()=>{hoverDot=hoverNode=null;hideTip()});
cv.addEventListener('click',e=>{
  if(drag&&drag.moved) return;
  const p=pos(e),h=hit(p.x,p.y);
  if(h.dot){S.dot=h.dot;renderPanel()}
  else if(h.node&&h.node!==S.node) zoomTo(h.node);
  else if(S.dot){S.dot=null;renderPanel()}
});
function zoomTo(n){S.node=n;S.dot=null;hoverNode=null;manual=false;hideTip();renderAll()}
function back(){S.node=null;S.dot=null;manual=false;renderAll()}
window.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT') return;
  if(e.key==='Escape'){ if(S.dot){S.dot=null;renderPanel()} else if(S.node) back(); else if(S.hl!=null){S.hl=null;renderAll()} }
});

const tip=document.getElementById('tip');
function showTip(c,p){
  tip.innerHTML=`${esc(c.body)}<div class="m">▲ ${fmt(c.score)} · ${c.replies} replies · <span style="color:${c.k>=0?NC(NARRS[c.k]):T.off}">${c.k>=0?esc(NARRS[c.k].short):'off-topic'}</span></div>`;
  tip.style.opacity=1;
  const tw=tip.offsetWidth,th=tip.offsetHeight;let x=p.x+16,y=p.y+16;
  if(x+tw>W-8)x=p.x-tw-16; if(y+th>H-8)y=p.y-th-16;
  tip.style.left=x+'px';tip.style.top=y+'px';
}
function hideTip(){tip.style.opacity=0}

document.getElementById('modeSeg').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;S.mode=b.dataset.mode;
  document.querySelectorAll('#modeSeg button').forEach(x=>x.classList.toggle('on',x===b));renderAll();
});

/* ---------- search ---------- */
const qi=document.getElementById('q'),dd=document.getElementById('dd');
let ddItems=[],ddAct=0;
// The topics, from data/events.json. A search that mentions any of a topic's keywords opens that topic.
let TOPICS=[];
async function getJSON(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(url+' → '+r.status);return r.json()}
async function loadEvents(){
  try{TOPICS=await getJSON(DATA+'events.json')}catch(e){console.warn('Could not load events.json',e);TOPICS=[]}
  TOPICS.forEach(t=>{t.keys=[...(t.keywords||t.aliases||[]),t.query,t.title].filter(Boolean).map(k=>k.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim())});
}
function matchTopic(q){
  const t=' '+q.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()+' ';
  let best=null,bs=0;
  TOPICS.forEach(tp=>{const sc=tp.keys.filter(k=>t.includes(' '+k+' ')||t.includes(' '+k+'s ')).length;if(sc>bs){bs=sc;best=tp}});
  return best;
}
let CUR=null;
const missing=document.getElementById('missing');
function go(tp){location.hash=tp?tp.slug:'home'}
function showNote(title,msg,btn){
  missing.innerHTML=`<h3>${esc(title)}</h3>${msg?`<p>${msg}</p>`:''}${btn?`<button id="missBack">${esc(btn.label)}</button>`:''}`;
  missing.classList.add('on'); if(btn) document.getElementById('missBack').onclick=btn.go;
}
async function route(){
  const tp=TOPICS.find(t=>t.slug===location.hash.slice(1));
  if(!tp){CUR=null;document.body.classList.add('at-home');hq.value='';herr.innerHTML='';setTimeout(()=>hq.focus(),0);return}
  CUR=tp; document.body.classList.remove('at-home');
  document.getElementById('evTitle').textContent=tp.title;
  document.getElementById('evTag').style.display=tp.sample?'':'none';
  S.node=null;S.dot=null;S.hl=null;manual=false;hideTip();
  if(!tp.model){
    document.getElementById('question').textContent='';
    document.getElementById('modeSeg').style.visibility='hidden';
    showNote('Loading '+tp.title+'…');renderAll();
    try{
      const base=tp.path||(DATA+tp.slug+'/');
      const [cl,co]=await Promise.all([getJSON(base+'clusters.json'),getJSON(base+'connectivity.json')]);
      tp.model=buildModel(cl,co);
    }catch(e){
      console.warn(e);
      if(CUR!==tp) return;
      const local=location.protocol==='file:'?' The page also has to be opened through a local web server (see the README), not by double-clicking the file.':'';
      showNote(tp.title,`We don’t have data for this topic yet. Add <code>${esc((tp.path||DATA+tp.slug+'/'))}clusters.json</code> and <code>connectivity.json</code>.${local}`,
        TOPICS.find(t=>t.model)?{label:'Back home',go:()=>go(null)}:null);
      renderAll(); return;
    }
    if(CUR!==tp) return;
  }
  setModel(tp.model);
  missing.classList.remove('on');
  document.getElementById('question').textContent=AXIS.question;
  document.getElementById('modeSeg').style.visibility='';
  renderAll(); lastAspect=0;
  requestAnimationFrame(()=>{resize();adaptAspect();fit(true)});
}
window.addEventListener('hashchange',route);
document.getElementById('homeLink').onclick=()=>go(null);

/* ---------- home page ---------- */
const hq=document.getElementById('hq'),herr=document.getElementById('herr');
function renderHome(){
  document.getElementById('htry').innerHTML=TOPICS.length?'<span>Try</span>'+TOPICS.map((t,i)=>`<button type="button" data-t="${i}">${esc(t.title)}</button>`).join(''):'';
  document.getElementById('hfoot').textContent=TOPICS.length?TOPICS.length+' events analyzed':'No events found: check data/events.json (and that the page is served over HTTP).';
}
document.getElementById('htry').addEventListener('click',e=>{const b=e.target.closest('[data-t]');if(b)go(TOPICS[+b.dataset.t])});
document.getElementById('hform').addEventListener('submit',e=>{
  e.preventDefault(); const q=hq.value.trim(); if(!q) return;
  const tp=matchTopic(q);
  if(tp) go(tp); else herr.innerHTML=`<b>Oops!</b> We don’t have data on “${esc(q)}” yet. Try one of these:`;
});
hq.addEventListener('input',()=>{herr.innerHTML=''});

function applyQuery(q){
  S.q=q.trim().toLowerCase();
  DOTS.forEach(c=>c.m=!S.q||c.body.toLowerCase().includes(S.q)||(c.claim||'').toLowerCase().includes(S.q));
  renderAll();
}
function buildDD(){
  const raw=qi.value.trim(); ddItems=[];
  let h='';
  if(!raw){
    h='<div class="h">Analyzed events</div>';
    TOPICS.forEach(t=>{ddItems.push(()=>go(t));h+=`<div class="it" data-i="${ddItems.length-1}">${esc(t.title)}<span class="n">${t===CUR?'current':''}</span></div>`});
  }else{
    const t=matchTopic(raw);
    if(t){ddItems.push(()=>go(t));h=`<div class="h">Event</div><div class="it" data-i="0">${esc(t.title)}<span class="n">${t===CUR?'current':''}</span></div>`}
    else h=`<div class="it na" style="cursor:default">Oops! No data on “${esc(raw)}” yet.</div>`;
  }
  dd.innerHTML=h; ddAct=0; markAct(); dd.classList.add('open');
}
function markAct(){dd.querySelectorAll('.it').forEach((el,i)=>el.classList.toggle('act',i===ddAct))}
function choose(i){const f=ddItems[i];dd.classList.remove('open');qi.blur();qi.value='';if(f)f()}
qi.addEventListener('input',buildDD);
qi.addEventListener('focus',buildDD);
qi.addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'){ddAct=Math.min(ddItems.length-1,ddAct+1);markAct();e.preventDefault()}
  else if(e.key==='ArrowUp'){ddAct=Math.max(0,ddAct-1);markAct();e.preventDefault()}
  else if(e.key==='Enter'){choose(ddAct)}
  else if(e.key==='Escape'){dd.classList.remove('open');qi.blur()}
});
dd.addEventListener('mousedown',e=>{const it=e.target.closest('.it');if(it){e.preventDefault();choose(+it.dataset.i)}});
qi.addEventListener('blur',()=>setTimeout(()=>dd.classList.remove('open'),100));

/* ---------- theme ---------- */
const SUN='<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/></svg>';
const MOON='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z"/></svg>';
function setTheme(t){
  document.documentElement.dataset.theme=t; T=THEMES[t]; recolor();
  document.querySelectorAll('[data-theme-btn]').forEach(b=>{b.innerHTML=t==='dark'?SUN:MOON;b.title=t==='dark'?'Switch to light mode':'Switch to dark mode'});
  try{localStorage.setItem('reaction-theme',t)}catch(e){}
    renderAll();
}
document.querySelectorAll('[data-theme-btn]').forEach(b=>b.onclick=()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));

/* ============================================================
   Panel + overlays
   ============================================================ */
const SM=()=>S.mode==='stance';
const NC=n=>SM()?'var(--mute)':n.color;
function split(dots){const on=dots.filter(d=>d.stance!=null);if(!on.length)return null;
  const lo=on.filter(d=>d.stance<.4).length/on.length,hi=on.filter(d=>d.stance>.6).length/on.length;return{lo,mid:1-lo-hi,hi}}
function splitBar(dots){const p=split(dots);if(!p)return '';
  return `<div class="sbar" title="${pct(p.lo)} ${AXIS['0']} · ${pct(p.hi)} ${AXIS['1']}"><i style="flex:${p.lo};background:#b5722e"></i><i style="flex:${p.mid};background:${T.mid}"></i><i style="flex:${p.hi};background:#3a9ea5"></i></div>`}
const stanceKey=()=>`<div class="skey"><span class="d" style="background:#b5722e"></span>${AXIS['0']}<span class="d" style="background:${T.mid};margin-left:8px"></span>mixed<span class="d" style="background:#3a9ea5;margin-left:8px"></span>${AXIS['1']}</div>`;
function narrList(vals,showSum,scope){
  if(SM()){
    scope=scope||DOTS;
    return stanceKey()+vals.map(o=>{const n=NARRS[o.k],on=S.hl===n.id;
      return `<div class="nrow st ${S.hl!=null&&!on?'dim':''}" data-hl="${n.id}">
        <div class="l"><span class="t">${esc(n.label)}</span><span class="n">${pct(o.v)}</span></div>
        ${splitBar(scope.filter(d=>d.k===n.id))}
        ${on&&showSum?`<div class="nsum" style="margin-left:0">${esc(n.summary)}</div>`:''}</div>`}).join('');
  }
  const max=Math.max(...vals.map(v=>v.v));
  return vals.map(o=>{const n=NARRS[o.k],on=S.hl===n.id;
    return `<div class="nrow ${S.hl!=null&&!on?'dim':''}" data-hl="${n.id}">
      <div class="l"><span class="sw" style="background:${n.color}"></span><span class="t">${esc(n.label)}</span><span class="n">${pct(o.v)}</span></div>
      <div class="bar"><i style="width:${o.v/max*100}%;background:${n.color}"></i></div>
      ${on&&showSum?`<div class="nsum">${esc(n.summary)}</div>`:''}</div>`}).join('');
}
function stanceBlock(dots){
  const on=dots.filter(d=>d.stance!=null); if(!on.length) return '<div class="sub">No stance data.</div>';
  const lo=on.filter(d=>d.stance<.4).length/on.length,hi=on.filter(d=>d.stance>.6).length/on.length,mid=1-lo-hi;
  const mean=on.reduce((a,d)=>a+d.stance,0)/on.length;
  return `<div class="stance"><div style="flex:${lo};background:#b5722e"></div><div style="flex:${mid};background:${T.mid}"></div><div style="flex:${hi};background:#3a9ea5"></div></div>
    <div class="stl"><span>${pct(lo)} ${AXIS['0']}</span><span>${pct(hi)} ${AXIS['1']}</span></div>`;
}
function redditLink(c){
  const id=String(c.id||'').replace(/^t1_/,''),post=String(c.post_id||'').replace(/^t3_/,'');
  if(!/^[a-z0-9]{5,10}$/i.test(id)||!/^[a-z0-9]{5,10}$/i.test(post)) return '';
  return `<p style="margin-top:16px"><a href="https://www.reddit.com/r/${encodeURIComponent(c.sub.name)}/comments/${post}/_/${id}/" target="_blank" rel="noopener">View on Reddit ↗</a></p>`;
}
function renderPanel(){
  const el=document.getElementById('panel');
  if(CUR&&!CUR.model){el.innerHTML=`<div class="kick">This event</div><div class="big" style="margin-top:3px">${esc(CUR.title)}</div><div class="sub">No data for this topic yet.</div>`;return}
  const vis=d=>(S.hl==null||d.k===S.hl)&&(!S.q||d.m);
  if(S.dot){
    const c=S.dot,n=c.k>=0?NARRS[c.k]:null;
    el.innerHTML=`<div class="link" data-back="1" style="border:0;padding-top:0"><span>← ${S.node?'r/'+esc(c.sub.name):'Back'}</span><span></span></div>
      <div class="kick" style="margin-top:14px;color:${n?NC(n):T.off}">${n?esc(n.label):'Off-topic'}</div>
      <blockquote>“${esc(c.body)}”</blockquote>
      ${c.claim?`<div class="claim">Claim: ${esc(c.claim)}</div>`:''}
      <dl><dt>Author</dt><dd>${esc(c.author)}</dd><dt>Score</dt><dd>▲ ${c.score}</dd><dt>Replies</dt><dd>${c.replies}</dd>
      <dt>Stance</dt><dd style="color:${stanceColor(c.stance)}">${stanceWords(c.stance)}</dd><dt>Emotion</dt><dd>${c.emotion}</dd></dl>
      ${redditLink(c)}`;
  }else if(S.node){
    const n=S.node,dots=n.dots.filter(vis);
    const nb=SUBS.filter(x=>x!==n).map(x=>({x,j:sim(n,x)})).sort((a,b)=>b.j-a.j).slice(0,3);
    const top=dots.filter(d=>!d.noise).sort((a,b)=>b.eng-a.eng).slice(0,5);
    el.innerHTML=`<div class="kick">${n.group} · ${n.n} comments</div><h2 style="margin-top:3px">r/${esc(n.name)}</h2>
      ${SM()
        ? `<div class="ph">${S.hl!=null?'Stance within “'+esc(NARRS[S.hl].short)+'”':'Where it lands'}</div>${stanceBlock(dots)}
           <div class="ph">Stance by narrative</div>${narrList(NARRS.map(x=>({k:x.id,v:share(n,x.id)})).filter(o=>o.v>=.03).sort((a,b)=>b.v-a.v),false,n.dots)}`
        : `<div class="ph">Narratives</div>${narrList(NARRS.map(x=>({k:x.id,v:share(n,x.id)})).filter(o=>o.v>=.03).sort((a,b)=>b.v-a.v),true)}`}
      <div class="ph">Tells the most similar story to</div>
      ${nb.map(o=>`<div class="brow" data-node="${o.x.name}"><span class="t">r/${esc(o.x.name)}</span><span class="track"><i style="width:${o.j*100}%"></i></span><span class="n">${pct(o.j)}</span></div>`).join('')}
      <div class="ph">Shares the most commenters with</div>
      ${SUBS.filter(x=>x!==n&&jac(n,x)>0).sort((a,b)=>jac(n,b)-jac(n,a)).slice(0,3).map(x=>`<div class="brow" data-node="${x.name}" title="~${sharedPeople(n,x)} people in both"><span class="t">r/${esc(x.name)}</span><span class="track"><i style="width:${jac(n,x)/MAXJ*100}%"></i></span><span class="n">${pct(jac(n,x))}</span></div>`).join('')||'<div class="sub">Almost nobody in common.</div>'}
      <div class="ph">Top comments${S.hl!=null?' in this narrative':''}${S.q?' matching “'+esc(S.q)+'”':''}</div>
      ${top.map(c=>`<div class="card" data-dot="${c.id}">${esc(c.body)}<div class="m">▲ ${fmt(c.score)} · ${c.replies} replies · ${esc(c.author)}</div></div>`).join('')||'<div class="sub">No matching comments.</div>'}`;
  }else{
    el.innerHTML=`<div class="kick">This event</div><div class="big" style="margin-top:3px">${DOTS.length.toLocaleString()} comments</div>
      <div class="sub">across ${SUBS.length} subreddits</div>
      <div class="ph">Narratives</div>${narrList(NARRS.map(x=>({k:x.id,v:overall[x.id]})).sort((a,b)=>b.v-a.v),true)}
      <div class="how">Each cloud is a subreddit and each dot is a comment. Bigger dots got more upvotes and replies. Subreddits that tell similar stories sit closer together, and thicker lines mean more commenters in common.</div>`;
  }
}
function renderTop(){
  let h='';
  if(S.node) h+=`<button class="crumb" id="backBtn">← All communities</button><span style="color:var(--faint)">/</span><span class="here">r/${esc(S.node.name)}</span>`;
  if(manual) h+=`<button class="crumb" id="resetBtn">Reset view</button>`;
  if(S.q) h+=`<span class="chip">“${esc(S.q)}” · ${DOTS.filter(c=>c.m).length}<button id="clearQ" title="Clear">×</button></span>`;
  if(S.hl!=null&&!S.node) h+=`<span class="chip" style="font-style:normal">${SM()?'':`<span class="sw" style="background:${NARRS[S.hl].color}"></span>`}${esc(NARRS[S.hl].short)}<button id="clearHl" title="Clear">×</button></span>`;
  const c=document.getElementById('crumbs');c.innerHTML=h;
  const bb=document.getElementById('backBtn');if(bb)bb.onclick=back;
  const rb=document.getElementById('resetBtn');if(rb)rb.onclick=()=>{manual=false;renderTop()};
  const cq=document.getElementById('clearQ');if(cq)cq.onclick=()=>{qi.value='';applyQuery('')};
  const ch=document.getElementById('clearHl');if(ch)ch.onclick=()=>{S.hl=null;renderAll()};
  const sl=document.getElementById('slegend');
  sl.style.display=S.mode==='stance'?'flex':'none';
  sl.innerHTML=`<span>${AXIS['0']}</span><span class="g" style="background:linear-gradient(90deg,#b5722e,${T.mid},#3a9ea5)"></span><span>${AXIS['1']}</span>`;
}
function renderAll(){renderPanel();renderTop()}
document.getElementById('panel').addEventListener('click',e=>{
  const h=e.target.closest('[data-hl]'); if(h){const k=+h.dataset.hl;S.hl=S.hl===k?null:k;renderAll();return}
  const n=e.target.closest('[data-node]'); if(n){zoomTo(SUBS.find(s=>s.name===n.dataset.node));return}
  const d=e.target.closest('[data-dot]'); if(d){S.dot=DOTS.find(c=>c.id===d.dataset.dot);renderPanel();return}
  if(e.target.closest('[data-back]')){S.dot=null;renderPanel()}
});

/* ============================================================
   Boot
   ============================================================ */
let saved=null;try{saved=localStorage.getItem('reaction-theme')}catch(e){}
setTheme(saved||(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'));
(async()=>{await loadEvents();renderHome();route()})();
(function frame(){fit();draw();requestAnimationFrame(frame)})();
