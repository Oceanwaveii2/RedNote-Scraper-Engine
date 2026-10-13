"""Read RedNote in an app-owned, non-activating research WebView.

The research window is distinct from manual Browse and the mining browser.
Only fixed, read-only page scripts are evaluated. A page gate is a pause, not
an invitation to change authentication or try a different transport.
"""

from __future__ import annotations

import threading
import time
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import app
from browser_capture import NOTE_SCRIPT, SCROLL_SCRIPT, merge_comments
from profile_capture import PROFILE_SNAPSHOT_SCRIPT


class ListCheckpointStore:
    """Append-only discovered items with indexed cursors and resumable list state."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lists (
                    list_key TEXT PRIMARY KEY, end_marker TEXT NOT NULL DEFAULT '',
                    stalled INTEGER NOT NULL DEFAULT 0, active_url TEXT,
                    observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items (
                    list_key TEXT NOT NULL REFERENCES lists(list_key),
                    ordinal INTEGER NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, observed_at TEXT NOT NULL,
                    PRIMARY KEY(list_key,ordinal), UNIQUE(list_key,kind,item_id));
                CREATE INDEX IF NOT EXISTS items_page ON items(list_key,ordinal);
                CREATE TRIGGER IF NOT EXISTS items_no_update BEFORE UPDATE ON items
                    BEGIN SELECT RAISE(ABORT,'Discovered items are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS items_no_delete BEFORE DELETE ON items
                    BEGIN SELECT RAISE(ABORT,'Discovered items are append-only'); END;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            yield db
            db.commit()
        finally:
            db.close()

    def state(self, key, legacy=None):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM lists WHERE list_key=?', (key,)).fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                db.execute('INSERT INTO lists VALUES (?,?,?,?,?)',
                           (key, '', 0, None, now))
                if legacy:
                    if legacy.get('key') != key or legacy.get('category') != 'list':
                        raise ValueError('Legacy checkpoint identity mismatch')
                    for item in legacy.get('items', []):
                        if isinstance(item, dict) and item.get('kind') and item.get('id'):
                            self._append(db, key, item)
                    db.execute('UPDATE lists SET end_marker=?,stalled=?,active_url=? WHERE list_key=?',
                               (legacy.get('endMarker') or '', int(bool(legacy.get('stalled'))),
                                legacy.get('activeUrl'), key))
                db.commit()
                row = db.execute('SELECT * FROM lists WHERE list_key=?', (key,)).fetchone()
            count = db.execute('SELECT count(*) FROM items WHERE list_key=?', (key,)).fetchone()[0]
            return {'count': count, 'endMarker': row['end_marker'],
                    'stalled': bool(row['stalled']), 'activeUrl': row['active_url']}

    def exists(self, key):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM lists WHERE list_key=?', (key,)).fetchone() is not None

    @staticmethod
    def _append(db, key, item):
        ordinal = db.execute('SELECT coalesce(max(ordinal)+1,0) FROM items WHERE list_key=?', (key,)).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?)',
                   (key, ordinal, item['kind'], item['id'],
                    json.dumps(item, ensure_ascii=False), item.get('observedAt') or datetime.now(timezone.utc).isoformat()))

    def append(self, key, items, end_marker, stalled=False, active_url=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in items:
                self._append(db, key, item)
            db.execute('UPDATE lists SET end_marker=?,stalled=?,active_url=coalesce(?,active_url),observed_at=? WHERE list_key=?',
                       (end_marker, int(stalled), active_url, datetime.now(timezone.utc).isoformat(), key))
            count = db.execute('SELECT count(*) FROM items WHERE list_key=?', (key,)).fetchone()[0]
            db.commit()
            return count

    def page(self, key, cursor, limit):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload_json FROM items WHERE list_key=? ORDER BY ordinal LIMIT ? OFFSET ?',
                (key, limit, cursor))]


LIST_SCRIPT = r"""(() => {
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 if(/\/(?:login|auth|captcha|verify)(?:\/|$)/i.test(location.pathname))return {guard:'login'};
 if(Array.from(document.querySelectorAll('[class*="captcha"],[id*="captcha"],.verify-container,.security-check')).some(visible))return {guard:'challenge'};
 if(Array.from(document.querySelectorAll('.login-container,.login-modal,.login-box')).some(visible))return {guard:'login'};
 const error=Array.from(document.querySelectorAll('.error-page,.error-container,.access-denied')).find(visible);
 if(error&&/429|频繁|稍后|too many|rate.limit/i.test(error.innerText||''))return {guard:'rate_limit'};
 if(!/^(?:www\.)?(?:rednote|xiaohongshu)\.com$/.test(location.hostname))return {error:'RedNote page unavailable'};
 const found=new Map();
 for(const a of document.querySelectorAll('a[href]')){
   if(!visible(a))continue;
   let u;try{u=new URL(a.getAttribute('href'),location.href);}catch(_){continue;}
   if(u.origin!==location.origin)continue;
   const p=/\/(?:explore|discovery\/item)\/([a-f0-9]{24})(?:\/|$)/i.exec(u.pathname)
     ||/\/user\/profile\/[a-f0-9]{24}\/([a-f0-9]{24})(?:\/|$)/i.exec(u.pathname);
   const profile=/\/user\/profile\/([a-f0-9]{24})(?:\/|$)/i.exec(u.pathname);
   if(!p&&!profile)continue;
   const kind=p?'post':'profile',id=(p||profile)[1].toLowerCase();
   const card=a.closest('.note-item,.note-card,.feeds-page,.user-item,.user-card,[class*="search-result"]')||a;
   const title=(a.getAttribute('aria-label')||card.querySelector('.title,.note-title,.user-name,.nickname')?.innerText||a.innerText||'').trim().slice(0,240);
   const snippet=(card.innerText||'').trim().slice(0,600);
   const key=kind+':'+id;
   if(!found.has(key))found.set(key,{kind,id,url:u.origin+u.pathname,title,snippet});
 }
 const endings=Array.from(document.querySelectorAll('.end,.no-more,[class*="no-more"],[class*="end-tip"]')).filter(visible).map(e=>(e.innerText||'').trim());
 const endMarker=endings.find(t=>/没有更多|到底了|全部加载|暂无|no more|the end|all loaded/i.test(t))||'';
 return {url:location.origin+location.pathname,items:Array.from(found.values()),endMarker,ready:document.readyState};
})()"""

SCROLL_SCRIPT = r"""(() => {
 let root=document.scrollingElement||document.documentElement;
 for(const e of document.querySelectorAll('.feeds-container,.note-list,.search-result-container,[class*="scroll-container"]')){
   if(e.scrollHeight>e.clientHeight+80&&getComputedStyle(e).overflowY!=='hidden'){root=e;break;}
 }
 const before=root.scrollTop;root.scrollTop+=Math.max(500,root.clientHeight*.88);
 return {scrolled:root.scrollTop>before,atBottom:root.scrollTop+root.clientHeight>=root.scrollHeight-8};
})()"""

ACCOUNT_LINK_SCRIPT = r"""(() => {
 const rows=[];
 for(const a of document.querySelectorAll('nav a[href],header a[href],aside a[href],[class*="side"] a[href],[class*="user"] a[href]')){
   const h=a.getAttribute('href')||'',m=/\/user\/profile\/([a-f0-9]{24})(?:[/?#]|$)/i.exec(h);
   if(!m)continue;
   const label=(a.innerText||a.getAttribute('aria-label')||'').trim().slice(0,80);
   rows.push({id:m[1].toLowerCase(),url:new URL(h,location.href).origin+'/user/profile/'+m[1].toLowerCase(),label});
 }
 return rows.filter((r,i)=>rows.findIndex(v=>v.id===r.id)===i);
})()"""

LIBRARY_TAB_SCRIPT = r"""(kind => {
 const terms=kind==='likes'?/^(?:喜欢|赞过|点赞|Likes?|Liked)$/i:/^(?:收藏|收藏的|我的收藏|Favorites?|Saved|Collections?)$/i;
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const matches=Array.from(document.querySelectorAll('a,button,[role="tab"],.tab-item,.user-tab')).filter(visible).filter(e=>terms.test((e.innerText||'').trim()));
 const tab=matches.find(e=>e.closest('.user-page,.user-profile,.profile-page,.user-container'))||matches[0];
 if(!tab)return {error:'Account library tab is not exposed on this profile page.'};
 const label=(tab.innerText||'').trim(),before=location.href;
 tab.click();return {clicked:true,label,before};
})"""


class ResearchBrowser:
    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = threading.RLock()
        self.last_navigation = 0.0
        self.list_cache = {}
        self.post_cache = {}
        self.account_profile_url = None
        self.checkpoint_dir = Path(app.BASE_DIR) / 'research-checkpoints'
        self.list_store = ListCheckpointStore(self.checkpoint_dir / 'discovered-lists.sqlite')

    def _list_state(self, key):
        legacy_path = self._checkpoint_path('list', key)
        legacy = None
        if legacy_path.is_file() and not self.list_store.exists(key):
            try:
                legacy = json.loads(legacy_path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                raise ValueError('A legacy research checkpoint is unreadable; its original file was preserved.') from error
        return self.list_store.state(key, legacy)

    def _checkpoint_path(self, category, key):
        digest = hashlib.sha256((category + ':' + key).encode('utf-8')).hexdigest()
        return self.checkpoint_dir / (category + '-' + digest + '.json')

    def _restore(self, category, key, fallback):
        if key in (self.list_cache if category == 'list' else self.post_cache):
            return (self.list_cache if category == 'list' else self.post_cache)[key]
        checkpoint = self._checkpoint_path(category, key)
        try:
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if saved.get('key') != key or saved.get('category') != category:
                raise ValueError('Checkpoint identity mismatch')
            if category == 'list':
                items = saved.get('items') if isinstance(saved.get('items'), list) else []
                fallback.update(items=items,
                                seen={(row.get('kind'), row.get('id')) for row in items if isinstance(row, dict)},
                                endMarker=saved.get('endMarker') or '', stalled=bool(saved.get('stalled')))
            else:
                fallback['comments'] = saved.get('comments') if isinstance(saved.get('comments'), list) else []
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('A research checkpoint is unreadable; its original file was preserved.') from error
        (self.list_cache if category == 'list' else self.post_cache)[key] = fallback
        return fallback

    def _save(self, category, key, value):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        payload = {'schema': 'rednote.research-checkpoint/1', 'category': category,
                   'key': key, 'observedAt': self._now()}
        if category == 'list':
            payload.update(items=value['items'], endMarker=value['endMarker'], stalled=value['stalled'])
        else:
            payload['comments'] = value['comments']
        target = self._checkpoint_path(category, key)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        temporary.replace(target)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _window(self):
        import webview
        with self.workspace._window_lock:
            if self.workspace._closing.is_set():
                raise ValueError('The RedNote app is closing.')
            window = self.workspace._research
            if window is None or window not in webview.windows:
                factory = self.workspace._research_factory
                if factory is None:
                    raise ValueError('The background research browser is not ready.')
                window = factory()
                self.workspace._research = window
        return window

    def _site_ready(self):
        blocked = self.workspace._online_cooldown_error()
        if blocked:
            raise ValueError(blocked['error'])
        if self.workspace._clone_busy():
            raise ValueError('Pause the profile clone before live research uses the shared site session.')

    def _pause_result(self, error, cursor):
        detail = str(error)
        reason = 'cooldown' if 'cooldown' in detail.casefold() else (
            'clone_busy' if 'profile clone' in detail.casefold() else None)
        if reason is None:
            raise error
        return {'state': 'paused_needs_attention', 'reason': reason,
                'detail': detail, 'items': [], 'cursor': cursor,
                'nextCursor': cursor, 'observedAt': self._now()}

    def _navigate(self, target):
        self._site_ready()
        target = self.workspace._explorer_target(target)
        window = self._window()
        current = window.get_current_url() or ''
        if current != target:
            remaining = 1.5 - (time.monotonic() - self.last_navigation)
            if remaining > 0:
                time.sleep(remaining)
            window.load_url(target)
            self.last_navigation = time.monotonic()
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                state = window.evaluate_js('({url:location.href,ready:document.readyState})')
                if isinstance(state, dict) and state.get('ready') == 'complete':
                    parsed = urlsplit(state.get('url') or '')
                    wanted = urlsplit(target)
                    same_page = parsed.hostname in app.REDNOTE_HOSTS and (
                        parsed.path == wanted.path or
                        (wanted.path == '/' and parsed.path in {'/explore', '/home', '/discovery'}))
                    if same_page and wanted.path == '/search_result':
                        same_page = parse_qs(parsed.query).get('keyword') == parse_qs(wanted.query).get('keyword')
                    gate_page = parsed.hostname in app.REDNOTE_HOSTS and any(
                        part in parsed.path for part in ('/login', '/auth', '/captcha', '/verify'))
                    if same_page or gate_page:
                        return window
            except Exception:
                pass
            time.sleep(.35)
        raise ValueError('The background RedNote page did not finish loading.')

    def status(self):
        import webview
        with self.workspace._window_lock:
            window = self.workspace._research
            present = window is not None and window in webview.windows
        return {'present': present, 'purpose': 'background_research',
                'manualBrowseIndependent': bool(present and window is not self.workspace._explorer),
                'miningIndependent': bool(present and window is not self.workspace._source),
                'foregroundActivation': False,
                'cooldownRemainingSeconds': self.workspace._profile_cloner().cooldown_remaining() if self.workspace._clone_service else 0,
                'url': (window.get_current_url() or '').split('?', 1)[0] if present else None}

    def _page_items(self, key, target, cursor, limit):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Choose a nonnegative cursor and a page size of 1–100.')
        cache = self._list_state(key)
        window = self._navigate(target)
        advances = 0
        while cache['count'] < cursor + limit and advances < 5 and not cache['endMarker']:
            page = window.evaluate_js(LIST_SCRIPT)
            if not isinstance(page, dict):
                raise ValueError('The RedNote page did not expose a readable result list.')
            if page.get('guard'):
                pause = self.workspace._profile_cloner().register_site_cooldown() if page['guard'] == 'rate_limit' else None
                return {'state': 'paused_needs_attention', 'reason': page['guard'], 'items': [],
                        'cursor': cursor, 'nextCursor': cursor, 'observedAt': self._now(),
                        'cooldown': pause}
            if page.get('error'):
                raise ValueError(page['error'])
            before = cache['count']
            fresh = []
            for item in page.get('items', []):
                if key.startswith('library:') and item.get('kind') != 'post':
                    continue
                key_id = (item.get('kind'), item.get('id'))
                if key_id[0] in {'post', 'profile'} and key_id[1]:
                    item['sourceLane'] = 'account_library' if key.startswith('library:') else 'live'
                    item['observedAt'] = self._now()
                    item['contentDigest'] = hashlib.sha256(json.dumps(
                        {name: item.get(name) for name in ('kind', 'id', 'url', 'title', 'snippet')},
                        ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                    fresh.append(item)
            cache['endMarker'] = page.get('endMarker') or ''
            cache['count'] = self.list_store.append(key, fresh, cache['endMarker'],
                                                    active_url=cache.get('activeUrl'))
            if cache['count'] > before:
                cache['stalled'] = False
            if cache['count'] >= cursor + limit or cache['endMarker']:
                break
            scroll = window.evaluate_js(SCROLL_SCRIPT)
            advances += 1
            time.sleep(1.5)
            if cache['count'] == before and not (isinstance(scroll, dict) and scroll.get('scrolled')):
                cache['stalled'] = True
                break
        self.list_store.append(key, [], cache['endMarker'], cache['stalled'], cache.get('activeUrl'))
        items = self.list_store.page(key, cursor, limit)
        next_cursor = cursor + len(items)
        complete = bool(cache['endMarker']) and next_cursor >= cache['count']
        return {'state': 'complete' if complete else 'partial', 'items': items,
                'cursor': cursor, 'nextCursor': None if complete else next_cursor,
                'discovered': cache['count'], 'endMarker': cache['endMarker'] or None,
                'stalled': cache['stalled'], 'observedAt': self._now(),
                'coverage': 'normally rendered results; no total inferred when the site has not exposed an end marker'}

    def search(self, query, cursor=0, limit=30):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('Enter a RedNote search term up to 500 characters.')
        term = query.strip()
        target = 'https://www.rednote.com/search_result?' + urlencode({'keyword': term})
        with self.lock:
            try:
                return {'query': term, **self._page_items('search:' + term, target, cursor, limit)}
            except ValueError as error:
                return {'query': term, **self._pause_result(error, cursor)}

    def library(self, kind, profile_url=None, cursor=0, limit=30):
        if kind not in {'saved', 'favorites', 'likes'}:
            raise ValueError('Choose saved, favorites or likes.')
        with self.lock:
            try:
                return self._library_page(kind, profile_url, cursor, limit)
            except ValueError as error:
                return {'listKind': kind, **self._pause_result(error, cursor)}

    def _library_page(self, kind, profile_url, cursor, limit):
            if profile_url:
                account_source = 'user_supplied_unverified'
                target = self.workspace._explorer_target(profile_url)
                if not urlsplit(target).path.startswith('/user/profile/'):
                    raise ValueError('Choose your RedNote profile URL for the account library.')
            elif self.account_profile_url:
                account_source = 'signed_in_navigation_link'
                target = self.account_profile_url
            else:
                account_source = 'signed_in_navigation_link'
                home = self._navigate('https://www.rednote.com/')
                links = home.evaluate_js(ACCOUNT_LINK_SCRIPT)
                own = [row for row in links if row.get('label') in {'我', '我的', 'Me', 'Profile', '个人主页'}]
                if len(own) != 1:
                    return {'state': 'needs_profile_url', 'reason': 'The signed-in account profile could not be identified unambiguously from navigation.',
                            'items': [], 'cursor': cursor, 'nextCursor': cursor}
                target = own[0]['url']
                self.account_profile_url = target
            key = 'library:' + kind + ':' + target
            cache = self._list_state(key)
            remembered = cache.get('activeUrl')
            existing = self.workspace._research
            if remembered and existing and existing.get_current_url() == remembered:
                library_url = remembered
                selected = {'label': kind}
            else:
                window = self._navigate(target)
                selected = window.evaluate_js(LIBRARY_TAB_SCRIPT + '(' + json.dumps('likes' if kind == 'likes' else 'favorites') + ')')
                if not isinstance(selected, dict) or selected.get('error'):
                    return {'state': 'unavailable', 'reason': selected.get('error', 'Library tab unavailable.') if isinstance(selected, dict) else 'Library tab unavailable.',
                            'items': [], 'cursor': cursor, 'nextCursor': cursor}
                time.sleep(1.5)
                library_url = window.get_current_url() or target
            cache['activeUrl'] = library_url
            self.list_store.append(key, [], cache['endMarker'], cache['stalled'], library_url)
            result = self._page_items(key, library_url, cursor, limit)
            for item in result.get('items', []):
                item['listKind'] = kind
            return {'listKind': kind, 'accountProfileUrl': target.split('?', 1)[0],
                    'accountSource': account_source, 'tabLabel': selected.get('label'), **result}

    def read_post(self, url, cursor=0, limit=50):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Choose a nonnegative cursor and a page size of 1–100.')
        with self.lock:
            try:
                return self._read_post_page(url, cursor, limit)
            except ValueError as error:
                return {'url': str(url).split('?', 1)[0], **self._pause_result(error, cursor)}

    def read_profile(self, url):
        """Read one linked commenter profile on demand, without cloning its posts."""
        with self.lock:
            try:
                target = self.workspace._explorer_target(url)
                match = re.fullmatch(r'/user/profile/([a-fA-F0-9]{24})/?', urlsplit(target).path)
                if not match:
                    raise ValueError('Choose a RedNote profile link from a comment.')
                window = self._navigate(target)
                page = window.evaluate_js(PROFILE_SNAPSHOT_SCRIPT)
                if not isinstance(page, dict):
                    raise ValueError('The profile page did not expose a readable profile.')
                if page.get('guard'):
                    guard = page['guard']
                    reason = guard.get('kind', 'access_gate') if isinstance(guard, dict) else str(guard)
                    cooldown = self.workspace._profile_cloner().register_site_cooldown() if reason == 'rate_limit' else None
                    return {'state': 'paused_needs_attention', 'reason': reason,
                            'profileUrl': target.split('?', 1)[0], 'cooldown': cooldown,
                            'observedAt': self._now()}
                if page.get('error'):
                    raise ValueError(page['error'])
                if str(page.get('profileId') or '').lower() != match.group(1).lower():
                    raise ValueError('The visible profile identity did not match the requested link.')
                fields = {key: page.get(key) for key in
                          ('name', 'displayId', 'bio', 'avatar', 'followers',
                           'following', 'likes', 'tags', 'location')}
                observed = self._now()
                return {'state': 'observed', 'profileId': match.group(1).lower(),
                        'profileUrl': target.split('?', 1)[0], 'sourceLane': 'live',
                        'observedAt': observed, 'observedFields': fields,
                        'reportedPostCount': page.get('reportedPostCount'),
                        'visiblePostLinks': [
                            {'postId': row.get('postId'), 'url': str(row.get('url') or '').split('?', 1)[0],
                             'title': row.get('title')} for row in page.get('posts', [])[:12]],
                        'coverage': 'One normally rendered profile snapshot. Self-described or site-displayed fields are not verified demographics; posts were not cloned.'}
            except ValueError as error:
                return {'profileUrl': str(url).split('?', 1)[0], **self._pause_result(error, 0)}

    def _read_post_page(self, url, cursor, limit):
            target = self.workspace._explorer_target(url)
            if not (any(x in urlsplit(target).path for x in ('/discovery/item/', '/explore/')) or
                    re.search(r'/user/profile/[a-fA-F0-9]{24}/[a-fA-F0-9]{24}(?:/|$)', urlsplit(target).path)):
                raise ValueError('Choose a RedNote post URL.')
            window = self._navigate(target)
            post_key = target.split('?', 1)[0]
            cache = self._restore('post', post_key, {'comments': []})
            advances = 0
            while advances < 5:
                page = window.evaluate_js(NOTE_SCRIPT)
                if not isinstance(page, dict):
                    raise ValueError('The post page did not expose a readable note.')
                if page.get('guard'):
                    self._save('post', post_key, cache)
                    pause = self.workspace._profile_cloner().register_site_cooldown() if page['guard'] == 'rate_limit' else None
                    return {'state': 'paused_needs_attention', 'reason': page['guard'],
                            'url': target.split('?', 1)[0], 'cooldown': pause}
                if page.get('error'):
                    raise ValueError(page['error'])
                cache['comments'] = merge_comments(cache['comments'], page.get('comments', []))
                self._save('post', post_key, cache)
                if len(cache['comments']) >= cursor + limit or page.get('endMarker'):
                    break
                advance = window.evaluate_js(SCROLL_SCRIPT)
                advances += 1
                time.sleep(1.5)
                if not isinstance(advance, dict) or not advance.get('scrolled') and not advance.get('clicked'):
                    break
            comments = cache['comments'][cursor:cursor + limit]
            comments = [dict(comment, authorProfileUrl=(
                'https://www.rednote.com/user/profile/' + str(comment.get('authorId')).lower())
                if re.fullmatch(r'[a-fA-F0-9]{24}', str(comment.get('authorId') or '')) else None)
                for comment in comments]
            complete = bool(page.get('endMarker')) and cursor + len(comments) >= len(cache['comments'])
            digest = hashlib.sha256(json.dumps({'postId': page.get('postId'),
                'title': page.get('title'), 'text': page.get('text'), 'comments': comments},
                ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
            return {'state': 'complete' if complete else 'partial', 'sourceUrl': page.get('sourceUrl'),
                    'sourceLane': 'live', 'contentDigest': digest,
                    'postId': page.get('postId'), 'profileId': page.get('profileId'),
                    'title': page.get('title'), 'text': page.get('text'), 'mediaType': page.get('mediaType'),
                    'mediaCount': len(page.get('media', [])), 'reportedCommentCount': page.get('reportedCommentCount'),
                    'comments': comments, 'cursor': cursor,
                    'nextCursor': None if complete else cursor + len(comments),
                    'loadedComments': len(cache['comments']), 'endMarker': page.get('endMarker') or None,
                    'observedAt': self._now(), 'coverage': 'normally loaded comments only'}
