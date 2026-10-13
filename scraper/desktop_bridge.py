"""Harvest-owned browser workspace; does not attach to Codex or export sessions."""
import json
import re
import secrets
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from werkzeug.serving import make_server
import app
from observation_bridge import accept_observation, development_pack, clean_url
from browser_capture import NOTE_SCRIPT, SCROLL_SCRIPT, merge_comments, save_media

PROFILE = 'https://www.rednote.com/'

PROFILE_SCRIPT = r"""(() => {
 if(!['www.rednote.com','www.xiaohongshu.com'].includes(location.hostname)) return {error:'Open a RedNote profile first.'};
 const match=/\/user\/profile\/([a-f0-9]{24})/.exec(location.pathname);
 if(!match) return {error:'Navigate to a RedNote profile page first.'};
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const candidates=Array.from(document.querySelectorAll('h1,[class*=user-name],[class*=nickname],[class*=profile-name]')).filter(visible).map(e=>e.innerText.trim()).filter(Boolean);
 const title=(document.title||'').replace(/\s*[-|·]\s*(小红书|RedNote).*$/i,'').trim();
 let rows=Array.from(document.querySelectorAll('a[href]')).map(a=>{
   const href=a.getAttribute('href')||'';
   const m=/\/(?:discovery\/item|explore|user\/profile\/[a-f0-9]{24})\/([a-f0-9]{24})/.exec(href) || /\/(?:discovery\/item|explore)\/([a-f0-9]{24})/.exec(href);
   if(!m) return null;
   const title=(a.innerText||a.getAttribute('aria-label')||'').trim();
   if(title==='Pinned'||title==='置顶') return null;
   return {url:"https://" + location.hostname + "/discovery/item/" + m[1], title: title || ("Note " + m[1].slice(0, 8))};
 }).filter(Boolean);
 if(rows.length===0){
   const cards=Array.from(document.querySelectorAll('.note-item')).filter(visible);
   rows=cards.map((c, i)=>{
     const textNodes=Array.from(c.querySelectorAll('span, a, p')).map(e=>e.innerText?.trim()).filter(Boolean);
     const cardTitle=textNodes[0] || ('Post ' + (i+1));
     const img=c.querySelector('img[src*="rednotecdn.com"], img[src*="xhscdn.com"]');
     const m=img?.src ? img.src.match(/\/([a-f0-9]{32})\//) : null;
     const noteId=m ? m[1].slice(0, 24) : ('card_' + i);
     return {url:"https://" + location.hostname + "/discovery/item/" + noteId, title: cardTitle, coverUrl: img?.src || ''};
   });
 }
 return {profileUrl:location.origin+location.pathname,profileId:match[1],profileName:candidates[0]||title||match[1],posts:Array.from(new Map(rows.map(r=>[r.url,r])).values())};
})()"""

from source_toolbar import LIVE_TOOLBAR_SCRIPT, EXPLORER_TOOLBAR_SCRIPT


class BrowserToolbarAPI:
    """Only explicit browser/archive controls are exposed to source pages."""
    def __init__(self, workspace):
        self._workspace = workspace

    def dispatch(self, action, payload=None):
        payload = payload if isinstance(payload, dict) else {}
        workspace = self._workspace
        methods = {'back': workspace.back, 'forward': workspace.forward,
                   'reload': workspace.reload, 'capture': workspace.capture_post,
                   'scroll-comments': workspace.scroll_comments,
                   'focus-controls': workspace.focus_controls,
                   'open-explorer': workspace.open_explorer}
        try:
            if action == 'clone-state':
                return workspace._source_control_state()
            if action == 'pause-clone':
                state = workspace._source_control_state()
                job_id = state.get('activeJobId') or state.get('jobId')
                return workspace.pause_profile_clone(job_id) if job_id else {'error': 'No active profile clone.'}
            if action in methods:
                return methods[action]()
            if action == 'navigate':
                return workspace.navigate(payload.get('url', ''))
            if action == 'clone':
                return workspace.start_profile_clone(payload.get('url', ''), payload.get('options', {}))
            if action == 'download-open-media':
                if workspace._clone_busy():
                    return {'error': 'Pause the profile clone before saving media manually.'}
                data = workspace._source.evaluate_js(NOTE_SCRIPT)
                if not isinstance(data, dict) or data.get('error'):
                    return {'error': 'Open a post before saving its media.'}
                return workspace.download_open_media('video' if data.get('mediaType') == 'video' else 'photo')
            return {'error': 'Unsupported browser control.'}
        except (ValueError, RuntimeError) as exc:
            from profile_clone import safe_error
            return {'error': safe_error(exc)}

class ExplorerToolbarAPI:
    """Manual navigation only; never exposes mining controls to this page."""
    def __init__(self,workspace):
        self._workspace=workspace

    def dispatch(self,action,payload=None):
        payload=payload if isinstance(payload,dict) else {}
        workspace=self._workspace
        try:
            if action=='translate-visible':return workspace.translate_visible(payload)
            if action=='external-browser':return workspace.open_translation_browser(payload.get('browser','edge'))
            if action=='navigate':return workspace.embedded_navigate(payload.get('url',''))
            if action in {'back','forward','reload'}:return workspace.explorer_history(action)
            if action=='status':return workspace.explorer_status()
            if action=='focus-controls':return workspace.focus_controls()
            return {'error':'Unsupported browsing control.'}
        except Exception:
            return {'error':'Browsing control unavailable. Reopen Browse RedNote in Studio.'}


class CloneBrowserAdapter:
    """Capture in an app-owned hidden WebView; never attach to the desktop."""
    def __init__(self, workspace):
        self.workspace = workspace

    def navigate(self, url):
        target = str(url).strip()
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname not in app.REDNOTE_HOSTS:
            raise ValueError('Choose a RedNote HTTPS profile or post URL.')
        source = self.workspace._capture_window()
        # Keep a currently loaded signed page intact when starting its archive.
        from profile_clone import clean_url as canonical_url
        if canonical_url(source.get_current_url()) != canonical_url(target):
            source.load_url(target)
        return {'url': canonical_url(target)}

    def _evaluate(self, name):
        import profile_capture
        source = self.workspace._capture_window()
        try:return source.evaluate_js(getattr(profile_capture, name))
        except Exception as exc:
            from profile_clone import CaptureAttention
            raise CaptureAttention('browser_unavailable','The background capture browser closed or became unavailable. Resume after reopening the app.') from exc

    def profile_snapshot(self):
        return self._evaluate('PROFILE_SNAPSHOT_SCRIPT')

    def advance_profile(self):
        return self._evaluate('PROFILE_ADVANCE_SCRIPT')

    def post_snapshot(self):
        return self._evaluate('POST_SNAPSHOT_SCRIPT')

    def advance_comments(self):
        return self._evaluate('COMMENTS_ADVANCE_SCRIPT')


class Workspace:
    def __init__(self):
        self._source = None
        self._controls = None
        self._explorer = None
        self._research = None
        self._capture = None
        self._port = 0
        self._token = ''
        self._last_capture = 0
        self._lock = threading.Lock()
        self._posts = {}
        self._last_scroll = 0
        self._profile_batch_stop = threading.Event()
        self._profile_batch_lock = threading.Lock()
        self._profile_batch_job = None
        self._window_lock = threading.Lock()
        self._controls_factory = None
        self._source_factory = None
        self._explorer_factory = None
        self._research_factory = None
        self._capture_factory = None
        self._clone_service = None
        self._clone_service_lock = threading.RLock()
        self._clone_job_id = None
        self._closing = threading.Event()
        from rednote_v2.research_browser import ResearchBrowser
        from rednote_v2.research_jobs import ResearchJobs
        self._research_browser = ResearchBrowser(self)
        self._research_jobs = ResearchJobs(self._research_browser)

    def research_status(self):
        return self._research_browser.status()

    def research_search(self, query, cursor=0, limit=30):
        if self._research_jobs.active():
            raise ValueError('Pause the account-library scan before using live search in its research window.')
        return self._research_browser.search(query, cursor, limit)

    def research_library(self, kind, profile_url=None, cursor=0, limit=30):
        if self._research_jobs.active():
            raise ValueError('Pause the account-library scan before reading another library page.')
        return self._research_browser.library(kind, profile_url, cursor, limit)

    def research_post(self, url, cursor=0, limit=50):
        if self._research_jobs.active():
            raise ValueError('Pause the account-library scan before opening another post.')
        return self._research_browser.read_post(url, cursor, limit)

    def research_profile(self, url):
        if self._research_jobs.active():
            raise ValueError('Pause the account-library scan before opening a commenter profile.')
        return self._research_browser.read_profile(url)

    def research_job_start(self, kind, profile_url=None):
        return self._research_jobs.start(kind, profile_url)

    def research_job_status(self, job_id=None):
        return self._research_jobs.status(job_id)

    def research_job_pause(self, job_id):
        return self._research_jobs.pause(job_id)

    def research_job_resume(self, job_id):
        return self._research_jobs.resume(job_id)

    def _profile_cloner(self):
        with self._clone_service_lock:
            if self._clone_service is None:
                from rednote_engine import EngineService
                self._engine_service = EngineService(app.BASE_DIR, browser=CloneBrowserAdapter(self))
                self._clone_service = self._engine_service.collector
            return self._clone_service

    def _clone_busy(self):
        if self._clone_service is None:
            return False
        state = self._clone_service.status()
        if isinstance(state, dict) and state.get('activeJobId'):
            return True  # A pause is pending until the in-flight operation unwinds.
        rows = state.get('jobs', [state]) if isinstance(state, dict) else []
        return any(row.get('status') in {'queued', 'running', 'enumerating', 'collecting', 'waiting', 'cooldown'}
                   for row in rows if isinstance(row, dict))

    def _online_cooldown_error(self):
        if self._clone_service is None and (app.BASE_DIR / 'clone_queue.json').is_file():
            self._profile_cloner()  # Restore a persisted pause before any new request.
        if self._clone_service is None:
            return None
        remaining = self._clone_service.cooldown_remaining()
        if remaining > 0:
            return {'error': f'The website cooldown is active for another {int(remaining) + 1} seconds. Saved archives remain available.'}
        return None

    def _browser_mutation_error(self):
        cooldown = self._online_cooldown_error()
        if cooldown:
            return cooldown
        if self._clone_busy():
            return {'error': 'Pause the profile clone and wait for its current operation before changing this browser.'}
        if self._profile_batch_job and self._profile_batch_job.state in {'queued', 'running', 'cooldown'}:
            return {'error': 'Stop the profile batch and wait for its current operation before changing this browser.'}
        return None

    def _source_control_state(self):
        if self._clone_service is None and (app.BASE_DIR / 'clone_queue.json').is_file():
            self._profile_cloner()
        return self._clone_service.control_state() if self._clone_service is not None else {'busy': False, 'cooldownRemainingSeconds': 0}

    def start_profile_clone(self, url, options=None):
        with self._profile_batch_lock:
            self._reject_active_rednote_download()
            if self._profile_batch_job and self._profile_batch_job.state in {'queued', 'running', 'cooldown'}:
                raise ValueError('Pause the existing profile batch before starting a full clone.')
            result = self._profile_cloner().start(url, options=options, confirm_permission=True)
            self._clone_job_id = result.get('jobId')
            return result

    def profile_clone_status(self, job_id=None):
        return self._profile_cloner().status(job_id or None)

    def translate_visible(self,payload):
        if not isinstance(payload,dict):return {'error':'Choose visible text to translate.'}
        try:
            texts=payload.get('texts');target=payload.get('target','en')
            if not isinstance(texts,list) or not 1<=len(texts)<=12 or not all(isinstance(t,str) for t in texts):return {'error':'Choose up to 12 text sections.'}
            if target not in {'en','zh'}:return {'error':'Choose English or Chinese.'}
            return app.v2_blueprint.codex_engine.translate(texts,target,owner='browser-'+str(payload.get('mode','explorer')))
        except Exception as exc:
            return {'error':str(exc) if isinstance(exc,ValueError) else 'GPT translation unavailable. Connect Codex and choose a model in Studio.'}

    def open_translation_browser(self,browser='edge'):
        import os,subprocess
        if browser not in {'edge','chrome'}:return {'error':'Choose Edge or Chrome.'}
        page=self._explorer
        if page is None:return {'error':'Open Browse RedNote first.'}
        target=self._explorer_target(page.get_current_url())
        relative='Microsoft/Edge/Application/msedge.exe' if browser=='edge' else 'Google/Chrome/Application/chrome.exe'
        choices=[Path(os.environ.get(k,''))/relative for k in ['ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA']]
        executable=next((p for p in choices if p.is_file()),None)
        if executable is None:return {'error':browser.title()+' is not installed.'}
        subprocess.Popen([str(executable),'--new-window',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'message':'Opened in '+browser.title()+'. Right-click the page and choose Translate to English; use Show original to return to Chinese. This browser uses its own login.'}

    def selective_choices(self, profile_id, chosen=None, ignored=None):
        if not re.fullmatch(r'[a-f0-9]{24}', str(profile_id)):
            return {'error': 'Choose a profile first.'}
        path = app.BASE_DIR / 'selective-batch-choices.json'
        try:
            stored = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        except (ValueError, OSError):
            stored = {}
        if chosen is not None:
            def valid(values):
                return list(dict.fromkeys(v for v in (values or []) if isinstance(v, str) and re.fullmatch(r'[a-f0-9]{24}', v)))[:2000]
            kept = valid(chosen)
            stored[profile_id] = {'kept': kept, 'ignored': [v for v in valid(ignored) if v not in kept], 'updatedAt': datetime.now(timezone.utc).isoformat()}
            self._write_manifest(path, stored)
        return stored.get(profile_id, {'kept': [], 'ignored': []})

    def selective_preview(self):
        page=self._explorer
        if page is None:return {'error':'Open the optional embedded browser for selective previews, or select retained cards in Studio.'}
        import profile_capture
        result=page.evaluate_js(profile_capture.PROFILE_SNAPSHOT_SCRIPT)
        if not isinstance(result,dict) or result.get('error'):return {'error':'Open a RedNote profile in Browse RedNote, scroll its cards, then refresh the preview.'}
        if result.get('guard'):return {'error':'The website requires attention; no preview was collected.'}
        return {'profileId':result.get('profileId'),'sourceUrl':page.get_current_url(),'posts':result.get('posts',[]),'message':'Loaded profile cards only; scroll Browse RedNote and refresh to add more. No automatic discovery or downloads.'}

    def start_selected_posts(self,profile_url,urls):
        with self._profile_batch_lock:
            blocked=self._browser_mutation_error()
            if blocked:return blocked
            self._reject_active_rednote_download()
            ready=self._ready_source()
            if ready.get('error'):return ready
            result=self._profile_cloner().start_selected(profile_url,urls,confirm_permission=True)
            self._clone_job_id=result.get('jobId');return result

    def start_single_post(self, url):
        with self._profile_batch_lock:
            blocked=self._browser_mutation_error()
            if blocked:return blocked
            self._reject_active_rednote_download()
            ready=self._ready_source()
            if ready.get('error'):return ready
            result=self._profile_cloner().start_single(url,confirm_permission=True)
            self._clone_job_id=result.get('jobId')
            return result

    def pause_profile_clone(self, job_id):
        return self._profile_cloner().pause(job_id)

    def configure_profile_clone_pacing(self, job_id, post_seconds, media_seconds):
        return self._profile_cloner().configure_pacing(job_id, post_seconds, media_seconds)

    def automatic_profile_batch(self, job_id):
        with self._profile_batch_lock:
            self._reject_active_rednote_download()
            result = self._profile_cloner().automatic_batch(job_id)
            self._clone_job_id = job_id
            return result

    def stop_profile_clone(self, job_id):
        return self._profile_cloner().stop(job_id)

    def _recover_interrupted_clone(self):
        if self._source is not None:
            self._profile_cloner().recover_interrupted()

    def _ready_source(self):
        restored=self.open_browser()
        if restored.get('error'):return restored
        source=self._source
        deadline=time.monotonic()+15
        while source is not None and not source.events.loaded.is_set() and time.monotonic()<deadline:time.sleep(.25)
        if source is None or not source.events.loaded.is_set():return {'error':'Browser is still loading. Wait for it to load, then Resume mining.'}
        try:
            if not source.evaluate_js("Boolean(document && document.readyState !== 'loading')"):return {'error':'Browser is still loading. Try Resume mining again shortly.'}
        except Exception:
            self._source=None
            self.open_browser()
            return {'error':'A stale Browser window was recreated. Wait for it to load, then Resume mining.'}
        return {}

    def resume_profile_clone(self, job_id):
        ready=self._ready_source()
        if ready.get('error'):return ready
        with self._profile_batch_lock:
            self._reject_active_rednote_download()
            if self._profile_batch_job and self._profile_batch_job.state in {'queued', 'running', 'cooldown'}:
                raise ValueError('Stop the existing profile batch before resuming the full clone.')
            result = self._profile_cloner().resume(job_id)
            self._clone_job_id = job_id
            return result

    def retry_profile_clone(self, job_id, post_id=None, post_ids=None):
        with self._profile_batch_lock:
            self._reject_active_rednote_download()
            if self._profile_batch_job and self._profile_batch_job.state in {'queued', 'running', 'cooldown'}:
                raise ValueError('Stop the existing profile batch before retrying the full clone.')
            result = self._profile_cloner().retry(job_id, post_id or None, post_ids)
            self._clone_job_id = job_id
            return result

    def _reject_active_rednote_download(self):
        with app.jobs_lock:
            active = any(job.platform == 'xiaohongshu' and job.state in {'queued', 'running', 'cooldown'} for job in app.jobs.values())
        if active:
            raise ValueError('Wait for the existing RedNote download to finish before starting or resuming a full clone.')

    def shutdown(self):
        self._research_jobs.shutdown()
        if self._clone_service is not None:
            self._clone_service.shutdown()
        self._profile_batch_stop.set()

    def _request_shutdown(self):
        """Close this app after flushing its durable capture queue."""
        if self._closing.is_set():
            return {'message': 'App shutdown is already pending.'}
        self._closing.set()
        def close_owned_windows():
            # Let the authenticated HTTP response finish before closing WebView.
            time.sleep(0.25)
            self.shutdown()
            with self._window_lock:
                windows = (self._source, self._controls, self._explorer, self._research, self._capture)
                self._source_factory = self._controls_factory = self._explorer_factory = self._research_factory = self._capture_factory = None
            for window in windows:
                if window is not None:
                    try:
                        window.destroy()
                    except Exception:
                        pass
        threading.Thread(target=close_owned_windows, name='rednote-close', daemon=True).start()
        return {'message': 'Queue preserved; app shutdown requested.'}

    def capture_post(self):
        if self._clone_busy():
            return {'error': 'Pause the profile clone before capturing this browser manually.'}
        if not self._lock.acquire(blocking=False):
            return {'error':'Another capture is running.'}
        try:
            data=self._source.evaluate_js(NOTE_SCRIPT)
            if not isinstance(data,dict) or data.get('error'):
                return {'error':data.get('error','Capture returned no post') if isinstance(data,dict) else 'Capture returned no post'}
            post_id=data['postId']
            data['comments']=merge_comments(self._posts.get(post_id,{}).get('comments',[]),data.get('comments',[]))
            data['images']=list(dict.fromkeys(self._posts.get(post_id,{}).get('images',[])+data.get('images',[])))
            self._posts[post_id]=data
            from profile_clone import archive_safe
            safe_data=archive_safe(data)
            directory=app.BASE_DIR/'development-exports'
            directory.mkdir(exist_ok=True)
            now=datetime.now(timezone.utc).isoformat()
            record=accept_observation(dict(schema='noir.browser-observation/1',sourceUrl=safe_data['sourceUrl'],title=safe_data['title'],text=safe_data.get('text',''),comments=safe_data['comments'][:500],coverage='visible-comments' if safe_data['comments'] else 'visible-note',observedAt=now),app.BASE_DIR/'bridge-inbox')
            target=directory/f'post-{post_id}-{time.time_ns()}.json'
            # Raw media URLs stay in memory; persisted packages omit request queries.
            target.write_text(json.dumps({'pack':development_pack([record]),'comments':safe_data['comments'],'commentCoverage':'loaded-comments-only','reportedComments':safe_data.get('reportedComments',''),'imageCount':len(data['images']),'videoCount':len(data.get('videos',[]))},ensure_ascii=False,indent=2),encoding='utf-8')
            
            # Use the same immutable archive path as full-profile collection.
            # Parent IDs, source identities and previous local media survive recapture.
            from profile_archive import ProfileArchiveStore
            profile_id = data.get('profileId')
            archive_warning = None
            if profile_id:
                ProfileArchiveStore(app.BASE_DIR).store_post(profile_id, safe_data, [], {
                    'state': 'partial', 'comments': 'loaded-comments-only',
                    'reason': 'Manual capture saved currently loaded comments; media was not downloaded by this action.'})
            else:
                archive_warning = 'Saved raw capture only: the source did not expose a profile identity.'

            return {'postId':post_id,'comments':len(data['comments']),'images':len(data['images']),'videos':len(data.get('videos',[])),'commentCoverage':'loaded-comments-only','export':str(target),'archiveWarning':archive_warning}
        except Exception:
            return {'error':'Post capture failed. Open the post in its own page and wait for it to load.'}
        finally:
            self._lock.release()

    def scroll_comments(self):
        cooldown = self._online_cooldown_error()
        if cooldown:
            return cooldown
        if self._clone_busy():
            return {'error': 'Pause the profile clone before scrolling comments manually.'}
        if time.monotonic()-self._last_scroll<3:
            return {'error':'Wait 3 seconds between comment scroll requests.'}
        self._last_scroll=time.monotonic()
        try:return self._source.evaluate_js(SCROLL_SCRIPT)
        except Exception:return {'error':'Comment scrolling unavailable.'}

    def download_open_media(self,media_type='photo'):
        with self._profile_batch_lock:
            return self._download_open_media(media_type)

    def _download_open_media(self,media_type='photo'):
        cooldown = self._online_cooldown_error()
        if cooldown:
            return cooldown
        if self._clone_busy():
            return {'error': 'Pause the profile clone before starting another media download.'}
        result=self.capture_post()
        if result.get('error'):return result
        data=self._posts[result['postId']]
        urls=data['images'] if media_type=='photo' else [v['url'] for v in data.get('videos',[]) if v.get('url','').startswith('https://')]
        if media_type not in {'photo','video'} or not urls:
            return {'error':'No downloadable media URL is exposed. Blob-only video requires a separate supported download path.'}
        job=app.Job(id=__import__('uuid').uuid4().hex,url=data['sourceUrl'],platform='xiaohongshu',media_type=media_type)
        with app.jobs_lock:app.jobs[job.id]=job
        def work():
            # One serial media batch; no browser credentials are exported.
            with app.worker_lock,app.capture_process_guard():
                folder=app.BASE_DIR/'downloads'/f'post-{result["postId"]}-{job.id[:8]}'
                folder.mkdir(parents=True,exist_ok=True)
                manifest={'sourceUrl':data['sourceUrl'],'files':[],'failures':[]}
                try:
                    job.state='running';job.total_items=len(urls)
                    for index,url in enumerate(urls):
                        app.reserve_job_slot(job)
                        job.state='running';job.phase='downloading'
                        if index == 0:job.started_at=time.time()
                        job.current_item=index+1;job.note(f'Downloading media {index+1}/{len(urls)}')
                        try:
                            media=save_media(url,folder/f'media-{index+1}',media_type,lambda n:setattr(job,'downloaded_bytes',n))
                            manifest['files'].append(media);job.files.append(str(folder/media['file']))
                        except Exception:
                            manifest['failures'].append({'index':index+1,'reason':'Media unavailable, unsupported or outside size budget'})
                            break
                        job.progress=100*(index+1)/len(urls)
                    job.state='complete' if len(manifest['files'])==len(urls) else 'failed'
                    if job.state=='failed':job.error='Media batch incomplete; see manifest.'
                    job.note(f'Saved {len(manifest["files"])}/{len(urls)} media files')
                finally:
                    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        threading.Thread(target=work,daemon=True).start()
        return {'jobId':job.id,'message':'Open-page media queued.'}

    def navigate(self, url):
        blocked = self._browser_mutation_error()
        if blocked:
            return blocked
        try:
            target = str(url).strip()
            if not target:
                return {'error': 'URL cannot be empty.'}
            if not target.startswith(('http://', 'https://')):
                if target.startswith(('rednote.com', 'www.rednote.com', 'xiaohongshu.com', 'www.xiaohongshu.com')):
                    target = 'https://' + target
                elif re.match(r'^[a-f0-9]{24}$', target):
                    target = f'https://www.rednote.com/discovery/item/{target}'
                else:
                    target = f'https://www.rednote.com/search_result?keyword={target}'
            parsed = urlsplit(target)
            if parsed.scheme != 'https' or parsed.hostname not in app.REDNOTE_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
                return {'error': 'Choose a RedNote HTTPS URL or a search term.'}
            from rednote_v2.host import same_navigation_target
            if same_navigation_target(self._source,target):
                return {'message':'This page is already open. Use Reload to refresh it.'}
            self._source.load_url(target)
            return {'message': 'Opened in browser.', 'url': clean_url(target)}
        except Exception as e:
            return {'error': f'Navigation failed: {e}'}

    def current_page(self):
        try:
            raw = self._source.get_current_url() or ''
            from urllib.parse import urlsplit
            parsed = urlsplit(raw)
            return {'url': f'https://{parsed.hostname}{parsed.path}' if parsed.scheme == 'https' and parsed.hostname in app.REDNOTE_HOSTS else ''}
        except Exception:
            return {'url': ''}

    def eval_js(self, script):
        blocked = self._browser_mutation_error()
        if blocked:
            return blocked
        try:
            return {'result': self._source.evaluate_js(str(script))}
        except Exception as e:
            return {'error': f'Eval failed: {e}'}

    def profile_preview(self):
        """Read note links rendered in the current profile viewport only."""
        try:
            result=self._source.evaluate_js(PROFILE_SCRIPT)
            if not isinstance(result,dict) or result.get('error'):
                return result if isinstance(result,dict) else {'error':'Profile scan returned no data.'}
            result['posts']=result.get('posts',[])[:25]
            return result
        except Exception:return {'error':'Could not read visible profile cards. Open a profile and wait for it to load.'}

    @staticmethod
    def _safe_profile_name(value, profile_id):
        normalized=unicodedata.normalize('NFKC',str(value or '')).strip()
        normalized=re.sub(r'[<>:"/\\|?*\x00-\x1f]+',' ',normalized)
        normalized=re.sub(r'\s+',' ',normalized).strip(' .')[:64]
        if not normalized or normalized.upper() in {'CON','PRN','AUX','NUL'} or re.fullmatch(r'(?:COM|LPT)[1-9]',normalized.upper()):
            normalized='RedNote Profile'
        return f'{normalized} ({profile_id})'

    def start_profile_batch(self, profile_name, note_urls, confirm_permission=False):
        """Queue visible profile notes in order, with bounded downloads and disk manifests."""
        if confirm_permission is not True:
            raise ValueError('Confirm that you have permission to save this profile’s content.')
        cooldown = self._online_cooldown_error()
        if cooldown:
            raise ValueError(cooldown['error'])
        current=self.current_page().get('url','')
        match=re.search(r'/(?:user/profile|profile)/([a-f0-9]{24})',current)
        if not match:
            raise ValueError('Open a RedNote profile before starting a profile collection.')
        profile_id=match.group(1)
        if not isinstance(note_urls,list) or not note_urls:
            raise ValueError('Scan the visible profile cards and choose at least one post.')
        if len(note_urls)>25:
            raise ValueError('A profile batch is limited to 25 posts. Scan and run another batch after this one finishes.')
        normalized=[];seen=set()
        for value in note_urls:
            parsed=urlsplit(str(value))
            if parsed.scheme!='https' or parsed.hostname not in {'www.rednote.com','rednote.com','www.xiaohongshu.com','xiaohongshu.com'}:
                raise ValueError('Profile batches accept RedNote HTTPS post links only.')
            post_match=re.fullmatch(r'/(?:discovery/item|explore)/([a-f0-9]{24})/?',parsed.path)
            if not post_match:
                raise ValueError('One of the selected links is not a RedNote post.')
            url=f'https://www.rednote.com/discovery/item/{post_match.group(1)}'
            if url not in seen:seen.add(url);normalized.append(url)
        if not normalized:
            raise ValueError('No unique RedNote posts were selected.')
        if self._clone_busy():
            raise ValueError('Pause the full profile clone before starting a visible-card batch.')
        with self._profile_batch_lock:
            if self._profile_batch_job and self._profile_batch_job.state in {'queued','running','cooldown'}:
                raise ValueError('A profile collection is already running in this Harvest window.')
            label=self._safe_profile_name(profile_name,profile_id)
            batch_id=uuid.uuid4().hex[:10]
            batch_root=app.DOWNLOAD_DIR/'Profiles'/label
            target=batch_root/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+batch_id)
            target.mkdir(parents=True,exist_ok=False)
            manifest={'schema':'codex-red-note.profile-batch/1','profileName':label,'profileUrl':current,
                'startedAt':datetime.now(timezone.utc).isoformat(),'completedAt':None,'status':'queued',
                'pacingSeconds':60,'perMediaCooldownSeconds':app.MIN_SECONDS_BETWEEN_JOBS,
                'commentCoverage':'loaded-comments-only','queue':[{'order':i+1,'sourceUrl':url,'status':'queued'} for i,url in enumerate(normalized)],
                'posts':[],'folder':str(target)}
            manifest_path=target/'manifest.json'
            self._write_manifest(manifest_path,manifest)
            job=app.Job(id=batch_id,url=current,platform='xiaohongshu',media_type='profile_batch',state='queued')
            job.note(f'Queued {len(normalized)} visible profile posts for {label}')
            with app.jobs_lock:app.jobs[job.id]=job
            self._profile_batch_stop.clear()
            self._profile_batch_job=job
            thread=threading.Thread(target=self._run_profile_batch,args=(job,normalized,target,manifest,manifest_path),daemon=True)
            thread.start()
            return {'jobId':job.id,'queuedPosts':len(normalized),'profileFolder':str(batch_root),'batchFolder':str(target),'message':'Profile collection queued in visible-card order.'}

    @staticmethod
    def _write_manifest(path, value):
        temp=Path(str(path)+'.partial')
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)

    def _run_profile_batch(self, job, urls, target, manifest, manifest_path):
        previous_post_start=None
        try:
            job.state='running';job.phase='opening profile posts';job.started_at=time.time()
            manifest['status']='running';self._write_manifest(manifest_path,manifest)
            for post_index,url in enumerate(urls,1):
                manifest['queue'][post_index-1]['status']='waiting'
                self._write_manifest(manifest_path,manifest)
                if self._profile_batch_stop.is_set():
                    job.state='stopped';job.note('Stopped at your request. Completed files and the partial manifest were kept.');break
                if previous_post_start is not None:
                    remaining=60-(time.monotonic()-previous_post_start)
                    if remaining>0:
                        job.state='cooldown';job.phase='profile pacing'
                        while remaining>0 and not self._profile_batch_stop.wait(min(1,remaining)):
                            remaining=60-(time.monotonic()-previous_post_start)
                        if self._profile_batch_stop.is_set():
                            job.state='stopped';job.note('Stopped during the 60-second profile pacing interval.');break
                previous_post_start=time.monotonic()
                job.state='running';job.phase='opening post';job.note(f'Opening post {post_index} of {len(urls)}')
                manifest['queue'][post_index-1]['status']='opening'
                self._write_manifest(manifest_path,manifest)
                self._source.load_url(url)
                expected=urlsplit(url).path
                deadline=time.monotonic()+45
                while time.monotonic()<deadline and not self._profile_batch_stop.is_set():
                    current=self.current_page().get('url','')
                    if urlsplit(current).path==expected:
                        break
                    time.sleep(.5)
                if self._profile_batch_stop.is_set():
                    job.state='stopped';job.note('Stopped while opening a profile post.');break
                if urlsplit(self.current_page().get('url','')).path!=expected:
                    raise RuntimeError('Post page did not finish opening. Collection stopped without retrying.')
                time.sleep(3)
                capture=self.capture_post()
                if capture.get('error'):
                    raise RuntimeError(capture['error']+' Collection stopped without retrying.')
                data=self._posts.get(capture['postId'])
                if not data:
                    raise RuntimeError('The post capture was not available in memory. Collection stopped.')
                post_dir=target/'posts'/capture['postId'];media_dir=post_dir/'media'
                media_dir.mkdir(parents=True,exist_ok=True)
                comments=[{'text':row.get('text',''),'reply':bool(row.get('reply'))} for row in data.get('comments',[]) if row.get('text')]
                row={'postId':capture['postId'],'sourceUrl':data['sourceUrl'],'title':data.get('title',''),'text':data.get('text',''),
                    'capturedAt':datetime.now(timezone.utc).isoformat(),'commentCoverage':'loaded-comments-only',
                    'reportedCommentCount':data.get('reportedComments',''),'comments':comments,
                    'media':[],'failures':[],'status':'captured'}
                self._write_manifest(post_dir/'capture.json',{'schema':'codex-red-note.profile-post/1',**row})
                manifest['queue'][post_index-1]['status']='captured'
                manifest['queue'][post_index-1]['postId']=capture['postId']
                candidates=[]
                for media_url in data.get('images',[]):candidates.append(('photo',media_url))
                for video in data.get('videos',[]):
                    media_url=video.get('url','') if isinstance(video,dict) else ''
                    if media_url.startswith('https://'):candidates.append(('video',media_url))
                    elif media_url.startswith('blob:'):
                        row['failures'].append({'kind':'video','reason':'The page exposes this video only as a browser blob; this batch does not bypass that restriction.'})
                unique=[];media_seen=set()
                for kind,media_url in candidates:
                    if media_url not in media_seen:media_seen.add(media_url);unique.append((kind,media_url))
                job.total_items=(job.total_items or 0)+len(unique)
                for media_index,(kind,media_url) in enumerate(unique,1):
                    if self._profile_batch_stop.is_set():
                        job.state='stopped';job.note('Stopped between media files; completed downloads were kept.');break
                    job.phase='cooldown and media download';job.note(f'Post {post_index}/{len(urls)} · saving {kind} {media_index}/{len(unique)}')
                    try:
                        with app.worker_lock,app.capture_process_guard():
                            app.reserve_job_slot(job)
                            job.state='running';job.phase='downloading media'
                            base=job.downloaded_bytes
                            saved=save_media(media_url,media_dir/f'{kind}-{media_index:02d}',kind,lambda n:setattr(job,'downloaded_bytes',base+n))
                        saved['kind']=kind;row['media'].append(saved);job.files.append(str(media_dir/saved['file']))
                        job.current_item+=1;job.progress=min(100,100*job.current_item/max(1,job.total_items or 1))
                    except Exception as exc:
                        message=str(exc)
                        row['failures'].append({'kind':kind,'reason':'No retry: '+message[:180]})
                        job.note(f'{kind.title()} unavailable for post {post_index}; continuing without retry.')
                        lowered=message.lower()
                        if any(signal in lowered for signal in ('429','403','too many requests','captcha','challenge','rate limit','rate-limited')):
                            job.state='needs_attention';job.error='The site returned a challenge or access/rate-limit response. The batch stopped; resolve it in the browser and do not retry automatically.'
                            break
                row['status']='partial' if row['failures'] else 'captured'
                row['savedMedia']=len(row['media']);row['commentCount']=len(comments)
                manifest['queue'][post_index-1]['status']='needs_attention' if job.state=='needs_attention' else row['status']
                manifest['queue'][post_index-1]['postId']=row['postId']
                self._write_manifest(post_dir/'capture.json',{'schema':'codex-red-note.profile-post/1',**row})
                manifest['posts'].append({'postId':row['postId'],'sourceUrl':row['sourceUrl'],'title':row['title'],
                    'commentCount':row['commentCount'],'commentCoverage':row['commentCoverage'],'savedMedia':row['savedMedia'],
                    'status':row['status'],'folder':str(post_dir)})
                self._write_manifest(manifest_path,manifest)
                if job.state in {'needs_attention','stopped'}:break
            if job.state not in {'needs_attention','stopped','failed'}:
                job.state='complete';job.phase='complete';job.progress=100
                job.note(f'Profile collection complete: {len(manifest["posts"])} posts recorded.')
        except Exception as exc:
            job.state='failed';job.phase='stopped';job.error=str(exc)[:300];job.note('Collection stopped safely. See the profile manifest for completed items.')
        finally:
            for queued in manifest['queue']:
                if queued['status'] in {'queued','waiting','opening'}:
                    queued['status']='stopped' if job.state=='stopped' else 'not_attempted'
            if job.state in {'needs_attention','failed'} and 'post_index' in locals() and 1 <= post_index <= len(manifest['queue']):
                manifest['queue'][post_index-1]['status']=job.state
                if job.error:manifest['queue'][post_index-1]['note']=job.error[:180]
            manifest['status']=job.state
            manifest['completedAt']=datetime.now(timezone.utc).isoformat()
            manifest['summary']={'postsRecorded':len(manifest['posts']),'filesSaved':len(job.files),'bytesSaved':job.downloaded_bytes,'state':job.state,'error':job.error}
            self._write_manifest(manifest_path,manifest)

    def stop_profile_batch(self, job_id=''):
        job=self._profile_batch_job
        if not job or (job_id and job.id!=job_id):return {'error':'No matching profile collection is running.'}
        if job.state not in {'queued','running','cooldown'}:return {'error':'The profile collection has already stopped.'}
        self._profile_batch_stop.set()
        return {'jobId':job.id,'message':'Stop requested. The current network request will finish before the batch stops.'}

    def back(self):
        blocked = self._browser_mutation_error()
        if blocked:
            return blocked
        try:self._source.evaluate_js('history.back()');return {'message':'Going back.'}
        except Exception:return {'error':'Back navigation unavailable.'}

    def forward(self):
        blocked = self._browser_mutation_error()
        if blocked:
            return blocked
        try:self._source.evaluate_js('history.forward()');return {'message':'Going forward.'}
        except Exception:return {'error':'Forward navigation unavailable.'}

    def reload(self):
        blocked = self._browser_mutation_error()
        if blocked:
            return blocked
        try:self._source.evaluate_js('location.reload()');return {'message':'Reloading.'}
        except Exception:return {'error':'Reload unavailable.'}

    def download(self, url, media_type='video'):
        with self._profile_batch_lock:
            return self._download(url, media_type)

    def _download(self, url, media_type='video'):
        cooldown = self._online_cooldown_error()
        if cooldown:
            return cooldown
        if self._clone_busy():
            return {'error': 'Pause the profile clone before starting another media download.'}
        try:
            value=str(url).strip() or self._source.get_current_url()
            platform,normalized=app.normalized_source_url(value)
            if platform!='xiaohongshu' or media_type not in {'video','photo'}:
                raise ValueError('Choose a Rednote post and photo or video.')
            job=app.Job(id=__import__('uuid').uuid4().hex,url=normalized,platform=platform,media_type=media_type)
            with app.jobs_lock:
                app.jobs[job.id]=job
            destination=app.BASE_DIR/'downloads'
            threading.Thread(target=app.run_job,args=(job,destination,None),daemon=True).start()
            return {'jobId':job.id,'message':'Download queued. Files go to the downloads folder.'}
        except ValueError as error:
            return {'error':str(error)}
        except Exception:
            return {'error':'Unable to queue this link.'}

    def progress(self, job_id):
        job=app.jobs.get(str(job_id))
        return job.public() if job else {'error':'Download not found.'}

    def open_downloads(self):
        import os
        os.startfile(str(app.BASE_DIR/'downloads'))
        return {'message':'Downloads folder opened.'}

    def _restore_window(self, attribute, factory_attribute, label):
        import webview
        try:
            # Native evaluation can block, and close callbacks need this lock.
            # Probe outside it; EdgeChromium may return None instead of raising
            # when the native control has already been disposed.
            with self._window_lock:
                if self._closing.is_set():return {'error':'The app is closing.'}
                candidate=getattr(self,attribute,None)
                needs_probe=attribute in {'_source','_explorer'} and candidate is not None and candidate in webview.windows and candidate.events.loaded.is_set()
            if needs_probe:
                try:alive=candidate.evaluate_js('Boolean(document)') is True
                except Exception:alive=False
                if not alive:
                    with self._window_lock:
                        if getattr(self,attribute,None) is candidate:setattr(self,attribute,None)
                    try:candidate.destroy()
                    except Exception:pass
            with self._window_lock:
                window = getattr(self, attribute, None)
                if self._closing.is_set():return {'error':'The app is closing.'}
                if window is None or window not in webview.windows:
                    if attribute == '_source':
                        blocked = self._browser_mutation_error()
                        if blocked:
                            return blocked
                    factory = getattr(self, factory_attribute, None)
                    if factory is None:
                        return {'error': f'{label} window is not ready.'}
                    window = factory()
                    setattr(self, attribute, window)
            if self._closing.is_set():return {'error':'The app is closing.'}
            window.restore()
            window.show()
            return {'message': f'{label} focused.'}
        except Exception:
            return {'error': f'Could not restore {label}. Reopen it after the current operation finishes.'}

    def _window_health(self):
        import webview
        result={}
        with self._window_lock:
            for label,attribute in [('browser','_source'),('studio','_controls'),('explorer','_explorer'),('research','_research'),('capture','_capture')]:
                window=getattr(self,attribute,None)
                present=window is not None and window in webview.windows
                chrome=getattr(window,'_rednote_chrome',None) if present else None
                result[label]={'present':present,'loaded':bool(present and window.events.loaded.is_set()),
                               'nativeChromeReady':bool(chrome and chrome.ready),
                               'textSelectionEnabled':bool(present and window.text_select),
                               'easyDragEnabled':bool(present and window.easy_drag)}
        return result

    def _capture_window(self):
        import webview
        from profile_clone import CaptureAttention
        with self._window_lock:
            if self._closing.is_set():
                raise CaptureAttention('browser_unavailable', 'The app is closing.')
            window = self._capture
            if window is None or window not in webview.windows:
                if self._capture_factory is None:
                    raise CaptureAttention('browser_unavailable', 'The background capture browser is not ready.')
                window = self._capture_factory()
                self._capture = window
        return window

    def _window_closed(self, attribute, window):
        owned=False
        with self._window_lock:
            if getattr(self,attribute,None) is window:
                setattr(self,attribute,None)
                owned=True
        # Only the window used by the collector controls the collector pause.
        # Closing manual Mining must not stop a hidden background repair.
        if owned and attribute=='_capture' and not self._closing.is_set() and self._clone_service is not None:
            state=self._clone_service.control_state()
            job_id=state.get('activeJobId') or state.get('jobId')
            if job_id and state.get('busy'):
                self._clone_service.pause(job_id)

    def open_browser(self):
        return self._restore_window('_source', '_source_factory', 'Browser')

    @staticmethod
    def _explorer_target(value):
        target=str(value or '').strip()
        if not target or len(target)>4096:raise ValueError('Enter a RedNote address or search term (up to4096characters).')
        if not target.startswith(('http://','https://')):
            if target.startswith(('rednote.com','www.rednote.com','xiaohongshu.com','www.xiaohongshu.com','xhslink.com','www.xhslink.com')):
                target='https://'+target
            elif re.fullmatch(r'[a-fA-F0-9]{24}',target):
                target='https://www.rednote.com/discovery/item/'+target.lower()
            elif '://' in target:
                raise ValueError('Use a RedNote HTTPS address or a search term.')
            else:
                target='https://www.rednote.com/search_result?'+urlencode({'keyword':target})
        parsed=urlsplit(target)
        if parsed.scheme!='https' or parsed.hostname not in app.REDNOTE_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError('Use a RedNote/Xiaohongshu HTTPS address or a search term.')
        return target

    def open_explorer(self,url=None):
        blocked=self._online_cooldown_error()
        if blocked:return blocked
        return self.open_embedded_explorer(url)

    def open_embedded_explorer(self,url=None):
        try:target=self._explorer_target(url) if url is not None else None
        except ValueError as exc:return {'error':str(exc)}
        restored=self._restore_window('_explorer','_explorer_factory','Browse RedNote')
        if restored.get('error'):return restored
        if target is not None:return self.embedded_navigate(target)
        return {'message':'RedNote browser opened. Browse and Mining share the existing app login; mining stays on its own page.'}

    def explorer_navigate(self,url):
        return self.open_embedded_explorer(url)

    def embedded_navigate(self,url):
        blocked=self._online_cooldown_error()
        if blocked:return blocked
        try:
            target=self._explorer_target(url)
            explorer=self._explorer
            if explorer is None:return {'error':'Open Browse RedNote first.'}
            from rednote_v2.host import same_navigation_target
            if same_navigation_target(explorer,target):
                return {'message':'This page is already open. Use Reload to refresh it.'}
            explorer.load_url(target)
            return {'message':'Opened in Browse RedNote. Mining continues on its own page.'}
        except ValueError as exc:return {'error':str(exc)}
        except Exception:return {'error':'Browsing window unavailable. Reopen Browse RedNote in Studio.'}

    def explorer_history(self,action):
        blocked=self._online_cooldown_error()
        if blocked:return blocked
        scripts={'back':'history.back()','forward':'history.forward()','reload':'location.reload()'}
        if action not in scripts:return {'error':'Unsupported browsing navigation.'}
        try:
            if self._explorer is None:return {'error':'Open Browse RedNote first.'}
            self._explorer.evaluate_js(scripts[action])
            return {'message':{'back':'Back','forward':'Forward','reload':'Reloading'}[action]+'.'}
        except Exception:return {'error':'Browsing window unavailable. Reopen Browse RedNote in Studio.'}

    def explorer_language(self,payload):
        """App-owned language controls and safe counters; no arbitrary JS or source text."""
        page=self._explorer
        chrome=getattr(page,'_rednote_chrome',None) if page else None
        if not chrome or not chrome.ready:return {'error':'Browse window is not ready.'}
        action=payload.get('action','status')
        if action=='translate':
            if chrome.busy:return {'error':'Browse translation is already running.'}
            def translate_page():
                result=chrome.translate()
                if result.get('error'):chrome._message(result['error'])
            threading.Thread(target=translate_page,name='rednote-page-translate',daemon=True).start()
            return {'started':True}
        if action=='cancel':return chrome.cancel_translation()
        if action=='view':
            view=payload.get('view')
            if view not in {'Original','Translated'}:return {'error':'Choose Original or Translated.'}
            chrome.choose_language(view)
        elif action!='status':return {'error':'Unsupported page language action.'}
        from rednote_v2.page_translation import STATUS
        result=page.evaluate_js(STATUS)
        return {**result,'requestedView':payload.get('view') if action=='view' else chrome.view,'busy':chrome.busy,'auto':chrome.auto_page,'cacheEntries':len(chrome.page_cache),
            'lastError':chrome.translation_error,'phase':chrome.translation_phase} if isinstance(result,dict) else {'error':'Language status unavailable.'}

    def explorer_status(self):
        state={'window':'explorer','purpose':'manual_browsing','miningPageIndependent':True,
               'cooldownRemainingSeconds':self._profile_cloner().cooldown_remaining()}
        explorer=self._explorer
        if explorer is None:return {**state,'present':False,'loaded':False,'toolbarReady':False,'engine':'Edge WebView2','session':'app browser-session'}
        state.update(miningPageIndependent=explorer is not self._source,
                     windowId=str(explorer.uid),miningWindowId=str(self._source.uid) if self._source is not None else None)
        try:
            loaded=explorer.evaluate_js("document.readyState!=='loading'")
            chrome=getattr(explorer,'_rednote_chrome',None)
            mining_chrome=getattr(self._source,'_rednote_chrome',None) if self._source else None
            mining_geometry=mining_chrome.geometry() if mining_chrome else {}
            browsing_geometry=chrome.geometry() if chrome else {'toolbarReady':False}
            shared=bool(browsing_geometry.get('userDataFolder') and browsing_geometry.get('userDataFolder')==mining_geometry.get('userDataFolder') and browsing_geometry.get('profileName')==mining_geometry.get('profileName'))
            return {**state,'present':True,'loaded':bool(loaded),'engine':'Edge WebView2','sharedProfileVerified':shared,'miningChrome':mining_geometry,**browsing_geometry}
        except Exception:return {**state,'present':False,'loaded':False,'toolbarReady':False}

    def inject_explorer_toolbar(self):
        if self._explorer is not None:
            try:
                from rednote_v2.browser_chrome import install
                install(self,self._explorer,'explorer')
            except Exception as exc:
                app.v2_blueprint.store.event('host.native_chrome','unavailable',counts={'errorClass':type(exc).__name__})

    def capture(self):
        with self._lock:
            if time.monotonic() - self._last_capture < 45:
                return {'error':'Wait 45 seconds between captures.'}
            if not self._source:
                return {'error':'Source window unavailable'}
            # Only rendered links and their visible labels. No cookies, storage,
            # page internals, request interception or undisclosed media endpoints.
            script = """(() => {
              if (!['www.rednote.com','www.xiaohongshu.com'].includes(location.hostname)) return [];
              const rows = Array.from(document.querySelectorAll('a[href]')).filter(a => {
                const r=a.getBoundingClientRect();
                return r.width && r.height && r.bottom>0 && r.top<innerHeight;
              }).filter(a=>/\\/(?:discovery\\/item\\/[a-f0-9]{24}|user\\/profile\\/[a-f0-9]{24}\\/[a-f0-9]{24})/.test(a.getAttribute('href')||''))
              .map(a=>{const u=new URL(a.href);return {sourceUrl:u.origin+u.pathname,title:a.innerText.trim()};})
              .filter(a=>a.title && a.title!=='Pinned');
              return Array.from(new Map(rows.map(r=>[r.sourceUrl,r])).values());
            })()"""
            try:
                clean_url(self._source.get_current_url())
                rows = self._source.evaluate_js(script)
                now = datetime.now(timezone.utc).isoformat()
                self._last_capture = time.monotonic()
                records=[accept_observation(dict(row,schema='noir.browser-observation/1',coverage='profile-card',observedAt=now),app.BASE_DIR/'bridge-inbox') for row in rows]
                pack=development_pack(records)
                directory=app.BASE_DIR/'development-exports'
                directory.mkdir(exist_ok=True)
                target=directory/f'harvest-{time.time_ns()}.json'
                target.write_text(json.dumps(pack,ensure_ascii=False,indent=2),encoding='utf-8')
                return {'observations':len(records),'candidates':len(pack['candidates']),'export':str(target),'coverage':'profile-card'}
            except Exception:
                return {'error':'Capture unavailable. Sign in or finish loading the source page, then retry.'}

    def inject_live_toolbar(self):
        if not self._source:
            return
        try:
            from rednote_v2.browser_chrome import install
            install(self,self._source,'miner')
        except Exception as exc:
            app.v2_blueprint.store.event('host.native_chrome','unavailable',counts={'errorClass':type(exc).__name__})

    def focus_controls(self):
        return self._restore_window('_controls', '_controls_factory', 'Studio')

    def restore_windows(self):
        browser = self.open_browser()
        studio = self.focus_controls()
        explorer = self.open_explorer()
        errors = [result['error'] for result in (browser, studio, explorer) if 'error' in result]
        return {'error': '; '.join(errors)} if errors else {'message': 'Mining Browser, Studio and Browse RedNote restored.'}

def main():
    import sys
    import logging
    import webview
    smoke='--smoke-test' in sys.argv
    from desktop_session import claim_session, restore_existing_session
    session_guard = claim_session(app.BASE_DIR) if not smoke else True
    if session_guard is None:
        restore_existing_session(app.BASE_DIR)
        return
    if hasattr(app,'v2_blueprint'):
        app.v2_blueprint.start_owned_workers()
    logging.basicConfig(filename=str(app.BASE_DIR/'browser-startup.log'),level=logging.WARNING)
    workspace=Workspace()
    app.app.browser_workspace=workspace
    app.app.bridge_token=secrets.token_urlsafe(32)

    @app.app.get('/master')
    def master_view():
        from flask import send_file
        import sys
        bundle=Path(getattr(sys,'_MEIPASS',app.BASE_DIR))/'static'/'v2'/'index.html'
        return send_file(bundle)

    server=make_server('127.0.0.1',0,app.app,threaded=True)
    (app.BASE_DIR/'harvest-host.json').write_text(json.dumps({'schema':'noir.harvest-host/1','port':server.server_port,'browserControl':True,'bridgeToken':app.app.bridge_token}),encoding='utf-8')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    workspace._port = server.server_port
    workspace._token = app.app.bridge_token

    def create_controls():
        from rednote_v2.host import StudioAPI,enable_native_menus
        controls=webview.create_window('RED NOTE — Studio 2.0',url=f'http://127.0.0.1:{server.server_port}/master',js_api=StudioAPI(workspace),width=1280,height=860,min_size=(900,650),background_color='#101012',text_select=True,easy_drag=False)
        controls.events.loaded += lambda:enable_native_menus(controls)
        controls.events.closed += lambda: workspace._window_closed('_controls',controls)
        return controls

    def create_browser():
        browser = webview.create_window('RedNote — Mining',url=None if smoke else PROFILE,html='<p>Native startup test</p>' if smoke else None,width=1100,height=850,min_size=(760,580),background_color='#111014',js_api=BrowserToolbarAPI(workspace),text_select=True,easy_drag=False)
        browser.events.loaded += lambda: workspace.inject_live_toolbar()
        from rednote_v2.host import enable_native_menus
        browser.events.loaded += lambda:enable_native_menus(browser)
        if not smoke:
            browser.events.loaded += lambda: workspace._recover_interrupted_clone()
        browser.events.closed += lambda: workspace._window_closed('_source',browser)
        return browser

    def create_explorer():
        # A restored app must not issue an extra website request during a
        # retained cooldown. The local placeholder still has address controls.
        cooldown=workspace._online_cooldown_error() if not smoke else None
        placeholder='<div style="padding:100px 32px;color:#aaaab1;font:16px Segoe UI">Website requested a pause. Wait for the cooldown, then use the address bar to browse RedNote.</div>' if cooldown else None
        explorer=webview.create_window('RedNote — Browse (independent of mining)',url=None if smoke or cooldown else PROFILE,html='<p>Independent browsing startup</p>' if smoke else placeholder,
            width=1120,height=850,min_size=(760,580),background_color='#0E0E11',js_api=ExplorerToolbarAPI(workspace),text_select=True,easy_drag=False)
        explorer.events.loaded += lambda:workspace.inject_explorer_toolbar()
        from rednote_v2.host import enable_native_menus
        explorer.events.loaded += lambda:enable_native_menus(explorer)
        explorer.events.closed += lambda:workspace._window_closed('_explorer',explorer)
        return explorer

    def create_research():
        # Created only when an MCP research read needs the site. The WebView
        # shares the app login but never restores, shows, or focuses a window.
        research=webview.create_window('RedNote — Background Research',html='<html><body></body></html>',
            width=1120,height=850,hidden=True,focus=False,background_color='#0E0E11',
            text_select=False,easy_drag=False)
        research.events.closed += lambda:workspace._window_closed('_research',research)
        return research

    def create_capture():
        # Dedicated capture surface shares the app's signed-in WebView session.
        # It never restores, shows, or focuses the user's foreground window.
        capture=webview.create_window('RedNote — Background Capture',html='<html><body></body></html>',
            width=1120,height=850,hidden=True,focus=False,background_color='#0E0E11',
            text_select=False,easy_drag=False)
        capture.events.closed += lambda:workspace._window_closed('_capture',capture)
        return capture

    with workspace._window_lock:
        workspace._controls_factory = create_controls
        workspace._source_factory = create_browser
        workspace._explorer_factory = create_explorer
        workspace._research_factory = create_research
        workspace._capture_factory = create_capture
        controls = workspace._controls = create_controls()
        workspace._source = create_browser()
        workspace._explorer = create_explorer()

    def live_watchdog():
        time.sleep(1.5)
        workspace.inject_live_toolbar()
        workspace.inject_explorer_toolbar()
        while True:
            time.sleep(3)
            try:
                workspace.inject_live_toolbar()
                workspace.inject_explorer_toolbar()
            except Exception:
                pass

    if not smoke:
        threading.Thread(target=live_watchdog, daemon=True).start()
    def startup_check():
        if not smoke:
            return
        ready=controls.events.loaded.wait(20)
        source_ready=workspace._source.events.loaded.wait(20)
        explorer_ready=workspace._explorer.events.loaded.wait(20)
        (app.BASE_DIR/'native-smoke-result.json').write_text(json.dumps({'controlsReady':bool(ready),'sourceReady':bool(source_ready),'explorerReady':bool(explorer_ready),'windows':workspace._window_health(),'apiMethods':[name for name in dir(workspace) if not name.startswith('_')],'remoteRequests':False}))
        workspace._source.destroy()
        workspace._explorer.destroy()
        controls.destroy()
    try:
        webview.start(startup_check,gui='edgechromium',private_mode=smoke,storage_path=str(app.BASE_DIR/('smoke-session' if smoke else 'browser-session')),icon=str(Path(getattr(sys,'_MEIPASS',app.BASE_DIR))/'static'/'branding'/'rednote-app-icon-2026-10-03.ico'))
    finally:
        workspace.shutdown()
        if hasattr(app,'v2_blueprint'):
            app.v2_blueprint.store.stop.set()
        server.shutdown()

if __name__=='__main__':
    import sys
    if '--gallery-dl-worker' in sys.argv:
        sys.argv.remove('--gallery-dl-worker')
        import gallery_dl
        gallery_dl.main()
    elif '--verify-archive' in sys.argv:
        sys.argv.remove('--verify-archive')
        import archive_verify
        archive_verify.main()
    else:
        main()

