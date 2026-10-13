"""Synthetic source tests: no live sites, account sessions or fixture identities."""
import copy
import hashlib
import json
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib
from pathlib import Path
from urllib.error import HTTPError

from browser_capture import merge_comments
from profile_clone import CaptureAttention, CapturePaused, ProfileCloneService, parse_count

PROFILE_ID='a'*24


class Store:
    def __init__(self):self.profiles={};self.posts={};self.rebuilt=[];self.writes=0
    def store_profile(self,data,coverage=None):
        self.profiles[data['profileId']]=copy.deepcopy(data);self.writes+=1;self.rebuild(data['profileId'])
    def store_post(self,pid,data,media,coverage):
        self.posts[(pid,data['postId'])]=copy.deepcopy({**data,'media':media,'coverage':coverage})
        self.writes+=1;self.rebuild(pid)
    def get_post(self,post):return copy.deepcopy(next((v for (pid,p),v in self.posts.items() if p==post),{}))
    def rebuild(self,pid):self.rebuilt.append(pid)


class Browser:
    def __init__(self,count=31,enumeration_complete=True,comments_complete=True):
        self.count=count;self.profile_page=0;self.current=None;self.comment_page=0
        self.enumeration_complete=enumeration_complete;self.comments_complete=comments_complete
        self.visited=[];self.guard=None
    def navigate(self,url):
        self.visited.append(url)
        self.current=url.split('?',1)[0].rstrip('/').rsplit('/',1)[-1]
        self.comment_page=0
        if self.current==PROFILE_ID:self.profile_page=0
    def profile_snapshot(self):
        if self.guard:return {'guard':self.guard}
        if self.current not in (None,PROFILE_ID):return {'error':'Not on a profile.'}
        end=min(self.count,(self.profile_page+1)*8)
        posts=[{'postId':f'{i:024x}','url':f'https://www.rednote.com/user/profile/{PROFILE_ID}/{i:024x}?xsec_token=transient-secret','title':f'Note {i}','cover':'https://img.xhscdn.com/cover?secret=cover'} for i in range(1,end+1)]
        return {'profileId':PROFILE_ID,'name':'Synthetic Author','displayId':'synthetic-id','bio':'Bio','avatar':'https://img.xhscdn.com/avatar?xsec_token=transient-avatar','followers':'48.4K','following':'422','likes':'271.3K','posts':posts,'reportedPostCount':self.count if self.enumeration_complete else None}
    def advance_profile(self):self.profile_page+=1;return {'scrolled':True}
    def post_snapshot(self):
        if self.guard:return {'guard':self.guard}
        if self.current in (None,PROFILE_ID):return {'error':'Not on a note.'}
        comments=[{'id':'native-parent-'+self.current,'author':'Parent','authorId':'b'*24,'text':'Parent text','parentId':'','time':'2026','likes':'5','reply':False}]
        if self.comment_page:comments.append({'id':'native-reply-'+self.current,'author':'Reply','authorId':'c'*24,'text':'Reply 🧡','parentId':'native-parent-'+self.current,'time':'2026','likes':'1','reply':True})
        return {'postId':self.current,'profileId':PROFILE_ID,'authorName':'Synthetic Author','title':'Title','text':'Caption','mediaType':'video','cover':'https://img.xhscdn.com/cover','comments':comments,'reportedCommentCount':2 if self.comments_complete else None,'media':[{'kind':'video','sourceIndex':1,'url':'https://video.xhscdn.com/high.mp4'}]}
    def advance_comments(self):self.comment_page+=1;return {'clicked':1,'scrolled':True}


def download(url,destination,kind,notify):
    destination.parent.mkdir(parents=True,exist_ok=True)
    path=Path(str(destination)+('.mp4' if kind=='video' else '.jpg'))
    payload=('synthetic '+kind).encode();path.write_bytes(payload);notify(len(payload))
    return {'file':path.name,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'mime':'video/mp4' if kind=='video' else 'image/jpeg'}


def png_bytes():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\x00\x00\xff'))+chunk(b'IEND',b'')


def attachment_download(url,destination,kind,notify):
    if destination.parent.name!='comment_assets':return download(url,destination,kind,notify)
    destination.parent.mkdir(parents=True,exist_ok=True)
    path=Path(str(destination)+'.png');payload=png_bytes();path.write_bytes(payload);notify(len(payload))
    return {'file':path.name,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'mime':'image/png'}


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store();self.browser=Browser()
        self.service=ProfileCloneService(self.temp.name,self.store,self.browser,download)
        self.service._ensure_worker=lambda:None
        # Private synthetic seams avoid network pacing without weakening production options.
        self.service._wait=lambda job,seconds:self.service._check(job)
        self.service._pacing=lambda job,category:self.service._check(job)
        self.addCleanup(self.service.shutdown)
    def run_clone(self,options=None):
        opts={'pageWaitSeconds':.01,'stallRounds':3,'profileScrollBudget':20,'commentScrollBudget':10}
        opts.update(options or {})
        response=self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}?xsec_token=secret-profile',opts,confirm_permission=True)
        job=self.service.jobs[response['jobId']];job['status']='running';self.service._run(job)
        return job
    def test_feed_exhaustion_requires_all_source_ids_observed_at_bottom(self):
        self.browser.count=2;self.browser.enumeration_complete=False
        original=self.browser.profile_snapshot
        source_count=[2]
        def snapshot():
            data=original()
            data.update(atBottom=True,loading=False,feedBoundary={'hasMore':False,'fetching':False,'profileId':PROFILE_ID,'sourceNoteCount':source_count[0]})
            return data
        self.browser.profile_snapshot=snapshot
        self.browser.advance_profile=lambda:{'scrolled':False}
        job=self.run_clone()
        self.assertEqual(job['enumeration']['reason'],'source_feed_exhausted')
        self.assertEqual(job['enumeration']['status'],'complete')
        source_count[0]=3
        job=self.run_clone()
        self.assertEqual(job['enumeration']['status'],'partial')
        self.assertEqual(job['enumeration']['reason'],'enumeration_stalled')
    def test_automatic_batch_clears_selection_and_stages_assets(self):
        self.browser.count=2
        response=self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}',confirm_permission=True)
        job=self.service.jobs[response['jobId']]
        job.update(status='paused',selectedRetryOnly=True,selectedPostIds=['f'*24],priorityPostIds=['f'*24])
        self.service.automatic_batch(job['jobId'])
        self.assertNotIn('selectedRetryOnly',job)
        calls=[]
        original=self.service._capture_post
        def capture(j,row):
            calls.append((row['postId'],j.get('deferCommentAttachments')))
            original(j,row)
        self.service._capture_post=capture
        job['status']='running';self.service._run(job)
        self.assertEqual(calls[:2],[(f'{1:024x}',True),(f'{2:024x}',True)])
        self.assertTrue(all(row.get('contentComplete') for row in job['posts']))
        self.assertTrue(job['assetBackfill'])
        self.service.pause(job['jobId'])
        self.assertFalse(job.get('resumeOnStartup'))

    def test_profile_over_25_and_identity_media_and_comment_hierarchy(self):
        job=self.run_clone()
        self.assertEqual(job['status'],'complete')
        self.assertEqual(len(job['posts']),31)
        self.assertEqual(self.store.profiles[PROFILE_ID]['name'],'Synthetic Author')
        self.assertEqual(self.store.profiles[PROFILE_ID]['followers'],'48.4K')
        self.assertTrue(self.store.profiles[PROFILE_ID]['avatarMedia']['file'].endswith('.jpg'))
        first=self.store.posts[(PROFILE_ID,f'{1:024x}')]
        self.assertEqual(first['comments'][1]['parentId'],first['comments'][0]['id'])
        self.assertEqual(first['comments'][1]['author'],'Reply')
        self.assertEqual(sum(m['kind']=='video' for m in first['media']),1)
        self.assertTrue(first['media'][0]['file'].endswith('.mp4'))
        saved=self.service.queue_path.read_text()
        self.assertNotIn('xsec_token',saved);self.assertNotIn('transient-secret',saved)
        self.assertIn('xsec_token=transient-secret',self.browser.visited[0])
    def test_unknown_totals_and_stalls_are_partial(self):
        self.browser.count=2;self.browser.enumeration_complete=False;self.browser.comments_complete=False
        profile_advance=self.browser.advance_profile;comment_advance=self.browser.advance_comments
        def profile_bottom():profile_advance();return {'scrolled':False}
        def comments_bottom():comment_advance();return {'clicked':0,'scrolled':False}
        self.browser.advance_profile=profile_bottom;self.browser.advance_comments=comments_bottom
        job=self.run_clone()
        self.assertEqual(job['status'],'partial')
        self.assertEqual(job['enumeration']['reason'],'enumeration_stalled')
        self.assertEqual(job['posts'][0]['coverage']['comments']['reason'],'comments_stalled')
    def test_limit_is_honest_partial(self):
        job=self.run_clone({'maxPosts':2})
        self.assertEqual(len(job['posts']),2)
        self.assertEqual(job['enumeration']['reason'],'max_posts')
        self.assertEqual(job['status'],'partial')
    def test_media_failure_retained_and_retry_reuses_comments(self):
        self.browser.count=1
        original=self.service.downloader
        def bad(url,destination,kind,notify):
            if kind=='video':raise ValueError('Video unavailable')
            return original(url,destination,kind,notify)
        self.service.downloader=bad;job=self.run_clone()
        self.assertEqual(job['status'],'partial');self.assertEqual(len(self.store.posts[(PROFILE_ID,f'{1:024x}')]['comments']),2)
        self.service.downloader=original
        self.service.retry(job['jobId'],f'{1:024x}');job['status']='running';self.service._run(job)
        self.assertEqual(job['status'],'complete');self.assertEqual(job['progress']['comments'],2)
    def test_pause_and_restart_resume_retains_queue(self):
        self.browser.count=1
        def pausing(url,destination,kind,notify):
            if kind=='video':self.service.pause(next(iter(self.service.jobs)));notify(0)
            return download(url,destination,kind,notify)
        self.service.downloader=pausing
        with self.assertRaises(CapturePaused):self.run_clone()
        job=next(iter(self.service.jobs.values()));self.assertEqual(job['status'],'paused')
        # Media now precedes comment expansion, so an interruption in the first
        # video retains the initial loaded comment, then resumes the full thread.
        self.assertEqual(len(self.store.posts[(PROFILE_ID,f'{1:024x}')]['comments']),1)
        self.service.shutdown()
        resumed=ProfileCloneService(self.temp.name,self.store,self.browser,download)
        self.addCleanup(resumed.shutdown);resumed._ensure_worker=lambda:None
        resumed._wait=lambda job,seconds:resumed._check(job)
        resumed._pacing=lambda job,category:resumed._check(job)
        self.assertEqual(resumed.status(job['jobId'])['status'],'paused')
        resumed.resume(job['jobId']);restored=resumed.jobs[job['jobId']];restored['status']='running';resumed._run(restored)
        self.assertEqual(restored['status'],'complete')
        self.assertNotIn('xsec_token',resumed.queue_path.read_text())
    def test_access_response_stops_without_retry(self):
        self.browser.count=1
        def limited(url,destination,kind,notify):raise RuntimeError('HTTP Error 429: Too many requests')
        self.service.downloader=limited
        with self.assertRaises(CaptureAttention):self.run_clone()
    def test_comment_native_ids_and_reply_metadata_are_not_collapsed(self):
        rows=merge_comments([], [{'id':'1','text':'Same','author':'A','parentId':''},{'id':'2','text':'Same','author':'A','parentId':'1','reply':True,'likes':8,'time':'now'}])
        rows=merge_comments(rows,[{'id':'2','text':'Same','author':'A','parentId':'1','likes':9}])
        self.assertEqual(len(rows),2);self.assertEqual(rows[1]['likes'],9);self.assertTrue(rows[1]['reply'])
    def test_video_selects_highest_quality_and_carousel_renditions_are_unique(self):
        candidates=self.service._media_candidates({'videos':[{'url':'low','width':640,'height':360},{'url':'high','width':1920,'height':1080}]})
        self.assertEqual(candidates[0]['url'],'high')
        candidates=self.service._media_candidates({'media':[{'kind':'photo','sourceIndex':1,'url':'one'},{'kind':'photo','sourceIndex':1,'url':'one-alt'},{'kind':'photo','sourceIndex':2,'url':'two'}]})
        self.assertEqual(len(candidates),2)
    def test_approximate_counts_do_not_prove_complete(self):
        self.assertIsNone(parse_count('1.2K'));self.assertIsNone(parse_count('2万'));self.assertEqual(parse_count('共 15 条评论'),15)
    def test_production_pacing_cannot_be_lowered(self):
        for key,value in [('pageDelaySeconds',2),('postPacingSeconds',89),('mediaPacingSeconds',44)]:
            with self.assertRaises(ValueError):self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}',{key:value},confirm_permission=True)
    def test_rate_cooldown_persists_and_blocks_other_profiles_and_manual_resume(self):
        self.browser.count=1;job=self.run_clone()
        self.service._mark_attention(job,CaptureAttention('rate_limit','Rate limited',1800))
        self.assertGreaterEqual(self.service.cooldown_remaining(),1799)
        with self.assertRaises(ValueError):self.service.resume(job['jobId'])
        with self.assertRaises(ValueError):self.service.retry(job['jobId'])
        with self.assertRaises(ValueError):self.service.start(f'https://www.rednote.com/user/profile/{"b"*24}',confirm_permission=True)
        restored=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(restored.shutdown)
        self.assertEqual(restored.status(job['jobId'])['status'],'needs_attention')
        self.assertGreaterEqual(restored.status()['safety']['siteCooldownUntil']-time.time(),1799)
        with self.assertRaises(ValueError):restored.resume(job['jobId'])
    def test_pacing_reservations_survive_restart(self):
        self.browser.count=1;job=self.run_clone()
        ProfileCloneService._pacing(self.service,job,'media')
        ProfileCloneService._pacing(self.service,job,'post')
        saved=json.loads(self.service.queue_path.read_text())['safety']
        self.assertGreaterEqual(saved['nextMediaAt']-time.time(),44)
        self.assertGreaterEqual(saved['nextPostAt']-time.time(),89)
        restored=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(restored.shutdown)
        self.assertEqual(restored.safety['nextMediaAt'],saved['nextMediaAt'])
        self.assertEqual(restored.safety['nextPostAt'],saved['nextPostAt'])
    def test_options_reject_nonfinite_fractional_and_boolean_counts(self):
        for options in [{'pageDelaySeconds':float('nan')},{'mediaPacingSeconds':float('inf')},{'maxPosts':1.5},{'maxComments':True}]:
            with self.assertRaises(ValueError):self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}',options,confirm_permission=True)
        with self.assertRaises(ValueError):self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}')
    def test_actual_archive_store_recovers_comments_and_saved_files_on_retry(self):
        from profile_archive import ProfileArchiveStore
        self.store=ProfileArchiveStore(self.temp.name);self.service.store=self.store;self.browser.count=1
        job=self.run_clone();post_id=f'{1:024x}'
        prior=self.service._prior_post(PROFILE_ID,post_id)
        self.assertEqual(len(prior['comments']),2)
        self.assertEqual(prior['media'][0]['status'],'saved')
        calls=[]
        self.service.downloader=lambda *args:calls.append(args) or download(*args)
        self.service.retry(job['jobId'],post_id);job['status']='running';self.service._run(job)
        self.assertEqual(job['status'],'complete')
        self.assertEqual(len(self.store.get_post(post_id)['comments']),2)
        self.assertEqual(calls,[])
    def test_avatar_is_archived_before_first_profile_scroll_and_preserved_each_round(self):
        seen=[];advance=self.browser.advance_profile
        def inspect():
            seen.append(copy.deepcopy(self.store.profiles[PROFILE_ID].get('avatarMedia')))
            return advance()
        self.browser.advance_profile=inspect
        job=self.run_clone()
        self.assertTrue(seen)
        self.assertTrue(all(asset and asset['status']=='saved' for asset in seen))
        self.assertEqual(job['profile']['avatarMedia'],seen[0])
    def test_failed_avatar_retries_only_after_explicit_resume(self):
        self.browser.count=1;calls=[]
        def broken_avatar(url,destination,kind,notify):
            if destination.name=='avatar':calls.append(url);raise ValueError('Avatar unavailable')
            return download(url,destination,kind,notify)
        self.service.downloader=broken_avatar
        job=self.run_clone();self.assertEqual(job['status'],'partial');self.assertEqual(len(calls),1)
        self.service.downloader=download;self.service.resume(job['jobId']);job['status']='running';self.service._run(job)
        self.assertEqual(job['status'],'complete');self.assertEqual(job['profile']['avatarMedia']['status'],'saved')
    def test_resumed_discovery_catches_up_old_pages_before_stagnation(self):
        self.browser.count=31;job=self.run_clone()
        self.browser.count=40;self.browser.current=PROFILE_ID;self.browser.profile_page=0
        self.service.transient_posts[job['jobId']]={};job['enumeration']['status']='partial'
        job['status']='running';self.service._enumerate(job)
        self.assertEqual(len(job['posts']),40)
        self.assertEqual(job['enumeration']['status'],'complete')
        self.assertEqual(job['enumeration']['observedThisRun'],40)
        self.assertGreaterEqual(self.browser.profile_page,4)
        self.assertTrue(all(p['postId'] in self.service.transient_posts[job['jobId']] for p in job['posts']))
    def test_media_is_saved_and_rebuilt_before_comment_traversal(self):
        self.browser.count=1;seen=[];advance=self.browser.advance_comments
        def inspect():
            note=self.store.posts[(PROFILE_ID,f'{1:024x}')]
            seen.append(copy.deepcopy(note))
            self.assertEqual(note['coverage']['media']['status'],'complete')
            self.assertTrue(any(m['kind']=='video' and m['status']=='saved' for m in note['media']))
            self.assertIn(PROFILE_ID,self.store.rebuilt)
            return advance()
        self.browser.advance_comments=inspect
        job=self.run_clone();self.assertEqual(job['status'],'complete');self.assertTrue(seen)
    def test_pause_during_comments_preserves_complete_playable_media(self):
        self.browser.count=1
        self.browser.advance_comments=lambda:self.service.pause(next(iter(self.service.jobs)))
        with self.assertRaises(CapturePaused):self.run_clone()
        note=self.store.posts[(PROFILE_ID,f'{1:024x}')]
        self.assertEqual(note['coverage']['media']['status'],'complete')
        self.assertEqual(note['coverage']['comments']['reason'],'paused')
        self.assertTrue(all(m['status']=='saved' for m in note['media']))
    def test_resumed_comments_catches_up_preserved_ids_before_stagnation(self):
        self.browser.count=1;expected=[31];snapshot=self.browser.post_snapshot
        def paged():
            data=snapshot();count=min(expected[0],(self.browser.comment_page+1)*5)
            data['comments']=[{'id':f'comment-{i}','author':'Synthetic Reader','text':f'Comment {i}','parentId':''} for i in range(count)]
            data['reportedCommentCount']=expected[0]
            return data
        self.browser.post_snapshot=paged
        job=self.run_clone();self.assertEqual(job['progress']['comments'],31)
        expected[0]=40;self.service.retry(job['jobId'],f'{1:024x}');job['status']='running';self.service._run(job)
        self.assertEqual(job['status'],'complete');self.assertEqual(job['progress']['comments'],40)
        self.assertEqual(job['posts'][0]['coverage']['comments']['observedThisRun'],40)
    def test_compatible_h264_video_is_preferred_and_image_source_order_preserved(self):
        candidates=self.service._media_candidates({'videos':[{'url':'hevc-4k','codec':'h265','width':3840,'height':2160},{'url':'avc-1080','codec':'H.264','width':1920,'height':1080}]})
        self.assertEqual(candidates[0]['url'],'avc-1080')
        candidates=self.service._media_candidates({'media':[{'kind':'photo','url':'one','sourceIndex':1},{'kind':'photo','url':'two','sourceIndex':2},{'kind':'video','url':'hevc','codec':'hevc','width':3840,'height':2160},{'kind':'video','url':'avc','codec':'avc1','width':1920,'height':1080}]})
        self.assertEqual([m['url'] for m in candidates],['one','two','avc'])
    def test_control_state_is_lightweight_and_busy_until_paused_worker_unwinds(self):
        self.browser.count=1;job=self.run_clone()
        state=self.service.control_state()
        self.assertEqual(set(state),{'activeJobId','jobId','busy','status','phase','message','progress','cooldownRemainingSeconds'})
        self.assertEqual(state['status'],'complete');self.assertFalse(state['busy'])
        self.assertNotIn('posts',state);self.assertNotIn('sourceUrl',state)
        job['status']='paused';self.service.active_job=job['jobId']
        self.assertTrue(self.service.control_state()['busy'])
        self.service.active_job=None
        self.assertFalse(self.service.control_state()['busy'])
        job['status']='queued'
        self.assertEqual(self.service.control_state()['jobId'],job['jobId'])
    def test_503_retry_after_stops_media_and_preserves_requested_wait(self):
        self.browser.count=1;calls=[]
        def unavailable(url,destination,kind,notify):
            calls.append(url)
            raise HTTPError(url,503,'Service Unavailable',{'Retry-After':'1800'},None)
        self.service.downloader=unavailable
        with self.assertRaises(CaptureAttention) as caught:self.run_clone()
        self.assertEqual(len(calls),1)
        self.assertEqual(caught.exception.kind,'access_or_rate_limit')
        self.assertEqual(caught.exception.retry_after,1800)
    def test_comments_allow_more_than_stall_budget_scrolls_before_new_ids(self):
        self.browser.count=1;snapshot=self.browser.post_snapshot
        def delayed():
            data=snapshot()
            # The loaded first screen has no new IDs for12 genuine scrolls.
            data['comments']=data['comments'][:1] if self.browser.comment_page<12 else data['comments']
            return data
        self.browser.post_snapshot=delayed
        job=self.run_clone({'commentScrollBudget':20,'stallRounds':3})
        self.assertEqual(job['status'],'complete')
        self.assertGreaterEqual(self.browser.comment_page,12)
        self.assertEqual(job['posts'][0]['coverage']['comments']['captured'],2)
    def test_profile_allows_more_than_stall_budget_scrolls_before_new_cards(self):
        self.browser.count=16;snapshot=self.browser.profile_snapshot
        def delayed():
            data=snapshot()
            data['posts']=data['posts'][:8] if self.browser.profile_page<12 else data['posts']
            return data
        self.browser.profile_snapshot=delayed
        job=self.run_clone({'profileScrollBudget':20,'stallRounds':3})
        self.assertEqual(job['enumeration']['status'],'complete')
        self.assertGreaterEqual(job['enumeration']['rounds'],12)
        self.assertEqual(len(job['posts']),16)
    def test_moving_without_reaching_exact_comment_total_remains_budgeted_partial(self):
        self.browser.count=1;snapshot=self.browser.post_snapshot
        def incomplete():
            data=snapshot();data['comments']=data['comments'][:1];return data
        self.browser.post_snapshot=incomplete
        job=self.run_clone({'commentScrollBudget':10,'stallRounds':3})
        coverage=job['posts'][0]['coverage']['comments']
        self.assertEqual(job['status'],'partial')
        self.assertEqual(coverage['reason'],'scroll_budget')
        self.assertEqual((coverage['captured'],coverage['expected']),(1,2))
    def test_blank_native_comments_with_attachments_are_preserved_without_invented_text(self):
        attachments=[{'kind':'emoji','sourceUrl':'https://picasso-static.xiaohongshu.com/reaction.png','sourceIndex':1,'archiveStatus':'remote_only'}]
        rows=merge_comments([],[{'id':'native-emoji','text':'','parentId':'parent','author':'Reader','attachments':attachments},{'id':'empty-container','text':''}])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],'native-emoji')
        self.assertEqual(rows[0]['text'],'');self.assertEqual(rows[0]['attachments'],attachments)
        self.assertTrue(rows[0]['reply'])
    def test_targeted_retry_prioritizes_note_without_reordering_profile_cards(self):
        self.browser.count=3;self.browser.comments_complete=False
        job=self.run_clone();original=[p['postId'] for p in job['posts']]
        self.browser.visited=[];self.browser.comments_complete=True
        self.service.retry(job['jobId'],original[2]);job['status']='running';self.service._run(job)
        visited=[url.split('?',1)[0].rsplit('/',1)[-1] for url in self.browser.visited]
        self.assertEqual(visited,[original[2]])
        self.assertEqual([p['postId'] for p in job['posts']],original)
        self.assertEqual(job.get('priorityPostIds'),[]);self.assertEqual(job['status'],'paused')
    def test_attachment_only_comment_is_counted_but_missing_asset_keeps_note_partial(self):
        self.browser.count=1;snapshot=self.browser.post_snapshot
        def attachment():
            data=snapshot();data['comments']=[{'id':'native-emoji','text':'','attachments':[{'kind':'emoji','sourceIndex':1,'sourceUrl':'https://picasso-static.xiaohongshu.com/reaction.png','archiveStatus':'remote_only'}]}];data['reportedCommentCount']=1;return data
        self.browser.post_snapshot=attachment
        def unavailable(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':raise ValueError('The observed attachment is unavailable.')
            return download(url,destination,kind,notify)
        self.service.downloader=unavailable
        job=self.run_clone();coverage=job['posts'][0]['coverage']['comments']
        self.assertEqual(coverage['captured'],1);self.assertEqual(coverage['status'],'complete')
        self.assertEqual(coverage['unarchivedAttachments'],1);self.assertEqual(job['status'],'partial')
        self.assertFalse(job['coverage']['commentsComplete'])

    def test_resume_waits_for_paused_worker_to_unwind_without_mutating_queue(self):
        self.browser.count=1;job=self.run_clone()
        job['status']='running';self.service.active_job=job['jobId']
        self.service.pause(job['jobId']);before=copy.deepcopy(job)
        with self.assertRaisesRegex(ValueError,'worker to stop'):self.service.resume(job['jobId'])
        self.assertEqual(job,before);self.assertEqual(job['status'],'paused')
        self.assertTrue(self.service.control_state()['busy'])
        self.service.active_job=None
        self.assertEqual(self.service.resume(job['jobId'])['status'],'queued')

    def test_store_operations_do_not_trigger_redundant_full_rebuilds(self):
        self.browser.count=1;self.run_clone()
        self.assertGreater(self.store.writes,0)
        self.assertEqual(len(self.store.rebuilt),self.store.writes)

    def test_priority_retry_finds_initial_signed_card_before_full_partial_feed_traversal(self):
        job=self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}',{'pageWaitSeconds':.01,'stallRounds':3,'profileScrollBudget':20},confirm_permission=True)
        job=self.service.jobs[job['jobId']]
        job['posts']=[{'postId':f'{i:024x}','sourceUrl':f'https://www.rednote.com/discovery/item/{i:024x}','status':'partial','media':[],'commentCount':0} for i in range(1,248)]
        original=[p['postId'] for p in job['posts']]
        job['profile']={'profileId':PROFILE_ID,'name':'Synthetic Author'}
        job['enumeration']={'status':'partial','reason':'enumeration_stalled','discovered':247}
        self.service.retry(job['jobId'],original[2]);job['status']='running'
        captured=[]
        def capture(current,row):
            captured.append(row['postId'])
            self.assertEqual(self.browser.profile_page,0)
            self.assertIn('xsec_token=transient-secret',self.service.transient_posts[job['jobId']][row['postId']]['url'])
            row['status']='complete';self.service.pause(job['jobId'])
        self.service._capture_post=capture
        with self.assertRaises(CapturePaused):self.service._run(job)
        self.assertEqual(captured,[original[2]])
        self.assertEqual([p['postId'] for p in job['posts']],original)
        self.assertEqual(job['enumeration']['status'],'partial')
        self.assertEqual(job['enumeration']['reason'],'enumeration_stalled')
        self.assertEqual(job['enumeration']['navigationRefresh']['reason'],'target_links_found')

    def test_priority_retry_missing_card_uses_bounded_discovery_without_guessed_note_navigation(self):
        self.browser.count=8
        response=self.service.start(f'https://www.rednote.com/user/profile/{PROFILE_ID}',{'pageWaitSeconds':.01,'stallRounds':3,'profileScrollBudget':3},confirm_permission=True)
        job=self.service.jobs[response['jobId']];target=f'{99:024x}'
        job['posts']=[{'postId':target,'sourceUrl':f'https://www.rednote.com/discovery/item/{target}','status':'partial','media':[],'commentCount':0}]
        self.service.retry(job['jobId'],target);job['status']='running';captures=[]
        self.service._capture_post=lambda current,row:captures.append(row['postId'])
        with self.assertRaises(CaptureAttention) as caught:self.service._run(job)
        self.assertEqual(caught.exception.kind,'note_navigation_missing');self.assertEqual(captures,[])
        self.assertTrue(all(url.split('?',1)[0].endswith('/'+PROFILE_ID) for url in self.browser.visited))
        self.assertLessEqual(job['enumeration']['navigationRefresh']['rounds'],3)

    def test_priority_retry_reuses_matching_open_note_without_profile_navigation(self):
        self.browser.count=1;job=self.run_clone();target=job['posts'][0]['postId']
        self.service.transient_posts[job['jobId']]={};self.browser.current=target;self.browser.visited=[]
        self.service.retry(job['jobId'],target);job['status']='running';self.service._run(job)
        self.assertEqual(self.browser.visited,[]);self.assertEqual(job['status'],'complete')

    def test_selected_retry_stops_before_any_other_note_or_full_enumeration(self):
        self.browser.count=3;job=self.run_clone();target=job['posts'][0]['postId']
        for post in job['posts']:post['status']='partial'
        job['enumeration']['status']='partial'
        self.browser.current=target;self.service.transient_posts[job['jobId']]={}
        self.service.retry(job['jobId'],target);job['status']='running'
        visited=[]
        def capture(current,post):visited.append(post['postId']);post['status']='complete'
        self.service._capture_post=capture
        self.service._enumerate=lambda *args,**kwargs:self.fail('Selected retry started full discovery')
        self.service._run(job)
        self.assertEqual(visited,[target]);self.assertEqual(job['status'],'paused')
        self.assertEqual(job['reason'],'selected_note_checkpoint')
        self.assertEqual([p['status'] for p in job['posts']],['complete','partial','partial'])
        self.service.resume(job['jobId']);self.assertTrue(job['selectedRetryOnly'])
        self.service.retry(job['jobId']);self.assertNotIn('selectedRetryOnly',job)

    def test_interrupted_active_capture_recovers_but_user_pause_and_stop_do_not(self):
        self.browser.count=1;job=self.run_clone();job['status']='running'
        self.service.shutdown()
        restored=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(restored.shutdown);restored._ensure_worker=lambda:None
        self.assertTrue(restored.jobs[job['jobId']]['resumeOnStartup'])
        self.assertEqual(restored.recover_interrupted(),[job['jobId']])
        self.assertEqual(restored.jobs[job['jobId']]['status'],'queued')
        self.assertEqual(restored.recover_interrupted(),[])
        restored.pause(job['jobId']);restored.shutdown()
        paused=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(paused.shutdown);paused._ensure_worker=lambda:None
        self.assertEqual(paused.recover_interrupted(),[])
        self.assertEqual(paused.jobs[job['jobId']]['reason'],'user_paused')
        paused.stop(job['jobId']);paused.shutdown()
        stopped=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(stopped.shutdown);stopped._ensure_worker=lambda:None
        self.assertEqual(stopped.recover_interrupted(),[])
        self.assertEqual(stopped.jobs[job['jobId']]['reason'],'user_stopped')

    def test_interrupted_capture_never_automatically_resumes_a_site_cooldown(self):
        self.browser.count=1;job=self.run_clone();job['status']='running'
        self.service.safety['siteCooldownUntil']=time.time()+1800;self.service.shutdown()
        restored=ProfileCloneService(self.temp.name,self.store,self.browser,download);self.addCleanup(restored.shutdown);restored._ensure_worker=lambda:None
        self.assertEqual(restored.recover_interrupted(),[])
        self.assertEqual(restored.jobs[job['jobId']]['status'],'needs_attention')
        self.assertFalse(restored.jobs[job['jobId']]['resumeOnStartup'])

    def test_recovery_reuses_only_files_with_matching_recorded_hashes(self):
        self.browser.count=1;job=self.run_clone();asset=job['posts'][0]['media'][0]
        self.assertTrue(self.service._saved_exists(asset))
        path=Path(self.temp.name,asset['file']);path.write_bytes(b'x'*asset['bytes'])
        self.assertFalse(self.service._saved_exists(asset))

    def use_attachment_comments(self,count=1,source='https://picasso-static.xiaohongshu.com/reaction.png?signature=transient-signature'):
        snapshot=self.browser.post_snapshot
        def attachment():
            data=snapshot()
            data['comments']=[{'id':f'emoji-{self.browser.current}-{i}','text':'','parentId':'parent' if i else '', 'author':'Reader','attachments':[{'kind':'emoji','sourceIndex':1,'sourceUrl':source.split('?',1)[0],'requestUrl':source,'width':1,'height':1,'archiveStatus':'remote_only'}]} for i in range(count)]
            data['reportedCommentCount']=count
            return data
        self.browser.post_snapshot=attachment

    def test_valid_attachment_only_rows_save_raster_once_across_replies_posts_and_restart(self):
        self.browser.count=2;self.use_attachment_comments(2);calls=[]
        def save(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append((url,kind))
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=save;job=self.run_clone()
        self.assertEqual(job['status'],'complete');self.assertEqual(len(calls),1)
        self.assertIn('signature=transient-signature',calls[0][0]);self.assertEqual(calls[0][1],'photo')
        first=self.store.posts[(PROFILE_ID,f'{1:024x}')];asset=first['comments'][0]['attachments'][0]
        self.assertEqual(first['comments'][0]['text'],'');self.assertEqual(asset['archiveStatus'],'saved')
        self.assertEqual(Path(self.temp.name,asset['file']).read_bytes(),png_bytes())
        self.assertEqual(asset['mime'],'image/png');self.assertEqual(asset['sha256'],hashlib.sha256(png_bytes()).hexdigest())
        self.assertEqual(job['progress']['savedMedia'],2) # Two videos, no emoji in note photo counts.
        self.assertEqual(job['posts'][0]['coverage']['comments']['savedAttachments'],2)
        self.assertEqual(first['comments'][1]['attachments'][0]['file'],asset['file'])
        self.assertNotIn('signature=',self.service.comment_asset_index.read_text())
        self.assertNotIn('requestUrl',json.dumps(first));self.assertNotIn('transient-signature',self.service.queue_path.read_text())
        restored=ProfileCloneService(self.temp.name,self.store,self.browser,save);self.addCleanup(restored.shutdown)
        restored._ensure_worker=lambda:None;restored._wait=lambda job,seconds:restored._check(job);restored._pacing=lambda job,category:restored._check(job)
        target=job['posts'][0]['postId'];restored.retry(job['jobId'],target);loaded=restored.jobs[job['jobId']];loaded['status']='running';restored._run(loaded)
        self.assertEqual(len(calls),1)

    def test_attachment_cache_rejects_tampered_or_outside_files(self):
        self.browser.count=1;self.use_attachment_comments();self.service.downloader=attachment_download
        job=self.run_clone();asset=self.store.posts[(PROFILE_ID,f'{1:024x}')]['comments'][0]['attachments'][0]
        self.assertTrue(self.service._verified_comment_asset(asset))
        outside={**asset,'file':'../outside.png'};self.assertFalse(self.service._verified_comment_asset(outside))
        path=Path(self.temp.name,asset['file']);path.write_bytes(b'x'*asset['bytes'])
        self.assertFalse(self.service._verified_comment_asset(asset))

    def test_invalid_attachment_url_is_rejected_without_downloader_call(self):
        self.browser.count=1;self.use_attachment_comments(source='https://untrusted.invalid/emoji.png');calls=[]
        def save(url,destination,kind,notify):calls.append(url);return attachment_download(url,destination,kind,notify)
        self.service.downloader=save;job=self.run_clone()
        self.assertEqual(job['status'],'partial');self.assertFalse(any('untrusted.invalid' in url for url in calls))
        coverage=job['posts'][0]['coverage']['comments'];self.assertEqual(coverage['unarchivedAttachments'],1)

    def test_failed_attachment_is_not_retried_each_scroll_or_next_post_until_explicit_retry(self):
        self.browser.count=2;snapshot=self.browser.post_snapshot;calls=[]
        def attachment():
            data=snapshot();data['comments'][0]['attachments']=[{'kind':'emoji','sourceIndex':1,'sourceUrl':'https://picasso-static.xiaohongshu.com/reaction.png'}]
            return data
        self.browser.post_snapshot=attachment
        def bad(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append(url);raise ValueError('Unavailable attachment')
            return download(url,destination,kind,notify)
        self.service.downloader=bad;job=self.run_clone()
        self.assertEqual(len(calls),1);self.assertEqual(job['status'],'partial')
        self.service.downloader=attachment_download;self.service.retry(job['jobId']);job['status']='running';self.service._run(job)
        self.assertEqual(job['status'],'complete')

    def test_attachment_pause_preserves_rows_and_existing_media(self):
        self.browser.count=1;self.use_attachment_comments()
        def pause(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':self.service.pause(next(iter(self.service.jobs)));notify(0)
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=pause
        with self.assertRaises(CapturePaused):self.run_clone()
        job=next(iter(self.service.jobs.values()));post=self.store.posts[(PROFILE_ID,f'{1:024x}')]
        self.assertEqual(job['status'],'paused');self.assertEqual(post['comments'][0]['text'],'')
        self.assertEqual(post['coverage']['media']['status'],'complete')
        self.assertEqual(post['coverage']['comments']['unarchivedAttachments'],1)

    def test_attachment_rate_response_stops_serial_worker_and_applies_site_cooldown(self):
        self.browser.count=2;self.use_attachment_comments();calls=[]
        def rate(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':
                calls.append(url);raise HTTPError(url,429,'Too Many Requests',{'Retry-After':'1800'},None)
            return download(url,destination,kind,notify)
        self.service.downloader=rate
        with self.assertRaises(CaptureAttention) as caught:self.run_clone()
        job=next(iter(self.service.jobs.values()));self.service._mark_attention(job,caught.exception)
        self.assertEqual(len(calls),1);self.assertGreaterEqual(self.service.cooldown_remaining(),1799)
        self.assertEqual(job['posts'][0]['status'],'needs_attention');self.assertEqual(job['posts'][1]['status'],'queued')
        self.assertNotIn('transient-signature',self.service.comment_asset_index.read_text())

    def test_fresh_remote_attachment_metadata_does_not_erase_saved_file(self):
        prior=[{'id':'native','text':'','attachments':[{'kind':'emoji','sourceIndex':1,'sourceUrl':'https://img.xhscdn.com/emoji.png','file':'clone_media/comment_assets/emoji.png','bytes':4,'sha256':'a'*64,'mime':'image/png','status':'saved','archiveStatus':'saved'}]}]
        fresh=[{'id':'native','text':'','attachments':[{'kind':'emoji','sourceIndex':1,'sourceUrl':'https://img.xhscdn.com/emoji.png','requestUrl':'https://img.xhscdn.com/emoji.png?signature=fresh','archiveStatus':'remote_only','width':48}]}]
        asset=merge_comments(prior,fresh)[0]['attachments'][0]
        self.assertEqual(asset['file'],'clone_media/comment_assets/emoji.png');self.assertEqual(asset['archiveStatus'],'saved')
        self.assertEqual(asset['width'],48);self.assertIn('signature=fresh',asset['requestUrl'])

    def test_attachment_variants_keep_their_own_observed_signed_urls(self):
        self.browser.count=1;snapshot=self.browser.post_snapshot;calls=[]
        def observed():
            data=snapshot();data['comments']=[{'id':'variant-'+str(size),'text':'','attachments':[{'kind':'emoji','sourceIndex':1,'width':size,'height':size,'sourceUrl':'https://img.xhscdn.com/same.png','requestUrl':'https://img.xhscdn.com/same.png?variant='+str(size)}]} for size in (24,48)];data['reportedCommentCount']=2;return data
        self.browser.post_snapshot=observed
        def save(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append(url)
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=save;job=self.run_clone()
        self.assertEqual(calls,['https://img.xhscdn.com/same.png?variant=24','https://img.xhscdn.com/same.png?variant=48'])
        assets=[c['attachments'][0] for c in self.store.posts[(PROFILE_ID,job['posts'][0]['postId'])]['comments']]
        self.assertNotEqual(assets[0]['file'],assets[1]['file'])

    def test_attachment_metadata_budget_stops_before_network_download(self):
        self.browser.count=1;self.use_attachment_comments();calls=[]
        def save(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append(url)
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=save
        with patch('profile_clone.MAX_COMMENT_ASSET_INDEX_BYTES',100):job=self.run_clone()
        self.assertEqual(calls,[]);self.assertEqual(job['status'],'partial')
        self.assertEqual(job['posts'][0]['coverage']['comments']['attachmentReason'],'comment_asset_index_budget')

    def test_unique_note_attachment_budget_prevents_second_asset_download(self):
        self.browser.count=1;self.use_attachment_comments(count=2);snapshot=self.browser.post_snapshot;calls=[]
        def observed():
            data=snapshot();asset=data['comments'][1]['attachments'][0]
            asset['sourceUrl']='https://img.xhscdn.com/second.png';asset['requestUrl']=asset['sourceUrl'];return data
        self.browser.post_snapshot=observed
        def save(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append(url)
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=save
        with patch('profile_clone.COMMENT_ATTACHMENT_BUDGET_BYTES',len(png_bytes())):job=self.run_clone()
        self.assertEqual(len(calls),1);self.assertEqual(job['status'],'partial')
        self.assertEqual(job['posts'][0]['coverage']['comments']['savedAttachments'],1)
        self.assertEqual(job['posts'][0]['coverage']['comments']['attachmentReason'],'comment_attachment_budget')

    def test_comment_walk_finishes_before_attachment_download(self):
        self.browser.count=1;snapshot=self.browser.post_snapshot;calls=[]
        def observed():
            data=snapshot();data['comments'][0]['attachments']=[{'kind':'emoji','sourceIndex':1,'sourceUrl':'https://img.xhscdn.com/emoji.png'}];return data
        self.browser.post_snapshot=observed
        def save(url,destination,kind,notify):
            if destination.parent.name=='comment_assets':calls.append(self.browser.comment_page)
            return attachment_download(url,destination,kind,notify)
        self.service.downloader=save;self.run_clone()
        self.assertEqual(calls,[1])

    def test_pause_after_committed_attachment_keeps_file_and_checkpoint(self):
        self.browser.count=1;self.use_attachment_comments()
        def save(url,destination,kind,notify):
            result=attachment_download(url,destination,kind,notify)
            if destination.parent.name=='comment_assets':self.service.pause(next(iter(self.service.jobs)))
            return result
        self.service.downloader=save
        with self.assertRaises(CapturePaused):self.run_clone()
        job=next(iter(self.service.jobs.values()));post=self.store.posts[(PROFILE_ID,job['posts'][0]['postId'])]
        asset=post['comments'][0]['attachments'][0]
        self.assertTrue(self.service._verified_comment_asset(asset))
        self.assertEqual(post['coverage']['comments']['savedAttachments'],1)


if __name__=='__main__':unittest.main()
