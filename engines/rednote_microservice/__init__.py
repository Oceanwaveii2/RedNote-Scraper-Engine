"""UI-independent acquisition core. No desktop, webview or Studio imports."""
from .service import EngineService, BrowserPort, UnavailableBrowser

__all__ = ['EngineService', 'BrowserPort', 'UnavailableBrowser']
