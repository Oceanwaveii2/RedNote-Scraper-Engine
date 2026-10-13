"""Fresh, local browser controls using a restricted native app API."""

LIVE_TOOLBAR_SCRIPT = r'''(() => {
  const id='rednote-archive-toolbar';
  if(document.getElementById(id)) return;
  const host=document.createElement('div'); host.id=id;
  host.style.cssText='position:fixed;inset:0 0 auto;height:52px;z-index:2147483647';
  const root=host.attachShadow({mode:'open'});
  root.innerHTML=`<style>
    *{box-sizing:border-box}section{display:flex;gap:6px;align-items:center;height:52px;padding:8px;background:#0e0e11;border-bottom:1px solid #ffffff12;color:#ededf0;font:13px "Segoe UI",sans-serif}
    button,input{font:inherit;color:inherit;background:#19191e;border:1px solid #ffffff18;border-radius:8px;padding:7px 10px;min-height:34px}button{cursor:pointer;white-space:nowrap}button:hover{border-color:#ff2442}button:disabled{opacity:.5;cursor:wait}
    input{flex:1;min-width:110px;background:#151519}button.main{background:#e31b38;color:#ffffff;border-color:#e31b38;font-weight:700}output{width:125px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#aaaab1;font-size:11px}
    .ownership{position:fixed;inset:52px 0 0;display:flex;align-items:center;justify-content:center;background:#00000099;backdrop-filter:blur(1px);overscroll-behavior:contain}.ownership[hidden],button[hidden]{display:none}.ownership div{max-width:390px;padding:24px;background:#19191e;border:1px solid #ffffff20;border-radius:16px;box-shadow:0 12px 50px #000a;text-align:center;line-height:1.5}.ownership strong{display:block;color:#ff2442;font-size:18px;margin-bottom:8px}.ownership p{margin:10px 0;color:#aaaab1}
    @media(max-width:950px){.optional{display:none}output{width:80px}button,input{padding:7px}}
  </style><section aria-label="RedNote archive browser controls">
    <button data-action="back" title="Back">←</button><button data-action="forward" title="Forward">→</button><button data-action="reload" title="Reload">↻</button>
    <input aria-label="RedNote address" spellcheck="false" autocomplete="off"><button data-action="navigate">Go</button><input type="search" class="site-search" aria-label="Search RedNote" placeholder="Search RedNote…"><button data-action="search" class="main">Search</button>
    <button data-action="clone" class="main">Clone profile</button><button data-action="capture" class="optional">Save note</button><button data-action="scroll-comments" class="optional">Load comments</button>
    <button data-action="download-open-media" class="optional">Save media</button><button data-action="pause-clone" hidden>Pause</button><button data-action="open-explorer">Browse</button><button data-action="focus-controls">Studio</button><output role="status">Ready</output>
  </section><div class="ownership" hidden><div><strong>Profile capture in progress</strong><p class="capture-phase"></p><p>This browser follows the saved queue. Browse RedNote opens an independent page while capture continues.</p><button data-action="open-explorer">Browse RedNote</button><button data-action="pause-clone">Pause capture</button><button data-action="focus-controls">Open Studio</button></div></div>`;
  (document.body||document.documentElement).appendChild(host);
  const input=root.querySelector('input'),status=root.querySelector('output');
  const canonical=()=>location.origin+location.pathname;
  input.value=canonical();
  const style=document.createElement('style');style.textContent='html{scroll-padding-top:60px}body{padding-top:52px !important}';host.after(style);
  const search=root.querySelector('.site-search');
  search.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();call('search');}};
  async function call(action){
    if(action==='search'){const term=search.value.trim();if(!term)return;input.value='https://www.rednote.com/search_result?'+new URLSearchParams({keyword:term});action='navigate';}
    const payload=action==='clone'?{url:location.href,options:{}}:action==='navigate'?{url:input.value}:{};
    try{
      if(!window.pywebview?.api?.dispatch)throw Error('App controls are connecting');
      status.textContent='Working…';
      const result=await window.pywebview.api.dispatch(action,payload);
      if(result?.error)throw Error(result.error);
      status.textContent=result?.message||(result?.jobId?'Profile queued':result?.comments!==undefined?`${result.comments} comments saved`:'Done');
      status.title=status.textContent;
    }catch(error){status.textContent=error.message;status.title=error.message;}
  }
  for(const button of root.querySelectorAll('button'))button.onclick=()=>call(button.dataset.action);
  input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();call('navigate');}};
  const ownership=root.querySelector('.ownership'),phase=root.querySelector('.capture-phase');
  let polling=false,wasCaptureBlocked=false;
  async function updateCapture(){
    if(polling||!window.pywebview?.api?.dispatch)return;
    polling=true;
    try{
      const state=await window.pywebview.api.dispatch('clone-state');
      const cooldown=Number(state?.cooldownRemainingSeconds)||0,blocked=!!state?.busy||cooldown>0;
      ownership.hidden=!blocked;
      ownership.querySelector('strong').textContent=cooldown>0?'Website requested a pause':'Profile capture in progress';
      const progress=state?.progress||{};
      phase.textContent=cooldown>0?`Cooldown: ${Math.ceil(cooldown/60)} minute(s). Resume manually in Studio afterward.`:(state?.phase||'Capturing').replaceAll('_',' ')+` · ${progress.savedMedia||0} media · ${progress.comments||0} comments saved`;
      input.disabled=blocked;search.disabled=blocked;
      for(const button of root.querySelectorAll('section button')){
        const action=button.dataset.action;
        button.disabled=blocked&&!['focus-controls','pause-clone','open-explorer'].includes(action);
        if(action==='pause-clone')button.hidden=!state?.busy;
      }
      if(blocked){status.textContent=cooldown>0?'Website pause':(state?.phase||'Capturing').replaceAll('_',' ');status.title=state?.message||status.textContent;}
      else if(wasCaptureBlocked){status.textContent=(state?.status||'Ready').replaceAll('_',' ');status.title=state?.message||status.textContent;}
      wasCaptureBlocked=blocked;
    }catch(_){/* Native app may be closing. */}finally{polling=false;}
  }
  ownership.addEventListener('wheel',e=>e.preventDefault(),{passive:false});
  ownership.addEventListener('touchmove',e=>e.preventDefault(),{passive:false});
  document.addEventListener('keydown',e=>{if(!ownership.hidden&&e.isTrusted&&!e.composedPath().includes(host)&&['Enter',' ','ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();}},true);
  updateCapture();setInterval(updateCapture,3000);
  setInterval(()=>{if(root.activeElement!==input)input.value=canonical();},1500);
})()'''


EXPLORER_TOOLBAR_SCRIPT = r'''(() => {
  const id='rednote-explorer-toolbar';
  if(document.getElementById(id)) return;
  const host=document.createElement('div');host.id=id;
  host.style.cssText='position:fixed;inset:0 0 auto;height:114px;z-index:2147483647';
  const root=host.attachShadow({mode:'open'});
  root.innerHTML=`<style>
    *{box-sizing:border-box}header{height:114px;background:#0e0e11;border-bottom:1px solid #ffffff12;color:#ededf0;font:13px "Segoe UI",sans-serif}
    section{display:flex;align-items:center;gap:6px;padding:8px 12px 4px;height:50px}button,input,select{font:inherit;color:inherit;background:#19191e;border:1px solid #ffffff18;border-radius:8px;padding:7px 10px;min-height:34px}
    button{cursor:pointer;white-space:nowrap}button:hover{border-color:#ff2442}button:disabled{opacity:.5;cursor:wait}button.main{background:#e31b38;color:#fff;border-color:#e31b38;font-weight:700}input{flex:1;min-width:100px;background:#151519}
    strong{color:#ff2442;white-space:nowrap;font-size:12px}output{display:block;color:#aaaab1;font-size:11px;padding:2px 14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .pause{position:fixed;inset:114px 0 0;display:flex;align-items:center;justify-content:center;background:#0009;backdrop-filter:blur(1px);overscroll-behavior:contain}.pause[hidden]{display:none}.pause div{max-width:380px;padding:24px;background:#19191e;border:1px solid #ffffff20;border-radius:16px;text-align:center;line-height:1.6}
    .pause strong{font-size:18px}.pause p{color:#aaaab1}@media(max-width:850px){section>strong{display:none}section{padding-inline:8px}button{padding:7px}}
  </style><header><section aria-label="Independent RedNote browsing controls">
    <strong>BROWSE REDNOTE</strong><button data-action="back" title="Back">←</button><button data-action="forward" title="Forward">→</button><button data-action="reload" title="Reload">↻</button>
    <input aria-label="RedNote address or search" placeholder="Paste a RedNote link or search…" spellcheck="false" autocomplete="off"><button data-action="navigate" class="main">Go</button><input type="search" class="site-search" aria-label="Search RedNote" placeholder="Search RedNote…"><button data-action="search" class="main">Search</button>
    <button data-action="copy-link">Copy link</button><button data-action="focus-controls">Studio</button>
  </section><section style="height:36px;padding-top:0" aria-label="Translation controls"><select class="translation-provider" aria-label="Translation app"><option value="gpt">GPT · Studio model</option><option value="edge">Edge · browser tools</option><option value="chrome">Chrome · browser tools</option></select><button data-action="translate-original">中文 Original</button><button data-action="translate-en">English</button><button data-action="translate-both">Both</button><span style="font-size:11px;color:#aaaab1">Visible text only</span></section><output role="status">Independent browsing · mining stays on its own page · same app login</output></header>
  <div class="pause" hidden><div><strong>Website requested a pause</strong><p></p><button data-action="focus-controls">Open Studio</button></div></div>`;
  (document.body||document.documentElement).appendChild(host);
  const style=document.createElement('style');style.textContent='html{scroll-padding-top:122px}body{padding-top:114px !important}';host.after(style);
  const input=root.querySelector('input'),output=root.querySelector('output'),pause=root.querySelector('.pause');
  const address=()=>{if(location.protocol!=='https:')return 'https://www.rednote.com/';const u=new URL(location.href);return u.origin+u.pathname+(u.searchParams.has('keyword')?'?'+new URLSearchParams({keyword:u.searchParams.get('keyword')}).toString():'');};
  input.value=address();
  const search=root.querySelector('.site-search');
  search.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();call('search');}};
  async function call(action){
    if(action.startsWith("translate-")){try{await translateViewport(action==='translate-original'?'original':action==='translate-en'?'english':'both');}catch(error){output.textContent=error.message;}return;}
    if(action==='search'){const term=search.value.trim();if(!term)return;input.value='https://www.rednote.com/search_result?'+new URLSearchParams({keyword:term});action='navigate';}
    try{
      if(action==='copy-link'){
        await navigator.clipboard.writeText(location.href);
        output.textContent='Link copied. Paste it into Single post or Import profile in Studio.';
        return;
      }
      if(!window.pywebview?.api?.dispatch)throw Error('App controls are connecting');
      const result=await window.pywebview.api.dispatch(action,action==='navigate'?{url:input.value}:{});
      if(result?.error)throw Error(result.error);
      output.textContent=result?.message||'Ready';
    }catch(error){output.textContent=action==='copy-link'?'Copy unavailable. Use the source page’s Share → Copy link.':error.message;}
  }
  for(const button of root.querySelectorAll('button'))button.onclick=()=>call(button.dataset.action);
  input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();call('navigate');}};
  const originals=new Map();let translationEpoch=0;
  function restoreOriginals(){translationEpoch++;for(const [node,record] of originals){if(node.isConnected&&node.nodeValue===record.shown)node.nodeValue=record.original;}originals.clear();}
  async function translateViewport(mode){
    if(mode==='original'){restoreOriginals();output.textContent='Original text restored.';return;}
    const selected=root.querySelector('.translation-provider').value;
    if(selected!=='gpt'){
      if(mode==='both'){output.textContent='Side-by-side mode uses the selected Studio GPT model. Edge/Chrome offer translated or original views in their own window.';return;}
      const result=await window.pywebview.api.dispatch('external-browser',{browser:selected});output.textContent=result.error||result.message;return;
    }
    restoreOriginals();const epoch=translationEpoch;
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),nodes=[];let node;
    while((node=walker.nextNode())&&nodes.length<48){
      const value=node.nodeValue.trim(),parent=node.parentElement;
      if(!value||node.nodeValue.length>1800||!parent||parent.closest('script,style,noscript,input,textarea,select,button,[contenteditable],#rednote-explorer-toolbar')||!/[\u3400-\u9fff]/.test(value))continue;
      const style=getComputedStyle(parent);if(style.visibility==='hidden'||style.display==='none')continue;
      const range=document.createRange();range.selectNodeContents(node);const box=range.getBoundingClientRect();
      if(box.width>0&&box.height>0&&box.bottom>114&&box.top<innerHeight&&box.right>0&&box.left<innerWidth)nodes.push({node,text:node.nodeValue});
    }
    if(!nodes.length){output.textContent='No visible Chinese text found. Scroll to the text and translate again.';return;}
    output.textContent='Requesting GPT translation of visible text…';
    let done=0;
    try{
      while(done<nodes.length&&epoch===translationEpoch){
        let batch=[],size=0;for(let i=done;i<nodes.length&&batch.length<8;i++){if(size+nodes[i].text.length>5800)break;batch.push(nodes[i]);size+=nodes[i].text.length;}
        const result=await window.pywebview.api.dispatch('translate-visible',{texts:batch.map(x=>x.text),target:'en'});
        if(result.error)throw Error(result.error);if(epoch!==translationEpoch)return;
        batch.forEach((item,i)=>{if(item.node.isConnected&&item.node.nodeValue===item.text){const shown=mode==='both'?item.text+'\n'+result.translations[i]:result.translations[i];originals.set(item.node,{original:item.text,shown});item.node.nodeValue=shown;}});
        done+=batch.length;output.textContent=`GPT · ${done}/${nodes.length} visible segments · scroll and click again for more`;
      }
    }catch(error){output.textContent=error.message;}
  }
  let polling=false;
  async function refresh(){
    if(polling||!window.pywebview?.api?.dispatch)return;
    polling=true;
    try{
      const state=await window.pywebview.api.dispatch('status');
      const cooldown=Number(state?.cooldownRemainingSeconds)||0;
      pause.hidden=cooldown<=0;
      pause.querySelector('p').textContent=`Wait ${Math.ceil(cooldown/60)} minute(s). Resume mining manually in Studio after the website pause.`;
      input.disabled=cooldown>0;search.disabled=cooldown>0;
      for(const button of root.querySelectorAll('section button'))button.disabled=cooldown>0&&!['copy-link','focus-controls'].includes(button.dataset.action);
      if(root.activeElement!==input)input.value=address();
    }catch(_){/* Native app may be closing. */}finally{polling=false;}
  }
  pause.addEventListener('wheel',e=>e.preventDefault(),{passive:false});
  pause.addEventListener('touchmove',e=>e.preventDefault(),{passive:false});
  document.addEventListener('keydown',e=>{if(!pause.hidden&&e.isTrusted&&!e.composedPath().includes(host)&&['Enter',' ','ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();}},true);
  refresh();setInterval(refresh,3000);
})()'''
