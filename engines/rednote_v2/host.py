"""The trusted Studio gets a small command surface, never Workspace wholesale."""
from urllib.parse import urlsplit, urlunsplit


def same_navigation_target(window, target):
    """Read the platform's cached URL without waiting on cleared loaded events.

    WebView2 assigning its current Source can be a no-op. Calling pywebview
    load_url first would clear its readiness events with no completion to reset
    them. Preserve query/fragment exactly; only normalize an empty root path.
    """
    try:
        gui=getattr(window,'gui',None)
        current=gui.get_current_url(window.uid) if gui else getattr(window,'real_url',None)
        def normalized(value):
            p=urlsplit(str(value or ''))
            return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',p.query,p.fragment))
        return bool(current) and normalized(current)==normalized(target)
    except Exception:return False


class StudioAPI:
    def __init__(self, workspace):
        self._workspace=workspace

    def command(self, action, payload=None):
        w=self._workspace
        page=w._controls
        if page is None:return {'error':'Studio window is unavailable.'}
        current=urlsplit(page.get_current_url() or '')
        if current.scheme!='http' or current.hostname!='127.0.0.1' or current.port!=w._port or current.path!='/master':
            return {'error':'This command is available only inside local Studio.'}
        p=payload if isinstance(payload,dict) else {}
        try:
            if action=='browse':return w.explorer_navigate(p['url']) if p.get('url') else w.open_explorer()
            if action=='embedded':return w.open_embedded_explorer(p.get('url'))
            if action=='browser-status':return w.explorer_status()
            if action=='miner':return w.open_browser()
            if action=='preview':return w.selective_preview()
            if action=='choices':return w.selective_choices(p.get('profileId',''),p.get('kept'),p.get('ignored'))
            if action=='single':return w.start_single_post(p.get('url',''))
            if action=='selected':
                if not isinstance(p.get('urls'),list) or not 1<=len(p['urls'])<=2000:return {'error':'Keep at least one post on this page.'}
                return w.start_selected_posts(p.get('profileUrl',''),p['urls'])
            if action=='resume':return w.resume_profile_clone(p.get('jobId',''))
            if action=='pause':return w.pause_profile_clone(p.get('jobId',''))
            if action=='stop':return w.stop_profile_clone(p.get('jobId',''))
            return {'error':'Unsupported Studio action.'}
        except Exception as exc:
            from profile_clone import safe_error
            return {'error':safe_error(exc)}


def enable_native_menus(window):
    """Isolate pywebview platform access; menus must not require debug mode."""
    try:
        native=window.native
        from System import Action
        def apply():
            core=native.browser.webview.CoreWebView2
            core.Settings.AreDefaultContextMenusEnabled=True
            core.Settings.AreBrowserAcceleratorKeysEnabled=True
            core.Settings.AreDevToolsEnabled=False
        from .browser_chrome import dispatch_native_ui
        dispatch_native_ui(native,apply)
    except Exception:
        # No site-packages patch or unsafe debug fallback. Ctrl+C works in the
        # local UI even on hosts that cannot expose this platform handle.
        try:
            import app
            app.v2_blueprint.store.event('host.context_menu','unavailable')
        except Exception:pass
        return False
    try:
        import app
        app.v2_blueprint.store.event('host.context_menu','enabled')
    except Exception:pass
    return True
