// Workspace navigation around the existing Red Engine feature modules.
(() => {
  const sectionIds=['overview','study','tools','sessions','findings','contracts'];
  const byId=id=>document.getElementById(id);
  const shell=document.querySelector('.re-shell'),sidebar=document.querySelector('.re-sidebar'),topbar=document.querySelector('.re-topbar');
  const rail=document.createElement('aside');rail.className='re-context-rail';rail.setAttribute('aria-label','Study context');shell.append(rail);
  const quick=document.createElement('form');quick.className='re-quick-search';quick.innerHTML='<input type="search" aria-label="Search captured archive" placeholder="Search captured archive…"><button type="submit" aria-label="Search">⌕</button>';
  topbar.insertBefore(quick,topbar.querySelector('.re-top-actions'));
  const newStudy=document.createElement('button');newStudy.className='re-new-study';newStudy.type='button';newStudy.textContent='＋ New study';sidebar.insertBefore(newStudy,sidebar.querySelector('.re-rail-label'));
  let studies=[],profiles=[],selectedStudyId=localStorage.getItem('re-workspace-study')||'',view='overview';
  const api=async path=>{const response=await fetch('research/'+path,{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'Research service unavailable');return data;};
  const sessionSources=byId('re-session-sources'),sessionMessages=byId('re-session-messages'),sessionStatus=byId('re-session-submit-status');
  function sessionMessage(role,message){const row=document.createElement('div');row.className='re-session-message '+role;const name=document.createElement('b');name.textContent=role==='user'?'You':'Red Engine';const text=document.createElement('p');text.textContent=message;row.append(name,text);sessionMessages.append(row);sessionMessages.scrollTop=sessionMessages.scrollHeight;}
  let sessionSourceLoad=0,openEvidenceProfile=null,selectedEvidencePosts=new Set();
  function updateEvidenceCount(){const counter=byId('re-session-source-count');if(counter)counter.textContent=selectedEvidencePosts.size+' / 5 selected';}
  const assetPreview=asset=>'engine/api/v2/assets/'+encodeURIComponent(asset.id)+'/preview';
  function appendEvidenceImage(host,asset){if(!asset){host.textContent='Image unavailable';return;}const img=document.createElement('img');img.src=assetPreview(asset);img.alt='';img.loading='lazy';img.draggable=false;img.onerror=()=>{img.remove();host.textContent='Image unavailable';};host.append(img);}
  async function loadSessionSources(){const load=++sessionSourceLoad;sessionSources.replaceChildren();if(!selectedStudyId){sessionSources.textContent='Choose a saved study to select evidence.';return;}
    sessionSources.textContent='Loading study profiles…';
    try{const study=studies.find(item=>item.id===selectedStudyId),pinned=new Set(study?.post_ids||[]),members=(study?.profile_ids||[]).map(id=>profiles.find(profile=>profile.id===id)).filter(Boolean);
      if(load!==sessionSourceLoad)return;sessionSources.replaceChildren();if(!members.length){sessionSources.textContent='This study has no captured profiles.';return;}
      if(openEvidenceProfile&&!members.some(profile=>profile.id===openEvidenceProfile))openEvidenceProfile=null;
      if(!openEvidenceProfile){sessionSources.classList.remove('re-evidence-open');for(const profile of members){const button=document.createElement('button');button.type='button';button.className='re-evidence-profile';const avatar=document.createElement('span');avatar.className='re-evidence-profile-avatar';appendEvidenceImage(avatar,profile.assets?.find(asset=>asset.role==='avatar'&&asset.verification==='verified'));const details=document.createElement('span');const name=document.createElement('strong');name.textContent=profile.name||profile.source_id||'Captured profile';const count=document.createElement('small');count.textContent=pinned.size+' study posts · Double-click to open mirror posts';details.append(name,count);const arrow=document.createElement('span');arrow.textContent='›';arrow.className='re-evidence-profile-arrow';button.append(avatar,details,arrow);button.ondblclick=()=>{openEvidenceProfile=profile.id;loadSessionSources();};button.onclick=()=>{sessionStatus.textContent='Double-click '+name.textContent+' to browse its mirrored posts.';};sessionSources.append(button);}return;}
      const profile=members.find(profile=>profile.id===openEvidenceProfile);sessionSources.classList.add('re-evidence-open');const header=document.createElement('div');header.className='re-evidence-folder-head';const back=document.createElement('button');back.type='button';back.textContent='← Profiles';back.onclick=()=>{openEvidenceProfile=null;loadSessionSources();};const label=document.createElement('div');label.innerHTML='<strong></strong><small></small>';label.querySelector('strong').textContent=profile.name||'Captured profile';label.querySelector('small').textContent='Mirror Storage posts · only posts pinned in this study can be sent';header.append(back,label);sessionSources.append(header);
      const response=await fetch('engine/api/v2/profiles/'+encodeURIComponent(profile.id)+'/posts',{cache:'no-store'});if(!response.ok)throw Error('Mirror posts unavailable');const data=await response.json();if(load!==sessionSourceLoad)return;
      for(const post of data.items||[]){const available=pinned.has(post.id),card=document.createElement('label');card.className='re-evidence-card'+(available?'':' re-evidence-outside-study');const input=document.createElement('input');input.type='checkbox';input.value=post.id;input.name='re-session-post';input.disabled=!available;input.checked=selectedEvidencePosts.has(post.id);input.setAttribute('aria-label','Select '+(post.title||'captured post'));
        const media=document.createElement('span');media.className='re-evidence-media';appendEvidenceImage(media,post.assets?.find(asset=>asset.verification==='verified'&&['cover','image','thumbnail'].includes(asset.role)));const title=document.createElement('strong');title.textContent=post.title||'Captured post '+post.id.slice(0,8);const meta=document.createElement('span');meta.className='re-evidence-meta';meta.textContent=available?'Pinned in study':'Mirror post · outside study';card.append(input,media,title,meta);sessionSources.append(card);}
      const note=document.createElement('small');note.className='re-evidence-note';note.textContent=(data.items?.length||0)+' mirrored posts · '+pinned.size+' pinned in this study';sessionSources.append(note);updateEvidenceCount();
    }catch(error){if(load===sessionSourceLoad)sessionSources.textContent=error.message;}}
  sessionSources.addEventListener('change',event=>{if(!event.target.matches('input[name="re-session-post"]'))return;if(event.target.checked&&selectedEvidencePosts.size>=5){event.target.checked=false;sessionStatus.textContent='Select no more than five posts per session request.';}else if(event.target.checked)selectedEvidencePosts.add(event.target.value);else selectedEvidencePosts.delete(event.target.value);updateEvidenceCount();});
  const composer=document.querySelector('.re-session-composer'),settings=document.createElement('details');settings.className='re-session-settings';const settingsSummary=document.createElement('summary');settingsSummary.textContent='Research settings · presets, scope and search terms';settings.append(settingsSummary);
  for(const item of [...composer.children])if(item.classList.contains('re-preset-picker')||item.matches('fieldset,.re-form-pair,.re-terms-label,.re-preset-save,.re-field-note,#preset-terms,#scope-preview'))settings.append(item);
  composer.append(settings);
  byId('re-submit-session').onclick=async()=>{const button=byId('re-submit-session'),task=byId('study-question').value.trim(),postIds=[...selectedEvidencePosts],provider=byId('re-session-provider').value;
    if(!selectedStudyId){sessionStatus.textContent='Select or create a saved study first.';return;}
    if(!task){sessionStatus.textContent='Write a research question before submitting.';byId('study-question').focus();return;}
    if(!postIds.length){sessionStatus.textContent='Select at least one captured post from this study.';return;}
    button.disabled=true;sessionStatus.textContent='Saving pinned evidence and session request…';
    try{const id=encodeURIComponent(selectedStudyId),options=body=>({method:'POST',headers:{'Content-Type':'application/json','X-RedNote-Action':'explicit'},body:JSON.stringify(body)});
      const response=await fetch('research/api/studies/'+id+'/contexts',options({task,post_ids:postIds,comment_ids:[],asset_ids:[],include_english_aids:true}));const context=await response.json();if(!response.ok)throw Error(context.error||'Could not save evidence context');
      const jobResponse=await fetch('research/api/studies/'+id+'/jobs',options({context_id:context.id,provider}));const job=await jobResponse.json();if(!jobResponse.ok)throw Error(job.error||'Could not create session job');
      sessionStatus.textContent='Submitted · source context '+context.id+' · '+job.state+'.';await loadStudyDetails();
    }catch(error){sessionStatus.textContent='Not submitted: '+error.message;}finally{button.disabled=false;}};
  const count=value=>Number(value||0).toLocaleString();
  function route(next){view=sectionIds.includes(next)?next:'overview';document.body.dataset.reView=view;
    document.querySelectorAll('.re-nav a').forEach(link=>link.classList.toggle('active',link.hash==='#'+view));
    history.replaceState(null,'','#'+view);window.scrollTo({top:0,behavior:'instant'});renderRail();}
  document.querySelectorAll('.re-nav a[href^="#"]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();route(link.hash.slice(1));}));
  window.addEventListener('hashchange',()=>route(location.hash.slice(1)));
  newStudy.onclick=()=>{route('study');byId('study-question')?.focus();};
  quick.onsubmit=event=>{event.preventDefault();const query=quick.querySelector('input').value.trim();route('study');const target=byId('preset-search-query');target.value=query;byId('preset-search-form').requestSubmit();target.focus();};
  const overview=document.createElement('section');overview.className='re-workspace-home';overview.innerHTML='<div class="re-home-label">RED ENGINE · RESEARCH WORKSPACE</div><h1>Research your captured Red Note archive.</h1><p>Search source records, pin a study, then inspect the evidence, session jobs, and findings.</p><div class="re-home-actions"><button type="button" data-route="study">＋ New study</button><button type="button" data-route="study" data-search="true">⌕ Search archive</button><button type="button" data-route="findings">◇ Findings</button></div><div class="re-home-studies"><h2>Recent studies</h2><div class="re-home-study-list" role="list"><p>Loading saved studies…</p></div></div>';
  topbar.after(overview);
  overview.addEventListener('click',event=>{const button=event.target.closest('[data-route]');if(!button)return;route(button.dataset.route);if(button.dataset.search)byId('preset-search-query')?.focus();});
  const studyActions=document.createElement('section');studyActions.className='re-panel re-study-actions';
  studyActions.innerHTML='<div class="re-action-heading"><div><span>PIN SOURCE SCOPE</span><h3>Create a revisioned study</h3><p>Select captured profiles. The engine pins their current records and reports coverage.</p></div><button type="button" class="re-refresh-studies">↻ Refresh</button></div><label>Study title <input class="re-study-title" type="text" maxlength="160" placeholder="Name this study"></label><fieldset><legend>Captured profiles</legend><div class="re-profile-options">Loading profiles…</div></fieldset><button type="button" class="re-create-study">Create study from selected profiles</button><p class="re-create-status" role="status"></p>';
  byId('study').insertBefore(studyActions,byId('study').querySelector('.re-preset-search'));
  studyActions.querySelector('.re-refresh-studies').onclick=()=>refreshAll();
  studyActions.querySelector('.re-create-study').onclick=async()=>{
    const title=studyActions.querySelector('.re-study-title').value.trim(),question=byId('study-question').value.trim();
    const profileIds=[...studyActions.querySelectorAll('input[name="re-profile-id"]:checked')].map(input=>input.value);
    const notice=studyActions.querySelector('.re-create-status');
    if(!title||!question||!profileIds.length){notice.textContent='Enter a title and question, then select at least one captured profile.';return;}
    const button=studyActions.querySelector('.re-create-study');button.disabled=true;notice.textContent='Pinning current archive records…';
    try{const response=await fetch('research/api/studies',{method:'POST',headers:{'Content-Type':'application/json','X-RedNote-Action':'explicit'},body:JSON.stringify({title,question,preset_id:byId('research-preset').value||null,profile_ids:profileIds})});const study=await response.json();if(!response.ok)throw Error(study.error||'Study could not be created');
      selectedStudyId=study.id;localStorage.setItem('re-workspace-study',study.id);notice.textContent='Study saved · '+count(study.counts?.post)+' posts · '+count(study.counts?.comment)+' comments pinned.';await loadStudies();}
    catch(error){notice.textContent=error.message;}finally{button.disabled=false;}
  };
  const liveFindings=document.createElement('section');liveFindings.className='re-live-records';liveFindings.innerHTML='<div class="re-action-heading"><div><span>SAVED ENGINE RECORDS</span><h3>Findings in selected study</h3></div><button type="button">↻ Refresh</button></div><div class="re-live-finding-list">Choose a saved study.</div>';
  byId('findings').insertBefore(liveFindings,byId('findings').querySelector('.re-finding-grid'));
  liveFindings.querySelector('button').onclick=loadStudyDetails;
  const liveJobs=document.createElement('section');liveJobs.className='re-live-records';liveJobs.innerHTML='<div class="re-action-heading"><div><span>SAVED ENGINE RECORDS</span><h3>AI session jobs</h3></div><button type="button">↻ Refresh</button></div><div class="re-live-job-list">Choose a saved study.</div>';
  byId('sessions').insertBefore(liveJobs,byId('sessions').querySelector('.re-session-grid'));
  liveJobs.querySelector('button').onclick=loadStudyDetails;
  for(const [sectionId,selectors] of [['sessions',['.re-session-grid','.re-two-way','.re-readiness','.re-job-preview','.re-upgrade-strip']],['findings',['.re-finding-grid']]]){
    const section=byId(sectionId),notes=document.createElement('details');notes.className='re-legacy-notes';const summary=document.createElement('summary');summary.textContent='Design and implementation notes';notes.append(summary);
    for(const selector of selectors){const item=section.querySelector(selector);if(item)notes.append(item);}section.append(notes);
  }
  function renderProfiles(){const host=studyActions.querySelector('.re-profile-options');host.replaceChildren();
    if(!profiles.length){host.textContent='No captured profiles available from Mirror Storage.';return;}
    for(const profile of profiles){const label=document.createElement('label'),checkbox=document.createElement('input'),name=document.createElement('span');checkbox.type='checkbox';checkbox.name='re-profile-id';checkbox.value=profile.id;name.textContent=(profile.name||profile.source_id||profile.id)+' · '+profile.id.slice(0,8);label.append(checkbox,name);host.append(label);}
  }
  function studyButton(study){const button=document.createElement('button');button.type='button';button.className='re-study-item';button.classList.toggle('selected',study.id===selectedStudyId);
    const title=document.createElement('strong'),detail=document.createElement('small');title.textContent=study.title;detail.textContent=count(study.counts?.post)+' posts · '+count(study.counts?.comment)+' comments';button.append(title,detail);
    button.onclick=()=>{if(selectedStudyId!==study.id){openEvidenceProfile=null;selectedEvidencePosts.clear();updateEvidenceCount();}selectedStudyId=study.id;localStorage.setItem('re-workspace-study',study.id);renderStudies();loadStudyDetails();loadSessionSources();};return button;}
  function renderStudies(){const home=overview.querySelector('.re-home-study-list');home.replaceChildren();
    if(!studies.length)home.textContent='No studies pinned yet.';
    else studies.slice(0,7).forEach(study=>home.append(studyButton(study)));
    renderRail();}
  function renderRail(){rail.replaceChildren();const heading=document.createElement('div');heading.className='re-context-heading';heading.innerHTML='<b>Study context</b><small>Source-backed workspace</small>';rail.append(heading);
    const current=studies.find(study=>study.id===selectedStudyId);
    const card=document.createElement('div');card.className='re-context-card';const label=document.createElement('small');label.textContent='SELECTED STUDY';const title=document.createElement('strong');title.textContent=current?.title||'No study selected';const meta=document.createElement('p');meta.textContent=current?count(current.counts?.profile)+' profiles · '+count(current.counts?.post)+' posts · '+count(current.counts?.comment)+' comments':'Choose a study to inspect its jobs and findings.';card.append(label,title,meta);rail.append(card);
    const caption=document.createElement('h3');caption.textContent='Recent studies';rail.append(caption);for(const study of studies.slice(0,8))rail.append(studyButton(study));
    const actions=document.createElement('div');actions.className='re-context-actions';for(const [label,target] of [['Search archive','study'],['Session jobs','sessions'],['Findings','findings']]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>route(target);actions.append(button);}rail.append(actions);
  }
  async function loadStudies(){try{studies=(await api('api/studies')).items||[];if(!studies.some(study=>study.id===selectedStudyId))selectedStudyId=studies[0]?.id||'';renderStudies();await loadStudyDetails();await loadSessionSources();}catch(error){overview.querySelector('.re-home-study-list').textContent=error.message;renderRail();}}
  async function loadStudyDetails(){const findingHost=liveFindings.querySelector('.re-live-finding-list'),jobHost=liveJobs.querySelector('.re-live-job-list');findingHost.replaceChildren();jobHost.replaceChildren();
    if(!selectedStudyId){findingHost.textContent=jobHost.textContent='Choose a study in the right rail.';return;}
    try{const id=encodeURIComponent(selectedStudyId);const [findings,jobs]=await Promise.all([api('api/studies/'+id+'/findings'),api('api/studies/'+id+'/jobs')]);
      if(!findings.items?.length)findingHost.textContent='No finding records for this study.';
      for(const finding of findings.items||[]){const card=document.createElement('article');card.className='re-live-card';const title=document.createElement('strong'),meta=document.createElement('small');title.textContent=finding.claim||finding.title||finding.id;meta.textContent=(finding.status||'proposed')+' · '+(finding.id||'');card.append(title,meta);findingHost.append(card);}
      if(!jobs.items?.length)jobHost.textContent='No session jobs for this study.';
      for(const job of jobs.items||[]){const card=document.createElement('article');card.className='re-live-card';const title=document.createElement('strong'),meta=document.createElement('small');title.textContent=(job.provider||job.kind||'AI job')+' · '+(job.status||'unknown');meta.textContent=job.id||'';card.append(title,meta);jobHost.append(card);}
      sessionMessages.replaceChildren();
      const recent=(jobs.items||[]).slice(0,8).reverse();
      if(!recent.length){const intro=document.createElement('div');intro.className='re-session-intro';intro.textContent='No session requests for this study yet. Select 1–5 posts and submit a question.';sessionMessages.append(intro);}
      const histories=await Promise.all(recent.map(async job=>{try{return {job,context:await api('api/studies/'+id+'/contexts/'+encodeURIComponent(job.context_id))};}catch{return {job,context:null};}}));
      for(const {job,context} of histories){sessionMessage('user',context?.payload?.task||'Saved evidence request');sessionMessage('engine',(job.provider||'Model')+' · '+(job.state||job.status||'awaiting session')+' · '+job.id+'. '+(job.sent?'Provider receipt recorded.':'Awaiting provider session; no model answer yet.'));}
    }catch(error){findingHost.textContent=jobHost.textContent=error.message;}
  }
  async function refreshAll(){try{profiles=(await fetch('engine/api/v2/profiles',{cache:'no-store'}).then(response=>response.json())).items||[];renderProfiles();}catch(error){studyActions.querySelector('.re-profile-options').textContent='Mirror Storage unavailable: '+error.message;}await loadStudies();}
  route(location.hash.slice(1));refreshAll();
})();
