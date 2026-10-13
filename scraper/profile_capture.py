"""Read normally rendered RedNote profile cards without deriving IDs from images."""

PROFILE_SNAPSHOT_SCRIPT = r"""(() => {
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const guard=()=>{
   if(/\/(?:login|auth|captcha|verify)(?:\/|$)/i.test(location.pathname))return {kind:'login',message:'The site requires login or verification.'};
   if(Array.from(document.querySelectorAll('[class*="captcha"],[id*="captcha"],.verify-container,.security-check')).some(visible))return {kind:'challenge',message:'The site requires a human verification step.'};
   const error=Array.from(document.querySelectorAll('.error-page,.error-container,.access-denied')).find(visible);
   if(error&&/429|频繁|稍后|too many|rate.limit/i.test(error.innerText||''))return {kind:'rate_limit',message:'The site requested a pause.'};
   if(Array.from(document.querySelectorAll('.login-container,.login-modal,.login-box')).some(visible))return {kind:'login',message:'The site requires login.'};
   return null;
 };
 const blocked=guard();if(blocked)return {guard:blocked,error:blocked.message};
 if(!/^(?:www\.)?(?:rednote|xiaohongshu)\.com$/.test(location.hostname))return {error:'Open a RedNote profile.'};
 const match=/^\/user\/profile\/([a-f0-9]{24})\/?$/i.exec(location.pathname);
 if(!match)return {error:'Open the profile page, rather than a note detail page.'};
 const profileId=match[1].toLowerCase();
 const read=(selector,base=document)=>Array.from(base.querySelectorAll(selector)).filter(visible).map(e=>e.innerText?.trim()).find(Boolean)||'';
 const header=Array.from(document.querySelectorAll('.user-info,.user-page-info,.profile-info,.user-profile')).find(visible)||document;
 let basic={},interactions=[];
 try {
   const u=window.__INITIAL_STATE__?.user||{};
   const d=u.userPageData||u.userInfo||{};
   const candidate=d.basicInfo||d.user||d;
   const id=candidate.userId||candidate.user_id||candidate.id;
   if(!id||String(id).toLowerCase()===profileId){basic=candidate;interactions=d.interactions||[];}
 }catch(_){}
 const name=read('.user-nickname,.user-name,.nickname,.profile-name,h1',header)||basic.nickname||basic.nickName||'';
 const displayText=read('.user-redId,.user-redid,.red-id,.user-id',header);
 const displayId=(/[:：]\s*(\S+)/.exec(displayText)||[])[1]||basic.redId||basic.red_id||displayText;
 const locationText=read('.user-IP,.user-ip,.ip-location,.user-location',header);
 const ipLocation=locationText.replace(/^(?:IP location|IP属地|IP地址)\s*[:：]?\s*/i,'')||basic.ipLocation||basic.ip_location||'';
 const bio=read('.user-desc,.user-description,.user-bio,.desc,.bio',header)||basic.desc||basic.description||'';
 const avatarNode=Array.from(header.querySelectorAll('.user-avatar img,.avatar img,img.user-image,.user-image img')).find(e=>e.naturalWidth>20);
 const avatar=avatarNode?.currentSrc||avatarNode?.src||basic.images||basic.imageb||basic.avatar||'';
 const tags=Array.from(header.querySelectorAll('.user-tag,.tag-item,.user-tags .tag,.tags .tag')).filter(visible).map(e=>e.innerText?.trim()).filter(Boolean);
 const stats={followers:'',following:'',likes:''};
 for(const e of Array.from(header.querySelectorAll('.user-interactions .count,.interactions .count,.user-interactions .interaction,.interaction-item,.stats-item'))){
   const block=e.closest('.interaction-item,.interaction,.stats-item')||e.parentElement||e;
   const label=(block.innerText||'').trim();const number=e.classList.contains('count')?e.innerText.trim():read('.count,.number,.value',e);
   if(/followers|粉丝/i.test(label))stats.followers=number;
   else if(/following|follow\b|关注/i.test(label))stats.following=number;
   else if(/likes|saves|获赞|收藏/i.test(label))stats.likes=number;
 }
 for(const i of interactions){const type=String(i.type||i.name||'');const count=i.count??i.value??'';if(/fans|followers|粉丝/i.test(type)&&!stats.followers)stats.followers=count;else if(/follows|following|关注/i.test(type)&&!stats.following)stats.following=count;else if(/interaction|likes|获赞/i.test(type)&&!stats.likes)stats.likes=count;}
 const seen=new Map();
 for(const a of document.querySelectorAll('.note-item a[href],.note-card a[href],section[data-note-id] a[href],a[href*="/explore/"],a[href*="/discovery/item/"],a[href*="/user/profile/"]')){
   const href=a.getAttribute('href')||'';
   const m=/\/(?:explore|discovery\/item)\/([a-f0-9]{24})(?:[/?#]|$)/i.exec(href)||/\/user\/profile\/[a-f0-9]{24}\/([a-f0-9]{24})(?:[/?#]|$)/i.exec(href);
   if(!m)continue;
   const card=a.closest('.note-item,.note-card,section[data-note-id]')||a;
   const id=m[1].toLowerCase();const image=card.querySelector('img');
   let url='';try{const u=new URL(href,location.href);url=u.href;}catch(_){continue;}
   const previous=seen.get(id)||{};
   const title=read('.title,.note-title,.footer .title',card)||a.getAttribute('aria-label')||previous.title||'';
   const cover=image?.currentSrc||image?.src||previous.cover||'';
   const cardLikes=read('.like-wrapper .count,.like .count,.likes-count,.like-count,.footer .count',card);
   const type=card.querySelector('.play-icon,.video-icon,[class*="play-icon"],svg use[href*="video"],svg use[xlink\\:href*="video"]')?'video':previous.mediaType||'photo';
   // Prefer the cover's signed navigation URL over an unsigned footer link.
   if(!previous.url||url.includes('xsec_token=')||!previous.url.includes('xsec_token='))previous.url=url;
   seen.set(id,{...previous,postId:id,title,cover,mediaType:type,pinned:/置顶|Pinned/i.test(card.innerText||''),...(cardLikes?{likes:cardLikes,likesCount:cardLikes}:{})});
 }
 const noteCount=read('.notes-tab .count,.note-count,[data-note-count]');
 const countText=/^\d+$/.test(noteCount.replace(/[,\s]/g,''))?Number(noteCount.replace(/[,\s]/g,'')):null;
 const endNodes=Array.from(document.querySelectorAll('.feeds-container .end,.feeds-container .end-container,.feeds-container .end-text,.feeds-container .load-more,.note-list .end,.note-list .no-more,.feeds-container [class*="no-more"],.feeds-container [class*="end-tip"],.feeds-container [class*="empty"]'));
 const endMarker=endNodes.filter(visible).map(e=>(e.innerText||'').trim()).find(t=>/没有更多|到底了|全部加载|暂无笔记/i.test(t)||/^(?:-?\s*THE END\s*-?|You've reached the end|No more(?: notes| posts)?|All (?:notes|posts)(?: loaded)?|No (?:notes|posts))\.?$/i.test(t))||'';
 const load=Array.from(document.querySelectorAll('.feeds-container .loading,.feeds-container [class*="loading"]')).some(visible);
 const main=document.scrollingElement||document.documentElement;
 let feedBoundary=null;
 try {
   const unref=v=>v?._value??v;
   const u=window.__INITIAL_STATE__?.user||{};
   const tab=unref(u.activeTab),queries=unref(u.noteQueries),notes=unref(u.notes),fetching=unref(u.isFetchingNotes);
   const q=queries?.[0],items=notes?.[0];
   if(tab?.index===0&&String(q?.userId||'').toLowerCase()===profileId&&Array.isArray(items)&&items.length&&unref(u.firstFetchNote)===false&&fetching?.[0]===false&&typeof q.hasMore==='boolean'){
     const ids=items.map(n=>String(n?.id||n?.noteId||'').toLowerCase());
     if(ids.every(id=>/^[a-f0-9]{24}$/.test(id)))feedBoundary={hasMore:q.hasMore,sourceNoteCount:new Set(ids).size,fetching:false,profileId};
   }
 }catch(_){}
 return {profileId,name,profileName:name,displayId,bio,avatar,...stats,tags,location:ipLocation,profileUrl:location.origin+location.pathname,sourceUrl:location.origin+location.pathname,posts:Array.from(seen.values()),reportedPostCount:countText,endMarker,feedBoundary,loading:load,atBottom:main.scrollTop+main.clientHeight>=main.scrollHeight-8};
})()"""

PROFILE_SCROLL_SCRIPT = r"""(() => {
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const card=Array.from(document.querySelectorAll('.note-item,.note-card')).find(visible);
 let scroll=document.scrollingElement||document.documentElement;
 for(let e=card?.parentElement;e&&e!==document.body;e=e.parentElement){if(e.scrollHeight>e.clientHeight+20&&/(auto|scroll)/.test(getComputedStyle(e).overflowY)){scroll=e;break;}}
 const before=scroll.scrollTop;scroll.scrollTop+=Math.max(480,scroll.clientHeight*.95);
 const end=Array.from(document.querySelectorAll('.feeds-container .end,.feeds-container .end-container,.feeds-container .end-text,.feeds-container .load-more,.note-list .end,.note-list .no-more,.feeds-container [class*="no-more"],.feeds-container [class*="end-tip"],.feeds-container [class*="empty"]')).filter(visible).map(e=>(e.innerText||'').trim()).find(t=>/没有更多|到底了|全部加载|暂无笔记/i.test(t)||/^(?:-?\s*THE END\s*-?|You've reached the end|No more(?: notes| posts)?|All (?:notes|posts)(?: loaded)?|No (?:notes|posts))\.?$/i.test(t))||'';
 return {scrolled:scroll.scrollTop!==before,atBottom:scroll.scrollTop+scroll.clientHeight>=scroll.scrollHeight-8,endMarker:end};
})()"""

# Public adapter names, shared by the desktop bridge and the MCP control layer.
PROFILE_ADVANCE_SCRIPT = PROFILE_SCROLL_SCRIPT
from browser_capture import NOTE_SCRIPT as POST_SNAPSHOT_SCRIPT
from browser_capture import SCROLL_SCRIPT as COMMENTS_ADVANCE_SCRIPT
