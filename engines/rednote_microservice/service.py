"""Own the existing collector through an injected browser port, without a UI."""
from pathlib import Path
from typing import Protocol
import threading


class BrowserPort(Protocol):
    def navigate(self, url: str): ...
    def profile_snapshot(self): ...
    def advance_profile(self): ...
    def post_snapshot(self): ...
    def advance_comments(self): ...


class UnavailableBrowser:
    def __getattr__(self, name):
        if name not in {'navigate', 'profile_snapshot', 'advance_profile', 'post_snapshot', 'advance_comments'}:
            raise AttributeError(name)
        def unavailable(*args, **kwargs):
            from profile_clone import CaptureAttention
            raise CaptureAttention('browser_unavailable', 'Attach an owned browser adapter before capture. Engine has no windows.')
        return unavailable


class EngineService:
    def __init__(self, base_dir, browser: BrowserPort | None = None, archive=None, downloader=None):
        self.base = Path(base_dir).resolve()
        self.browser = browser or UnavailableBrowser()
        self.browser_attached = browser is not None
        self.archive = archive
        self.downloader = downloader
        self._collector = None
        self._lock = threading.RLock()

    @property
    def collector(self):
        with self._lock:
            if self._collector is None:
                from profile_archive import ProfileArchiveStore
                from profile_clone import ProfileCloneService
                self._collector = ProfileCloneService(self.base, self.archive or ProfileArchiveStore(self.base), self.browser, self.downloader)
            return self._collector

    def command(self, action, payload=None):
        p = payload or {}
        if action in {'profile', 'single', 'selected', 'resume', 'retry'} and not self.browser_attached:
            raise ValueError('Browser adapter is disconnected. No capture job was started or resumed.')
        if action == 'status': return self.collector.status(p.get('jobId'))
        if action == 'pause': return self.collector.pause(p['jobId'])
        if action == 'stop': return self.collector.stop(p['jobId'])
        if action == 'resume': return self.collector.resume(p['jobId'])
        if action == 'retry': return self.collector.retry(p['jobId'], p.get('postId'))
        if action == 'single': return self.collector.start_single(p['url'], confirm_permission=True)
        if action == 'selected': return self.collector.start_selected(p['profileUrl'], p['urls'], confirm_permission=True)
        if action == 'profile': return self.collector.start(p['url'], p.get('options'), confirm_permission=True)
        raise ValueError('Unsupported engine command')

    def shutdown(self):
        with self._lock:
            if self._collector is not None:
                self._collector.shutdown()
