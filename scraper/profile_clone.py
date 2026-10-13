"""Durable, paced profile cloning through the app's normally rendered browser.

The adapter navigates ordinary pages and reads their loaded DOM/state. This module
does not read cookies, call private endpoints, or claim completion from a stall.
"""
import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from media_reporting import reconcile
from download_ledger import DownloadLedger
from browser_capture import allowed_media_url, merge_comments, save_media

HOSTS={'www.rednote.com','rednote.com','www.xiaohongshu.com','xiaohongshu.com'}
PROFILE_PATH=re.compile(r'^/user/profile/([a-f0-9]{24})/?$', re.I)
POST_PATH=re.compile(r'^/(?:discovery/item|explore|user/profile/[a-f0-9]{24})/([a-f0-9]{24})/?$', re.I)
DEFAULT_OPTIONS={
    'maxPosts':0, 'maxComments':0, 'profileScrollBudget':1200,
    'commentScrollBudget':300, 'stallRounds':8,
    'pageWaitSeconds':40, 'pageDelaySeconds':1.5,
    'postPacingSeconds':10, 'mediaPacingSeconds':10,
}
MINIMUM_PACING={'pageDelaySeconds':1.5,'postPacingSeconds':10,'mediaPacingSeconds':10}
MINIMUM_RATE_COOLDOWN_SECONDS=900
COMMENT_ATTACHMENT_BUDGET_BYTES=25_000_000
MAX_COMMENT_ASSET_INDEX_BYTES=8_000_000


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean_url(value):
    if not isinstance(value, str):return ''
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'https','http'}:return ''
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    except ValueError:return ''


def safe_error(value):
    text=str(value)
    text=re.sub(r'https?://[^\s\)\]<>]+', lambda m:clean_url(m.group()), text)
    text=re.sub(r'(?i)(xsec_token|token|cookie|authorization)\s*[:=]\s*[^\s&,;]+', r'\1=[redacted]', text)
    return text[:400]


def archive_safe(value):
    if isinstance(value, dict):
        return {k:archive_safe(v) for k,v in value.items() if str(k).lower() not in {'cookie','cookies','authorization','headers','xsec_token','token','requesturl','request_url'}}
    if isinstance(value, list):return [archive_safe(v) for v in value]
    if isinstance(value, str) and value.startswith(('https://','http://')):return clean_url(value)
    return value


def parse_count(value):
    """Only exact integer counts establish capture completion."""
    if isinstance(value, bool):return None
    if isinstance(value, int) and value>=0:return value
    text=str(value or '').strip().replace(',','')
    match=re.fullmatch(r'(?:共)?\s*(\d+)\s*(?:条评论|条|comments?)?', text, re.I)
    return int(match.group(1)) if match else None


class CapturePaused(Exception):pass


class CommentAttachmentBudget(ValueError):
    def __init__(self,count):
        self.minimum_bytes=count
        super().__init__('Comment attachment exceeds the remaining per-note size budget.')


class CaptureAttention(Exception):
    def __init__(self, kind, message, retry_after=None):
        self.kind=kind
        self.retry_after=retry_after
        super().__init__(safe_error(message))


class ProfileCloneService:
    def __init__(self, base_dir, archive_store, browser_adapter, downloader=None):
        self.base_dir=Path(base_dir).resolve()
        self.store=archive_store
        self.browser=browser_adapter
        self.downloader=downloader or save_media
        self.queue_path=self.base_dir/'clone_queue.json'
        self.media_root=self.base_dir/'clone_media'
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.lock=threading.RLock()
        self.condition=threading.Condition(self.lock)
        self.jobs={}
        self.thread=None
        self.closed=False
        self.active_job=None
        self.transient_urls={}
        self.transient_posts={}
        self.transient_attachment_urls={}
        self.comment_asset_root=self.media_root/'comment_assets'
        self.comment_asset_index=self.comment_asset_root/'index.json'
        self.comment_assets={}
        self.media_verification_cache={}
        self.media_report_times={}
        self.ledger=DownloadLedger(self.base_dir)
        self.safety={'nextMediaAt':0,'nextPostAt':0,'siteCooldownUntil':0,'nextResumeAt':''}
        self._load()
        self._load_comment_assets()

    def _load(self):
        if not self.queue_path.exists():return
        try:
            saved=json.loads(self.queue_path.read_text(encoding='utf-8'))
            rows=saved.get('jobs', [])
            for key in ('nextMediaAt','nextPostAt','siteCooldownUntil'):
                try:
                    value=float(saved.get('safety',{}).get(key,0))
                    if math.isfinite(value) and value>=0:self.safety[key]=value
                except (TypeError,ValueError):pass
            self.safety['nextResumeAt']=str(saved.get('safety',{}).get('nextResumeAt',''))
            for row in rows:
                if not isinstance(row, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(row.get('jobId',''))):continue
                pending_single=(row.get('scope')=='single_post' and not row.get('profileId') and
                                re.fullmatch(r'[a-f0-9]{24}',str(row.get('postId',''))) and
                                len(row.get('posts',[]))==1 and row['posts'][0].get('postId')==row['postId'])
                if not pending_single and not re.fullmatch(r'[a-f0-9]{24}', str(row.get('profileId',''))):continue
                options=dict(DEFAULT_OPTIONS)
                options.update(row.get('options',{}))
                for key,minimum in MINIMUM_PACING.items():
                    try:options[key]=max(minimum,float(options[key])) if math.isfinite(float(options[key])) else minimum
                    except (TypeError,ValueError):options[key]=minimum
                row['options']=options
                self.safety['siteCooldownUntil']=max(self.safety['siteCooldownUntil'],float(row.get('cooldownUntil',0)))
                if row.get('status') in {'queued','running','cooldown'} or (row.get('status')=='paused' and row.get('reason')=='app_closed'):
                    row['resumeOnStartup']=True
                    row['status']='paused';row['reason']='interrupted';row['message']='Capture interrupted; waiting for the app browser to load before continuing.'
                    for post in row.get('posts',[]):
                        if post.get('status') in {'opening','capturing','downloading'}:post['status']='queued'
                self.jobs[row['jobId']]=archive_safe(row)
        except (OSError, ValueError, TypeError):
            # Preserve an unreadable queue as evidence; do not silently overwrite it.
            target=self.queue_path.with_name('clone_queue.unreadable-'+uuid.uuid4().hex[:8]+'.json')
            self.queue_path.rename(target)

    def _persist(self):
        temp=self.queue_path.with_suffix('.json.partial')
        # Checkpoints can contain hundreds of posts and are rewritten on each
        # capture step. Compact JSON keeps the same schema and atomic replace
        # while reducing repeated serialization and disk traffic.
        temp.write_text(json.dumps({'schema':'rednote.profile-clone/1','safety':self.safety,'jobs':archive_safe(list(self.jobs.values()))}, ensure_ascii=False, separators=(',',':')), encoding='utf-8')
        temp.replace(self.queue_path)

    def _load_comment_assets(self):
        if not self.comment_asset_index.exists():return
        try:
            if self.comment_asset_index.stat().st_size>MAX_COMMENT_ASSET_INDEX_BYTES:raise ValueError('Comment asset index exceeds its local metadata budget.')
            data=json.loads(self.comment_asset_index.read_text(encoding='utf-8'))
            entries=data.get('assets',{})
            if not isinstance(entries,dict) or len(entries)>10000:raise ValueError('Invalid comment asset index.')
            self.comment_assets={source:archive_safe(asset) for source,asset in entries.items() if source==clean_url(source) and allowed_media_url(source) and isinstance(asset,dict)}
        except (OSError,ValueError,TypeError):
            # Keep corrupt metadata for review, just as an interrupted queue is kept.
            self.comment_asset_index.rename(self.comment_asset_index.with_name('index.unreadable-'+uuid.uuid4().hex[:8]+'.json'))

    def _comment_assets_payload(self,assets):
        slots=sum(len(entry.get('variants',{})) if isinstance(entry,dict) and 'variants' in entry else 1 for entry in assets.values())
        payload=json.dumps({'schema':'rednote.comment-assets/1','assets':archive_safe(assets)},ensure_ascii=False,indent=2)
        if slots>10000 or len(payload.encode('utf-8'))>MAX_COMMENT_ASSET_INDEX_BYTES:
            raise ValueError('The shared comment asset metadata budget was reached.')
        return payload

    def _persist_comment_assets(self,assets=None):
        proposed=self.comment_assets if assets is None else assets
        payload=self._comment_assets_payload(proposed)
        self.comment_asset_root.mkdir(parents=True,exist_ok=True)
        temp=self.comment_asset_index.with_suffix('.json.partial')
        temp.write_text(payload,encoding='utf-8')
        temp.replace(self.comment_asset_index)
        if assets is not None:self.comment_assets=assets

    @staticmethod
    def _comment_asset_variant(asset):
        dims=[]
        for field in ('width','height'):
            value=asset.get(field,asset.get('source'+field.title()))
            dims.append(value if isinstance(value,int) and not isinstance(value,bool) and value>0 else 0)
        return str(asset.get('kind',''))+':'+str(dims[0])+'x'+str(dims[1])

    def _comment_cache_entry(self,source,variant):
        entry=self.comment_assets.get(source,{})
        if 'variants' in entry:return entry.get('variants',{}).get(variant,{})
        return entry if self._comment_asset_variant(entry)==variant else {}

    def _comment_cache_proposal(self,source,variant,descriptor):
        proposed=copy.deepcopy(self.comment_assets)
        entry=proposed.get(source,{})
        variants=dict(entry.get('variants',{}))
        if entry and 'variants' not in entry:variants[self._comment_asset_variant(entry)]=entry
        variants[variant]=archive_safe(descriptor)
        proposed[source]={'sourceUrl':source,'variants':variants}
        return proposed

    def _cache_comment_asset(self,source,variant,descriptor):
        self._persist_comment_assets(self._comment_cache_proposal(source,variant,descriptor))

    def _verified_comment_asset(self,asset):
        """Shared cache hits require a contained file, recorded size and hash."""
        try:
            raw=asset.get('file') or asset.get('path') or ''
            path=(self.base_dir/raw).resolve()
            expected_hash=str(asset.get('sha256','')).lower()
            size=asset.get('bytes')
            if not path.is_relative_to(self.comment_asset_root) or not path.is_file():return False
            stat=path.stat()
            if not isinstance(size,int) or isinstance(size,bool) or size<=0 or stat.st_size!=size:return False
            if not re.fullmatch(r'[a-f0-9]{64}',expected_hash):return False
            if asset.get('mime') not in {'image/jpeg','image/png','image/webp','image/avif','image/gif'}:return False
            key=(str(path),size,stat.st_mtime_ns,stat.st_ctime_ns,expected_hash,asset.get('mime'))
            verified=getattr(self,'_comment_integrity_cache',None)
            if verified is None:self._comment_integrity_cache=verified={}
            if key in verified:return verified[key]
            digest=hashlib.sha256()
            with path.open('rb') as file:
                for block in iter(lambda:file.read(65536),b''):digest.update(block)
            valid=digest.hexdigest()==expected_hash
            if len(verified)>=5000:verified.clear()
            verified[key]=valid
            return valid
        except (OSError,ValueError,TypeError):return False

    def _remember_comment_attachment_urls(self,job,comments):
        transient=self.transient_attachment_urls.setdefault(job['jobId'],{})
        for comment in comments or []:
            if not isinstance(comment,dict):continue
            for asset in comment.get('attachments',[]):
                if not isinstance(asset,dict):continue
                observed=asset.get('requestUrl') or asset.get('sourceUrl') or ''
                canonical=clean_url(observed)
                if allowed_media_url(observed) and canonical==clean_url(asset.get('sourceUrl') or observed):
                    transient[(canonical,self._comment_asset_variant(asset))]=observed

    def _save_comment_attachments(self,job,comments,row,on_checkpoint=None):
        attempt=job.setdefault('attachmentAttemptId',uuid.uuid4().hex)
        transient=self.transient_attachment_urls.get(job['jobId'],{})
        used={}
        for comment in comments:
            for asset in comment.get('attachments',[]):
                if isinstance(asset,dict) and self._verified_comment_asset(asset):
                    identity=(clean_url(asset.get('sourceUrl','')),self._comment_asset_variant(asset));used[identity]=max(used.get(identity,0),asset['bytes'])
        budget=row.setdefault('commentAttachmentBudget',{'limitBytes':COMMENT_ATTACHMENT_BUDGET_BYTES,'exhausted':False})
        budget.update(limitBytes=COMMENT_ATTACHMENT_BUDGET_BYTES,bytes=sum(used.values()))
        if budget['bytes']>COMMENT_ATTACHMENT_BUDGET_BYTES:budget['exhausted']=True
        for comment in comments:
            for asset in comment.get('attachments',[]):
                if not isinstance(asset,dict):continue
                self._check(job)
                source=clean_url(asset.get('sourceUrl') or asset.get('requestUrl') or '')
                variant=self._comment_asset_variant(asset)
                identity=(source,variant)
                cap=2_000_000 if asset.get('kind')=='emoji' else 5_000_000
                remaining=COMMENT_ATTACHMENT_BUDGET_BYTES-sum(used.values())+used.get(identity,0)
                if self._verified_comment_asset(asset) and asset['bytes']<=cap:
                    self.ledger.record(job,'reused',asset,'comment_emoji' if asset.get('kind')=='emoji' else 'comment_image',asset.get('sourceIndex'));continue
                cached=self._comment_cache_entry(source,variant)
                if self._verified_comment_asset(cached) and cached['bytes']<=cap and cached['bytes']<=remaining:
                    asset.update({k:v for k,v in cached.items() if k in {'file','path','bytes','sha256','mime'}})
                    asset.update(status='saved',archiveStatus='saved');asset.pop('error',None)
                    used[identity]=max(used.get(identity,0),cached['bytes']);budget['bytes']=sum(used.values())
                    self.ledger.record(job,'reused',asset,'comment_emoji' if asset.get('kind')=='emoji' else 'comment_image',asset.get('sourceIndex'));continue
                if self._verified_comment_asset(cached) and cached['bytes']>remaining:
                    budget['exhausted']=True
                if budget['exhausted'] or remaining<=0:
                    asset.update(status='failed',archiveStatus='failed',errorCode='comment_attachment_budget',error='The 25MB unique comment attachment budget for this note was reached.')
                    continue
                if cached.get('attemptId')==attempt and cached.get('status')!='saved':
                    asset.update(status='failed',archiveStatus='failed',error=cached.get('error','Comment attachment could not be saved.'))
                    continue
                if asset.get('kind') not in {'emoji','image','gif','sticker','gift'} or not allowed_media_url(source):
                    asset.update(status='failed',archiveStatus='failed',error='Only observed raster attachments on a supported media host can be saved.')
                    continue
                observed=transient.get(identity)
                if not observed:
                    # A retained row without its newly observed request URL stays
                    # visible and partial; do not guess a lost signed URL.
                    asset.update(archiveStatus='remote_only');continue
                destination=self.comment_asset_root/hashlib.sha256((source+'\0'+variant).encode('utf-8')).hexdigest()
                reserved_path=(destination.parent/(destination.name+'-'+'f'*12+'.webp')).relative_to(self.base_dir).as_posix()
                reservation={'sourceUrl':source,'kind':asset.get('kind'),'sourceWidth':asset.get('width'),'sourceHeight':asset.get('height'),'status':'reserved','attemptId':attempt,'file':reserved_path,'path':reserved_path,'bytes':cap,'sha256':'f'*64,'mime':'image/webp','error':'x'*400}
                try:self._comment_assets_payload(self._comment_cache_proposal(source,variant,reservation))
                except ValueError:
                    asset.update(status='failed',archiveStatus='failed',errorCode='comment_asset_index_budget',error='The shared comment asset metadata budget was reached.');continue
                job['phase']='saving_comment_attachments';job['message']='Saving a normally loaded comment attachment for offline viewing.'
                try:
                    saved=self._download_one(job,observed,destination,'comment_emoji' if asset.get('kind')=='emoji' else 'comment_image',asset.get('sourceIndex',0),byte_limit=remaining)
                except CaptureAttention as exc:
                    self._cache_comment_asset(source,variant,{'sourceUrl':source,'kind':asset.get('kind'),'sourceWidth':asset.get('width'),'sourceHeight':asset.get('height'),'status':'failed','attemptId':attempt,'error':safe_error(exc)})
                    raise
                saved={**archive_safe(saved),'sourceUrl':source,'attemptId':attempt,'kind':asset.get('kind'),'sourceWidth':asset.get('width'),'sourceHeight':asset.get('height')}
                if saved.get('status')=='saved' and not self._verified_comment_asset(saved):
                    saved.update(status='failed',error='The saved comment attachment failed its local file integrity check.')
                if saved.get('errorCode')=='comment_attachment_budget':budget['exhausted']=True
                else:self._cache_comment_asset(source,variant,saved)
                if saved.get('status')=='saved':
                    asset.update({k:v for k,v in saved.items() if k in {'file','path','bytes','sha256','mime'}})
                    asset.update(status='saved',archiveStatus='saved');asset.pop('error',None)
                    used[identity]=max(used.get(identity,0),saved['bytes']);budget['bytes']=sum(used.values())
                else:
                    asset.update(status='failed',archiveStatus='failed',error=saved.get('error','Comment attachment could not be saved.'))
                    if saved.get('errorCode'):asset['errorCode']=saved['errorCode']
                # Once a downloader has committed a file, retain its descriptor
                # and cache checkpoint before acknowledging a post-commit pause.
                if on_checkpoint:on_checkpoint()
                self._check(job)

    def _comment_attachment_coverage(self,comments):
        assets=[asset for comment in comments for asset in comment.get('attachments',[]) if isinstance(asset,dict)]
        saved=sum(self._verified_comment_asset(asset) for asset in assets)
        reason='comment_attachment_budget' if any(asset.get('errorCode')=='comment_attachment_budget' for asset in assets) else 'comment_asset_index_budget' if any(asset.get('errorCode')=='comment_asset_index_budget' for asset in assets) else ''
        return {'attachments':len(assets),'savedAttachments':saved,'unarchivedAttachments':len(assets)-saved,'attachmentCoverage':'partial' if saved<len(assets) else 'complete','attachmentReason':reason}

    def _ensure_worker(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread=threading.Thread(target=self._worker, name='rednote-profile-clone', daemon=True)
            self.thread.start()

    def start(self, profile_url, options=None, confirm_permission=False):
        if confirm_permission is not True:
            raise ValueError('Confirm permission to save this profile before starting capture.')
        parsed=urlsplit(str(profile_url).strip())
        match=PROFILE_PATH.fullmatch(parsed.path)
        if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443) or not match:
            raise ValueError('Use an HTTPS RedNote profile URL with its 24-character source ID.')
        opts=dict(DEFAULT_OPTIONS)
        for key,value in (options or {}).items():
            key={'postLimit':'maxPosts','delaySeconds':'pageDelaySeconds'}.get(key,key)
            if key not in opts:raise ValueError('Unknown profile capture option: '+str(key))
            if key in {'maxPosts','maxComments','profileScrollBudget','commentScrollBudget','stallRounds'}:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value!=int(value):
                    raise ValueError(key+' must be an integer count.')
                number=int(value)
                if number<0 or (key not in {'maxPosts','maxComments'} and number<1):raise ValueError(key+' must be a non-negative count or positive budget.')
                if number>100000:raise ValueError(key+' exceeds the supported budget.')
                opts[key]=number
            else:
                number=float(value)
                minimum=MINIMUM_PACING.get(key,0)
                if isinstance(value,bool) or not math.isfinite(number) or number<minimum or number>3600:raise ValueError(key+' must be a finite number between '+str(minimum)+' and 3600 seconds.')
                opts[key]=number
        with self.condition:
            if self.closed:raise RuntimeError('The profile collector is closed.')
            self._enforce_cooldown()
            profile_id=match.group(1).lower()
            duplicate=next((j for j in self.jobs.values() if j.get('scope') not in {'single_post','selected_posts'} and j['profileId']==profile_id and j['status'] in {'queued','running','cooldown','paused','needs_attention'}),None)
            if duplicate:
                self.transient_urls[duplicate['jobId']]=str(profile_url).strip()
                return copy.deepcopy(duplicate)
            job_id=uuid.uuid4().hex
            job={'jobId':job_id,'profileId':profile_id,'sourceUrl':clean_url(str(profile_url).strip()),'status':'queued','phase':'enumerating','createdAt':utc_now(),'updatedAt':utc_now(),'options':opts,'posts':[],'attachmentAttemptId':uuid.uuid4().hex,
                 'profile':{},'enumeration':{'status':'pending','reason':'','rounds':0,'discovered':0,'expected':None},'coverage':{},'progress':{'discoveredPosts':0,'capturedPosts':0,'completePosts':0,'failedPosts':0,'savedMedia':0,'comments':0,'bytes':0},'message':'Profile queued for a full local reconstruction.','reason':''}
            self.ledger.begin(job)
            self.jobs[job_id]=job
            self.transient_urls[job_id]=str(profile_url).strip()
            self.transient_posts[job_id]={}
            self._persist();self._ensure_worker();self.condition.notify_all()
            return copy.deepcopy(job)

    def start_selected(self, profile_url, post_urls, confirm_permission=False):
        if confirm_permission is not True:raise ValueError('Confirm permission to capture selected posts.')
        parsed=urlsplit(str(profile_url));m=re.fullmatch(r'/user/profile/([a-f0-9]{24})/?',parsed.path)
        if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443) or not m:raise ValueError('Choose a RedNote profile preview first.')
        if not isinstance(post_urls,list) or not 1<=len(post_urls)<=100:raise ValueError('Select between 1 and 100 posts.')
        urls={}
        for value in post_urls:
            u=urlsplit(str(value));note=POST_PATH.fullmatch(u.path)
            if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443) or not note:raise ValueError('Selected posts must be valid RedNote HTTPS note links.')
            urls[note.group(1).lower()]=str(value)
        with self.condition:
            if self.closed:raise RuntimeError('Collector closed.')
            self._enforce_cooldown()
            if self.active_job or any(j.get('status') in {'queued','running','cooldown'} for j in self.jobs.values()):raise ValueError('Pause the miner and wait for the current operation before starting selected capture.')
            jid=uuid.uuid4().hex
            job={'jobId':jid,'scope':'selected_posts','profileId':m.group(1),'sourceUrl':clean_url(str(profile_url)),
                'status':'queued','phase':'opening_note','createdAt':utc_now(),'updatedAt':utc_now(),'options':dict(DEFAULT_OPTIONS),
                'posts':[{'postId':pid,'sourceUrl':clean_url(url),'status':'queued','media':[],'commentCount':0} for pid,url in urls.items()],
                'attachmentAttemptId':uuid.uuid4().hex,'profile':{},'enumeration':{'status':'not_applicable','scope':'selected_posts','reason':'Only explicitly selected posts; no profile traversal.','discovered':len(urls),'expected':len(urls)},
                'coverage':{},'progress':{},'batchMode':False,'deferCommentAttachments':False,'resumeOnStartup':False,'message':'Selected posts queued; unchecked posts will not be visited.','reason':''}
            self.jobs[jid]=job;self.transient_posts[jid]={pid:{'url':url} for pid,url in urls.items()}
            self.ledger.begin(job);self._checkpoint(job);self._ensure_worker();self.condition.notify_all();return copy.deepcopy(job)

    def _run_selected(self,job):
        self._restore_proven_content(job)
        for row in job['posts']:
            self._check(job)
            if row.get('status')=='complete':continue
            if row.get('contentComplete'):self._backfill_post_assets(job,row)
            else:self._capture_post(job,row)
        self._check(job)
        complete=all(p.get('status')=='complete' for p in job['posts'])
        job.update(status='complete' if complete else 'partial',phase='finished',reason='' if complete else 'selected_coverage_incomplete',coverage={'status':'complete' if complete else 'partial','scope':'selected_posts','wholeProfileComplete':False},message='Selected batch finished. Whole-profile coverage was not changed.')
        self._checkpoint(job)

    def start_single(self, post_url, confirm_permission=False):
        """Queue one note; the worker resolves its real author before archiving."""
        if confirm_permission is not True:
            raise ValueError('Confirm permission to save this post before starting capture.')
        supplied=str(post_url).strip()
        parsed=urlsplit(supplied)
        match=POST_PATH.fullmatch(parsed.path)
        if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443) or not match:
            raise ValueError('Paste an HTTPS RedNote/Xiaohongshu post URL with its 24-character note ID.')
        post_id=match.group(1).lower()
        with self.condition:
            if self.closed:raise RuntimeError('The collector is closed.')
            self._enforce_cooldown()
            if self.active_job or any(j.get('status') in {'queued','running','cooldown'} for j in self.jobs.values()):
                raise ValueError('Pause the current miner and wait for its current operation before processing a single post.')
            duplicate=next((j for j in reversed(list(self.jobs.values())) if j.get('scope')=='single_post' and j.get('postId')==post_id),None)
            if duplicate:
                self.transient_posts[duplicate['jobId']]={post_id:{'url':supplied}}
                # Revalidate local files on every explicit start; a persisted
                # completed flag alone cannot prove that they still exist.
                if duplicate.get('status')=='complete':duplicate['status']='partial'
                return self.resume(duplicate['jobId'])
            job_id=uuid.uuid4().hex
            row={'postId':post_id,'sourceUrl':clean_url(supplied),'status':'queued','media':[],'commentCount':0}
            retained=self.store.get_post(post_id) or {}
            known_author=str(retained.get('profileId') or retained.get('profile_id') or '').lower()
            if not re.fullmatch(r'[a-f0-9]{24}',known_author):known_author=''
            job={'jobId':job_id,'scope':'single_post','postId':post_id,'profileId':known_author,
                 'sourceUrl':clean_url(supplied),'status':'queued','phase':'opening_note',
                 'createdAt':utc_now(),'updatedAt':utc_now(),'options':dict(DEFAULT_OPTIONS),
                 'posts':[row],'attachmentAttemptId':uuid.uuid4().hex,'profile':{},
                 'enumeration':{'status':'not_applicable','scope':'single_post','reason':'One requested note; full profile enumeration is not requested.','discovered':1,'expected':1,'rounds':0},
                 'coverage':{},'progress':{},'batchMode':False,'deferCommentAttachments':False,
                 'resumeOnStartup':False,'message':'Single post queued: its media, comment tree and exposed discussion attachments.','reason':''}
            self.ledger.begin(job)
            self.jobs[job_id]=job
            self.transient_posts[job_id]={post_id:{'url':supplied}}
            self._checkpoint(job);self._ensure_worker();self.condition.notify_all()
            return copy.deepcopy(job)

    def status(self, job_id=None):
        with self.lock:
            if job_id is not None:
                if job_id not in self.jobs:raise ValueError('Unknown profile capture job.')
                return {**copy.deepcopy(self.jobs[job_id]),'safety':copy.deepcopy(self.safety)}
            return {'activeJobId':self.active_job,'jobs':copy.deepcopy(list(self.jobs.values())),'safety':copy.deepcopy(self.safety)}

    def control_state(self):
        """Small, credential-free status for frequent source-toolbar polling."""
        with self.lock:
            job=self.jobs.get(self.active_job)
            if job is None:
                ordered=list(self.jobs.values())
                job=next((row for row in reversed(ordered) if row.get('status') in {'queued','running','cooldown'}),None)
                if job is None:job=ordered[-1] if ordered else {}
            return {'activeJobId':self.active_job,'jobId':job.get('jobId'),'profileId':job.get('profileId'),
                    'currentPostId':job.get('currentPostId'),'updatedAt':job.get('updatedAt'),
                    'busy':bool(self.active_job or job.get('status') in {'queued','running','cooldown'}),
                    'status':job.get('status','idle'),'phase':job.get('phase','idle'),
                    'message':job.get('message','No profile capture is active.'),
                    'progress':dict(job.get('progress',{})),
                    'cooldownRemainingSeconds':self.cooldown_remaining()}

    def pause(self, job_id):
        with self.condition:
            job=self._get(job_id)
            if job['status'] in {'queued','running','cooldown'} or job.get('resumeOnStartup'):
                job['resumeOnStartup']=False
                job['status']='paused';job['reason']='user_paused';job['message']='Paused. Captured posts, files and comment threads are retained.'
                self._checkpoint(job);self.condition.notify_all()
            return copy.deepcopy(job)

    def stop(self, job_id):
        with self.condition:
            job=self._get(job_id)
            if job['status']!='complete':
                job['status']='paused';job['reason']='user_stopped';job['resumeOnStartup']=False
                job['message']='Stopped. Saved files and the queue are retained; Resume starts it again.'
                self._checkpoint(job);self.condition.notify_all()
            return copy.deepcopy(job)

    def automatic_batch(self, job_id):
        """Capture every note's content first, then fill shared discussion assets."""
        with self.lock:
            if self._get(job_id).get('scope') in {'single_post','selected_posts'}:return self.resume(job_id)
        with self.condition:
            job=self._get(job_id)
            if self.closed:raise RuntimeError('The profile collector is closed.')
            self._enforce_cooldown(job)
            if self.active_job==job_id:
                if job.get('batchMode'):return copy.deepcopy(job)
                raise ValueError('Pause the current operation before changing to automatic profile mode.')
            job.update(batchMode=True,deferCommentAttachments=True,status='queued',reason='',message='Automatic profile capture queued: post media and comment trees first, discussion assets afterward.',resumeOnStartup=False)
            for key in ('selectedRetryOnly','selectedPostIds','priorityPostIds','assetBackfill'):job.pop(key,None)
            for row in job['posts']:
                prior=self._prior_post(job['profileId'],row['postId'])
                proof=prior.get('commentTreeProof') or row.get('commentTreeProof') or {}
                ids=sorted(str(c['id']) for c in prior.get('comments',[]) if c.get('id'))
                matches=proof.get('status')=='complete' and proof.get('idHash')==hashlib.sha256('\n'.join(ids).encode()).hexdigest()
                media=prior.get('media',row.get('media',[]))
                media_complete=prior.get('coverage',{}).get('media',{}).get('status')=='complete' and bool(media) and all(m.get('status')=='saved' and self._saved_exists(m) for m in media)
                row['contentComplete']=bool(matches and media_complete)
                if matches:row['commentTreeProof']=proof
                if row.get('status')!='complete' and not row['contentComplete']:row['status']='queued'
            self._checkpoint(job);self._ensure_worker();self.condition.notify_all()
            return copy.deepcopy(job)

    def recover_interrupted(self):
        """Called after the owned browser loads; never undo a user pause/stop."""
        recovered=[]
        with self.condition:
            if self.closed:return recovered
            for job in self.jobs.values():
                if job.get('status')!='paused' or not job.get('resumeOnStartup'):continue
                job['resumeOnStartup']=False
                if self.cooldown_remaining() or float(job.get('cooldownUntil',0))>time.time():
                    job.update(status='needs_attention',reason='site_cooldown',message='The interrupted queue requires manual resume after the website cooldown.')
                else:
                    job.update(status='queued',reason='',message='Continuing the interrupted capture from retained files and checkpoints.')
                    job['recoveryCount']=job.get('recoveryCount',0)+1
                    recovered.append(job['jobId'])
                self._checkpoint(job)
            if recovered:self._ensure_worker();self.condition.notify_all()
        return recovered

    def resume(self, job_id):
        with self.condition:
            job=self._get(job_id)
            if self.closed:raise RuntimeError('The profile collector is closed.')
            if self.active_job==job_id:raise ValueError('Wait for the paused capture worker to stop before resuming.')
            self._enforce_cooldown(job)
            if job['status'] in {'paused','needs_attention','failed','partial'}:
                job['resumeOnStartup']=False
                for key, minimum in MINIMUM_PACING.items():
                    job['options'][key]=max(minimum, float(job['options'].get(key,minimum)))
                self.ledger.begin(job)
                job['performanceMode']='conservative source pacing'
                job['attachmentAttemptId']=uuid.uuid4().hex
                job['status']='queued';job['reason']='';job['message']='Retained profile capture queued to resume.'
                if job.get('profile',{}).get('avatarMedia',{}).get('status')=='failed':job['profile'].pop('avatarMedia',None)
                for post in job['posts']:
                    if post.get('status') in {'opening','capturing','downloading','needs_attention','failed','partial'} and (not job.get('selectedRetryOnly') or post['postId'] in job.get('selectedPostIds',job.get('priorityPostIds',[]))):post['status']='queued'
                if job.get('selectedRetryOnly'):
                    job['priorityPostIds']=[p['postId'] for p in job['posts'] if p['postId'] in job.get('selectedPostIds',job.get('priorityPostIds',[])) and p.get('status')!='complete']
                self._checkpoint(job);self._ensure_worker();self.condition.notify_all()
            return copy.deepcopy(job)

    def retry(self, job_id, post_id=None, post_ids=None):
        with self.condition:
            job=self._get(job_id)
            if self.closed:raise RuntimeError('The profile collector is closed.')
            self._enforce_cooldown(job)
            if self.active_job==job_id:raise ValueError('Pause the job before retrying a note.')
            for key, minimum in MINIMUM_PACING.items():
                job['options'][key]=max(minimum, float(job['options'].get(key,minimum)))
            if post_id and post_ids is not None:raise ValueError('Choose a single post or a repair batch.')
            if post_ids is not None:
                if not isinstance(post_ids,list) or not 1<=len(post_ids)<=30 or any(not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{24}',value) for value in post_ids):
                    raise ValueError('Choose 1–30 retained post IDs for a paced repair batch.')
                requested=list(dict.fromkeys(post_ids))
                existing={p['postId'] for p in job['posts']}
                if any(value not in existing for value in requested):raise ValueError('A requested note is not in this profile queue.')
                targets=[p for p in job['posts'] if p['postId'] in requested]
            else:
                requested=[post_id] if post_id else []
                targets=[p for p in job['posts'] if (p['postId']==post_id if post_id else p.get('status')!='complete')]
                if post_id and not targets:raise ValueError('That note is not in this profile queue.')
            for post in targets:post['status']='queued';post['error']=''
            if requested:
                job['priorityPostIds']=requested[:];job['selectedPostIds']=requested[:]
                job['selectedRetryOnly']=True
                # A selected repair targets the missing comment tree. Avatars
                # and emoji keep their live links without triggering a bulk
                # media backfill while recovering a small source-count gap.
                job['deferCommentAttachments']=True
            else:
                job.pop('selectedRetryOnly',None)
                job.pop('selectedPostIds',None)
                if job.get('batchMode'):
                    job.pop('assetBackfill',None);job['deferCommentAttachments']=True
            avatar_retry=not requested and job.get('profile',{}).get('avatarMedia',{}).get('status')=='failed'
            if avatar_retry:job['profile'].pop('avatarMedia',None)
            if targets or avatar_retry or job['enumeration'].get('status')!='complete':
                job['attachmentAttemptId']=uuid.uuid4().hex
                job['status']='queued';job['reason']='';job['message']='Retry queued; saved media and comments will be reused.'
                self._checkpoint(job);self._ensure_worker();self.condition.notify_all()
            return copy.deepcopy(job)

    def configure_pacing(self, job_id, post_seconds, media_seconds):
        with self.condition:
            job=self._get(job_id)
            if self.closed:raise RuntimeError('The profile collector is closed.')
            if self.active_job==job_id or job['status'] not in {'paused','needs_attention','failed','partial'}:
                raise ValueError('Pause capture before changing its pacing.')
            try:
                post=float(post_seconds);media=float(media_seconds)
            except (TypeError,ValueError):
                raise ValueError('Pacing must be numeric seconds.') from None
            if not math.isfinite(post) or not math.isfinite(media) or post<MINIMUM_PACING['postPacingSeconds'] or media<MINIMUM_PACING['mediaPacingSeconds']:
                raise ValueError('Post and media pacing must each be at least 10 seconds.')
            job['options']['postPacingSeconds']=post
            job['options']['mediaPacingSeconds']=media
            job['performanceMode']='configured source pacing'
            self._checkpoint(job)
            return copy.deepcopy(job)

    def shutdown(self):
        with self.condition:
            self.closed=True
            for job in self.jobs.values():
                if job['status'] in {'queued','running','cooldown'}:job['status']='paused';job['reason']='app_closed';job['resumeOnStartup']=True;job['message']='App closed; capture will continue after its browser loads next time.'
            self._persist();self.condition.notify_all()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=2)

    def _get(self, job_id):
        if job_id not in self.jobs:raise ValueError('Unknown profile capture job.')
        return self.jobs[job_id]

    def cooldown_remaining(self):
        """Website-wide pause, also usable by manual capture/download controls."""
        with self.lock:return max(0,math.ceil(self.safety['siteCooldownUntil']-time.time()))

    def register_site_cooldown(self, retry_after=None):
        """Persist a website pause observed by capture or background research."""
        delay = MINIMUM_RATE_COOLDOWN_SECONDS
        try:
            requested = float(retry_after or 0)
            if math.isfinite(requested):delay = max(delay, requested)
        except (ValueError, TypeError):pass
        with self.lock:
            until = max(self.safety['siteCooldownUntil'], time.time() + delay)
            self.safety['siteCooldownUntil'] = until
            self.safety['nextResumeAt'] = datetime.fromtimestamp(until, timezone.utc).isoformat()
            self._persist()
            return {'remainingSeconds': math.ceil(until-time.time()),
                    'nextResumeAt': self.safety['nextResumeAt']}

    def _enforce_cooldown(self,job=None):
        until=max(self.safety['siteCooldownUntil'],float((job or {}).get('cooldownUntil',0)))
        remaining=until-time.time()
        if remaining>0:
            when=datetime.fromtimestamp(until,timezone.utc).isoformat()
            raise ValueError('The site requested a pause. Resume after '+when+' ('+str(math.ceil(remaining))+' seconds remaining).')

    def _checkpoint(self, job):
        checkpoint_started=time.monotonic()
        with self.lock:
            job['updatedAt']=utc_now()
            posts=job['posts']
            job['progress']={'discoveredPosts':len(posts),'capturedPosts':sum(p.get('status') in {'complete','partial'} for p in posts),'completePosts':sum(p.get('status')=='complete' for p in posts),'failedPosts':sum(p.get('status') in {'failed','needs_attention'} for p in posts),'savedMedia':sum(sum(m.get('status')=='saved' and m.get('kind') in {'photo','video'} for m in p.get('media',[])) for p in posts),'comments':sum(p.get('commentCount',0) for p in posts),'bytes':sum(sum(m.get('bytes',0) for m in p.get('media',[]) if m.get('status')=='saved') for p in posts)}
            job['completedPosts']=job['progress']['capturedPosts'];job['totalPosts']=len(posts)
            job['progress']['retainedPosts']=sum(bool(p.get('commentCount') or any(m.get('status')=='saved' for m in p.get('media',[]))) for p in posts)
            job['progress']['contentCompletePosts']=sum(bool(p.get('contentComplete')) for p in posts)
            job['progress']['partialCommentTrees']=sum(p.get('coverage',{}).get('comments',{}).get('status')=='partial' for p in posts)
            # Full media reconciliation verifies the entire retained profile.
            # The lightweight progress counters above remain live per step;
            # spread this expensive audit out during capture and always run it
            # at a resting checkpoint.
            report_last=self.media_report_times.get(job['jobId'])
            if report_last is None and job.get('mediaReporting') and job.get('status') in {'queued','running'}:
                report_last=self.media_report_times[job['jobId']]=time.monotonic()
            if job.get('profileId') and (time.monotonic()-(report_last or 0)>=180 or job.get('status') in {'paused','complete','partial','failed','needs_attention'}):
                reconcile_started=time.monotonic()
                job['mediaReporting']=reconcile(self,job);self.media_report_times[job['jobId']]=time.monotonic()
                performance=job.setdefault('performance',{})
                performance['mediaReconcileCount']=performance.get('mediaReconcileCount',0)+1
                performance['mediaReconcileSeconds']=performance.get('mediaReconcileSeconds',0)+time.monotonic()-reconcile_started
            job['failures']=[{'postId':p['postId'],'status':p.get('status'),'error':p.get('error',''),'coverage':p.get('coverage',{})} for p in posts if p.get('status') in {'failed','needs_attention','partial'}]
            metrics=job.setdefault('performance',{})
            metrics['checkpointCount']=metrics.get('checkpointCount',0)+1
            metrics['checkpointCounterSeconds']=metrics.get('checkpointCounterSeconds',0)+time.monotonic()-checkpoint_started
            write_started=time.monotonic()
            self._persist()
            metrics['checkpointWriteSeconds']=metrics.get('checkpointWriteSeconds',0)+time.monotonic()-write_started
            # Persist the last measured write too when a job reaches a resting
            # state. Earlier write timings are included in the next checkpoint.
            if job.get('status') in {'paused','complete','partial','failed','needs_attention'}:
                self._persist()

    def _check(self, job):
        if self.closed or job['status']=='paused':raise CapturePaused()
        if self.cooldown_remaining():raise CaptureAttention('site_cooldown','A website-wide cooldown is active. Resume manually after it expires.')

    def _wait(self, job, seconds):
        deadline=time.monotonic()+seconds
        while True:
            self._check(job)
            remaining=deadline-time.monotonic()
            if remaining<=0:return
            with self.condition:self.condition.wait(timeout=min(.25,remaining))

    def _pacing(self, job, category):
        key='nextMediaAt' if category=='media' else 'nextPostAt'
        interval=job['options']['mediaPacingSeconds' if category=='media' else 'postPacingSeconds']
        while True:
            self._check(job)
            with self.lock:delay=self.safety[key]-time.time()
            if delay<=0:break
            job['phase']=category+'_pacing';job['pacingUntil']=self.safety[key];self._checkpoint(job)
            started=time.monotonic();self._wait(job,delay)
            metrics=job.setdefault('performance',{});metrics['pacingSeconds']=metrics.get('pacingSeconds',0)+time.monotonic()-started
        with self.lock:
            job['pacingUntil']=0
            self.safety[key]=time.time()+interval
            self._persist()

    @staticmethod
    def _guard(data):
        guard=data.get('guard') if isinstance(data,dict) else None
        if guard:
            if isinstance(guard,dict):raise CaptureAttention(guard.get('kind','challenge'),guard.get('message','The site requires attention.'),guard.get('retryAfterSeconds') or guard.get('retryAfter'))
            raise CaptureAttention(str(guard),data.get('error','The site requires attention.'))

    def _read(self, job, method, expected_id=None, field='profileId'):
        deadline=time.monotonic()+max(.1,job['options']['pageWaitSeconds'])
        last_error='The page did not expose the requested content.'
        while True:
            self._check(job)
            read_started=time.monotonic()
            try:
                data=getattr(self.browser,method)()
            finally:
                metrics=job.setdefault('performance',{})
                key='snapshot_'+method
                metrics[key+'Seconds']=metrics.get(key+'Seconds',0)+time.monotonic()-read_started
                metrics[key+'Calls']=metrics.get(key+'Calls',0)+1
            if isinstance(data,dict):
                self._guard(data)
                if not data.get('error') and (not expected_id or str(data.get(field,'')).lower()==expected_id):return data
                last_error=data.get('error') or 'The loaded page does not match the queued source ID.'
            if time.monotonic()>=deadline:raise RuntimeError(safe_error(last_error))
            self._wait(job,min(.5,job['options']['pageDelaySeconds'] or .1))

    def _navigate(self, job, url):
        started=time.monotonic()
        try:
            return self.browser.navigate(url)
        finally:
            metrics=job.setdefault('performance',{})
            metrics['navigationDispatchSeconds']=metrics.get('navigationDispatchSeconds',0)+time.monotonic()-started
            metrics['navigationCalls']=metrics.get('navigationCalls',0)+1

    def _worker(self):
        while True:
            with self.condition:
                if self.closed:return
                job=next((j for j in self.jobs.values() if j['status']=='queued'),None)
                if not job:self.condition.wait(timeout=1);continue
                self.active_job=job['jobId'];job['status']='running';self._checkpoint(job)
            try:
                self._run(job)
            except CapturePaused:
                with self.lock:
                    job['status']='paused'
                    for post in job['posts']:
                        if post.get('status') in {'opening','capturing','downloading'}:post['status']='queued'
                    self._checkpoint(job)
            except CaptureAttention as exc:
                self._mark_attention(job,exc)
            except Exception as exc:
                if job.get('status')=='paused':self._checkpoint(job)
                elif any(signal in str(exc).lower() for signal in ('disposed','browser is unavailable','webview2')):
                    self._mark_attention(job,CaptureAttention('browser_unavailable','The app Browser became unavailable. Restore it before resuming.'))
                else:
                    job['status']='failed';job['reason']='collector_error';job['message']=safe_error(exc);self._checkpoint(job)
            finally:
                with self.condition:self.active_job=None;self.condition.notify_all()

    def _mark_attention(self,job,exc):
        with self.lock:
            if job.get('status')=='paused' and job.get('reason') in {'user_paused','user_stopped'}:
                self._checkpoint(job);return
            job['status']='needs_attention';job['reason']=exc.kind;job['message']=safe_error(exc)
            if exc.kind in {'rate_limit','access_or_rate_limit'}:
                delay=MINIMUM_RATE_COOLDOWN_SECONDS
                try:
                    requested=float(exc.retry_after or 0)
                    if math.isfinite(requested):delay=max(delay,requested)
                except (ValueError,TypeError):pass
                until=max(self.safety['siteCooldownUntil'],time.time()+delay)
                self.safety['siteCooldownUntil']=until
                self.safety['nextResumeAt']=datetime.fromtimestamp(until,timezone.utc).isoformat()
                job['cooldownUntil']=until;job['nextResumeAt']=self.safety['nextResumeAt']
                job['message']+=' A website-wide cooldown must finish before manual resume.'
            self._checkpoint(job)

    def _restore_proven_content(self,job):
        for row in job['posts']:
            prior=self._prior_post(job['profileId'],row['postId'])
            proof=prior.get('commentTreeProof') or row.get('commentTreeProof') or {}
            ids=sorted(str(c['id']) for c in prior.get('comments',[]) if c.get('id'))
            media=prior.get('media') or row.get('media',[])
            matches=proof.get('status')=='complete' and proof.get('idHash')==hashlib.sha256('\n'.join(ids).encode()).hexdigest()
            valid=bool(media) and all(m.get('status') in {'saved','downloaded'} and self._saved_exists(m) for m in media)
            media_complete=prior.get('coverage',{}).get('media',{}).get('status')=='complete'
            row['contentComplete']=bool(matches and valid and media_complete)
            if row['contentComplete']:row['commentTreeProof']=proof
            if row['contentComplete']:
                # A separate single-note job may have completed this archive
                # while the full profile queue was paused. Rehydrate its facts.
                row['media']=copy.deepcopy(media);row['commentCount']=len(prior.get('comments',[]))
                row['coverage']=copy.deepcopy(prior.get('coverage',{}))
                if prior.get('sourceMediaInventory'):row['sourceMediaInventory']=copy.deepcopy(prior['sourceMediaInventory'])
                if prior.get('coverage',{}).get('status')=='complete' and not self._comment_attachment_coverage(prior.get('comments',[]))['unarchivedAttachments']:
                    row['status']='complete'
            if row.get('status')=='complete' and (not row['contentComplete'] or self._comment_attachment_coverage(prior.get('comments',[]))['unarchivedAttachments']):
                row.update(status='partial',error='Retained completion requires missing or invalid local assets to be repaired.')

    def _run(self, job):
        if not job.get('downloadActivity'):self.ledger.begin(job)
        if job.get('scope')=='selected_posts':return self._run_selected(job)
        if job.get('scope')=='single_post':return self._run_single(job)
        if job.get('batchMode') and not job.get('selectedRetryOnly'):self._restore_proven_content(job)
        priority_ids=list(dict.fromkeys(job.get('priorityPostIds',[])))
        indexed={post['postId']:post for post in job['posts']}
        priority_ids=[post_id for post_id in priority_ids if post_id in indexed and indexed[post_id].get('status')!='complete']
        captured_this_run=set()
        if priority_ids:
            navigation=self.transient_posts.setdefault(job['jobId'],{})
            missing=[post_id for post_id in priority_ids if not navigation.get(post_id,{}).get('url')]
            if missing:
                # A paused note may still be open with its valid signed context.
                try:current=self.browser.post_snapshot()
                except Exception:current={}
                self._guard(current)
                current_id=current.get('postId') if isinstance(current,dict) and not current.get('error') else None
                if current_id in missing and len(missing)==1:
                    navigation[current_id]={'reuseCurrent':True}
                    missing.remove(current_id)
            if missing:self._enumerate(job,target_post_ids=missing)
            # Some retained notes disappear from bounded profile scrolling while
            # their original first-party link remains in the captured archive.
            # Try only that exact link; _capture_post verifies the loaded note
            # ID and author before any new content is stored.
            for post_id in missing:
                if navigation.get(post_id):continue
                retained_url=str(indexed[post_id].get('sourceUrl') or '')
                parsed=urlsplit(retained_url)
                match=POST_PATH.fullmatch(parsed.path)
                if (parsed.scheme=='https' and parsed.hostname in HOSTS and match
                        and match.group(1).lower()==post_id):
                    navigation[post_id]={'url':retained_url,'retainedSourceFallback':True}
            missing=[post_id for post_id in priority_ids if not navigation.get(post_id)]
            if missing:
                for post_id in missing:
                    indexed[post_id].update(status='needs_attention',error='This note link was not exposed by bounded normal profile discovery.')
                self._checkpoint(job)
            for post_id in priority_ids:
                if post_id in missing:continue
                self._check(job);self._capture_post(job,indexed[post_id]);captured_this_run.add(post_id)
                if post_id in job.get('priorityPostIds',[]):job['priorityPostIds'].remove(post_id)
                self._checkpoint(job)
            if missing:
                raise CaptureAttention('note_navigation_missing','Some requested notes were not exposed by bounded profile discovery. Captured notes were retained; inspect the missing source cards before retrying.')
        self._check(job)
        if job.get('selectedRetryOnly') and not (job['enumeration'].get('status')=='complete' and all(p.get('status')=='complete' for p in job['posts'])):
            job['status']='paused';job['phase']='selected_note_finished';job['reason']='selected_note_checkpoint'
            job['message']='Selected repair capture finished. Review coverage before resuming the remaining profile queue.'
            job['coverage']={**job.get('coverage',{}),'status':'partial','reason':'selected_note_checkpoint','enumerationComplete':job['enumeration'].get('status')=='complete'}
            self.store.store_profile(archive_safe(job['profile']),job['coverage']);self._checkpoint(job)
            return
        navigation=self.transient_posts.get(job['jobId'],{})
        missing_navigation=any(p.get('status')!='complete' and not (job.get('batchMode') and (job.get('assetBackfill') or p.get('contentComplete'))) and not navigation.get(p['postId']) for p in job['posts'])
        if job['enumeration'].get('status')!='complete' or missing_navigation:self._enumerate(job)
        self._check(job)
        profile=job['profile']
        if profile.get('avatar') and not profile.get('avatarMedia'):
            original=self.transient_posts.setdefault(job['jobId'],{}).get('_avatar',profile['avatar'])
            profile['avatarMedia']=self._download_one(job,original,self.media_root/job['profileId']/'avatar','avatar',0)
            self.store.store_profile(archive_safe(profile),job.get('coverage'))
        indexed={post['postId']:post for post in job['posts']}
        work_order=sorted(job['posts'],key=lambda post:(post.get('coverage',{}).get('media',{}).get('status')=='complete',bool(post.get('media')))) if job.get('batchMode') else job['posts']
        for post in work_order:
            self._check(job)
            if post.get('status')=='complete':
                self.ledger.record(job,'post_skipped',{'status':'complete','postId':post['postId']},kind='post');continue
            if post['postId'] in captured_this_run:continue
            if job.get('batchMode') and (job.get('assetBackfill') or post.get('contentComplete')):
                self.ledger.record(job,'post_skipped',{'status':'retained','postId':post['postId']},kind='post');continue
            self._capture_post(job,post)
            if post['postId'] in job.get('priorityPostIds',[]):
                job['priorityPostIds'].remove(post['postId']);self._checkpoint(job)
        if job.get('batchMode'):
            job['assetBackfill']=True;job['deferCommentAttachments']=False
            job['phase']='discussion_asset_backfill';job['message']='Post content pass finished. Completing local discussion images and avatars.';self._checkpoint(job)
            for post in job['posts']:
                self._check(job)
                if post.get('status')=='complete' or post.get('coverage',{}).get('media',{}).get('status')!='complete':continue
                self._backfill_post_assets(job,post)
        self._check(job)
        enumeration=job['enumeration']
        all_posts_complete=all(p.get('status')=='complete' for p in job['posts'])
        header_complete=bool(profile.get('name')) and (not profile.get('avatar') or (profile.get('avatarMedia',{}).get('status')=='saved' and self._saved_exists(profile['avatarMedia'])))
        complete=enumeration.get('status')=='complete' and all_posts_complete and header_complete
        job['coverage']={'status':'complete' if complete else 'partial','enumeration':copy.deepcopy(enumeration),'enumerationComplete':enumeration.get('status')=='complete','commentsComplete':all(p.get('coverage',{}).get('comments',{}).get('status')=='complete' and not p.get('coverage',{}).get('comments',{}).get('unarchivedAttachments') for p in job['posts']),'profileMetadata':{'status':'complete' if header_complete else 'partial'},'capturedPosts':sum(p.get('status') in {'complete','partial'} for p in job['posts']),'completePosts':sum(p.get('status')=='complete' for p in job['posts']),'failedPosts':sum(p.get('status') in {'failed','needs_attention'} for p in job['posts']),'reason':'' if complete else ('profile_header_partial' if not header_complete else 'post_capture_partial' if enumeration.get('status')=='complete' else enumeration.get('reason','enumeration_partial'))}
        job['status']='complete' if complete else 'partial';job['phase']='finished';job['reason']=job['coverage']['reason'];job['message']='Local profile reconstruction finished.' if complete else 'The local profile is available with partial coverage. See capture details for missing or limited items.'
        self.store.store_profile(archive_safe(profile),job['coverage']);self._checkpoint(job)

    def _enumerate(self, job, target_post_ids=None):
        targets=set(target_post_ids or [])
        prior_enumeration=copy.deepcopy(job['enumeration'])
        job['phase']='refreshing_note_links' if targets else 'enumerating'
        job['message']='Rediscovering the requested note link through normal profile cards.' if targets else 'Loading profile identity and all normally exposed note cards.';self._checkpoint(job)
        # Reuse a matching current profile, preserving the signed browser context.
        try:current=self.browser.profile_snapshot()
        except Exception:current={}
        self._guard(current)
        if not isinstance(current,dict) or current.get('error') or current.get('profileId')!=job['profileId']:
            self._navigate(job,self.transient_urls.get(job['jobId'],job['sourceUrl']))
            self._wait(job,job['options']['pageDelaySeconds'])
        rows={p['postId']:p for p in job['posts']}
        existing_profile=job.get('profile',{})
        transient=self.transient_posts.setdefault(job['jobId'],{})
        avatar_attempted=False
        observed_this_run=set()
        last_advance_progress=False
        stagnant=0;reason='scroll_budget';complete=False
        for round_index in range(job['options']['profileScrollBudget']+1):
            data=self._read(job,'profile_snapshot',job['profileId'])
            before_observed=len(observed_this_run)
            for card in data.get('posts',[]):
                if not isinstance(card,dict):continue
                post_id=str(card.get('postId','')).lower()
                url=str(card.get('url') or card.get('sourceUrl') or '')
                parsed=urlsplit(url)
                match=POST_PATH.fullmatch(parsed.path)
                if not match or parsed.hostname not in HOSTS or parsed.scheme!='https':continue
                matched_id=match.group(1).lower()
                if post_id and post_id!=matched_id:continue
                post_id=matched_id
                observed_this_run.add(post_id)
                if job['options']['maxPosts'] and post_id not in rows and len(rows)>=job['options']['maxPosts']:continue
                prior=rows.get(post_id,{'postId':post_id,'status':'queued','media':[],'commentCount':0})
                prior.update({'sourceUrl':'https://'+parsed.hostname+'/discovery/item/'+post_id,'title':str(card.get('title','')),'cover':clean_url(str(card.get('cover') or card.get('coverUrl') or '')),'mediaType':card.get('mediaType',''),'pinned':bool(card.get('pinned'))})
                for field in ('likes','likesCount'):
                    if card.get(field) not in (None,''):prior[field]=card[field]
                rows[post_id]=prior
                transient[post_id]={'url':url,'cover':card.get('cover') or card.get('coverUrl') or ''}
            profile={k:v for k,v in data.items() if k not in {'posts','error','guard','loading','atBottom','endMarker','feedBoundary'}}
            profile['profileId']=job['profileId'];profile['sourceUrl']=job['sourceUrl'];profile['profileUrl']=job['sourceUrl']
            if not profile.get('name'):profile['name']=profile.get('profileName') or existing_profile.get('name','')
            transient['_avatar']=data.get('avatar') or transient.get('_avatar','')
            profile['posts']=[{k:v for k,v in p.items() if k in {'postId','sourceUrl','title','cover','mediaType','pinned','likes','likesCount'}} for p in rows.values()]
            avatar_media=job.get('profile',{}).get('avatarMedia') or existing_profile.get('avatarMedia')
            if avatar_media:profile['avatarMedia']=avatar_media
            job['profile']=archive_safe(profile);job['posts']=list(rows.values())
            expected=parse_count(data.get('reportedPostCount'))
            # A restart begins at the profile top. Rediscovering retained IDs is
            # progress while catching up, not a reason to stall after eight steps.
            stagnant=stagnant+1 if len(observed_this_run)==before_observed and not data.get('loading') and not last_advance_progress else 0
            job['enumeration']={'status':'running','reason':'','rounds':round_index,'discovered':len(rows),'observedThisRun':len(observed_this_run),'expected':expected,'stagnantRounds':stagnant}
            self.store.store_profile(job['profile'],{'status':'capturing','enumeration':copy.deepcopy(job['enumeration'])});self._checkpoint(job)
            # The offline identity becomes usable during discovery, before the
            # full feed has been enumerated. One avatar request, no cover burst.
            if not avatar_attempted and data.get('avatar') and not avatar_media:
                avatar_attempted=True;job['phase']='saving_profile_avatar'
                job['profile']['avatarMedia']=self._download_one(job,data['avatar'],self.media_root/job['profileId']/'avatar','avatar',0)
                self.store.store_profile(job['profile'],{'status':'capturing','enumeration':copy.deepcopy(job['enumeration'])})
                job['phase']='enumerating';self._checkpoint(job)
            if targets and all(transient.get(post_id) for post_id in targets):
                reason='target_links_found';break
            if data.get('endMarker') and not (expected is not None and len(observed_this_run)<expected):
                complete=True;reason='end_marker';job['enumeration']['endMarker']=str(data['endMarker'])[:100];break
            if expected is not None and len(observed_this_run)>=expected:
                complete=True;reason='reported_count';break
            boundary=data.get('feedBoundary') or {}
            source_count=boundary.get('sourceNoteCount')
            if (boundary.get('hasMore') is False and boundary.get('fetching') is False
                    and boundary.get('profileId')==job['profileId']
                    and isinstance(source_count,int) and not isinstance(source_count,bool) and source_count>0
                    and len(observed_this_run)>=source_count and data.get('atBottom') is True
                    and not data.get('loading') and stagnant>=2):
                complete=True;reason='source_feed_exhausted'
                job['enumeration']['boundaryProof']=copy.deepcopy(boundary);break
            if job['options']['maxPosts'] and len(rows)>=job['options']['maxPosts']:
                reason='max_posts';break
            if stagnant>=job['options']['stallRounds']:
                reason='enumeration_stalled';break
            if round_index>=job['options']['profileScrollBudget']:break
            advanced=self.browser.advance_profile()
            self._guard(advanced)
            last_advance_progress=self._advance_progress(advanced)
            self._wait(job,job['options']['pageDelaySeconds'])
        if targets:
            # Finding a retry link proves navigation availability, not that the
            # whole retained profile has now been enumerated to its end.
            refresh={'status':'ready' if reason=='target_links_found' else 'partial','reason':reason,'rounds':round_index,'observedThisRun':len(observed_this_run),'requestedPostIds':sorted(targets),'foundPostIds':sorted(post_id for post_id in targets if transient.get(post_id))}
            job['enumeration']={**prior_enumeration,'discovered':len(rows),'navigationRefresh':refresh}
            self.store.store_profile(job['profile'],{'status':'capturing','enumeration':copy.deepcopy(job['enumeration'])})
        else:job['enumeration'].update(status='complete' if complete else 'partial',reason=reason)
        self._checkpoint(job)

    def _prior_post(self, profile_id, post_id):
        try:
            value=self.store.get_post(post_id)
            if isinstance(value,dict):
                value=value.get('post',value)
                if value.get('profileId',value.get('profile_id',profile_id))!=profile_id:return {}
                for descriptor in value.get('media',[]):
                    if descriptor.get('status')=='downloaded' and self._saved_exists(descriptor):descriptor['status']='saved'
                return value
        except (KeyError,ValueError,TypeError):pass
        return {}

    def _backfill_post_assets(self,job,row):
        """Save retained discussion assets without recrawling a captured note."""
        prior=self._prior_post(job['profileId'],row['postId'])
        if not prior:return
        comments=merge_comments(prior.get('comments',[]),[])
        media=copy.deepcopy(row.get('media') or prior.get('media') or [])
        coverage=copy.deepcopy(row.get('coverage') or prior.get('coverage') or {})
        tree=copy.deepcopy(coverage.get('comments',{}))
        proof=prior.get('commentTreeProof') or row.get('commentTreeProof') or {}
        ids=sorted(str(c['id']) for c in comments if c.get('id'))
        if (proof.get('status')=='complete' and proof.get('idHash')==hashlib.sha256('\n'.join(ids).encode()).hexdigest()
                and proof.get('captured')==len(comments) and tree.get('reason')=='paused'):
            # A pause in attachment work does not invalidate retained tree proof.
            tree={**tree,**copy.deepcopy(proof)}
        job['currentPostId']=row['postId']
        def checkpoint():
            prior['comments']=comments
            coverage['comments']={**tree,**self._comment_attachment_coverage(comments)}
            coverage['comments'].pop('attachmentsDeferred',None)
            complete=(coverage.get('media',{}).get('status')=='complete'
                      and tree.get('status')=='complete'
                      and not coverage['comments'].get('unarchivedAttachments'))
            coverage['status']='complete' if complete else 'partial'
            row.update(coverage=copy.deepcopy(coverage),status=coverage['status'],media=media,commentCount=len(comments))
            self.store.store_post(job['profileId'],archive_safe(prior),media,coverage)
            self._checkpoint(job)
        try:
            self._save_comment_attachments(job,comments,row,on_checkpoint=checkpoint)
        finally:
            checkpoint()

    def _single_saved_complete(self,job,row):
        if not job.get('profileId'):return False
        prior=self._prior_post(job['profileId'],row['postId'])
        row['media']=copy.deepcopy(prior.get('media',[]));row['commentCount']=len(prior.get('comments',[]))
        row['coverage']=copy.deepcopy(prior.get('coverage',{}))
        if prior.get('sourceMediaInventory'):row['sourceMediaInventory']=copy.deepcopy(prior['sourceMediaInventory'])
        self._restore_proven_content(job)
        attachments=self._comment_attachment_coverage(prior.get('comments',[]))
        return bool(row.get('contentComplete') and prior.get('coverage',{}).get('status')=='complete' and not attachments['unarchivedAttachments'])

    def _finish_single_reuse(self,job,row):
        with self.condition:
            self._check(job)
            row['status']='complete'
            self.ledger.record(job,'post_skipped',{'postId':row['postId'],'status':'complete'})
            job.update(status='complete',phase='finished',reason='',coverage=copy.deepcopy(row['coverage']),message='This post is already verified locally; saved media and complete comment evidence were reused.')
            self._checkpoint(job)

    def _run_single(self,job):
        """Process exactly one note without modifying full-profile evidence."""
        row=job['posts'][0];post_id=row['postId']
        self._check(job)
        job['currentPostId']=post_id;job['phase']='opening_note'
        job['message']='Opening the requested single post; no profile traversal.'
        self._checkpoint(job)
        if self._single_saved_complete(job,row):
            self._finish_single_reuse(job,row);return
        # Resolve the author from a normal loaded note. No fabricated author ID
        # or whole-profile discovery is needed, including after a relaunch.
        self._pacing(job,'post')
        transient=self.transient_posts.setdefault(job['jobId'],{}).get(post_id,{})
        current=self.browser.post_snapshot()
        if isinstance(current,dict):self._guard(current)
        if isinstance(current,dict) and not current.get('error') and str(current.get('postId','')).lower()==post_id:
            self._guard(current);data=current
        else:
            self._navigate(job,transient.get('url') or row['sourceUrl'])
            self._wait(job,job['options']['pageDelaySeconds'])
            data=self._read(job,'post_snapshot',post_id,'postId')
        author=str(data.get('profileId','')).lower()
        if not re.fullmatch(r'[a-f0-9]{24}',author):
            raise CaptureAttention('note_author_missing','The loaded note did not expose its author ID. Open the note in Browser, then Resume this post.')
        if job.get('profileId') and job['profileId']!=author:
            raise CaptureAttention('note_author_mismatch','The note author changed from the retained author ID. Inspect the source before retrying.')
        job['profileId']=author
        if self._single_saved_complete(job,row):
            self._finish_single_reuse(job,row);return
        job['deferCommentAttachments']=False
        self._capture_post(job,row,initial_snapshot=data)
        with self.condition:
            self._check(job)
            complete=row.get('status')=='complete'
            job.update(status='complete' if complete else 'partial',phase='finished',reason='' if complete else 'incomplete_post',coverage=copy.deepcopy(row.get('coverage',{})),message='Single post saved with complete media, comment text and exposed discussion attachments.' if complete else 'Single post retained. Some source comments or assets remain incomplete; inspect coverage before retrying.')
            self._checkpoint(job)

    def _capture_post(self, job, row, initial_snapshot=None):
        post_id=row['postId'];transient=self.transient_posts.get(job['jobId'],{}).get(post_id,{})
        if initial_snapshot is None:self._pacing(job,'post')
        self._check(job)
        row['status']='opening';job['phase']='opening_note';job['currentPostId']=post_id;job['message']='Opening note '+str(job['posts'].index(row)+1)+' of '+str(len(job['posts']))+'.';self._checkpoint(job)
        prior=self._prior_post(job['profileId'],post_id)
        comments=merge_comments(prior.get('comments',[]),[])
        media=copy.deepcopy(row.get('media') or prior.get('media') or [])
        media_coverage={'status':'pending'}
        comment_coverage={'status':'partial','reason':'pending','captured':len(comments)}
        data=None
        try:
            if initial_snapshot is not None:
                data=initial_snapshot
            elif transient.get('reuseCurrent'):
                data=self._read(job,'post_snapshot',post_id,'postId')
                transient.pop('reuseCurrent',None)
            else:
                self._navigate(job,transient.get('url') or row['sourceUrl'])
                self._wait(job,job['options']['pageDelaySeconds'])
                data=self._read(job,'post_snapshot',post_id,'postId')
            if transient.get('retainedSourceFallback') and data.get('profileId')!=job['profileId']:
                raise CaptureAttention('note_author_mismatch','The retained note link did not expose the expected author ID. Inspect the source before retrying.')
            if data.get('profileId') and data['profileId']!=job['profileId']:
                raise RuntimeError('The note author does not match the queued profile. This item was retained as a failed capture.')
            data['profileId']=job['profileId']
            initial_comments=list(data.get('comments',[]))
            comments[:]=merge_comments(comments,data.get('comments',[]))
            data['comments']=comments
            row['commentCount']=len(comments)
            comment_coverage={'status':'partial','reason':'pending','captured':len(comments),'expected':parse_count(data.get('reportedCommentCount',data.get('reportedComments')))}
            row['status']='downloading';job['phase']='saving_note_media';job['message']='Saving playable media for note '+str(job['posts'].index(row)+1)+' of '+str(len(job['posts']))+'.';self._checkpoint(job)
            candidates=self._media_candidates(data)
            row['sourceMediaInventory']={**(data.get('sourceMediaInventory') or {}),'discoveredImages':sum(c['kind']=='photo' for c in candidates),'discoveredVideos':sum(c['kind']=='video' for c in candidates)}
            initial={'status':'capturing','comments':comment_coverage,'media':{'status':'capturing','expected':len(candidates),'saved':sum(m.get('status')=='saved' and m.get('kind') in {'photo','video'} for m in media)}}
            self.store.store_post(job['profileId'],archive_safe(data),media,initial)
            for candidate in candidates:
                self._check(job)
                kind=candidate['kind'];index=candidate['sourceIndex']
                existing=next((m for m in media if m.get('kind')==kind and m.get('sourceIndex')==index and m.get('status')=='saved' and self._saved_exists(m)),None)
                if existing:
                    self.ledger.record(job,'reused',existing,kind,index);continue
                job['phase']='saving_note_media';job['message']='Saving '+kind+' '+str(index)+' for note '+str(job['posts'].index(row)+1)+' of '+str(len(job['posts']))+'.'
                descriptor=self._download_one(job,candidate.get('url',''),self.media_root/job['profileId']/post_id/(kind+'_'+str(index).zfill(3)),kind,index)
                media=[m for m in media if not (m.get('kind')==kind and m.get('sourceIndex')==index)]
                media.append(descriptor);row['media']=media
                current_coverage={'status':'capturing','comments':comment_coverage,'media':{'status':'capturing','expected':len(candidates),'saved':sum(m.get('status')=='saved' and m.get('kind') in {'photo','video'} for m in media)}}
                self.store.store_post(job['profileId'],archive_safe(data),media,current_coverage);self._checkpoint(job)
            cover=data.get('cover') or transient.get('cover') or row.get('cover','')
            if data.get('mediaType')=='video' and cover:
                if not any(m.get('kind')=='cover' and m.get('status')=='saved' and self._saved_exists(m) for m in media):
                    media.append(self._download_one(job,cover,self.media_root/job['profileId']/post_id/'cover','cover',0))
            inventory=data.get('sourceMediaInventory') or {}
            expected_media=inventory.get('images',0)+inventory.get('videos',0) if inventory.get('sourceDeclared') else len(candidates)
            saved_media=len({(m.get('kind'),m.get('sourceIndex')) for m in media if m.get('status')=='saved' and m.get('kind') in {'photo','video'} and self._saved_exists(m)})
            required_slots={(c['kind'],c['sourceIndex']) for c in candidates}
            valid_slots={(m.get('kind'),m.get('sourceIndex')) for m in media if m.get('status')=='saved' and self._saved_exists(m)}
            media_complete=bool(expected_media) and len(required_slots)>=expected_media and required_slots.issubset(valid_slots) and not data.get('videoUnavailable')
            media_coverage={'status':'complete' if media_complete else 'partial','expected':expected_media,'saved':saved_media,'reason':'' if media_complete else ('video_not_exposed' if data.get('videoUnavailable') else 'media_unavailable')}
            row['media']=media;row['coverage']={'status':'capturing','comments':comment_coverage,'media':media_coverage}
            self.store.store_post(job['profileId'],archive_safe(data),media,row['coverage']);self._checkpoint(job)
            # The local post is playable while its longer comment traversal runs.
            row['status']='capturing';job['phase']='capturing_comments';self._checkpoint(job)
            data['comments']=initial_comments
            comments,comment_coverage,data=self._collect_comments(job,row,data,comments,media,media_coverage)
            data['comments']=comments
            row['contentComplete']=media_complete and comment_coverage['status']=='complete'
            complete=media_complete and comment_coverage['status']=='complete' and not comment_coverage.get('unarchivedAttachments')
            coverage={'status':'complete' if complete else 'partial','comments':comment_coverage,'media':media_coverage}
            data['coverage']=coverage;row['coverage']=coverage;row['status']=coverage['status'];row['media']=media;row['commentCount']=len(comments);row['error']=''
            self.store.store_post(job['profileId'],archive_safe(data),media,coverage);self._checkpoint(job)
        except CapturePaused:
            if data is not None:
                data['comments']=comments;row['commentCount']=len(comments);row['media']=media
                paused_comments={'status':'partial','reason':'paused','captured':len(comments),'expected':parse_count(data.get('reportedCommentCount',data.get('reportedComments'))),**self._comment_attachment_coverage(comments)}
                row['coverage']={'status':'partial','comments':paused_comments,'media':media_coverage}
                self.store.store_post(job['profileId'],archive_safe(data),media,row['coverage'])
            raise
        except CaptureAttention as exc:
            row['status']='needs_attention';row['error']=safe_error(exc);row['media']=media
            if data is not None:
                data['comments']=comments;row['commentCount']=len(comments)
                self.store.store_post(job['profileId'],archive_safe(data),media,{'status':'partial','reason':exc.kind,'comments':{'status':'partial','captured':len(comments),**self._comment_attachment_coverage(comments)},'media':media_coverage})
            self._checkpoint(job);raise
        except Exception as exc:
            row['status']='failed';row['error']=safe_error(exc);row['media']=media
            if data is not None:
                data['comments']=comments;row['commentCount']=len(comments)
                self.store.store_post(job['profileId'],archive_safe(data),media,{'status':'partial','reason':'capture_error'})
            self._checkpoint(job)

    def _collect_comments(self,job,row,data,comments,media,media_coverage=None):
        stagnant=0;reason='scroll_budget';complete=False;rounds=0
        observed_this_run=set()
        last_advance_progress=False
        last_archive_refresh=0.0
        loading_since=None
        last_advance={}
        media_coverage=media_coverage or row.get('coverage',{}).get('media',{'status':'pending'})
        limit=job['options']['maxComments']
        expected=None
        for rounds in range(job['options']['commentScrollBudget']+1):
            self._check(job);before_observed=len(observed_this_run)
            observed=merge_comments([],data.get('comments',[]))
            observed_this_run.update(str(comment['id']) for comment in observed)
            self._remember_comment_attachment_urls(job,data.get('comments',[]))
            comments[:]=merge_comments(comments,data.get('comments',[]))
            if limit and len(comments)>limit:comments[:]=comments[:limit]
            expected=parse_count(data.get('reportedCommentCount',data.get('reportedComments')))
            row['commentCount']=len(comments);data['comments']=comments
            coverage={'status':'capturing','captured':len(comments),'observedThisRun':len(observed_this_run),'expected':expected,'rounds':rounds,**self._comment_attachment_coverage(comments)}
            coverage['diagnostics']={'unexpandedReplies':data.get('unexpandedReplies',0),'loading':bool(data.get('commentsLoading')),'newIds':len(observed_this_run)-before_observed,'stagnantRounds':stagnant,'blockedBranches':last_advance.get('blockedBranches',0)}
            job['phase']='capturing_comments';job['message']='Reading source comments: '+str(len(observed_this_run))+((' of '+str(expected)) if expected is not None else '')+' freshly observed; '+str(len(comments))+' retained for note '+str(job['posts'].index(row)+1)+' of '+str(len(job['posts']))+'.'
            row['coverage']={'status':'capturing','comments':coverage,'media':media_coverage}
            # Persist source evidence every round; refresh derived views periodically.
            now=time.monotonic()
            refresh_derived=rounds==0 or now-last_archive_refresh>=30
            self.store.store_post(job['profileId'],archive_safe(data),media,row['coverage'],refresh_derived=refresh_derived)
            if refresh_derived:last_archive_refresh=now
            self._checkpoint(job)
            if data.get('endMarker') and not data.get('commentsLoading') and not data.get('unexpandedReplies') and not (expected is not None and len(observed_this_run)<expected):
                complete=True;reason='end_marker';break
            if expected is not None and len(observed_this_run)>=expected and not data.get('commentsLoading') and not data.get('unexpandedReplies'):
                complete=True;reason='reported_count';break
            if limit and len(comments)>=limit:reason='max_comments';break
            stagnant=stagnant+1 if len(observed_this_run)==before_observed and not data.get('commentsLoading') and not last_advance_progress else 0
            if data.get('commentsLoading'):
                if loading_since is None:loading_since=time.monotonic()
                if time.monotonic()-loading_since>=job['options']['pageWaitSeconds']:
                    reason='comment_loading_timeout';break
            else:loading_since=None
            if stagnant>=job['options']['stallRounds']:
                reason='source_count_gap' if last_advance.get('atBottom') is True and not data.get('unexpandedReplies') and expected is not None and len(observed_this_run)<expected else 'comments_stalled'
                break
            if rounds>=job['options']['commentScrollBudget']:break
            result=self.browser.advance_comments();self._guard(result)
            last_advance=result if isinstance(result,dict) else {}
            if isinstance(result,dict) and result.get('error'):raise RuntimeError(safe_error(result['error']))
            last_advance_progress=self._advance_progress(result)
            if isinstance(result,dict) and result.get('blockedBranches') and not last_advance_progress and not result.get('loading'):
                reason='comment_branch_unresolved';break
            self._wait(job,job['options']['pageDelaySeconds'])
            data=self._read(job,'post_snapshot',row['postId'],'postId')
        result={'status':'complete' if complete else 'partial','reason':reason,'captured':len(comments),'observedThisRun':len(observed_this_run),'expected':expected,'rounds':rounds,'derivedIds':sum(c.get('idSource')=='derived' for c in comments),'unexpandedReplies':data.get('unexpandedReplies',0),'commentsLoading':bool(data.get('commentsLoading'))}
        result['diagnostics']={'atBottom':last_advance.get('atBottom'),
            'blockedBranches':last_advance.get('blockedBranches',0),
            'stagnantRounds':stagnant,'missingFromReported':max(0,expected-len(observed_this_run)) if expected is not None else None,
            'scrollContainer':last_advance.get('scrollContainer',''),
            'loadingTimedOut':reason=='comment_loading_timeout'}
        if complete:
            proof={**result,'observedAt':utc_now(),'idHash':hashlib.sha256('\n'.join(sorted(str(c['id']) for c in comments)).encode()).hexdigest()}
            row['commentTreeProof']=proof;data['commentTreeProof']=proof
        def checkpoint_attachments():
            data['comments']=comments
            current={**result,**self._comment_attachment_coverage(comments)}
            row['coverage']={'status':'capturing','comments':current,'media':media_coverage}
            self.store.store_post(job['profileId'],archive_safe(data),media,row['coverage']);self._checkpoint(job)
        # Walk and checkpoint all exposed comment branches first. The separate
        # serial media phase cannot hold up the visible comment enumeration.
        checkpoint_attachments()
        if not job.get('deferCommentAttachments'):
            self._save_comment_attachments(job,comments,row,on_checkpoint=checkpoint_attachments)
        else:
            result['attachmentsDeferred']=True
        return comments,{**result,**self._comment_attachment_coverage(comments)},data

    @staticmethod
    def _advance_progress(result):
        """A long already-loaded page may require many scrolls before new IDs."""
        if not isinstance(result,dict):return False
        try:expanded=float(result.get('clicked',0) or 0)>0
        except (ValueError,TypeError):expanded=False
        return result.get('scrolled') is True or expanded

    @staticmethod
    def _media_candidates(data):
        candidates=[]
        if isinstance(data.get('media'),list):
            candidates=[dict(m) for m in data['media'] if isinstance(m,dict) and m.get('kind') in {'photo','video'}]
        else:
            for i,image in enumerate(data.get('images',[]),1):
                candidates.append({'kind':'photo','url':image.get('url') if isinstance(image,dict) else image,'sourceIndex':i})
            videos=[v for v in data.get('videos',[]) if isinstance(v,dict)]
            videos.sort(key=ProfileCloneService._video_rank,reverse=True)
            if videos:candidates.append({'kind':'video','url':videos[0].get('url',''),'sourceIndex':1})
        # One video per note; sourceIndex identifies each independent carousel image.
        videos=[candidate for candidate in candidates if candidate['kind']=='video']
        selected_video=max(videos,key=ProfileCloneService._video_rank) if videos else None
        result=[];seen=set();has_video=False
        for i,candidate in enumerate(candidates,1):
            kind=candidate['kind'];index=int(candidate.get('sourceIndex') or i)
            if kind=='video':
                if has_video:continue
                candidate=dict(selected_video)
                index=1;has_video=True
            key=(kind,index)
            if key in seen:continue
            seen.add(key);candidate['sourceIndex']=index;result.append(candidate)
        return result

    @staticmethod
    def _video_rank(video):
        codec=str(video.get('codec') or video.get('vcodec') or '')
        compatible=bool(re.search(r'h[._-]?264|avc',codec,re.I))
        return (compatible,int(video.get('width',0) or 0)*int(video.get('height',0) or 0),float(video.get('bitrate',0) or 0))

    def _saved_exists(self,descriptor):
        raw=descriptor.get('path') or descriptor.get('file') or ''
        try:
            path=(self.base_dir/raw).resolve()
            if not path.is_relative_to(self.base_dir) or not path.is_file() or (descriptor.get('bytes') and path.stat().st_size!=descriptor['bytes']):return False
            expected=descriptor.get('sha256')
            if not isinstance(descriptor.get('bytes'),int) or descriptor['bytes']<=0 or not re.fullmatch(r'[a-fA-F0-9]{64}',str(expected or '')):return False
            stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,str(expected))
            if key in self.media_verification_cache:return True
            if expected:
                if not re.fullmatch(r'[a-fA-F0-9]{64}',str(expected)):return False
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=str(expected).lower():return False
            if len(self.media_verification_cache)>=5000:self.media_verification_cache.clear()
            self.media_verification_cache[key]=True
            return True
        except (OSError,TypeError,ValueError):return False

    def _download_one(self,job,url,destination,kind,index,byte_limit=None):
        self._check(job)
        fingerprint=self.ledger.fingerprint(url,kind)
        cached=self.ledger.cache.get(fingerprint,{})
        cap=2_000_000 if kind=='comment_emoji' else 5_000_000 if kind=='comment_image' else None
        within_budget=(cap is None or cached.get('bytes',0)<=cap) and (byte_limit is None or cached.get('bytes',0)<=byte_limit)
        if cached.get('status')=='saved' and within_budget and self._saved_exists(cached):
            descriptor={k:cached[k] for k in ('file','path','sha256','bytes','mime','status') if k in cached};descriptor.update(kind=kind,sourceIndex=index)
            self.ledger.record(job,'reused',descriptor,kind,index,fingerprint);return descriptor
        self._pacing(job,'media')
        job['phase']='saving_profile_avatar' if kind=='avatar' else 'saving_comment_attachments' if kind in {'comment_emoji','comment_image'} else 'saving_note_media';self._checkpoint(job)
        descriptor={'kind':kind,'sourceIndex':index,'status':'failed'}
        job['currentMediaBytes']=0;job['currentMediaKind']=kind
        transfer_started=time.monotonic()
        try:
            def notify(count):
                self._check(job)
                cap=2_000_000 if kind=='comment_emoji' else 5_000_000 if kind=='comment_image' else None
                if cap and count>cap:raise ValueError('Comment attachment exceeds its per-file size budget.')
                if byte_limit is not None and count>byte_limit:raise CommentAttachmentBudget(count)
                job['currentMediaBytes']=count
            saved=self.downloader(url,destination,'photo' if kind in {'avatar','cover','comment_emoji','comment_image'} else kind,notify)
            # The returned file is already committed. Check its declared size
            # without raising a pause that would lose the saved-file descriptor.
            cap=2_000_000 if kind=='comment_emoji' else 5_000_000 if kind=='comment_image' else None
            if cap and saved.get('bytes',0)>cap:raise ValueError('Comment attachment exceeds its per-file size budget.')
            if byte_limit is not None and saved.get('bytes',0)>byte_limit:raise CommentAttachmentBudget(saved['bytes'])
            path=(destination.parent/str(saved.get('file',''))).resolve()
            if not path.is_relative_to(self.base_dir):raise ValueError('Downloader returned a file outside the local archive.')
            relative=path.relative_to(self.base_dir).as_posix()
            descriptor.update(saved);descriptor.update(file=relative,path=relative,status='saved',kind=kind,sourceIndex=index)
        except CapturePaused:
            self.ledger.record(job,'interrupted',{'status':'interrupted'},kind,index,fingerprint);raise
        except CommentAttachmentBudget as exc:
            descriptor.update(error=safe_error(exc),errorCode='comment_attachment_budget',minimumBytes=exc.minimum_bytes)
        except Exception as exc:
            message=safe_error(exc)
            descriptor['error']=message
            retry_header=None
            try:retry_header=exc.headers.get('Retry-After')
            except (AttributeError,TypeError):pass
            unavailable_with_wait=getattr(exc,'code',None)==503 and bool(retry_header)
            if unavailable_with_wait or any(signal in message.lower() for signal in ('429','403','too many requests','captcha','challenge','rate limit','rate-limit','access denied')):
                retry_after=None
                try:
                    raw=retry_header
                    try:retry_after=float(raw)
                    except (ValueError,TypeError):
                        from email.utils import parsedate_to_datetime
                        retry_after=max(0,parsedate_to_datetime(raw).timestamp()-time.time())
                except Exception:pass
                self.ledger.record(job,'failed',{'status':'needs_attention'},kind,index,fingerprint)
                raise CaptureAttention('access_or_rate_limit','The site returned an access, verification or rate limit response. Resolve it in the app browser before resuming.',retry_after)
        metrics=job.setdefault('performance',{});metrics['transferSeconds']=metrics.get('transferSeconds',0)+time.monotonic()-transfer_started
        metrics['transferAttempts']=metrics.get('transferAttempts',0)+1
        metrics['successfulTransfers']=metrics.get('successfulTransfers',0)+int(descriptor.get('status')=='saved')
        self.ledger.record(job,('duplicate_content' if descriptor.get('reusedExisting') or descriptor.get('sha256') in self.ledger.known_hashes else 'downloaded') if descriptor.get('status')=='saved' else 'failed',descriptor,kind,index,fingerprint)
        return descriptor
