/* Read-only Mirror Storage pointers and browser-owned custom media. */
(() => {
  const sidebar = document.getElementById('boardSidebar');
  const canvas = document.getElementById('viewport');
  const assetUrls = new Map();
  const firstPostPages = new Map();
  const E = (tag, className = '', content = '') => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = content;
    return node;
  };
  const api = async path => {
    const response = await fetch('/mirror/' + path, {cache:'no-store'});
    const body = await response.json();
    if (!response.ok) throw Error(body.error || 'Mirror Storage unavailable');
    return body;
  };
  const avatar = profile => profile.assets?.find(asset => asset.role === 'avatar' && ['verified','unverified'].includes(asset.verification));
  const cover = post => post.assets?.find(asset => ['image','cover','thumbnail'].includes(asset.role) && ['verified','unverified'].includes(asset.verification));
  const preview = asset => asset ? '/mirror/api/v2/assets/' + encodeURIComponent(asset.id) + '/preview' : '';
  const savedProfile = node => node.story?.mirror_ref;
  const persistedCustom = 'scatter-custom-assets-v1';
  const suppliedLibrary=fetch('assets/custom-library.json',{cache:'no-store'}).then(response=>{if(!response.ok)throw Error('Custom library unavailable');return response.json();}).then(data=>data.items||[]);
  let profiles = [], profilesLoaded = false, studioBridgeLoaded = false, studioGroups = [], activeProfile = null, activeProfileNodeId = null, pageAfter = -1, pageNext = null, pendingSearch = '', activeTab = 'groups', postRequest = 0;

  const dock = E('section','mirror-graph-dock');
  dock.innerHTML = '<div class="mirror-cabinet-head"><div class="mirror-brand"><span class="mirror-brand-mark">小红书</span><div><b>Mirror Cabinet</b><small>Captured RedNote · drag to the graph</small></div></div><button type="button" class="mirror-cabinet-close" aria-label="Close Cabinet">✕</button></div><label class="mirror-cabinet-search">⌕ <input type="search" placeholder="Search groups and captured profiles" aria-label="Search Mirror Cabinet"></label><div class="mirror-graph-tabs"><button type="button" data-store="groups">Groups</button><button type="button" data-store="mirror">Profiles</button><button type="button" data-store="custom">Custom media</button></div><p class="mirror-graph-state" role="status">Loading archive…</p><div class="mirror-graph-list"></div><input class="mirror-custom-input" type="file" accept="image/*,video/*,audio/*" multiple hidden>';
  sidebar.querySelector('#mirrorOpen').after(dock);
  const state = dock.querySelector('.mirror-graph-state'), list = dock.querySelector('.mirror-graph-list');
  dock.hidden=true;
  const cabinetButton=document.getElementById('mirrorOpen');cabinetButton.textContent='▤ Mirror Cabinet';
  const showCabinet=()=>{dock.hidden=false;cabinetButton.setAttribute('aria-expanded','true');renderDock();requestGroups();};
  const hideCabinet=()=>{dock.hidden=true;cabinetButton.setAttribute('aria-expanded','false');};
  cabinetButton.setAttribute('aria-expanded','false');
  cabinetButton.onclick=()=>dock.hidden?showCabinet():hideCabinet();
  dock.querySelector('.mirror-cabinet-close').onclick=hideCabinet;
  dock.querySelectorAll('[data-store]').forEach(button => button.onclick = () => {activeTab=button.dataset.store;renderDock();});
  dock.querySelector('.mirror-cabinet-search input').oninput=()=>renderDock();
  const studioBridge=E('iframe','mirror-studio-bridge');studioBridge.title='Studio profile groups bridge';studioBridge.hidden=true;
  studioBridge.src='http://127.0.0.1:5185/#storage';document.body.append(studioBridge);
  function requestGroups(){if(profilesLoaded&&studioBridgeLoaded)studioBridge.contentWindow?.postMessage({type:'scatter-request-profile-groups'},'http://127.0.0.1:5185');}
  studioBridge.addEventListener('load',()=>{studioBridgeLoaded=true;requestGroups();publishCustomManifest();});
  window.addEventListener('message',event=>{
    if(event.origin!=='http://127.0.0.1:5185'||event.source!==studioBridge.contentWindow||event.data?.type!=='rednote-studio-profile-groups')return;
    studioGroups=Array.isArray(event.data.groups)?event.data.groups.filter(g=>typeof g.id==='string'&&typeof g.name==='string'):[];
    if(activeTab==='groups')renderDock();
    refreshGroupNodes();
  });

  function makeBase(kind,title,w=460,h=560) {
    return {id:'node-'+kind+'-'+crypto.randomUUID(),uiCreated:true,kind,title,x:0,y:0,w,h,
      story:{status:'authored_ui_template',draft:''},actor:{fictional_actor_ref:null,additional_actors:[]},override:{enabled:false,instructions:[]}};
  }
  function addNode(node,point=null,connectFrom=null) {
    if (typeof layout==='undefined' || typeof nodes==='undefined' || !layout || !nodes) return;
    const before=boardSnapshot();
    const rect=canvas.getBoundingClientRect();
    const grid=Number(localStorage.getItem('scatter-grid-size')||20),snap=localStorage.getItem('scatter-snap-enabled')!=='false';
    const place=value=>snap?Math.round(value/grid)*grid:value;
    node.x=place(point ? Math.max(0,(point.x-rect.left-pan.x)/scale-node.w/2) : Math.max(0,(canvas.clientWidth/2-pan.x)/scale-node.w/2));
    node.y=place(point ? Math.max(0,(point.y-rect.top-pan.y)/scale-node.h/2) : Math.max(0,(canvas.clientHeight/2-pan.y)/scale-node.h/2));
    nodes.push(node);sourcePositions[node.id]=[node.x,node.y,node.w,node.h];
    if (connectFrom && nodes.some(n=>n.id===connectFrom)) {
      layout.edges.push([connectFrom,node.id,'data path',{direction:'forward',active:true}]);
      node.story.branch_owner=connectFrom;
      (branchOwners[connectFrom]??=[]).push(node.id);
    }
    mount();persist();saveConnections();recordBoardChange(before);renderObjects();
    if(!point)focusNode(node.id);else select(node.id);
    return node;
  }
  function selectedBranch() {
    const n=nodes?.find(item=>item.id===active);
    return n?.story?.ui_template?.kind==='branch-default' || ['identity','alternative','horse-transform'].includes(n?.id) ? n.id : null;
  }
  const priorOwnedBranch=ownedBranch;
  ownedBranch=function(id){const found=new Map(priorOwnedBranch(id).map(node=>[node.id,node])),queue=[id],seen=new Set();
    while(queue.length){const parent=queue.shift();if(seen.has(parent))continue;seen.add(parent);
      for(const child of branchOwners[parent]||[]){const node=nodes.find(item=>item.id===child);if(node)found.set(node.id,node);queue.push(child);}}
    return [...found.values()];};
  function addProfile(profile,point=null) {
    const node=makeBase('profile',profile.name||'Mirror profile',720,900);
    node.image=preview(avatar(profile));node.postGroup=true;node.memberIds=[];
    node.containerScope='whole_profile';node.subtitle='Read-only Mirror Storage link · engine pin pending';
    node.story.mirror_ref={profile_id:profile.id,source_id:profile.source_id,source_url:profile.source_url,excluded_post_ids:[],coverage:profile.metadata?.coverage?.status||'unknown'};
    const created=addNode(node,point,selectedBranch());
    if(profileDialog.open){activeProfileNodeId=created.id;pageAfter=-1;profileDialog.querySelector('.mirror-profile-posts').replaceChildren();loadPosts();}
    status('Mirror profile linked by archive ID · open it to browse posts; source unchanged');
  }
  function mirrorRow(profile) {
    const row=E('div','mirror-graph-row');row.draggable=true;
    row.dataset.mirrorProfile=profile.id;
    row.addEventListener('dragstart',event=>{
      event.dataTransfer.effectAllowed='copy';
      event.dataTransfer.setData('application/x-scatter-mirror-profile',profile.id);
      event.dataTransfer.setData('text/plain',profile.name||'Mirror profile');
    });
    const img=E('img');img.alt='';img.src=preview(avatar(profile));img.draggable=false;
    const info=E('div');info.append(E('b','',profile.name||'Untitled profile'),E('small','',profile.metadata?.coverage?.status||'captured source'));
    const open=E('button','','Browse');open.onclick=()=>openProfile(profile.id);
    const add=E('button','','＋');add.title='Add profile node';add.setAttribute('aria-label','Add '+profile.name+' to graph');add.onclick=()=>addProfile(profile);
    row.append(img,info,open,add);return row;
  }
  const capturedMembers=group=>group.members?.map(member=>profiles.find(profile=>profile.id===member.id||
    (member.id==='fixture-lingzi'&&['lingzi','lingzu'].includes(String(profile.name||'').toLowerCase().replace(/[^a-z]/g,''))))).filter(Boolean)||[];
  function addGroup(group,point=null){
    const members=capturedMembers(group);
    const node=makeBase('profile',group.name,760,720);
    node.subtitle='Studio group · '+members.length+' captured profile'+(members.length===1?'':'s')+' linked';
    node.story.mirror_group_ref={group_id:group.id,member_profile_ids:members.map(profile=>profile.id),source:'studio-profile-groups-v1'};
    addNode(node,point,selectedBranch());
    status('Group node linked to Studio membership · manage members in Mirror Storage');
  }
  function groupRow(group){
    const row=E('div','mirror-group-row');row.draggable=true;
    row.addEventListener('dragstart',event=>{event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-scatter-mirror-group',group.id);event.dataTransfer.setData('text/plain',group.name);});
    const icon=E('span','mirror-group-icon');const members=capturedMembers(group);
    for(const profile of members.slice(0,3)){const image=E('img');image.alt='';image.src=preview(avatar(profile));image.draggable=false;icon.append(image);}
    if(!members.length)icon.textContent='▣';
    const info=E('div');const count=members.length;
    info.append(E('b','',group.name),E('small','',count+' captured · '+(group.members?.length||0)+' total members'));
    const add=E('button','','＋');add.title='Add linked group node';add.onclick=()=>addGroup(group);
    row.append(icon,info,add);return row;
  }
  function refreshGroupNodes(){
    if(!profilesLoaded||typeof nodes==='undefined'||!nodes)return;
    let changed=false;
    for(const node of nodes){const ref=node.story?.mirror_group_ref;if(!ref)continue;
      const group=studioGroups.find(item=>item.id===ref.group_id);if(!group)continue;
      const ids=capturedMembers(group).map(profile=>profile.id);
      if(JSON.stringify(ids)!==JSON.stringify(ref.member_profile_ids)){ref.member_profile_ids=ids;changed=true;}
    }
    if(changed){persist();mount();}
  }
  function openProfile(id,nodeId=null) {
    const profile=profiles.find(item=>item.id===id);
    if (!profile) return;
    activeProfile=profile;activeProfileNodeId=nodeId;pageAfter=-1;pageNext=null;pendingSearch='';
    profileDialog.querySelector('h2').textContent=profile.name||profile.source_id||'Mirror Profile';
    const portrait=profileDialog.querySelector('.mirror-profile-avatar'),asset=avatar(profile);portrait.src=preview(asset);portrait.hidden=!asset;
    profileDialog.querySelector('.mirror-profile-id').textContent='RedNote ID · '+(profile.source_id||profile.id);
    profileDialog.querySelector('input[type=search]').value='';
    profileDialog.querySelector('.mirror-profile-source').href=profile.source_url||'#';
    profileDialog.querySelector('.mirror-profile-source').hidden=!profile.source_url;
    profileDialog.querySelector('.mirror-profile-posts').replaceChildren();
    profileDialog.showModal();loadPosts();
  }
  function renderDock() {
    dock.querySelectorAll('[data-store]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.store===activeTab)));
    list.replaceChildren();const query=dock.querySelector('.mirror-cabinet-search input').value.trim().toLocaleLowerCase();
    if(activeTab==='groups') {
      state.textContent=studioGroups.length ? studioGroups.length+' Studio group'+(studioGroups.length===1?'':'s')+' · membership changes in Storage' : 'Studio groups unavailable. Open Studio Storage on port 5185, then refresh.';
      studioGroups.filter(group=>group.name.toLocaleLowerCase().includes(query)).forEach(group=>list.append(groupRow(group)));
    } else if(activeTab==='mirror') {
      state.textContent=profiles.length ? profiles.length+' captured profile'+(profiles.length===1?'':'s')+' · drag to canvas or browse' : 'No captured profiles available';
      profiles.filter(profile=>(profile.name||profile.source_id||'').toLocaleLowerCase().includes(query)).forEach(p=>list.append(mirrorRow(p)));
    } else {renderCustom();}
  }
  async function refreshProfiles() {
    try {profiles=(await api('api/v2/profiles')).items||[];profilesLoaded=true;renderDock();refreshGroupNodes();requestGroups();publishCustomManifest();}
    catch(error) {state.textContent=error.message;list.replaceChildren();}
  }
  canvas.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('application/x-scatter-mirror-profile')||event.dataTransfer.types.includes('application/x-scatter-mirror-group')||event.dataTransfer.types.includes('application/x-scatter-custom-asset')||event.dataTransfer.types.includes('application/x-scatter-class-tool')||event.dataTransfer.types.includes('application/x-scatter-catalog-node')){event.preventDefault();event.dataTransfer.dropEffect='copy';}},true);
  canvas.addEventListener('drop',event=>{
    const profileId=event.dataTransfer.getData('application/x-scatter-mirror-profile');
    const groupId=event.dataTransfer.getData('application/x-scatter-mirror-group');
    const customId=event.dataTransfer.getData('application/x-scatter-custom-asset');
    const classType=event.dataTransfer.getData('application/x-scatter-class-tool');
    const catalogType=event.dataTransfer.getData('application/x-scatter-catalog-node');
    if (!profileId&&!groupId&&!customId&&!classType&&!catalogType) return;
    event.preventDefault();event.stopImmediatePropagation();
    if (groupId) {const group=studioGroups.find(item=>item.id===groupId);if(group)addGroup(group,{x:event.clientX,y:event.clientY});}
    else if (profileId) {const p=profiles.find(item=>item.id===profileId);if(p)addProfile(p,{x:event.clientX,y:event.clientY});}
    else if(classType)createClass(classType,{x:event.clientX,y:event.clientY});
    else if(catalogType){const entry=nodeCatalog.find(item=>item[0]===catalogType),point={x:event.clientX,y:event.clientY};
      if(catalogType==='dudu-dossier'||catalogType==='dudu-cabinet'){
        const variant=catalogType.slice(5),rect=canvas.getBoundingClientRect(),w=variant==='cabinet'?1425:850,h=variant==='cabinet'?830:1355,grid=Number(localStorage.getItem('scatter-grid-size')||20),snap=localStorage.getItem('scatter-snap-enabled')!=='false';
        const place=value=>snap?Math.round(value/grid)*grid:value;
        addDuduProfile(variant,{x:place(Math.max(0,(point.x-rect.left-pan.x)/scale-w/2)),y:place(Math.max(0,(point.y-rect.top-pan.y)/scale-h/2))});
      }else if(entry){createCatalogNode(catalogType,entry[1],point);}}
    else addCustomNode(customId,{x:event.clientX,y:event.clientY});
  },true);

  const profileDialog=E('dialog','mirror-profile-dialog');
  profileDialog.innerHTML='<div class="mirror-profile-top"><span class="mirror-brand-mark">小红书</span><span>rednote <em>· captured mirror</em></span><button type="button" class="mirror-close" aria-label="Close profile">✕</button></div><div class="mirror-profile-hero"><img class="mirror-profile-avatar" alt="" draggable="false"><div><h2>Mirror Profile</h2><p class="mirror-profile-id"></p><p class="mirror-profile-note">Captured profile · source data remains in Mirror Storage</p><div class="mirror-profile-hero-actions"><a class="mirror-profile-source" target="_blank" rel="noopener noreferrer">Open live profile ↗</a><button type="button" class="mirror-add-profile">＋ Add profile node</button></div></div></div><div class="mirror-profile-toolbar"><strong>Notes</strong><label class="mirror-profile-search">⌕ <input type="search" placeholder="Search captured posts" aria-label="Search captured posts"></label></div><div class="mirror-profile-posts"></div><button type="button" class="mirror-more">Load more posts</button><p class="mirror-profile-message" role="status"></p>';
  document.body.append(profileDialog);
  profileDialog.querySelector('.mirror-close').onclick=()=>profileDialog.close();
  profileDialog.querySelector('.mirror-add-profile').onclick=()=>{if(activeProfile)addProfile(activeProfile);};
  profileDialog.querySelector('input[type=search]').oninput=event=>{pendingSearch=event.target.value.trim();pageAfter=-1;profileDialog.querySelector('.mirror-profile-posts').replaceChildren();clearTimeout(openProfile.searchTimer);openProfile.searchTimer=setTimeout(loadPosts,250);};
  profileDialog.querySelector('.mirror-more').onclick=()=>{if(pageNext!==null){pageAfter=pageNext;loadPosts();}};
  async function loadPosts() {
    if (!activeProfile) return;
    const current=activeProfile.id, requestId=++postRequest, query='?after='+pageAfter+'&limit=36&q='+encodeURIComponent(pendingSearch);
    const message=profileDialog.querySelector('.mirror-profile-message');message.textContent='Loading posts…';
    try {
      const result=await api('api/v2/profiles/'+encodeURIComponent(current)+'/posts'+query);
      if(activeProfile?.id!==current||requestId!==postRequest)return;
      const host=profileDialog.querySelector('.mirror-profile-posts');
      const linked=activeProfileNodeId?nodes.filter(n=>n.id===activeProfileNodeId&&savedProfile(n)?.profile_id===current):[];
      for(const post of result.items||[]) {
        const item=E('article','mirror-post-row');const image=E('button','mirror-post-cover');image.type='button';image.setAttribute('aria-label','Open '+(post.title||'captured post'));image.onclick=()=>openPost(post.id);const img=E('img');img.alt='';const coverAsset=cover(post);img.src=preview(coverAsset);img.hidden=!coverAsset;img.draggable=false;image.append(img);if(!coverAsset)image.append(E('span','mirror-post-no-image','Image unavailable in captured mirror'));
        const details=E('div');details.append(E('b','',post.title||post.source_id||'Untitled post'),E('small','',post.coverage?.status||'captured'));
        const check=E('input');check.type='checkbox';check.checked=!linked.some(n=>savedProfile(n).excluded_post_ids?.includes(post.id));
        check.setAttribute('aria-label','Count '+(post.title||'post')+' in linked profile nodes');
        check.disabled=!linked.length;
        check.onchange=()=>{const before=boardSnapshot();for(const n of linked){const excludes=new Set(savedProfile(n).excluded_post_ids||[]);check.checked?excludes.delete(post.id):excludes.add(post.id);savedProfile(n).excluded_post_ids=[...excludes];}persist();recordBoardChange(before);item.classList.toggle('negative',!check.checked);};
        item.classList.toggle('negative',!check.checked);
        const label=E('label','mirror-post-inclusion');label.append(check,E('span','','Count in graph'));
        const inspect=E('button','mirror-post-open','Clone / overrides ↗');inspect.onclick=()=>openPost(post.id);
        const footer=E('div','mirror-post-card-footer');footer.append(label,inspect);
        item.append(image,details,footer);host.append(item);
      }
      pageNext=result.next;profileDialog.querySelector('.mirror-more').hidden=pageNext===null;
      message.textContent=(result.items||[]).length+' posts loaded · '+(linked.length?'unchecked posts are ignored by this graph profile':'add a profile node to set positive or negative posts');
    } catch(error){message.textContent=error.message;}
  }
  async function openPost(id) {
    const message=profileDialog.querySelector('.mirror-profile-message');
    try {const post=await api('api/v2/posts/'+encodeURIComponent(id));
      const d=E('dialog','mirror-post-dialog');const head=E('div','dialoghead');const close=E('button','','✕');close.onclick=()=>d.close();head.append(E('h2','',post.title||'Post'),close);
      const body=E('p','mirror-post-body',post.body||'No captured body');const source=E('a','','Open live post');source.href=post.source_url||'#';source.target='_blank';source.rel='noopener noreferrer';source.hidden=!post.source_url;
      const comments=E('section','mirror-post-comments');comments.append(E('h3','','Captured comments'));
      const result=await api('api/v2/posts/'+encodeURIComponent(id)+'/comments?limit=40');
      for(const c of result.items||[]){const row=E('div','mirror-post-comment');const author=E(c.author_profile_url?'a':'span','mirror-comment-author',c.author||'Commenter');
        if(c.author_profile_url){author.href=c.author_profile_url;author.target='_blank';author.rel='noopener noreferrer';author.title='Open live Red Note profile';}
        const avatarAsset=c.assets?.find(asset=>asset.role==='avatar'&&['verified','unverified'].includes(asset.verification));
        if(avatarAsset){const image=E('img');image.src=preview(avatarAsset);image.alt='';image.draggable=false;author.prepend(image);}
        row.append(author,E('p','',c.body||''));comments.append(row);}
      if(!(result.items||[]).length)comments.append(E('p','','No captured comments in this page.'));
      const node=nodes.find(item=>item.id===activeProfileNodeId),reference=node?.story?.mirror_ref||node?.story?.mirror_group_ref;
      const overrides=E('section','mirror-post-overrides');overrides.append(E('h3','','Graph JSON override · '+(node?'local to this node':'add a linked node first')));
      const field=E('textarea');field.setAttribute('aria-label','Post JSON override');field.value=JSON.stringify(reference?.post_overrides?.[id]||{},null,2);field.disabled=!reference;
      const save=E('button','','Save JSON override');save.disabled=!reference;
      const resultText=E('small','');save.onclick=()=>{try{const parsed=JSON.parse(field.value||'{}');if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw Error('Override must be a JSON object');
        const before=boardSnapshot();(reference.post_overrides??={})[id]=parsed;persist();recordBoardChange(before);resultText.textContent='Saved to this graph node. Mirror archive unchanged.';
      }catch(error){resultText.textContent=error.message;}};
      overrides.append(field,save,resultText);
      d.append(head,body,source,overrides,comments);document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();
    } catch(error){message.textContent=error.message;}
  }

  // Browser-owned Custom Storage never enters the Red Note mirror archive.
  const dbReady=new Promise((resolve,reject)=>{const request=indexedDB.open('scatter-custom-assets',1);request.onupgradeneeded=()=>request.result.createObjectStore('files',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
  async function allAssets() {const db=await dbReady;return new Promise((resolve,reject)=>{const request=db.transaction('files').objectStore('files').getAll();request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
  async function oneAsset(id) {const db=await dbReady;return new Promise((resolve,reject)=>{const request=db.transaction('files').objectStore('files').get(id);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
  async function importAssets(files) {
    const allowed=[...files].filter(f=>/^(image|video|audio)\//.test(f.type));
    const db=await dbReady;
    await Promise.all(allowed.map(file=>new Promise((resolve,reject)=>{const transaction=db.transaction('files','readwrite');transaction.objectStore('files').put({id:crypto.randomUUID(),name:file.name,type:file.type,bytes:file.size,blob:file,added_at:Date.now()});transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);})))
    activeTab='custom';await renderCustom();await publishCustomManifest();state.textContent=allowed.length+' file'+(allowed.length===1?'':'s')+' saved to Custom Storage in this browser';
  }
  async function publishCustomManifest(){if(!profilesLoaded||!studioBridgeLoaded)return;try{const assets=await allAssets();studioBridge.contentWindow?.postMessage({type:'scatter-custom-manifest',items:assets.map(({id,name,type,bytes,added_at})=>({id,name,type,bytes,added_at}))},'http://127.0.0.1:5185');}catch{}}
  function addSuppliedNode(entry,point=null){const node=makeBase(entry.kind,entry.title,430,520);node.customLibraryId=entry.id;node.image=entry.preview;node.localURL=entry.media;node.subtitle=entry.origin+' · supplied board media';node.story.custom_asset={library_id:entry.id,source:'supplied_board_library',category:entry.category};addNode(node,point,selectedBranch());}
  async function renderCustom() {
    list.replaceChildren();const upload=E('button','mirror-custom-upload','＋ Import music, video or image');upload.onclick=()=>dock.querySelector('.mirror-custom-input').click();list.append(upload);
    try {const [supplied,assets]=await Promise.all([suppliedLibrary,allAssets()]);state.textContent=supplied.length+' supplied board references · '+assets.length+' imported file'+(assets.length===1?'':'s');
      for(const entry of supplied){const row=E('div','mirror-custom-row mirror-supplied-row');row.draggable=true;row.addEventListener('dragstart',event=>{event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-scatter-custom-asset','catalog:'+entry.id);});const img=E('img');img.alt='';img.src=entry.preview;img.draggable=false;const info=E('span');info.append(E('b','',entry.title),E('small','',entry.category+' · on board'));const focus=E('button','','Locate');focus.onclick=()=>focusNode(entry.boardNodeId);const add=E('button','','＋');add.title='Add another node';add.onclick=()=>addSuppliedNode(entry);row.append(img,info,focus,add);list.append(row);}
      for(const asset of assets){const row=E('div','mirror-custom-row');row.draggable=true;row.addEventListener('dragstart',event=>{event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-scatter-custom-asset',asset.id);});
        row.append(E('span','',(asset.type.startsWith('audio/')?'♫':asset.type.startsWith('video/')?'▷':'▧')),E('span','',asset.name));
        const add=E('button','','＋');add.title='Add to graph';add.onclick=()=>addCustomNode(asset.id);row.append(add);list.append(row);}
    }catch(error){state.textContent='Custom Storage unavailable: '+error.message;}
  }
  dock.querySelector('.mirror-custom-input').onchange=event=>{importAssets(event.target.files).catch(error=>state.textContent=error.message);event.target.value='';};
  async function addCustomNode(id,point=null) {
    try {if(id.startsWith('catalog:')){const entry=(await suppliedLibrary).find(item=>item.id===id.slice(8));if(!entry)throw Error('Supplied reference is missing');addSuppliedNode(entry,point);return;}const asset=await oneAsset(id);if(!asset)throw Error('Custom file is missing');
      const kind=asset.type.split('/')[0],node=makeBase(kind,asset.name,430,520);
      node.customAssetId=id;node.story.custom_asset={id,name:asset.name,mime:asset.type,bytes:asset.bytes};
      node.subtitle='Custom Storage · separate from Red Note mirror';
      node.localURL=URL.createObjectURL(asset.blob);assetUrls.set(node.id,node.localURL);
      addNode(node,point,selectedBranch());
    }catch(error){state.textContent=error.message;}
  }
  const originalAddFiles=addFiles;
  addFiles=function(files){importAssets(files).catch(error=>status('Custom Storage import failed: '+error.message));};
  // Explicit file imports now land in Custom Storage; they are placed by drag or +.
  document.getElementById('add').textContent='＋ Import to Custom Storage';

  // Default graph classes. Existing custom Rainee/Horse/Dudu designs stay as they are.
  const classTools=[
    ['protagonist','Protagonist class','♛'],['branch','Alternative branch','◇'],['mirror-profile','Mirror profile','▤'],
    ['prose','Prose Engine placeholder','✒'],['document','Story output with Prose and LLM','≡']
  ];
  function createStoryChain(point=null) {
    if (!layout || !nodes) return;
    const before=boardSnapshot(), parent=selectedBranch(), rect=canvas.getBoundingClientRect();
    const center=point ? {x:(point.x-rect.left-pan.x)/scale,y:(point.y-rect.top-pan.y)/scale} :
      {x:(canvas.clientWidth/2-pan.x)/scale,y:(canvas.clientHeight/2-pan.y)/scale};
    const output=makeBase('output','Story output',620,620);
    const prose=makeBase('instruction','Prose Engine',500,290);
    const llm=makeBase('instruction','Story LLM renderer',500,310);
    prose.instruction='Prose conditioning stage · processor not installed';
    prose.story.ui_template={kind:'prose-engine',engine_status:'not_installed'};
    llm.instruction='Separate story rendering session · provider dispatch not connected';
    llm.story.ui_template={kind:'llm-renderer',engine_status:'not_connected'};
    llm.story.renderer={session_role:'story_render',separate_from:'data_analysis',providers:{gpt:true,gemini:false,grok:false},active_provider:'gpt',credential_ref:null};
    output.story.ui_template={kind:'rednote-document'};
    output.story.document_html='<h1>Untitled story</h1><p>Start writing here.</p>';
    const x=Math.max(0,Math.round(center.x-output.w/2)),y=Math.max(0,Math.round(center.y-output.h/2));
    Object.assign(output,{x,y});Object.assign(llm,{x:Math.max(0,x+60),y:Math.max(0,y-llm.h-90)});
    Object.assign(prose,{x:Math.max(0,x+60),y:Math.max(0,llm.y-prose.h-90)});
    for(const node of [prose,llm,output]){nodes.push(node);sourcePositions[node.id]=[node.x,node.y,node.w,node.h];}
    const connect=(from,to,label)=>layout.edges.push([from,to,label,{direction:'forward',active:true,signal:'story'}]);
    if(parent&&nodes.some(n=>n.id===parent)){connect(parent,prose.id,'branch context');prose.story.branch_owner=parent;(branchOwners[parent]??=[]).push(prose.id);}
    connect(prose.id,llm.id,'conditioned context');connect(llm.id,output.id,'rendered draft');
    llm.story.branch_owner=prose.id;output.story.branch_owner=llm.id;
    (branchOwners[prose.id]??=[]).push(llm.id);(branchOwners[llm.id]??=[]).push(output.id);
    mount();persist();saveConnections();recordBoardChange(before);renderObjects();select(output.id);
    status('Story chain created · Prose → separate LLM renderer → Output · Undo available');
    return output;
  }
  window.scatterCreateStoryChain=createStoryChain;
  function createClass(type,point=null) {
    if(type==='mirror-profile'){activeTab='mirror';showCabinet();return;}
    if(type==='document'){createStoryChain(point);return;}
    let node;
    if(type==='protagonist'){node=makeBase('actor','New custom protagonist',760,520);node.actor.fictional_actor_ref=node.id;node.actor.profile_role='protagonist';node.actor.source_kind='custom';node.story.ui_template={kind:'protagonist-default'};branchOwners[node.id]=[];}
    if(type==='branch'){node=makeBase('identity','New alternative branch',540,360);node.actor.fictional_actor_ref=node.id;node.story.ui_template={kind:'branch-default'};branchOwners[node.id]=[];}
    if(type==='prose'){node=makeBase('instruction','Prose Engine',500,330);node.instruction='Engine slot reserved · no processor installed';node.story.ui_template={kind:'prose-engine',engine_status:'not_installed'};}
    const selected=nodes?.find(n=>n.id===active);
    const parent=type==='protagonist'?null:type==='branch'?(selected?.actor?.profile_role==='protagonist'?selected.id:'actor'):selectedBranch();
    addNode(node,point,parent);
  }
  const legacyStoryOutput=document.getElementById('textOutput');
  if(legacyStoryOutput)legacyStoryOutput.onclick=()=>createStoryChain();
  const grid=document.querySelector('.graph-tool-section .graph-tool-grid');
  if(grid)for(const [type,label,glyph] of classTools){const button=E('button','graph-icon-tool',glyph);button.type='button';button.title=label+' · drag onto canvas';button.setAttribute('aria-label',label);button.dataset.nodeType=type;window.scatterEnableCatalogPointer(button,type,label,createClass);button.onclick=()=>createClass(type);grid.append(button);}

  const originalCard=card;
  card=function(node){originalCard(node);const element=document.getElementById(node.id);if(!element)return;
    const type=node.story?.ui_template?.kind;
    if(node.story?.mirror_group_ref){
      element.classList.add('mirror-group-node');
      const opener=[...element.querySelectorAll('.actions button')].find(button=>button.textContent.includes('Open source reference'));
      if(opener){opener.textContent='Open Mirror Cabinet';opener.onclick=()=>{activeTab='groups';showCabinet();};}
      const ref=node.story.mirror_group_ref,group=studioGroups.find(item=>item.id===ref.group_id);
      const members=group?capturedMembers(group):ref.member_profile_ids.map(id=>profiles.find(profile=>profile.id===id)).filter(Boolean);
      const panel=E('section','mirror-group-members');
      panel.append(E('strong','','▣ GROUP · '+members.length+' captured profiles'));
      for(const profile of members){const button=E('button','mirror-group-member');button.type='button';
        const img=E('img');img.alt='';img.src=preview(avatar(profile));img.draggable=false;
        button.append(img,E('span','',profile.name||profile.source_id||profile.id));
        button.onclick=()=>openProfile(profile.id,node.id);panel.append(button);}
      const unresolved=(group?.members||[]).length-members.length;
      if(unresolved>0)panel.append(E('small','mirror-group-unresolved',unresolved+' member'+(unresolved===1?'':'s')+' not captured in Mirror Storage'));
      if(!members.length)panel.append(E('p','','No captured profiles are linked to this group yet.'));
      const manage=E('a','','Manage group in Storage ↗');manage.href='http://127.0.0.1:5185/#storage';manage.target='_blank';manage.rel='noopener noreferrer';panel.append(manage);
      element.append(panel);
    }
    if(savedProfile(node)) {
      element.classList.add('mirror-source-node');
      element.querySelector('.profile-post-window')?.remove();
      element.querySelector('.container-controls')?.remove();element.querySelector('.container-scope')?.remove();
      element.ondblclick=event=>{if(!event.target.closest('button,input,textarea'))openProfile(savedProfile(node).profile_id,node.id);};
      element.querySelectorAll('.dossier-actions button').forEach(button=>{if(button.textContent.includes('Restore story output'))button.remove();});
      const box=E('section','mirror-node-summary');box.append(E('b','','Mirror profile · archive linked'),E('p','',savedProfile(node).source_id+' · posts included by default · engine pin pending'));
      const open=E('button','','Browse captured posts');open.onclick=()=>openProfile(savedProfile(node).profile_id,node.id);box.append(open);element.append(box);
      const list=E('div','mirror-node-posts');box.append(list);
      const id=savedProfile(node).profile_id;
      if(!firstPostPages.has(id))firstPostPages.set(id,api('api/v2/profiles/'+encodeURIComponent(id)+'/posts?limit=6'));
      firstPostPages.get(id).then(result=>{
        if(!list.isConnected)return;
        for(const post of result.items||[]){const item=E('button','mirror-node-post');item.type='button';item.classList.toggle('negative',savedProfile(node).excluded_post_ids?.includes(post.id));
          const image=E('img');image.alt='';image.src=preview(cover(post));image.draggable=false;item.append(image,E('span','',post.title||post.source_id||'Post'));item.onclick=()=>openPost(post.id);list.append(item);}
      }).catch(()=>{if(list.isConnected)list.append(E('small','','Post previews unavailable. Open the profile to retry.'));});
    }
    if(type==='branch-default')element.classList.add('blue-node','mirror-default-branch');
    if(type==='protagonist-default'){
      element.classList.add('mirror-default-protagonist');
      element.append(E('p','mirror-protagonist-source','Custom character · separate from Red Note Mirror Storage'));
    }
    if(type==='prose-engine'){
      element.classList.add('mirror-prose-node');element.append(E('p','mirror-prose-state','Processor slot reserved · no story transformation runs here yet.'));
    }
    if(type==='llm-renderer'){
      element.classList.add('mirror-llm-node');
      const panel=E('section','mirror-llm-settings');
      panel.append(E('strong','','STORY RENDERER'),E('p','','Separate session from data analysis · dispatch not connected'));
      const config=node.story.renderer||{};config.providers??={gpt:true,gemini:false,grok:false};
      for(const provider of ['gpt','gemini','grok']){
        const label=E('label','mirror-llm-provider'),toggle=E('input');toggle.type='checkbox';toggle.checked=Boolean(config.providers[provider]);
        toggle.setAttribute('aria-label','Enable '+provider.toUpperCase()+' story renderer');
        toggle.onchange=()=>{const before=boardSnapshot();config.providers[provider]=toggle.checked;
          if(!config.providers[config.active_provider])config.active_provider=['gpt','gemini','grok'].find(name=>config.providers[name])||null;
          persist();recordBoardChange(before);};
        label.append(toggle,E('span','',provider==='gpt'?'GPT':provider==='gemini'?'Gemini':'Grok'));panel.append(label);
      }
      const activeLabel=E('label','mirror-llm-active','Render with '),activeSelect=E('select');
      activeSelect.setAttribute('aria-label','Active story renderer');
      for(const provider of ['gpt','gemini','grok']){const option=E('option','',provider==='gpt'?'GPT':provider==='gemini'?'Gemini':'Grok');option.value=provider;option.disabled=!config.providers[provider];activeSelect.append(option);}
      activeSelect.value=config.active_provider||'';
      panel.querySelectorAll('input[type=checkbox]').forEach(input=>input.addEventListener('change',()=>{
        for(const option of activeSelect.options)option.disabled=!config.providers[option.value];
        activeSelect.value=config.active_provider||'';
      }));
      activeSelect.onchange=()=>{const before=boardSnapshot();config.active_provider=activeSelect.value;persist();recordBoardChange(before);};
      activeLabel.append(activeSelect);panel.append(activeLabel);
      element.append(panel);
    }
    if(type==='rednote-document'){
      element.classList.add('mirror-document-node');element.querySelector('.paper')?.remove();
      const preview=E('div','mirror-document-preview');preview.textContent=(new DOMParser().parseFromString(node.story.document_html||'','text/html').body.textContent||'Untitled story').slice(0,420);
      const edit=E('button','primary','Open document editor');edit.onclick=()=>openDocument(node);element.append(preview,edit);
    }
  };
  const priorWorkspace=workspace;
  workspace=function(node){if(node.story?.ui_template?.kind==='rednote-document'){openDocument(node);return;}if(node.story?.mirror_group_ref){activeTab='groups';showCabinet();return;}if(savedProfile(node)){openProfile(savedProfile(node).profile_id,node.id);return;}priorWorkspace(node);};

  // A small browser editor with explicit formatting and sanitized local HTML.
  const editor=E('dialog','scatter-document-editor');editor.innerHTML='<div class="dialoghead"><div><h2>Red Note document</h2><p>Editable local draft · Prose Engine is not installed</p></div><button type="button" class="doc-close">✕</button></div><div class="document-toolbar"><input class="doc-title" aria-label="Document title" placeholder="Document title"><select class="doc-font" aria-label="Font"><option value="Arial">Arial</option><option value="Georgia">Georgia</option><option value="Times New Roman">Times New Roman</option><option value="Microsoft YaHei">Microsoft YaHei</option><option value="Noto Sans CJK SC">Noto Sans CJK SC</option></select><select class="doc-size" aria-label="Font size"><option value="2">Small</option><option value="3" selected>Body</option><option value="4">Large</option><option value="5">Heading</option></select><button type="button" data-command="bold"><b>B</b></button><button type="button" data-command="italic"><i>I</i></button><button type="button" data-command="insertUnorderedList">• List</button><button type="button" data-command="justifyLeft">Left</button><button type="button" data-command="justifyCenter">Center</button></div><div class="document-layout"><div class="document-paper" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Story document"></div><aside class="document-comments"><h3>Red Note comments</h3><select class="doc-profile" aria-label="Source profile"><option value="">Choose source profile</option></select><select class="doc-post" aria-label="Source post"><option value="">Choose source post</option></select><button type="button" class="doc-load-comments">Load captured comments</button><div class="doc-comment-list"><p>Choose a captured post to see its comments. No comments are invented.</p></div></aside></div><div class="document-footer"><button type="button" class="doc-save primary">Save document</button><button type="button" class="doc-export">Export HTML</button><span role="status"></span></div>';
  document.body.append(editor);let editing=null;
  editor.querySelector('.doc-close').onclick=()=>editor.close();
  let textSelection=null;
  document.addEventListener('selectionchange',()=>{const selection=window.getSelection(),paper=editor.querySelector('.document-paper');if(editor.open&&selection?.rangeCount&&paper.contains(selection.anchorNode))textSelection=selection.getRangeAt(0).cloneRange();});
  function format(command,value=null){const paper=editor.querySelector('.document-paper');paper.focus();if(textSelection){const selection=window.getSelection();selection.removeAllRanges();selection.addRange(textSelection);}document.execCommand(command,false,value);}
  editor.querySelectorAll('[data-command]').forEach(button=>{button.onmousedown=event=>event.preventDefault();button.onclick=()=>format(button.dataset.command);});
  editor.querySelector('.doc-font').onchange=event=>format('fontName',event.target.value);
  editor.querySelector('.doc-size').onchange=event=>format('fontSize',event.target.value);
  function cleanHTML(html){const doc=new DOMParser().parseFromString(html,'text/html'),safe=document.createElement('div'),allow=new Set(['P','BR','B','STRONG','I','EM','U','H1','H2','H3','UL','OL','LI','BLOCKQUOTE','DIV','SPAN','FONT']);
    function copy(from,to){for(const child of from.childNodes){if(child.nodeType===3){to.append(document.createTextNode(child.textContent));continue;}if(child.nodeType!==1||['SCRIPT','STYLE','IFRAME','OBJECT','SVG','MATH'].includes(child.tagName))continue;
      if(!allow.has(child.tagName)){copy(child,to);continue;}
      const clean=document.createElement(child.tagName.toLowerCase());
      for(const attr of child.attributes){if(['color','face','size'].includes(attr.name)&&/^[\w\s#.,()-]{1,80}$/.test(attr.value))clean.setAttribute(attr.name,attr.value);
        if(attr.name==='style'){const values=attr.value.split(';').map(pair=>pair.trim()).filter(pair=>/^(?:text-align|font-family|font-size|font-weight|font-style|color)\s*:\s*[\w\s#.,()%'-]+$/i.test(pair));if(values.length)clean.setAttribute('style',values.join(';'));}}
      copy(child,clean);to.append(clean);
    }}copy(doc.body,safe);return safe.innerHTML;}
  function openDocument(node){editing=node;editor.querySelector('h2').textContent=node.title;editor.querySelector('.doc-title').value=node.title;editor.querySelector('.document-paper').innerHTML=cleanHTML(node.story.document_html||node.story.draft||'');
    const select=editor.querySelector('.doc-profile');select.replaceChildren(new Option('Choose source profile',''));
    profiles.forEach(profile=>select.append(new Option(profile.name,profile.id)));
    if(node.story.source_profile_id){select.value=node.story.source_profile_id;loadDocumentPosts(node.story.source_post_id);}
    else {editor.querySelector('.doc-post').replaceChildren(new Option('Choose source post',''));editor.querySelector('.doc-comment-list').replaceChildren(E('p','','Choose a captured post to see its comments. No comments are invented.'));}
    editor.showModal();}
  async function loadDocumentPosts(preferred='') {
    const profileId=editor.querySelector('.doc-profile').value, select=editor.querySelector('.doc-post');
    select.replaceChildren(new Option('Choose source post',''));
    if(!profileId)return;
    const result=await api('api/v2/profiles/'+encodeURIComponent(profileId)+'/posts?limit=60');
    for(const post of result.items||[])select.append(new Option(post.title||post.source_id||post.id,post.id));
    if(preferred)select.value=preferred;
    editor.querySelector('.doc-comment-list').replaceChildren(E('p','',result.next!==null?'First 60 posts shown. Search the Mirror Profile browser for later posts.':'Select a post, then load its captured comments.'));
  }
  editor.querySelector('.doc-profile').onchange=()=>loadDocumentPosts().catch(error=>editor.querySelector('.doc-comment-list').textContent=error.message);
  editor.querySelector('.doc-load-comments').onclick=async()=>{
    const postId=editor.querySelector('.doc-post').value,host=editor.querySelector('.doc-comment-list');host.replaceChildren();
    if(!postId){host.append(E('p','','Choose a captured post first.'));return;}
    try {const result=await api('api/v2/posts/'+encodeURIComponent(postId)+'/comments?limit=40');
      for(const comment of result.items||[]){const row=E('article','doc-comment');const link=E('a');link.href=comment.author_profile_url||'#';link.target='_blank';link.rel='noopener noreferrer';link.textContent=comment.author||'Commenter';if(!comment.author_profile_url)link.removeAttribute('href');
        const imgAsset=comment.assets?.find(asset=>asset.role==='avatar'&&['verified','unverified'].includes(asset.verification));if(imgAsset){const img=E('img');img.src=preview(imgAsset);img.alt='';link.prepend(img);}
        row.append(link,E('p','',comment.body||''));host.append(row);}
      if(!(result.items||[]).length)host.append(E('p','','No captured comments on this page.'));
      if(result.next!==null)host.append(E('small','','First 40 comments shown; more are available in Mirror Storage.'));
    }catch(error){host.append(E('p','',error.message));}
  };
  editor.querySelector('.doc-save').onclick=()=>{if(!editing)return;const before=boardSnapshot();editing.title=editor.querySelector('.doc-title').value.trim()||'Untitled document';editing.story.document_html=cleanHTML(editor.querySelector('.document-paper').innerHTML);editing.story.draft=editor.querySelector('.document-paper').textContent;
    editing.story.source_profile_id=editor.querySelector('.doc-profile').value||null;editing.story.source_post_id=editor.querySelector('.doc-post').value||null;
    persist();recordBoardChange(before);mount();editor.querySelector('.document-footer [role=status]').textContent='Saved locally';};
  editor.querySelector('.doc-export').onclick=()=>{const html='<!doctype html><meta charset="utf-8"><title>'+((editing?.title||'Document').replace(/[<>&]/g,''))+'</title><article>'+cleanHTML(editor.querySelector('.document-paper').innerHTML)+'</article>';const url=URL.createObjectURL(new Blob([html],{type:'text/html'})),link=E('a');link.href=url;link.download=(editing?.title||'document').replace(/[^a-z0-9_-]/gi,'-')+'.html';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

  // Recover custom node media and branch ownership after the original layout loads.
  const ready=setInterval(async()=>{if(typeof nodes==='undefined'||typeof layout==='undefined'||!nodes||!layout)return;clearInterval(ready);
    for(const node of nodes){if(['protagonist-default','branch-default'].includes(node.story?.ui_template?.kind))branchOwners[node.id]??=[];
      const owner=node.story?.branch_owner;if(owner&&!(branchOwners[owner]||[]).includes(node.id))(branchOwners[owner]??=[]).push(node.id);
      if(node.customAssetId){try{const asset=await oneAsset(node.customAssetId);if(asset){node.localURL=URL.createObjectURL(asset.blob);assetUrls.set(node.id,node.localURL);}}catch{}}}
    mount();refreshProfiles();if(location.hash==='#custom'){activeTab='custom';showCabinet();}
  },120);
  window.addEventListener('beforeunload',()=>assetUrls.forEach(url=>URL.revokeObjectURL(url)));
})();
