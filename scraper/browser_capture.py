"""Capture loaded RedNote notes and safely save one selected CDN media rendition."""
import hashlib
import os
import tempfile
import threading
import requests
from urllib.error import HTTPError
from urllib.parse import urljoin
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

COMMENT_ACTION_HELPERS = r"""
 const harvestCommentRows='[data-comment-id],.comment-inner-container,.comment-item,.comment-item-sub,.reply-item,.parent-comment';
 const harvestControlClasses=/(?:^|\s)(?:show-more|more-comment|more-reply|reply-expand|expand-reply|expand-comment|reply-more|unfold|more-replies|show-more-replies|view-more|load-more-replies)(?:\s|$)/i;
 const harvestControls=()=>{
   const found=Array.from(root.querySelectorAll('.show-more,.more-comment,.reply-expand,.reply-more,.unfold,.more-replies,.show-more-replies,.view-more,button,[role="button"],a,span')).filter(visible).filter(e=>{
     const value=(e.innerText||'').replace(/\s+/g,' ').trim();const cls=typeof e.className==='string'?e.className:'';
     if(!value||value.length>70||/show\s*less|collapse|收起|折叠/i.test(value))return false;
     if(e.disabled||e.closest?.('[aria-disabled="true"],[aria-busy="true"],.disabled,.loading,[class*="loading"]'))return false;
     const known=harvestControlClasses.test(cls);
     const role=e.getAttribute?.('role')||'';const href=e.getAttribute?.('href')||'';
     const commentArea=!!e.closest?.('.comments-container,.reply-container,.sub-comments,.comment-list');
     const clickableText=(e.tagName==='SPAN'||e.tagName==='DIV')&&commentArea&&getComputedStyle(e).cursor==='pointer';
     const actual=known||e.tagName==='BUTTON'||role==='button'||clickableText||(e.tagName==='A'&&(!href||href==='#'||/^javascript:/i.test(href)));
     if(!actual)return false;
     if(!known&&e.closest?.('.content,.comment-content,.note-text,.comment-text'))return false;
     const english=/^(?:(?:view|show|load|see)\s+(?:(?:all|remaining|the rest of)\s+)?(?:\d+\s+)?(?:more\s+)?(?:comments?|repl(?:y|ies))|more\s+(?:comments?|repl(?:y|ies)))\b/i.test(value);
     const chinese=/^(?:(?:点击)?(?:查看|展开|显示|加载)(?:全部|更多|剩余|其余)?\s*(?:\d+\s*)?(?:条)?(?:评论|回复)|(?:还有|剩余)\s*\d+\s*条?\s*(?:评论|回复))/i.test(value);
     const short=commentArea&&/^(?:(?:view|show|load|see)\s+more|查看更多|展开更多|加载更多|更多回复|更多评论)$/i.test(value);
     return english||chinese||short||(known&&/^(?:展开|更多)$/.test(value));
   });
   return found.filter(e=>!found.some(other=>other!==e&&other.contains?.(e)));
 };
 const harvestControlSignature=(e,index)=>{
   const branch=e.closest?.(harvestCommentRows);
   const branchId=branch?.getAttribute?.('data-comment-id')||branch?.getAttribute?.('data-id')||branch?.id||'';
   return [branchId,e.getAttribute?.('data-id')||e.id||'',typeof e.className==='string'?e.className:'',(e.innerText||'').trim(),index].join('|');
 };
 const harvestCommentsLoading=()=>Array.from(root.querySelectorAll('.comments-container .loading,.comments-container [class*="loading"],.reply-container [class*="loading"],.comments-container [aria-busy="true"],.reply-container [aria-busy="true"]')).some(visible);
"""

NOTE_SCRIPT = r"""(() => {
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const guarded=()=>{
   if(/\/(?:login|auth|captcha|verify)(?:\/|$)/i.test(location.pathname))return {kind:'login',message:'The site requires login or verification.'};
   if(/^\/404\/?$/.test(location.pathname))return {kind:'source_unavailable',message:'The site returned an unavailable page. Retained content is preserved.'};
   if(Array.from(document.querySelectorAll('[class*="captcha"],[id*="captcha"],.verify-container,.security-check')).some(visible))return {kind:'challenge',message:'The site requires a human verification step.'};
   if(Array.from(document.querySelectorAll('.login-container,.login-modal,.login-box')).some(visible))return {kind:'login',message:'The site requires login.'};
   const error=Array.from(document.querySelectorAll('.error-page,.error-container,.access-denied')).find(visible);
   if(error&&/429|频繁|稍后|too many|rate.limit/i.test(error.innerText||''))return {kind:'rate_limit',message:'The site requested a pause.'};
   return null;
 };
 const guard=guarded();if(guard)return {guard,error:guard.message};
 if(!/^(?:www\.)?(?:rednote|xiaohongshu)\.com$/.test(location.hostname))return {error:'Open a RedNote note.'};
 const root=Array.from(document.querySelectorAll('.note-container,.note-detail-mask,.note-detail')).find(visible);
 if(!root)return {error:'Wait for the note detail to load.'};
 /*COMMENT_ACTION_HELPERS*/
 const text=(selector,base=root)=>Array.from(base.querySelectorAll(selector)).filter(visible).map(e=>e.innerText?.trim()).find(Boolean)||'';
 const idFromUrl=u=>(/\/(?:explore|discovery\/item)\/([a-f0-9]{24})/i.exec(u)||/\/user\/profile\/[a-f0-9]{24}\/([a-f0-9]{24})/i.exec(u)||[])[1]||'';
 let postId=idFromUrl(location.pathname);
 if(!postId)postId=idFromUrl(document.querySelector('link[rel="canonical"]')?.href||'');
 if(!postId){const id=root.getAttribute('data-note-id')||root.getAttribute('data-id')||'';if(/^[a-f0-9]{24}$/i.test(id))postId=id;}
 if(!postId)return {error:'This detail view did not expose its note ID.'};
 postId=postId.toLowerCase();
 let n={};try{const d=window.__INITIAL_STATE__?.note?.noteDetailMap?.[postId];n=d?.note||d?.noteCard||d||{};}catch(_){}
 const authorRoot=Array.from(root.querySelectorAll('.author-container,.author-wrapper,.note-author')).find(visible)||root;
 const authorLink=Array.from(authorRoot.querySelectorAll('a[href*="/user/profile/"]')).find(visible);
 let profileId=(/\/user\/profile\/([a-f0-9]{24})(?:[/?#]|$)/i.exec(authorLink?.getAttribute('href')||'')||[])[1]||n.user?.userId||n.user?.user_id||'';
 const authorName=text('.username,.user-name,.author-name,.name',authorRoot)||n.user?.nickname||n.user?.nickName||'';
 const authorAvatar=authorRoot.querySelector('img')?.src||n.user?.image||n.user?.avatar||'';
 const noteType=String(n.type||'').toLowerCase();
 const domVideo=Array.from(root.querySelectorAll('video')).find(visible);
 const isVideo=noteType==='video'||!!domVideo||!!n.video?.media;
 const https=u=>typeof u==='string'?u.replace(/^http:\/\//,'https://'):'';
 const images=[];
 if(!isVideo&&Array.isArray(n.imageList)){
   for(let index=0;index<n.imageList.length;index++){
     const img=n.imageList[index];
     // Each imageList row is one carousel item. infoList rows are renditions.
     const variants=Array.isArray(img.infoList)?img.infoList.filter(i=>i?.url):[];
     variants.sort((a,b)=>(Number(b.width||0)*Number(b.height||0))-(Number(a.width||0)*Number(a.height||0)));
     const url=https(img.urlOriginal||img.urlDefault||img.url||variants[0]?.url||'');
     if(url.startsWith('https://'))images.push({url,sourceIndex:index+1,width:img.width||null,height:img.height||null});
   }
 }
 if(!isVideo&&!images.length){
   const seen=new Set();
   for(const e of root.querySelectorAll('.swiper-slide:not(.swiper-slide-duplicate) img,.note-slider img,.media-container img')){
     const url=https(e.currentSrc||e.src);if(e.naturalWidth>100&&url.startsWith('https://')&&!seen.has(url)){seen.add(url);images.push({url,sourceIndex:images.length+1,width:e.naturalWidth,height:e.naturalHeight});}
   }
 }
 const videoVariants=[];
 if(isVideo){
   const streams=n.video?.media?.stream||{};
   for(const [codec,variants] of Object.entries(streams))for(const s of Array.isArray(variants)?variants:[]){
     const url=https(s.masterUrl||s.url||'');if(!url.startsWith('https://'))continue;
     videoVariants.push({url,codec:s.videoCodec||s.codec||codec,width:Number(s.width||0),height:Number(s.height||0),bitrate:Number(s.videoBitrate||s.bitrate||s.avgBitrate||0),duration:s.duration?Number(s.duration)/1000:null,mp4:!(/\.m3u8(?:[?#]|$)|\.mpd(?:[?#]|$)/i.test(url))});
   }
   const videoUrl=https(domVideo?.currentSrc||domVideo?.src||'');
   if(videoUrl.startsWith('https://')&&!videoVariants.some(v=>v.url===videoUrl))videoVariants.push({url:videoUrl,width:domVideo.videoWidth||0,height:domVideo.videoHeight||0,bitrate:0,duration:Number.isFinite(domVideo.duration)?domVideo.duration:null,mp4:!(/\.m3u8(?:[?#]|$)|\.mpd(?:[?#]|$)/i.test(videoUrl))});
 }
 // Prefer a browser-compatible AVC/H.264 MP4, then its best exposed resolution.
 videoVariants.sort((a,b)=>Number(b.mp4)-Number(a.mp4)||Number(/h[._-]?264|avc/i.test(b.codec||''))-Number(/h[._-]?264|avc/i.test(a.codec||''))||(b.width*b.height)-(a.width*a.height)||b.bitrate-a.bitrate);
 const selectedVideo=videoVariants.find(v=>v.mp4);
 const videos=selectedVideo?[{...selectedVideo,sourceIndex:1}]:[];
 const media=images.map(i=>({kind:'photo',...i}));if(selectedVideo)media.push({kind:'video',...selectedVideo,sourceIndex:1});
 const cover=https(n.imageList?.[0]?.urlDefault||n.imageList?.[0]?.url||domVideo?.poster||root.querySelector('.swiper-slide img,.note-slider img')?.src||'');
 const commentSelector='[data-comment-id],.comment-inner-container,.comment-item,.comment-item-sub,.reply-item,.parent-comment';
 const raw=Array.from(root.querySelectorAll(commentSelector)).filter(visible);
 const candidateSet=new Set(raw);
 const ownText=e=>Array.from(e.querySelectorAll('.content,.comment-content,.note-text,.text')).find(t=>{let owner=t.closest(commentSelector);return owner===e;});
 const candidates=raw.filter(e=>!!ownText(e));
 const nativeId=e=>{
   for(let node=e;node&&node!==root;node=node.parentElement){
     const value=node.getAttribute('data-comment-id')||node.getAttribute('data-id')||node.id||'';
     const m=/^(?:comment[-_])?([a-z0-9_-]{12,})$/i.exec(value);if(m)return m[1];
     if(node!==e&&candidateSet.has(node)&&ownText(node))break;
   }return '';
 };
 const shortHash=s=>{let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619);}return (h>>>0).toString(16);};
 const comments=[];const nodeIds=new Map();
 for(const e of candidates){
   const content=ownText(e);if(!content)continue;
   const contentText=(content.innerText||content.textContent||'').trim();
   const mediaNodes=Array.from(content.querySelectorAll('img,svg'));
   const attachments=mediaNodes.map((asset,index)=>{
     let source=asset.currentSrc||asset.src||'';const requestUrl=source;
     if(source){try{const u=new URL(source,location.href);source=u.origin+u.pathname;}catch(_){source='';}}
     const cls=typeof asset.className==='string'?asset.className:'';
     const alt=asset.getAttribute('alt')||asset.getAttribute('aria-label')||'';
     const label=cls+' '+(typeof asset.parentElement?.className==='string'?asset.parentElement.className:'')+' '+alt;
     const kind=/gift|礼物/i.test(label)?'gift':/\.gif(?:[?#]|$)|\bgif\b|动图/i.test(requestUrl+' '+label)?'gif':/sticker|贴纸|表情包/i.test(label)?'sticker':/emoji|emoticon|表情/i.test(label)?'emoji':asset.tagName==='SVG'?'svg':'image';
     return {kind,sourceIndex:index+1,sourceUrl:source,requestUrl,alt,width:asset.naturalWidth||null,height:asset.naturalHeight||null,archiveStatus:'remote_only'};
   }).filter(asset=>asset.sourceUrl||asset.kind==='svg');
   if(!contentText&&!attachments.length)continue;
   const richSegments=[];
   const mediaIndex=new Map(mediaNodes.map((node,index)=>[node,index+1]));
   const walk=node=>{
     if(richSegments.length>=100)return;
     if(node.nodeType===Node.TEXT_NODE){const value=node.textContent||'';if(value)richSegments.push({type:'text',text:value.slice(0,8000)});return;}
     if(node.nodeType!==Node.ELEMENT_NODE)return;
     if(node!==content&&!visible(node))return;
     if(node.matches('script,style,button,[role="button"],.show-more,.reply-more'))return;
     if(node.matches('img,svg')){const sourceIndex=mediaIndex.get(node);if(sourceIndex)richSegments.push({type:'asset',sourceIndex});return;}
     if(node.tagName==='BR'){richSegments.push({type:'text',text:'\n'});return;}
     for(const child of node.childNodes)walk(child);
   };
   walk(content);
   const authorNode=Array.from(e.querySelectorAll('.author-wrapper,.author,.user-info')).find(a=>a.closest(commentSelector)===e)||e;
   const authorLink=Array.from(authorNode.querySelectorAll('a[href*="/user/profile/"]')).find(a=>a.closest(commentSelector)===e);
   const authorId=(/\/user\/profile\/([a-f0-9]{24})/i.exec(authorLink?.getAttribute('href')||'')||[])[1]||'';
   const author=text('.name,.user-name,.username,.author-name',authorNode)||authorLink?.innerText?.trim()||'';
   const time=text('.date,.time,.comment-time',e);
   let parentNode=e.parentElement,parentId='';
   while(parentNode&&parentNode!==root){if(nodeIds.has(parentNode)){parentId=nodeIds.get(parentNode);break;}parentNode=parentNode.parentElement;}
   const replyGroup=e.closest('.reply-container,.sub-comments,[class*="reply-container"],[class*="sub-comment"]');
   if(!parentId&&replyGroup){const parent=replyGroup.parentElement?.querySelector(commentSelector);if(parent&&parent!==e)parentId=nodeIds.get(parent)||nativeId(parent);}
   const reply=!!parentId||!!replyGroup;
   const native=nativeId(e);const id=native||'dom_'+shortHash(parentId+'\u0000'+authorId+'\u0000'+author+'\u0000'+contentText+'\u0000'+time+(attachments.length?'\u0000'+attachments.map(asset=>asset.sourceUrl+'|'+asset.sourceIndex).join(','):''));
   nodeIds.set(e,id);
   const avatarOwner=e.closest('.comment-item,.comment-item-sub,.reply-item,[data-comment-id]')||e;
   const avatar=Array.from(avatarOwner.querySelectorAll('a[href*="/user/profile/"] img,.avatar img,.avatar-item img,.avatar-wrapper img,.user-avatar img')).find(image=>(image.closest('.comment-item,.comment-item-sub,.reply-item,[data-comment-id]')||e)===avatarOwner);
   const authorAvatar=avatar?.currentSrc||avatar?.src||'';
   if(authorAvatar.startsWith('https://')){
     try{const u=new URL(authorAvatar);attachments.push({kind:'image',role:'author_avatar',sourceIndex:0,sourceUrl:u.origin+u.pathname,requestUrl:authorAvatar,alt:author,width:avatar.naturalWidth||null,height:avatar.naturalHeight||null,archiveStatus:'remote_only'});}catch(_){}
   }
   comments.push({id,idSource:native?'source':'derived',parentId,reply,text:contentText,richSegments,attachments,author,authorId,authorAvatar,likes:text('.like-count,.like .count,.like-wrapper .count',e),time,location:text('.location,.ip-location',e)});
 }
 const reportedComments=n.interactInfo?.commentCount??n.interact_info?.comment_count??text('.comments-container .total,.comments-container .count,.comments-title,.comment-count');
 const reportedText=String(reportedComments??'').replace(/[,\s]/g,'');
 const reportedMatch=/^(?:共)?(\d+)(?:条评论|条|comments?)?$/i.exec(reportedText);
 const reportedCommentCount=reportedMatch?Number(reportedMatch[1]):null;
 const endMarker=Array.from(root.querySelectorAll('.comments-container .end,.comments-container .no-more,.comments-container .end-container,.comments-container [class*="no-more"],.comments-container [class*="end-tip"],.comments-container .empty')).filter(visible).map(e=>(e.innerText||'').trim()).find(t=>/没有更多|到底了|全部加载|no more|no comments|暂无评论|还没有评论/i.test(t)||/^[-–—\s]*the\s+end[-–—\s]*$/i.test(t))||'';
 const commentsLoading=harvestCommentsLoading();
 const replyButtons=harvestControls().length;
 return {sourceMediaInventory:{sourceDeclared:!!(n.type==='video'||Array.isArray(n.imageList)),images:isVideo?0:(Array.isArray(n.imageList)?n.imageList.length:0),videos:isVideo?1:0},postId,profileId,authorName,authorAvatar,sourceUrl:location.origin+'/discovery/item/'+postId,title:text('#detail-title,.note-content .title,.title')||n.title||'',text:text('#detail-desc,.note-content .desc,.desc')||n.desc||'',createdAt:n.time||n.createTime||null,likes:n.interactInfo?.likedCount??'',saves:n.interactInfo?.collectedCount??'',mediaType:isVideo?'video':'photo',cover,comments,images:images.map(i=>i.url),videos,media,reportedComments,reportedCommentCount,commentCountExact:reportedCommentCount!==null,endMarker,commentsLoading,unexpandedReplies:replyButtons,videoUnavailable:isVideo&&!selectedVideo,coverage:'loaded-comments-only'};
})()""".replace('/*COMMENT_ACTION_HELPERS*/',COMMENT_ACTION_HELPERS)

SCROLL_SCRIPT = r"""(() => {
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const root=Array.from(document.querySelectorAll('.note-container,.note-detail-mask,.note-detail')).find(visible);
 if(!root)return {error:'Open a note detail before loading comments.'};
 /*COMMENT_ACTION_HELPERS*/
 let clicked=0;
 if(harvestCommentsLoading())return {message:'Waiting for the current comment branch to finish loading.',clicked:0,scrolled:false,loading:true};
 const buttons=harvestControls();
 const ids=Array.from(root.querySelectorAll(harvestCommentRows)).map(e=>e.getAttribute?.('data-comment-id')||e.getAttribute?.('data-id')||e.id||'').join('|');
 const fingerprint=ids+'|'+root.querySelectorAll(harvestCommentRows).length;
 const state=globalThis.__HARVEST_COMMENT_ACTIONS__||(globalThis.__HARVEST_COMMENT_ACTIONS__={});
 let blockedBranches=0;
 for(let index=0;index<buttons.length;index++){
   const expand=buttons[index],signature=harvestControlSignature(expand,index);
   const prior=state[signature];const attempts=prior?.fingerprint===fingerprint?prior.attempts:0;
   if(attempts>=2){blockedBranches++;continue;}
   state[signature]={fingerprint,attempts:attempts+1};
   try{expand.click();return {message:'Expanded one comment branch; waiting for the site to load it.',clicked:1,scrolled:false,action:'expand',controlSignature:signature,blockedBranches};}catch(_){}
 }
 const first=Array.from(root.querySelectorAll('.comment-item,.comment-inner-container,.parent-comment,.comments-container')).find(visible);
 let scroll=null;
 for(let e=first?.parentElement;e&&e!==document.body;e=e.parentElement){if(e.scrollHeight>e.clientHeight+10&&/(auto|scroll)/.test(getComputedStyle(e).overflowY)){scroll=e;break;}}
 if(!scroll)scroll=Array.from(root.querySelectorAll('.note-scroller,.interaction-container,.comments-container')).find(e=>e.scrollHeight>e.clientHeight+10)||(document.scrollingElement||document.documentElement);
 const before=scroll.scrollTop;scroll.scrollTop+=Math.max(350,scroll.clientHeight*.9);
 const count=root.querySelectorAll('.comment-item,.comment-inner-container,.parent-comment').length;
 const endMarker=Array.from(root.querySelectorAll('.comments-container .end,.comments-container .no-more,.comments-container .end-container,.comments-container [class*="no-more"],.comments-container [class*="end-tip"],.comments-container .empty')).filter(visible).map(e=>(e.innerText||'').trim()).find(t=>/没有更多|到底了|全部加载|no more|no comments|暂无评论|还没有评论/i.test(t)||/^[-–—\s]*the\s+end[-–—\s]*$/i.test(t))||'';
 return {message:`Loaded ${count} comment rows; expanded ${clicked} reply groups.`,count,clicked,scrolled:scroll.scrollTop!==before,atBottom:scroll.scrollTop+scroll.clientHeight>=scroll.scrollHeight-8,endMarker,blockedBranches,scrollContainer:typeof scroll.className==='string'?scroll.className.slice(0,160):'',scrollTop:scroll.scrollTop,scrollHeight:scroll.scrollHeight,viewportHeight:scroll.clientHeight};
})()""".replace('/*COMMENT_ACTION_HELPERS*/',COMMENT_ACTION_HELPERS)


def merge_comments(prior, incoming):
    """Accumulate native identities and preserve parent, author and reply metadata."""
    result=[]
    known={}
    for row in [*(prior or []), *(incoming or [])]:
        if not isinstance(row, dict) or not isinstance(row.get('text',''), str):
            continue
        attachments=row.get('attachments') if isinstance(row.get('attachments'),list) else []
        if not row.get('text','').strip() and not attachments:
            continue
        row=dict(row)
        row.setdefault('text','')
        if not row.get('id'):
            signature='\0'.join(str(row.get(k, '')) for k in ('parentId','authorId','author','text','time'))
            if attachments:signature+='\0'+str([(asset.get('sourceUrl',''),asset.get('sourceIndex',0)) for asset in attachments if isinstance(asset,dict)])
            row['id']='derived_'+hashlib.sha256(signature.encode('utf-8')).hexdigest()[:32]
            row['idSource']='derived'
        row.setdefault('parentId', '')
        row['reply']=bool(row.get('reply') or row['parentId'])
        key=str(row['id'])
        if key in known:
            existing=result[known[key]]
            retained=merge_comment_attachments(existing.get('attachments',[]),attachments)
            existing.update({k:v for k,v in row.items() if k!='attachments' and v not in ('', None) and (k!='richSegments' or v)})
            if retained:existing['attachments']=retained
        else:
            known[key]=len(result)
            result.append(row)
    return result


def merge_comment_attachments(prior, incoming):
    """Fresh DOM metadata must not discard an already archived attachment."""
    result=[];known={}
    for asset in [*(prior or []),*(incoming or [])]:
        if not isinstance(asset,dict):continue
        asset=dict(asset)
        try:
            parsed=urlsplit(str(asset.get('sourceUrl') or asset.get('requestUrl') or ''))
            canonical=parsed.scheme+'://'+parsed.netloc+parsed.path if parsed.scheme and parsed.netloc else ''
        except ValueError:canonical=''
        key=(asset.get('sourceIndex',0),canonical)
        if key in known:
            existing=result[known[key]]
            same_variant=existing.get('kind')==asset.get('kind') and all(not existing.get(field) or not asset.get(field) or existing.get(field)==asset.get(field) for field in ('width','height'))
            if existing.get('file') and not asset.get('file') and same_variant:
                local={k:v for k,v in existing.items() if k in {'file','path','bytes','sha256','mime','status','archiveStatus','available','url'}}
                existing.update(asset);existing.update(local)
            else:
                if existing.get('file') and not asset.get('file') and not same_variant:
                    for field in ('file','path','bytes','sha256','mime','status','available','url'):existing.pop(field,None)
                existing.update(asset)
        else:
            known[key]=len(result);result.append(asset)
    return result


def allowed_media_url(url):
    try:
        parsed=urlsplit(url)
        host=(parsed.hostname or '').lower()
        return parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443) and any(host==suffix or host.endswith('.'+suffix) for suffix in ('xhscdn.com','xiaohongshu.com','rednote.com','rednotecdn.com'))
    except (TypeError, ValueError):
        return False


class MediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_media_url(newurl):
            raise ValueError('Media redirect outside the supported CDN.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_media_sessions=threading.local()

def _media_response(url):
    session=getattr(_media_sessions,'session',None)
    if session is None:
        session=requests.Session();session.trust_env=False
        _media_sessions.session=session
    for _ in range(6):
        response=session.get(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.rednote.com/','Accept-Encoding':'identity'},stream=True,timeout=(15,30),allow_redirects=False)
        if response.status_code in {301,302,303,307,308}:
            target=urljoin(url,response.headers.get('Location',''));response.close()
            if not allowed_media_url(target):raise ValueError('Media redirect outside the supported CDN.')
            url=target;continue
        if response.status_code>=400:
            code=response.status_code;headers=dict(response.headers);response.close()
            raise HTTPError(url,code,'Media request rejected',headers,None)
        return response
    raise ValueError('Too many media redirects.')

def save_media(url, destination, kind, notify=lambda n: None):
    """Download declared CDN files, with bounded size and atomic partial cleanup."""
    if kind not in {'photo','video'} or not allowed_media_url(url):
        raise ValueError('Unsupported media URL. A browser-only video cannot be saved by the public CDN worker.')
    cap=25_000_000 if kind=='photo' else 500_000_000
    destination=Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial=None
    try:
        with _media_response(url) as response:
            mime=response.headers.get('Content-Type','').split(';',1)[0].strip().lower()
            extensions={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/avif':'.avif','image/gif':'.gif','video/mp4':'.mp4','video/webm':'.webm'}
            if mime not in extensions or not mime.startswith('image/' if kind=='photo' else 'video/'):
                raise ValueError('The response is not the requested media type.')
            size=response.headers.get('Content-Length')
            if size and int(size)>cap:
                raise ValueError('Media exceeds the configured per-file size budget.')
            digest=hashlib.sha256();count=0
            with tempfile.NamedTemporaryFile(mode='wb', prefix=destination.name+'-', suffix='.partial', dir=destination.parent, delete=False) as output:
                partial=Path(output.name)
                while True:
                    chunk=response.raw.read(65536)
                    if not chunk:break
                    count+=len(chunk)
                    if count>cap:raise ValueError('Media exceeds the configured per-file size budget.')
                    output.write(chunk);digest.update(chunk);notify(count)
                output.flush();os.fsync(output.fileno())
            if not count:raise ValueError('Empty media response.')
            if size and count!=int(size):raise ValueError('Incomplete media response; file was not committed.')
            sha=digest.hexdigest();target=Path(str(destination)+extensions[mime])
            if target.exists():
                with target.open('rb') as existing:
                    prior=hashlib.file_digest(existing, 'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(existing.read()).hexdigest()
                if prior==sha:
                    partial.unlink();partial=None
                    return {'file':target.name,'bytes':count,'sha256':sha,'mime':mime,'reusedExisting':True}
                target=Path(str(destination)+'-'+sha[:12]+extensions[mime])
            partial.replace(target);partial=None
            return {'file':target.name,'bytes':count,'sha256':sha,'mime':mime}
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
