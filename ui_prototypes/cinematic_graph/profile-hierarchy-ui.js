// Profile board arrangement only. Mirror records and post membership are untouched.
const CORE_NODE_ART='assets/concepts/core-rainee-blue-shanghai-node-vertical.png';
const CORE_ART_RATIO=941/1672;
const hierarchyCard=card;
card=function(n){
  if(n.id==='identity'&&n.story?.ui_template?.kind==='core_route'){
    n.image=CORE_NODE_ART;
    n.h=Math.round(n.w/CORE_ART_RATIO);
  }
  hierarchyCard(n);
  if(n.id==='identity'&&n.story?.ui_template?.kind==='core_route'){
    const element=document.getElementById(n.id);
    element?.classList.add('core-route-node','blue-node');
    element?.querySelector('.handle')?.setAttribute('title','Drag to move the Core route and its owned references');
  }
};

function arrangeRaineeHierarchy(){
  if(!layout||!nodes?.length)return;
  const before=boardSnapshot();
  try{if(!localStorage.getItem('rednote-hierarchy-snapshot-v1'))localStorage.setItem('rednote-hierarchy-snapshot-v1',before)}catch{}
  cancelPortConnection();scene=false;document.body.classList.remove('scene');
  $('#sceneView').setAttribute('aria-pressed','false');$('#branchView').setAttribute('aria-pressed','true');
  const byId=id=>nodes.find(n=>n.id===id),used=new Set(),groups=[];
  const put=(id,x,y)=>{const n=byId(id);if(!n)return null;n.x=Math.round(x);n.y=Math.round(y);used.add(n.id);return n};
  const end=n=>n?n.y+n.h:0;
  const edge=(a,b,label)=>{if(byId(a)&&byId(b)&&!layout.edges.some(e=>e[0]===a&&e[1]===b))layout.edges.push([a,b,label,{direction:'forward',active:true}])};
  const outputFor=id=>byId('profile-stories-'+id);
  const profile=(id,x,y)=>{const n=put(id,x,y);if(n&&outputFor(id))put(outputFor(id).id,x,end(n)+130);return n};
  function frame(id,title,ids,margin=70){const items=ids.map(byId).filter(n=>n&&!n.groupedIn);if(!items.length)return;const x=Math.min(...items.map(n=>n.x))-margin,y=Math.min(...items.map(n=>n.y))-110;groups.push({id,title,x,y,w:Math.max(...items.map(n=>n.x+n.w))-x+margin,h:Math.max(...items.map(end))-y+margin})}

  // A single protagonist above three sibling blue route nodes.
  const coreX=120,bigX=1660,horseX=4620,coreW=1320,bigW=2750,horseW=1350;
  const actor=put('actor',Math.round((horseX+horseW-(byId('actor')?.w||1600))/2),90);
  const routeY=end(actor)+190;
  const core=byId('identity');if(core){core.story??={};core.story.ui_template={...(core.story.ui_template||{}),kind:'core_route'};core.image=CORE_NODE_ART;core.title='Core Rainee';core.subtitle='Main timeline · original character';core.w=600;core.h=Math.round(core.w/CORE_ART_RATIO);put('identity',coreX+(coreW-core.w)/2,routeY)}
  const lipRoute=byId('alternative'),horseRoute=byId('horse-transform');
  if(lipRoute)put('alternative',bigX+(bigW-lipRoute.w)/2,routeY);
  if(horseRoute){if(horseRoute.instruction==='Big Lip Rainee becomes Horse Rainee. Compare evidence, then author the derived identity.')horseRoute.instruction='Core Rainee becomes Horse Rainee in a parallel branch. Compare evidence, then author this form.';put('horse-transform',horseX+(horseW-horseRoute.w)/2,routeY)}
  const stateY=Math.max(end(core),end(lipRoute),end(horseRoute))+190;
  const form=byId('form'),horseForm=byId('horse-form');
  if(form)put('form',bigX+(bigW-form.w)/2,stateY);
  if(horseForm){if(horseForm.subtitle==='Next derived actor identity')horseForm.subtitle='Parallel form derived from Core Rainee';if(horseForm.actor?.derived_from==='rainee.big_lip')horseForm.actor.derived_from='rainee.base';put('horse-form',horseX+(horseW-horseForm.w)/2,stateY)}
  if(horseRoute?.actor?.derived_from==='rainee.big_lip')horseRoute.actor.derived_from='rainee.base';
  const horseProfile=byId('horse-source');if(horseProfile){if(horseProfile.story?.protagonist_profile?.source_identity_node==='form')horseProfile.story.protagonist_profile.source_identity_node='identity';if(horseProfile.actor?.derived_from==='rainee.big_lip')horseProfile.actor.derived_from='rainee.base'}

  // Core: captured profile and one shared shelf for its selected posts/stories.
  const coreProfile=profile('profile',coreX+120,stateY+90);
  let coreBottom=Math.max(end(coreProfile),end(outputFor('profile')));
  for(const id of ['assistant-gemini','assistant-codex']){const n=byId(id);if(n){put(id,coreX+240,coreBottom+180);coreBottom=end(n)}}

  // Big Lips: derived profiles, then source media, profile folders, and outputs.
  const characterY=end(form)+180;
  const dudu=nodes.find(n=>n.story?.ui_template?.kind==='dudu-dossier'),cabinet=nodes.find(n=>n.story?.ui_template?.kind==='dudu-cabinet');
  if(dudu)put(dudu.id,bigX+40,characterY);
  if(cabinet)put(cabinet.id,bigX+1060,characterY);
  const mediaY=Math.max(end(dudu),end(cabinet),end(form))+190;
  const tori=put('tori',bigX+40,mediaY),episode=put('main',bigX+1040,mediaY),photo=profile('photo-profile',bigX+1850,mediaY);
  const toriFx=put('tori-audio-fx',bigX+40,end(tori)+130);
  const junction=put('junction',bigX+1388,end(episode)+50);
  const mainOutput=put('main-output',bigX+1250,end(junction)+90);
  const row2=Math.max(end(toriFx),end(mainOutput),end(photo),end(outputFor('photo-profile')),end(junction))+210;
  const adela=put('adela',bigX+40,row2),adelaOut=put('adela-output',bigX+1040,row2);
  const animation=profile('animation-profile',bigX+1430,row2);
  const row3=Math.max(end(adela),end(adelaOut),end(animation),end(outputFor('animation-profile')))+210;
  const vanity=put('vanity',bigX+40,row3),vanityOut=put('vanity-output',bigX+1040,row3),retro=put('vanity-cinema',bigX+1540,row3);
  const music=byId('lip-music-junction');if(music)put(music.id,bigX+bigW+50,row3+180);
  if(typeof positionVideoMusicProcessors==='function')positionVideoMusicProcessors();

  // Horse: derived identity, themed profile, and a terminal draft window.
  const envelope=put('horse-source',horseX+(horseW-(horseProfile?.w||600))/2,end(horseForm)+180);
  let horseOutput=byId('horse-output');
  if(envelope&&!horseOutput){horseOutput={id:'horse-output',uiCreated:true,kind:'output',title:'Horse · text output',subtitle:'Horse branch · editable draft',x:0,y:0,w:640,h:480,story:{status:'authored_ui_template',draft:''},actor:{fictional_actor_ref:'rainee.horse',additional_actors:[]},override:{enabled:false}};nodes.push(horseOutput)}
  if(horseOutput)put('horse-output',horseX+(horseW-horseOutput.w)/2,end(envelope)+170);

  // Remove only the old sequential Big Lips → Horse and output → junction wires.
  const replaced=new Set(['form>horse-transform','form>horse-form','alternative>horse-transform','alternative>horse-form','horse-form>horse-transform','main-output>junction']);
  layout.edges=layout.edges.filter(e=>!replaced.has(e[0]+'>'+e[1]));
  edge('actor','identity','Core route');edge('actor','alternative','Big Lips route');edge('actor','horse-transform','Horse route');
  edge('identity','profile','Core source profile');edge('alternative','form','Big Lips derived form');edge('horse-transform','horse-form','Horse derived form');edge('horse-form','horse-source','Horse protagonist profile');edge('horse-source','horse-output','Horse story output');
  edge('main','junction','episode story context');edge('junction','main-output','Big Lips text output');
  if(dudu)edge('form',dudu.id,'derived protagonist profile');if(cabinet)edge('form',cabinet.id,'derived protagonist profile');

  // Everything else stays available below the three routes; nothing is deleted.
  const shelfY=Math.max(coreBottom,end(vanity),end(vanityOut),end(retro),end(horseOutput),...nodes.filter(n=>used.has(n.id)).map(end))+300;
  let sx=120,sy=shelfY,rowH=0;const extras=[];
  for(const n of nodes.filter(n=>!used.has(n.id)&&!n.groupedIn&&!n.outputProfile&&!n.story?.music_processing?.source_video_ref)){
    if(sx+n.w>horseX+horseW&&sx>120){sx=120;sy+=rowH+140;rowH=0}
    put(n.id,sx,sy);extras.push(n.id);sx+=n.w+130;rowH=Math.max(rowH,n.h);
  }
  frame('core-route-group','CORE · main timeline / Lingzu profile / selected posts',['identity','profile','profile-stories-profile']);
  frame('big-lips-route-group','BIG LIPS · derived character / video and profile references',['alternative','form',dudu?.id,cabinet?.id,'tori','tori-audio-fx','main','junction','main-output','photo-profile','profile-stories-photo-profile','adela','adela-output','animation-profile','profile-stories-animation-profile','vanity','vanity-output','vanity-cinema'].filter(Boolean));
  frame('horse-route-group','HORSE · parallel derived character / themed profile / output',['horse-transform','horse-form','horse-source','horse-output']);
  frame('assistant-group','ASSISTANTS · separate from story flow',['assistant-gemini','assistant-codex']);
  frame('unassigned-group','OTHER REFERENCES · retained for manual routing',extras);
  layout.groups=groups;
  layout.width=Math.max(...nodes.filter(n=>!n.groupedIn).map(n=>n.x+n.w))+210;
  layout.height=Math.max(...nodes.filter(n=>!n.groupedIn).map(end))+210;
  for(const n of nodes)sourcePositions[n.id]=[n.x,n.y,n.w,n.h];
  mount();persist();saveConnections();renderObjects();
  try{localStorage.setItem('rednote-pdf-groups-v1',JSON.stringify(groups));localStorage.setItem('rednote-profile-hierarchy-v1','applied')}catch{}
  recordBoardChange(before);fitBounds({x:0,y:0,w:layout.width,h:Math.min(4000,layout.height)});
  status('Rainee hierarchy organized · Core / Big Lips / Horse parallel routes · old positions recoverable · Undo available');
}

const hierarchyButton=$('#organizeBoard');
hierarchyButton.onclick=arrangeRaineeHierarchy;
hierarchyButton.title='Arrange Rainee above three independent routes; retain source posts and other references';
hierarchyButton.setAttribute('aria-label','Arrange Rainee profile hierarchy');
hierarchyButton.textContent='▦';
const horizontalButton=$('#horizontalBranches');if(horizontalButton){horizontalButton.onclick=arrangeRaineeHierarchy;horizontalButton.title='Reapply the Core / Big Lips / Horse profile hierarchy';horizontalButton.setAttribute('aria-label','Arrange Core, Big Lips and Horse routes');}

// Keep the approved vertical artwork proportional during pointer and keyboard resizing.
let coreResize=null;
viewport.addEventListener('pointerdown',e=>{if(!e.target.closest('#identity .resize-node'))return;const n=nodes.find(n=>n.id==='identity');if(n)coreResize={x:e.clientX,y:e.clientY,w:n.w,h:n.h};},true);
viewport.addEventListener('pointermove',e=>{if(!coreResize||drag?.mode!=='resize'||drag.id!=='identity')return;const n=nodes.find(n=>n.id==='identity');if(!n)return;const dw=(e.clientX-coreResize.x)/scale,dh=(e.clientY-coreResize.y)/scale;const proposed=Math.abs(dw/coreResize.w)>=Math.abs(dh/coreResize.h)?coreResize.w+dw:(coreResize.h+dh)*CORE_ART_RATIO;n.w=Math.round(Math.max(320,Math.min(1400*CORE_ART_RATIO,proposed)));n.h=Math.round(n.w/CORE_ART_RATIO);const el=document.getElementById(n.id);el.style.width=n.w+'px';el.style.height=n.h+'px';drawWires();});
viewport.addEventListener('pointerup',()=>{if(!coreResize)return;coreResize=null;const n=nodes.find(n=>n.id==='identity');if(n){sourcePositions[n.id]=[n.x,n.y,n.w,n.h];persist();}});
viewport.addEventListener('pointercancel',()=>{coreResize=null});
viewport.addEventListener('keydown',e=>{if(!e.target.closest('#identity .resize-node')||!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp'].includes(e.key))return;const n=nodes.find(n=>n.id==='identity');if(!n)return;e.preventDefault();e.stopImmediatePropagation();const before=boardSnapshot(),step=e.shiftKey?100:20,delta=['ArrowLeft','ArrowUp'].includes(e.key)?-step:step;n.w=Math.round(Math.max(320,Math.min(1400*CORE_ART_RATIO,n.w+delta*(e.key.endsWith('Up')||e.key.endsWith('Down')?CORE_ART_RATIO:1))));n.h=Math.round(n.w/CORE_ART_RATIO);const el=document.getElementById(n.id);el.style.width=n.w+'px';el.style.height=n.h+'px';sourcePositions[n.id]=[n.x,n.y,n.w,n.h];drawWires();persist();recordBoardChange(before);},true);
