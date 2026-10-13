// Board placement aids. Geometry only; existing custom node artwork is untouched.
(() => {
  const viewport=document.getElementById('viewport');
  const hud=document.createElement('div');hud.className='graph-precision-hud';
  hud.innerHTML='<button type="button" class="precision-grid" aria-pressed="true">▦ Grid</button><button type="button" class="precision-snap" aria-pressed="true">⌖ Snap</button><label>Step <select class="precision-step" aria-label="Grid step"><option>10</option><option selected>20</option><option>40</option><option>80</option></select></label><span class="precision-pointer" aria-live="off">X 0 · Y 0</span><label>X <input class="precision-x" type="number" step="1" aria-label="Selected node X coordinate"></label><label>Y <input class="precision-y" type="number" step="1" aria-label="Selected node Y coordinate"></label><button type="button" class="precision-apply" title="Apply selected node coordinates">Set</button>';
  viewport.append(hud);
  const gridButton=hud.querySelector('.precision-grid'),snapButton=hud.querySelector('.precision-snap'),stepSelect=hud.querySelector('.precision-step');
  const pointerLabel=hud.querySelector('.precision-pointer'),xInput=hud.querySelector('.precision-x'),yInput=hud.querySelector('.precision-y');
  if(localStorage.getItem('scatter-grid-hidden-default-v1')!=='done'){
    localStorage.setItem('scatter-grid-visible','false');
    localStorage.setItem('scatter-grid-hidden-default-v1','done');
  }
  let gridVisible=localStorage.getItem('scatter-grid-visible')==='true';
  let snapEnabled=localStorage.getItem('scatter-snap-enabled')!=='false';
  let step=Number(localStorage.getItem('scatter-grid-size')||20);
  if(![10,20,40,80].includes(step))step=20;
  stepSelect.value=String(step);
  const guideX=document.createElement('div'),guideY=document.createElement('div');guideX.className='graph-align-guide vertical';guideY.className='graph-align-guide horizontal';viewport.append(guideX,guideY);
  const ghost=document.createElement('div');ghost.className='graph-placement-ghost';ghost.hidden=true;ghost.innerHTML='<strong>＋ Drop node here</strong><span></span>';viewport.append(ghost);
  const clearGuides=()=>{guideX.hidden=true;guideY.hidden=true;};clearGuides();
  function updateGrid(){
    const px=Math.max(3,step*scale);viewport.classList.toggle('precision-grid-visible',gridVisible);
    viewport.style.setProperty('--precision-step',px+'px');
    viewport.style.setProperty('--precision-grid-x',pan.x+'px');
    viewport.style.setProperty('--precision-grid-y',pan.y+'px');
    gridButton.setAttribute('aria-pressed',String(gridVisible));snapButton.setAttribute('aria-pressed',String(snapEnabled));
  }
  gridButton.onclick=()=>{gridVisible=!gridVisible;localStorage.setItem('scatter-grid-visible',String(gridVisible));updateGrid();};
  snapButton.onclick=()=>{snapEnabled=!snapEnabled;localStorage.setItem('scatter-snap-enabled',String(snapEnabled));snapButton.setAttribute('aria-pressed',String(snapEnabled));clearGuides();};
  stepSelect.onchange=()=>{step=Number(stepSelect.value);localStorage.setItem('scatter-grid-size',String(step));updateGrid();};
  const round=value=>Math.round(value/step)*step;
  const selected=()=>typeof nodes!=='undefined'&&Array.isArray(nodes)?nodes.find(node=>node.id===active):null;
  function updateSelected(){const node=selected();if(document.activeElement!==xInput)xInput.value=node?String(Math.round(node.x)):'';if(document.activeElement!==yInput)yInput.value=node?String(Math.round(node.y)):'';xInput.disabled=yInput.disabled=hud.querySelector('.precision-apply').disabled=!node;}
  function moveOwned(node,x,y){
    const dx=x-node.x,dy=y-node.y;
    const descendants=typeof branchOwners!=='undefined'&&branchOwners[node.id]&&typeof ownedBranch==='function'?ownedBranch(node.id).filter(item=>item.id!==node.id):[];
    for(const item of descendants){item.x+=dx;item.y+=dy;sourcePositions[item.id]=[item.x,item.y,item.w,item.h];}
    node.x=x;node.y=y;sourcePositions[node.id]=[node.x,node.y,node.w,node.h];
    if(typeof layout!=='undefined'&&layout){const groupIds=node.id==='actor'?['core-route-group','big-lips-route-group','horse-route-group']:({identity:['core-route-group'],alternative:['big-lips-route-group'],'horse-transform':['horse-route-group']})[node.id]||[];
      for(const group of layout.groups||[])if(groupIds.includes(group.id)){group.x+=dx;group.y+=dy;}
    }
    mount();persist();drawWires();
  }
  hud.querySelector('.precision-apply').onclick=()=>{const node=selected(),x=Number(xInput.value),y=Number(yInput.value);if(!node||!Number.isFinite(x)||!Number.isFinite(y))return;
    const before=boardSnapshot();moveOwned(node,Math.max(0,x),Math.max(0,y));recordBoardChange(before);select(node.id);updateSelected();status('Node placed at X '+Math.round(node.x)+' · Y '+Math.round(node.y)+' · Undo available');};
  for(const input of [xInput,yInput])input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();hud.querySelector('.precision-apply').click();}};
  function worldAt(event){const rect=viewport.getBoundingClientRect();return {x:(event.clientX-rect.left-pan.x)/scale,y:(event.clientY-rect.top-pan.y)/scale,left:event.clientX-rect.left,top:event.clientY-rect.top};}
  function align(node){
    const threshold=10/scale,owned=new Set(typeof branchOwners!=='undefined'&&branchOwners[node.id]&&typeof ownedBranch==='function'?ownedBranch(node.id).map(item=>item.id):[node.id]);
    let bestX=null,bestY=null;
    for(const other of nodes){if(owned.has(other.id)||other.groupedIn)continue;
      for(const offset of [0,node.w/2,node.w])for(const anchor of [other.x,other.x+other.w/2,other.x+other.w]){const delta=anchor-(node.x+offset);if(Math.abs(delta)<threshold&&(!bestX||Math.abs(delta)<Math.abs(bestX.delta)))bestX={delta,anchor};}
      for(const offset of [0,node.h/2,node.h])for(const anchor of [other.y,other.y+other.h/2,other.y+other.h]){const delta=anchor-(node.y+offset);if(Math.abs(delta)<threshold&&(!bestY||Math.abs(delta)<Math.abs(bestY.delta)))bestY={delta,anchor};}
    }
    return {bestX,bestY};
  }
  viewport.addEventListener('pointermove',event=>{
    const at=worldAt(event);pointerLabel.textContent='X '+Math.round(at.x)+' · Y '+Math.round(at.y);
    if(typeof drag==='undefined'||drag?.mode!=='node'||typeof nodes==='undefined'||!nodes)return;
    const node=nodes.find(item=>item.id===drag.id);if(!node)return;
    const free=event.altKey||!snapEnabled,{bestX,bestY}=free?{}:align(node);
    const x=free?node.x:bestX?node.x+bestX.delta:round(node.x);
    const y=free?node.y:bestY?node.y+bestY.delta:round(node.y);
    const dx=x-node.x,dy=y-node.y;
    node.x=x;node.y=y;
    const element=document.getElementById(node.id);if(element){element.style.left=x+'px';element.style.top=y+'px';}
    if((dx||dy)&&typeof branchDrag!=='undefined'&&branchDrag?.id===node.id){for(const item of ownedBranch(node.id)){if(item.id===node.id)continue;item.x+=dx;item.y+=dy;const child=document.getElementById(item.id);if(child){child.style.left=item.x+'px';child.style.top=item.y+'px';}}for(const frame of branchDrag.frames||[]){const group=layout.groups.find(g=>g.id===frame.id);if(group){group.x+=dx;group.y+=dy;}}}
    if(dx||dy)drawWires();
    const rect=viewport.getBoundingClientRect();guideX.hidden=!bestX;guideY.hidden=!bestY;
    if(bestX)guideX.style.left=(pan.x+bestX.anchor*scale)+'px';
    if(bestY)guideY.style.top=(pan.y+bestY.anchor*scale)+'px';
    updateSelected();
  });
  viewport.addEventListener('pointerup',()=>{clearGuides();updateSelected();});
  viewport.addEventListener('pointercancel',clearGuides);
  hud.addEventListener('pointerdown',event=>event.stopPropagation());
  viewport.addEventListener('pointerdown',event=>{if(!event.target.closest('.graph-precision-hud'))queueMicrotask(updateSelected);},true);
  viewport.addEventListener('wheel',()=>requestAnimationFrame(updateGrid),{passive:true});
  viewport.addEventListener('pointermove',()=>{if(typeof drag!=='undefined'&&drag?.mode==='pan')updateGrid();});
  document.addEventListener('click',()=>queueMicrotask(updateSelected));
  viewport.addEventListener('dragover',event=>{if(!event.defaultPrevented)return;const types=event.dataTransfer?.types||[];if(!['application/x-scatter-mirror-profile','application/x-scatter-mirror-group','application/x-scatter-custom-asset','application/x-scatter-class-tool','application/x-scatter-catalog-node'].some(type=>types.includes(type)))return;
    const at=worldAt(event),x=snapEnabled?round(at.x):at.x,y=snapEnabled?round(at.y):at.y;
    ghost.hidden=false;ghost.style.left=(pan.x+x*scale)+'px';ghost.style.top=(pan.y+y*scale)+'px';ghost.querySelector('span').textContent='X '+Math.round(x)+' · Y '+Math.round(y);
    ghost.classList.toggle('is-group',types.includes('application/x-scatter-mirror-group'));
  });
  document.addEventListener('drop',()=>{ghost.hidden=true;},true);
  viewport.addEventListener('dragleave',event=>{if(!viewport.contains(event.relatedTarget))ghost.hidden=true;});
  window.addEventListener('blur',()=>{ghost.hidden=true;clearGuides();});
  updateGrid();updateSelected();
})();
