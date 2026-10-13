from pathlib import Path
import json
import sys
base=Path(__file__).resolve().parents[1]
html=(base/'templates/master_shell.html').read_text(encoding='utf-8')
prelude=r'''<script>
window.fixtureNote={post_id:'fixture_note',postId:'fixture_note',profile_id:'fixture',profileId:'fixture',title:'Local fixture',caption:'Captured caption',snapshot:'one',media_type:'video',comments_count:24,coverage:{state:'partial'},media:[],comments:[{id:'root',parentId:null,author:'Reader',text:'Root',attachments:[]},...Array.from({length:23},(_,i)=>({id:'reply_'+i,parentId:'root',author:'Reply reader',text:'Actual fixture reply '+i,attachments:[]}))]};
window.fixtureProfile={profile_id:'fixture',profileId:'fixture',name:'Local fixture profile',bio:'Fixture only',displayId:'test',avatar_url:'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="green"/></svg>',followers:'48.4K',likes:'271.3K',manifest:{postsCaptured:1,postsDiscovered:1,commentsCaptured:24},coverage:{state:'partial'}};
window.fixtureActive='job';
window.pywebview={api:{profile_clone_status:async()=>({activeJobId:window.fixtureActive,jobs:[{jobId:'job',profileId:'fixture',status:'paused',posts:[],progress:{comments:24}}]})}};
window.fetch=async url=>{let payload;if(url==='/api/master/profiles')payload={profiles:[{...window.fixtureProfile,localPostCount:1}],selection:{profileId:'fixture',excludedPostIds:[]}};else if(url==='/api/master/profiles/fixture')payload={profile:window.fixtureProfile,posts:[window.fixtureNote]};else if(url==='/api/master/posts/fixture_note')payload={post:window.fixtureNote,comments:window.fixtureNote.comments.map(c=>({...c,comment_id:c.id,parent_comment_id:c.parentId,username:c.author,text_chinese:c.text}))};else payload={};return {ok:true,json:async()=>structuredClone(payload)};};
</script>'''
html=html.replace('  <script>',prelude+'  <script>',1)
code=r'''async page => {
  await page.route('**/*',route=>route.abort());
  await page.setViewportSize({width:1280,height:960});
  await page.goto('about:blank');
  await page.setContent(HTML);
  await page.waitForFunction(()=>document.querySelector('#note-feed .note'));
  const recorded=await page.evaluate(async()=>{
    const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;
    const ctx=canvas.getContext('2d');const stream=canvas.captureStream(20);
    const chunks=[];const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});
    recorder.ondataavailable=e=>chunks.push(e.data);const stop=new Promise(resolve=>recorder.onstop=resolve);
    recorder.start();let i=0;const timer=setInterval(()=>{ctx.fillStyle=i++%2?'#72dbbc':'#081011';ctx.fillRect(0,0,64,64);},45);
    await new Promise(resolve=>setTimeout(resolve,1200));recorder.stop();await stop;clearInterval(timer);stream.getTracks().forEach(t=>t.stop());
    const url=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));window.fixtureNote.media=[{kind:'video',file:'fixture.webm',url,available:true}];
    await refreshLibrary();await openNote('fixture_note');
    const video=document.querySelector('#media-space video');video.muted=true;video.loop=true;await video.play();
    window.originalVideo=video;window.originalCard=document.querySelector('#note-feed .note');window.originalAvatar=document.querySelector('#portrait-holder img');
    document.querySelector('#comment-rail details').open=true;document.querySelector('.discussion-scroll').scrollTop=230;
    await refreshLibrary();await refreshJobs();
    const guards={resume:document.querySelector('#queue-resume').disabled,retry:document.querySelector('#queue-retry').disabled,noteRetry:document.querySelector('#note-retry').disabled,pollScheduled:Boolean(state.timer)};
    if(!Object.values(guards).every(Boolean))throw Error('Paused but active worker guard failed '+JSON.stringify(guards));
    clearTimeout(state.timer);window.fixtureActive=null;await refreshJobs();
    if(document.querySelector('#queue-resume').disabled||document.querySelector('#queue-retry').disabled||document.querySelector('#note-retry').disabled)throw Error('Resume/retry remained disabled after worker unwound');
    const timeBefore=video.currentTime;const scrollBefore=document.querySelector('.discussion-scroll').scrollTop;
    window.fixtureNote.snapshot='two';window.fixtureNote.comments.push({id:'new_reply',parentId:'root',author:'Reader',text:'Newly captured reply',attachments:[]});window.fixtureNote.comments_count++;
    await refreshLibrary();
    return {timeBefore,scrollBefore,guards};
  });
  await page.waitForTimeout(280);
  const result=await page.evaluate(recorded=>{
    const video=document.querySelector('#media-space video');
    const result={sameVideo:video===window.originalVideo,sameCard:document.querySelector('#note-feed .note')===window.originalCard,sameAvatar:document.querySelector('#portrait-holder img')===window.originalAvatar,timeBefore:recorded.timeBefore,timeAfter:video.currentTime,playing:!video.paused,replyExpanded:document.querySelector('#comment-rail details').open,commentCount:document.querySelectorAll('#comment-rail .comment').length,scrollBefore:recorded.scrollBefore,scrollAfter:document.querySelector('.discussion-scroll').scrollTop,newCommentVisible:document.querySelector('#comment-rail').textContent.includes('Newly captured reply'),guards:recorded.guards};
    if(!result.sameVideo||!result.sameCard||!result.sameAvatar||!result.playing||!result.replyExpanded||!result.newCommentVisible||result.commentCount!==25||Math.abs(result.scrollAfter-result.scrollBefore)>1||result.timeAfter<=result.timeBefore)throw Error('Polling state preservation failed '+JSON.stringify(result));
    video.pause();return result;
  },recorded);
  return result;
}'''.replace('HTML',json.dumps(html,ensure_ascii=False))
Path(sys.argv[1]).write_text(code,encoding='utf-8')
