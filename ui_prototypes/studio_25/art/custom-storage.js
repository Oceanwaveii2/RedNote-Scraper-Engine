// Studio Custom is a library view. Mirror captures remain in their own archive.
export function setupCustomStorage({state,views,render,esc}) {
  const stylesheet=document.createElement('link');stylesheet.rel='stylesheet';stylesheet.href='art/custom-storage.css?v=1';document.head.append(stylesheet);
  const key='rn25-scatter-custom-manifest-v1',graph='http://127.0.0.1:5186';
  let supplied=[],imported=[];
  try{imported=JSON.parse(localStorage.getItem(key)||'[]');if(!Array.isArray(imported))imported=[];}catch{imported=[];}
  fetch('art/custom-library.json',{cache:'no-store'}).then(response=>response.json()).then(data=>{supplied=Array.isArray(data.items)?data.items:[];if(state.route==='storage'&&state.tab==='custom')render();}).catch(()=>{});
  window.addEventListener('message',event=>{if(event.origin!==graph||event.data?.type!=='scatter-custom-manifest'||!Array.isArray(event.data.items))return;
    imported=event.data.items.filter(item=>typeof item.id==='string'&&typeof item.name==='string').map(item=>({id:item.id,name:item.name,type:String(item.type||''),bytes:Number(item.bytes||0)}));
    localStorage.setItem(key,JSON.stringify(imported));if(state.route==='storage'&&state.tab==='custom')render();});
  window.addEventListener('storage',event=>{if(event.key!==key)return;try{imported=JSON.parse(event.newValue||'[]');if(!Array.isArray(imported))imported=[];}catch{imported=[];}if(state.route==='storage'&&state.tab==='custom')render();});
  const base=views.storage;
  const card=item=>`<article class="cs-card"><div class="cs-media">${item.preview?`<img loading="lazy" src="${graph+esc(item.preview)}" alt="">`:`<span>${item.type?.startsWith('audio/')?'♫':item.type?.startsWith('video/')?'▷':'▧'}</span>`}</div><div><small>${esc(item.category||(/audio|video/.test(item.type||'')?'Imported media':'Custom file'))}</small><h3>${esc(item.title||item.name)}</h3><p>${esc(item.origin||'Browser import · stored in Scatter Custom')}</p><a href="${graph}/#custom" target="_blank" rel="noopener noreferrer">Open in Scatter Cabinet ↗</a></div></article>`;
  const customView=()=>{const html=base();const start=html.indexOf('<div class="tabs">'),end=html.indexOf('</div>',start);const tabs=end<0?'':html.slice(start,end+6);
    if(state.tab==='all'){const entry=`<section class="panel cs-entry"><div><span class="badge">CUSTOM LIBRARY</span><h2>Custom music, videos & art</h2><p>${supplied.length} supplied board references · ${imported.length} browser imports. Separate from the RedNote mirror.</p></div><button type="button" data-action="storage-tab" data-id="custom">Open Custom Storage →</button></section>`;return end<0?entry+html:html.slice(0,end+6)+entry+html.slice(end+6);}
    if(state.tab!=='custom')return html;
    const query=state.query.toLowerCase(),staticItems=supplied.filter(item=>(item.title+' '+item.category).toLowerCase().includes(query)),localItems=imported.filter(item=>item.name.toLowerCase().includes(query));
    return `<section class="cs-library"><div class="cs-head"><div><h1>Custom Storage</h1><p>Music videos, authored art, and browser imports used by Scatter. This library is separate from captured RedNote profiles.</p></div><a href="${graph}/#custom" target="_blank" rel="noopener noreferrer">Open Scatter Cabinet ↗</a></div>${tabs}<div class="cs-count">${staticItems.length} supplied board references · ${localItems.length} browser imports</div><div class="cs-grid">${staticItems.map(card).join('')}${localItems.map(card).join('')}</div>${!staticItems.length&&!localItems.length?'<p>No custom items match this search.</p>':''}<p class="cs-note">Imported file bytes stay in the Scatter browser storage. Open the Cabinet to play or place them on the graph.</p></section>`;};
  views.storage=customView;views.profiles=customView;
}
