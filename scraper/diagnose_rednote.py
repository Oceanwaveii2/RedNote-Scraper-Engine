"""One-request extractor diagnosis. URL enters via stdin; never print page state."""
import sys
import json
import re
from app import RedNoteIE, normalized_source_url
from yt_dlp import YoutubeDL
from yt_dlp.utils import js_to_json

class Quiet:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass

_, url = normalized_source_url(sys.stdin.read().strip())
with YoutubeDL({'quiet': True, 'logger': Quiet(), 'retries': 0, 'extractor_retries': 0}) as ydl:
    ie = RedNoteIE(ydl)
    note_id = ie._match_id(url)
    page = ie._download_webpage(url, note_id)
    state = ie._search_json(r'window\.__INITIAL_STATE__\s*=', page, 'state', note_id,
                           transform_source=js_to_json, fatal=False, default={})
    rows = (state.get('note') or {}).get('noteDetailMap') or {}
    title = re.search(r'<title>(.*?)</title>', page, re.S)
    print(json.dumps({'noteId': note_id, 'pageBytes': len(page),
                     'pageTitle': title.group(1) if title else None,
                     'statePresent': bool(state), 'noteMapCount': len(rows),
                     'requestedNotePresent': note_id in rows,
                     'notePayloadPresent': bool((rows.get(note_id) or {}).get('note')),
                     'embeddedVideoMarker': 'masterUrl' in page,
                     'challengeMarker': bool(re.search(r'captcha|访问频繁|验证后|access denied', page, re.I))}))
