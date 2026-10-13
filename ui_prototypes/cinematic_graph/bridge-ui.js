(() => {
  const dialog=document.querySelector('#aiReview'), provider=document.querySelector('#aiProvider'),
    model=document.querySelector('#aiModel'), status=document.querySelector('#aiStatus'),
    output=document.querySelector('#aiResult'), send=document.querySelector('#aiSend'),
    cancel=document.querySelector('#aiCancel'), consent=document.querySelector('#aiConsent');
  let providers={}, jobId=null, pollTimer=null;
  const api=async(path,data)=>{
    const r=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});
    const j=await r.json();if(!r.ok)throw Error(j.error||'Bridge unavailable');return j;
  };
  function render(){
    const p=providers[provider.value]||{status:'not_checked',models:[]};
    status.textContent=p.status.replaceAll('_',' ')+' · '+(p.message||'Use Check connection.')+
      (p.inferenceVerified?' · Inference verified':' · Inference not verified');
    model.replaceChildren();for(const m of p.models){const o=document.createElement('option');
      o.value=m.model;o.textContent=m.displayName||m.model;model.append(o);if(m.isDefault)o.selected=true;}
    send.disabled=!!jobId||!consent.checked||p.status!=='connected'||!model.value;
    cancel.disabled=!jobId;
    provider.disabled=!!jobId;model.disabled=!!jobId;
    document.querySelector('#aiConnect').disabled=!!jobId;
    cancel.textContent=provider.value==='gemini'?'Stop waiting':'Cancel request';
  }
  async function refresh(){const s=await api('/bridge/status');providers=s.providers;render();}
  function currentGeometry(){return {nodes:[...document.querySelectorAll('#cards>.node')].map(n=>({
    id:n.id,x:parseFloat(n.style.left)||0,y:parseFloat(n.style.top)||0,
    w:parseFloat(n.style.width)||0,h:parseFloat(n.style.height)||0}))};}
  async function poll(){
    try{const j=await api('/bridge/job/'+jobId);output.textContent=j.text||'Waiting for provider…';
      if(j.status==='running'){pollTimer=setTimeout(poll,700);return;}
      jobId=null;await refresh();render();}
    catch(e){jobId=null;output.textContent=e.message;render();}
  }
  async function begin(path,data){try{output.textContent='Working…';const j=await api(path,data);
    jobId=j.jobId;render();poll();}catch(e){output.textContent=e.message;render();}}
  document.querySelector('#openAIReview').onclick=async()=>{
    dialog.showModal();try{const snap=await api('/bridge/snapshot');
      const positions=new Map(currentGeometry().nodes.map(n=>[n.id,n]));
      snap.nodes=snap.nodes.map(n=>({...n,...positions.get(n.id)}));
      document.querySelector('#aiSnapshot').textContent=JSON.stringify(snap,null,2);await refresh();}
    catch(e){status.textContent=e.message;}};
  document.querySelector('#aiClose').onclick=()=>dialog.close();
  provider.onchange=()=>{consent.checked=false;render();};consent.onchange=render;
  document.querySelector('#aiConnect').onclick=()=>begin('/bridge/connect',{provider:provider.value});
  send.onclick=()=>begin('/bridge/review',{provider:provider.value,model:model.value,consent:consent.checked,
    question:document.querySelector('#aiQuestion').value,snapshot:currentGeometry()});
  cancel.onclick=async()=>{if(!jobId)return;try{await api('/bridge/cancel',{jobId});
    clearTimeout(pollTimer);jobId=null;output.textContent=provider.value==='gemini'?
      'Stopped waiting. The supplied Gemini kit cannot interrupt the remote agent; it may continue in Antigravity. No recommendations applied.':
      'Interrupted. Recommendations are not applied.';render();}
    catch(e){output.textContent=e.message;}};
})();
