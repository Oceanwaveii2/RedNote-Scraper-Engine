"""Reversible text-node rendering in the independent browser, never archive mutation."""
import json

BOOT = r"""
if (!['www.rednote.com','www.xiaohongshu.com'].includes(location.hostname)) return {error:'Unsupported page'};
let s=window.__rednoteGptPage;
if(!s || s.url!==location.href){
 if(s)for(const r of s.rows.values())if(r.node.isConnected&&r.node.nodeValue===r.rendered)r.node.nodeValue=r.original;
 s={url:location.href,rows:new Map(),nodes:new WeakMap(),seq:0,view:'Original'};
 Object.defineProperty(window,'__rednoteGptPage',{value:s,writable:true,configurable:true});
}
const render=(r)=>{
 if(!r.node.isConnected)return false;
 if(r.node.nodeValue!==r.rendered){s.rows.delete(r.id);s.nodes.delete(r.node);return false;}
 const translated=r.translation;
 const value=!translated||s.view==='Original'?r.original:s.view==='Both'?r.original+' / '+translated:r.original.replace(r.original.trim(),()=>translated);
 if(r.node.nodeValue!==value)r.node.nodeValue=value;r.rendered=value;return true;
};
for(const r of s.rows.values())if(!r.node.isConnected){s.rows.delete(r.id);s.nodes.delete(r.node);}
"""

def snapshot(target, pending_only=False):
    pattern=r'/[\u3400-\u9fff]/' if target=='en' else r'/[A-Za-z]/'
    return '(() => {'+BOOT+r"""
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 const grouped=new Map();let total=0,n,count=0,visited=0;
 while((n=walker.nextNode())&&count<240&&visited++<12000){
  const p=n.parentElement;if(!p||p.closest('script,style,noscript,textarea,input,select,[contenteditable],svg'))continue;
  let r=s.nodes.get(n);if(r&&n.nodeValue!==r.rendered){s.rows.delete(r.id);s.nodes.delete(n);r=null;}
  if(PENDING && r&&r.translation&&r.target===TARGET)continue;
  const original=r?r.original:n.nodeValue,t=original.trim();
  if(!PATTERN.test(t)||!t||t.length>1800)continue;
  const range=document.createRange();range.selectNodeContents(n);const b=range.getBoundingClientRect(),style=getComputedStyle(p);
  if(style.visibility==='hidden'||style.display==='none'||!b.width||!b.height||b.bottom<=0||b.top>=innerHeight||b.right<=0||b.left>=innerWidth)continue;
  if(!r){r={id:String(++s.seq),node:n,original,rendered:n.nodeValue,translation:null,target:null};s.nodes.set(n,r);s.rows.set(r.id,r);}
  let entry=grouped.get(t);
  if(!entry){if(total+t.length>6000||grouped.size>=48)break;entry={text:t,ids:[]};grouped.set(t,entry);total+=t.length;}
  entry.ids.push(r.id);count++;
 }return {url:s.url,entries:Array.from(grouped.values()),nodes:count};
})()""".replace('PATTERN',pattern).replace('PENDING','true' if pending_only else 'false').replace('TARGET',json.dumps(target))

def apply(url,entries,view,target='en'):
    payload=json.dumps({'url':url,'entries':entries,'view':view,'target':target},ensure_ascii=True)
    return '(() => {'+BOOT+'const p='+payload+r""";
 if(p.url!==s.url)return {applied:0,stale:true};
 if(!['Original','Translated','Both'].includes(p.view))return {error:'Invalid view'};
 s.view=p.view;let applied=0;
 for(const item of p.entries){if(typeof item.translation!=='string')continue;
  for(const id of item.ids){const r=s.rows.get(id);if(r&&r.original.trim()===item.text){r.translation=item.translation;r.target=p.target;if(render(r))applied++;}}
 }return {applied,view:s.view};
})()"""

def switch(view):
    return '(() => {'+BOOT+'const view='+json.dumps(view)+r""";
 if(!['Original','Translated','Both'].includes(view))return {error:'Invalid view'};
 s.view=view;let applied=0;for(const r of s.rows.values())if(render(r))applied++;
 return {applied,view};
})()"""

STATUS = r"""(() => {
 const s=window.__rednoteGptPage;
 if(!s||s.url!==location.href)return {view:'Original',tracked:0,translated:0,rendered:0,restored:0};
 const rows=Array.from(s.rows.values()).filter(r=>r.node.isConnected);
 return {view:s.view,tracked:rows.length,translated:rows.filter(r=>r.translation).length,
 rendered:rows.filter(r=>r.translation&&r.node.nodeValue===r.rendered&&r.rendered!==r.original).length,
 restored:rows.filter(r=>r.node.nodeValue===r.original).length};
})()"""
