from pathlib import Path
import json, shutil, hashlib
from datetime import datetime, timezone
root=Path(__file__).parent
audit=Path('D:/0/Noir Fiction Engine/00_ADMIN/RedNote_Graph_Containers_20261009')/datetime.now(timezone.utc).strftime('%H%M%S')
audit.mkdir(parents=True)
for name in ['app-pass2.js','index.html','style.css','layout.json']:
    shutil.copy2(root/name,audit/name)
s=(root/'app-pass2.js').read_text(encoding='utf-8')
s=s.replace('story:n.story,actor:n.actor,override:n.override','w:n.w,h:n.h,mirrorTemplate:n.mirrorTemplate,story:n.story,actor:n.actor,override:n.override')
s=s.replace("if(!ev.target.closest('.handle'))return;", "if(ev.target.closest('.resize-node'))return;if(!ev.target.closest('.handle'))return;")
s=s.replace("$('#cards').append(e)}", "addResizeHandle(e,n);$('#cards').append(e)}")
s=s.replace("else{const n=nodes.find(n=>n.id===drag.id);n.x=", "else if(drag.mode==='resize'){const n=nodes.find(n=>n.id===drag.id);n.w=Math.max(n.kind==='junction'?64:320,Math.min(1600,drag.nw+(e.clientX-drag.x)/scale));n.h=Math.max(n.kind==='junction'?64:340,Math.min(1400,drag.nh+(e.clientY-drag.y)/scale));const elem=document.getElementById(n.id);elem.style.width=n.w+'px';elem.style.height=n.h+'px';drawWires()}else{const n=nodes.find(n=>n.id===drag.id);n.x=")
s=s.replace("if(drag?.mode==='node')persist();", "if(drag&&drag.mode!=='pan'){const n=nodes.find(n=>n.id===drag.id);sourcePositions[n.id]=[n.x,n.y,n.w,n.h];persist();}")
s=s.replace("if(n)Object.assign(n,s)", "if(n)Object.assign(n,s);else if(s.mirrorTemplate){const template=nodes.find(t=>t.id===s.mirrorTemplate);if(template)nodes.push({...structuredClone(template),...s})}")
s=s.replace("const actions=el('div','actions')", "if(n.offline_page_ref){const data=el('button','data-container',n.kind==='profile'?'▤ Profile data · posts / media / discussion':'▤ Post data · media / discussion');data.onclick=()=>openContainer(n);e.append(data)}const actions=el('div','actions')")
s+='''
// Mirror containers are UI references; the capture engine is deliberately disconnected.
function addResizeHandle(e,n){
 const handle=el('button','resize-node','◢');handle.setAttribute('aria-label','Resize '+n.title);handle.title='Drag to resize · arrow keys resize by 20 px';
 handle.addEventListener('pointerdown',ev=>{select(n.id);drag={mode:'resize',id:n.id,x:ev.clientX,y:ev.clientY,nw:n.w,nh:n.h};viewport.setPointerCapture(ev.pointerId);ev.stopPropagation();ev.preventDefault()});
 handle.addEventListener('keydown',ev=>{if(!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp'].includes(ev.key))return;ev.preventDefault();ev.stopPropagation();const step=ev.shiftKey?100:20;n.w=Math.max(n.kind==='junction'?64:320,Math.min(1600,n.w+(ev.key==='ArrowRight'?step:ev.key==='ArrowLeft'?-step:0)));n.h=Math.max(n.kind==='junction'?64:340,Math.min(1400,n.h+(ev.key==='ArrowDown'?step:ev.key==='ArrowUp'?-step:0)));e.style.width=n.w+'px';e.style.height=n.h+'px';sourcePositions[n.id]=[n.x,n.y,n.w,n.h];drawWires();persist()});e.append(handle);
}
function openContainer(n){
 $('#containerTitle').textContent=n.title;$('#containerMeta').textContent='Offline '+n.offline_page_ref.kind+' container · '+n.offline_page_ref.binding_status.replaceAll('_',' ');
 const list=$('#containerContents');list.replaceChildren();
 for(const [title,description]of [['Profile & identity','Source creator, source URL and retained profile metadata. Separate from fictional Actor JSON.'],['Posts & pages','Whole offline pages, with original post order and source IDs.'],['Images & videos','Original media references; covers, avatars and attachments remain distinct.'],['Comments & replies','Original discussion hierarchy and attachment references. No AI interpretation in this view.'],['Evidence & provenance','Declared totals, saved files, verification state and last capture checkpoint.']]){const row=el('section','container-section');row.append(el('h3','',title),el('p','',description));list.append(row)}
 $('#containerNotice').textContent='UI prototype: source archive is not connected. No data is copied, downloaded, analyzed or sent to AI. Future engine binding will resolve this container to its complete retained dataset.';$('#containerDialog').showModal();
}
function renderMirror(){
 const list=$('#mirrorItems');list.replaceChildren();const query=$('#mirrorSearch').value.toLowerCase();
 for(const n of layout.nodes.filter(n=>n.offline_page_ref&&n.title.toLowerCase().includes(query))){const item=el('article','mirror-item');if(n.image){const image=el('img');image.src=n.image;image.alt=n.title;item.append(image)}const content=el('div');content.append(el('b','',n.title),el('p','',n.offline_page_ref.kind+' container · reference only'));const add=el('button','',nodes.some(t=>t.id===n.id)?'Locate on board':'Add to board');add.onclick=()=>addMirror(n);content.append(add);item.append(content);list.append(item)}
 if(!list.children.length)list.append(el('p','','No matching profile or post references.'));
}
function addMirror(template){
 const existing=nodes.find(n=>n.id===template.id);if(existing){$('#mirrorDialog').close();focusNode(existing.id);status('Existing container located · no duplicate node or download');return}
 const n=structuredClone(template);n.mirrorTemplate=template.id;n.x=Math.max(0,(viewport.clientWidth/2-pan.x)/scale-n.w/2);n.y=Math.max(0,(viewport.clientHeight/2-pan.y)/scale-n.h/2);nodes.push(n);sourcePositions[n.id]=[n.x,n.y,n.w,n.h];card(n);drawWires();persist();$('#mirrorDialog').close();focusNode(n.id);
}
$('#mirrorOpen').onclick=()=>{renderMirror();$('#mirrorDialog').showModal()};$('#mirrorSearch').oninput=renderMirror;
'''
(root/'app-pass2.js').write_text(s,encoding='utf-8')
html=(root/'index.html').read_text(encoding='utf-8').replace('<button id="add">','<button id="mirrorOpen">▤ Mirror Storage</button><button id="add">')
html=html.replace('<script src="app-pass2.js">','''<dialog id="mirrorDialog"><div class="dialoghead"><div><h2>Mirror Storage</h2><p class="muted">Add whole profile or post containers to the graph</p></div><button class="close" aria-label="Close Mirror Storage">✕</button></div><input id="mirrorSearch" type="search" placeholder="Search profiles and posts" aria-label="Search Mirror Storage"><p class="muted">Reference catalog · archive engine not connected. Items already on the board are located instead of duplicated.</p><div id="mirrorItems"></div></dialog>
<dialog id="containerDialog"><div class="dialoghead"><div><h2 id="containerTitle"></h2><p id="containerMeta" class="muted"></p></div><button class="close" aria-label="Close data container">✕</button></div><div id="containerContents"></div><p id="containerNotice" class="muted"></p></dialog><script src="app-pass2.js">''')
html=html.replace('Audio study · pass 1','Mirror containers · pass 2').replace('Drag a card header · Pan empty canvas','Drag header to move · Corner to resize · Pan empty canvas')
(root/'index.html').write_text(html,encoding='utf-8')
with (root/'style.css').open('a',encoding='utf-8')as f:f.write('''
/* Mirror container pass: profile imagery is large, nodes have persistent geometry. */
#profile .media-button{height:300px}#profile .media-button img,#profile .media-button svg{height:300px;object-fit:contain}#profile .handle b{font-size:30px;color:#fff}
.resize-node{position:absolute;right:3px;bottom:3px;padding:3px 7px;color:#ff7187;background:#13151c;border:0;cursor:nwse-resize;z-index:5;font-size:18px;touch-action:none}.resize-node:hover{background:#44202c}
.data-container{margin:8px 18px;padding:10px 14px;text-align:left;background:#281921;border-color:#693044;color:#ffd6de}.node.profile .actions{margin-top:8px}
#mirrorDialog,#containerDialog{width:min(960px,92vw);max-height:86vh;overflow:auto}#mirrorSearch{width:100%;padding:14px;background:#191b22;color:#fff;border:1px solid #444854;border-radius:10px}#mirrorItems{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px}.mirror-item{display:flex;gap:16px;padding:18px;border:1px solid #383d49;border-radius:12px;background:#12151b}.mirror-item img{width:120px;height:140px;object-fit:contain;background:#090a0d;border-radius:8px}.mirror-item p{font-size:12px;color:#b4b9c6}.container-section{padding:14px 20px;margin:12px 0;border:1px solid #363a46;border-radius:10px;background:#14171d}.container-section h3{margin:0 0 8px}.container-section p{margin:0;color:#b8bdc9;line-height:1.6}aside{overflow-y:auto}.privacy{position:static;margin-top:28px}@media(max-width:750px){#mirrorItems{grid-template-columns:1fr}}
''')
d=json.loads((root/'layout.json').read_text(encoding='utf-8'))
d['revision']='mirror-containers-2'
for n in d['nodes']:
    if n['id']=='profile':n['h']=520
    if n['id'].startswith('lingzi'):n['y']+=200
(root/'layout.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
manifest={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['app-pass2.js','index.html','style.css','layout.json']}
(audit/'updated-hashes.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(str(audit))
