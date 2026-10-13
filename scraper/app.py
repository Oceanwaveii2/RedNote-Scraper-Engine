from __future__ import annotations

import re
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import Flask, jsonify, render_template, request, send_file
from yt_dlp import YoutubeDL
from yt_dlp.extractor.xiaohongshu import XiaoHongShuIE
from yt_dlp.utils import js_to_json, ExtractorError
from threads_extractor import ThreadsIE
from trait_signals import derive_trait_candidates
from observation_bridge import accept_observation, development_pack


class RedNoteIE(XiaoHongShuIE):
    """Use yt-dlp's XiaoHongShu parser with the current RedNote web host."""

    IE_NAME = "rednote"
    IE_DESC = "RedNote"
    _VALID_URL = r"https?://(?:www\.)?(?:rednote|xiaohongshu)\.com/(?:explore|discovery/item)/(?P<id>[\da-f]+)"

    def _download_webpage(self, *args, **kwargs):
        webpage = super()._download_webpage(*args, **kwargs)
        self._harvest_page = webpage
        return webpage

    def _real_extract(self, url):
        note_id = self._match_id(url)
        self._harvest_page = self._download_webpage(url, note_id)
        state = self._search_json(
            r'window\.__INITIAL_STATE__\s*=', self._harvest_page,
            'public page state', note_id, transform_source=js_to_json,
            fatal=False, default={},
        )
        note = ((state.get('note') or {}).get('noteDetailMap') or {}).get(note_id, {}).get('note')
        if not isinstance(note, dict) or not note:
            kind = classify_rednote_page(self._harvest_page, state)
            raise ExtractorError(f'RedNote [{kind}]: requested note data is absent. A signed-in browser page is not automatically available to this downloader. No repeated retry was attempted.', expected=True)
        streams = ((note.get('video') or {}).get('media') or {}).get('stream') or {}
        formats = []
        for group in streams.values():
            for row in group if isinstance(group, list) else [group]:
                if not isinstance(row, dict):
                    continue
                for media_url in [row.get('masterUrl'), *(row.get('backupUrls') or [])]:
                    if isinstance(media_url, str) and media_url.startswith(('https://', 'http://')):
                        formats.append({'url': media_url, 'ext': 'mp4', 'width': row.get('width'), 'height': row.get('height')})
        # Use media declared by the received note; never probe guessed CDN URLs.
        info = {'id': note_id, 'title': note.get('title') or 'RedNote note',
                'description': note.get('desc') or '', 'formats': formats,
                'tags': [t['name'] for t in note.get('tagList', []) if isinstance(t, dict) and isinstance(t.get('name'), str)],
                'uploader_id': (note.get('user') or {}).get('userId')}
        image_rows = note.get('imageList') or []
        info['_harvest_image_urls'] = [
            row.get('urlDefault') or row.get('urlPre') or next(
                (item.get('url') for item in row.get('infoList', []) if item.get('url')), None)
            for row in image_rows if isinstance(row, dict)
        ]
        info['_harvest_expected_images'] = len(image_rows)
        info['_harvest_public_comments'] = public_comments_from_state(state, info['id'])
        return info


def classify_rednote_page(page: str, state: dict) -> str:
    """Classify missing public data without retaining credentials or page state."""
    if re.search(r'访问频繁|请求过于频繁|too many requests|access denied', page, re.I):
        return 'rate-limited'
    if re.search(r'<title>[^<]*(?:验证码|安全验证|captcha)', page, re.I):
        return 'challenge'
    if not state:
        return 'page-format-changed'
    if not ((state.get('note') or {}).get('noteDetailMap') or {}):
        return 'generic-page-or-session-required'
    return 'note-unavailable'


_package_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
_data_dir_override = os.environ.get("REDNOTE_DATA_DIR") if getattr(sys, "frozen", False) else None
if _data_dir_override:
    _selected_data_dir = Path(_data_dir_override).expanduser()
    if not _selected_data_dir.is_absolute() or not _selected_data_dir.is_dir():
        raise RuntimeError("REDNOTE_DATA_DIR must name an existing absolute app data directory")
    BASE_DIR = _selected_data_dir.resolve()
else:
    BASE_DIR = _package_dir
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
THREADS_HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
REDNOTE_HOSTS = {"rednote.com", "www.rednote.com", "xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}
MIN_SECONDS_BETWEEN_JOBS = 45
MAX_ITEMS = 0  # 0 means all videos exposed by the public URL.
COMMENT_EXPORT_SCHEMA = "reel-harvest.comments/1"
MAX_IMPORTED_COMMENTS = 500
MAX_COMMENT_LENGTH = 1200


@dataclass
class Job:
    id: str
    url: str
    platform: str = "instagram"
    media_type: str = "video"
    state: str = "queued"
    message: str = "Waiting in the local queue"
    phase: str = "queued"
    progress: float = 0
    current_item: int = 0
    total_items: int | None = None
    downloaded_bytes: int = 0
    files: list[str] = field(default_factory=list)
    archive: str | None = None
    error: str | None = None
    started_at: float | None = None
    last_activity_at: float | None = None
    events: list[str] = field(default_factory=list)
    comment_import: dict | None = None
    post_title: str | None = None
    post_text: str = ""
    post_text_available: bool = False
    comment_count: int = 0
    comment_status: str = "not_available"
    trait_candidate_count: int = 0

    def note(self, message: str) -> None:
        self.message = message
        self.last_activity_at = time.time()
        if not self.events or self.events[-1] != message:
            self.events.append(message)
            self.events = self.events[-5:]

    def public(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "mediaType": self.media_type,
            "state": self.state,
            "message": self.message,
            "phase": self.phase,
            "progress": round(self.progress, 1),
            "currentItem": self.current_item,
            "totalItems": self.total_items,
            "downloadedBytes": self.downloaded_bytes,
            "fileCount": len(self.files),
            "downloadReady": bool(self.archive),
            "error": self.error,
            "startedAt": self.started_at,
            "lastActivityAt": self.last_activity_at,
            "events": self.events,
            "postTitle": self.post_title,
            "postText": self.post_text,
            "postTextAvailable": self.post_text_available,
            "commentCount": self.comment_count,
            "commentStatus": self.comment_status,
            "traitCandidateCount": self.trait_candidate_count,
        }


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
worker_lock = threading.Lock()
last_job_started = 0.0
folder_tokens: dict[str, Path] = {}


@app.after_request
def disable_browser_caching(response):
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def is_rednote_host(host: str) -> bool:
    return host in REDNOTE_HOSTS or host.endswith((".xiaohongshu.com", ".rednote.com"))


def normalized_source_url(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
        raise ValueError("Enter a valid Instagram, Threads, or RedNote URL.")

    path = parsed.path or "/"
    if host in INSTAGRAM_HOSTS:
        if not re.match(r"^/(?:p|reel|reels|tv|stories|[A-Za-z0-9_.]+)/", path + "/"):
            raise ValueError("That Instagram URL type is not supported.")
        return "instagram", parsed._replace(scheme="https", query="", fragment="").geturl()

    if host in THREADS_HOSTS:
        if not re.match(r"^/(?:@[A-Za-z0-9._-]+/post/[A-Za-z0-9_-]+|t/[A-Za-z0-9_-]+|share/[A-Za-z0-9_-]+)/?$", path):
            raise ValueError("Enter a Threads post or share link. Profile scraping is not supported.")
        return "threads", parsed._replace(scheme="https", fragment="").geturl()

    if is_rednote_host(host):
        if host in {"xhslink.com", "www.xhslink.com"}:
            if not re.match(r"^/(?:a|s)/[A-Za-z0-9_-]+/?$", path):
                raise ValueError("Enter a RedNote note link or xhslink.com share link.")
            return "xiaohongshu", parsed._replace(scheme="https", fragment="").geturl()
        profile_note = re.fullmatch(r'/user/profile/[a-f0-9]{24}/([a-f0-9]{24})/?', path)
        if profile_note:
            path = '/discovery/item/' + profile_note.group(1)
        if not re.match(r"^/(?:explore|discovery/item)/[A-Za-z0-9_-]+/?$", path):
            raise ValueError("Enter a RedNote note link. Profile scraping is not supported.")
        canonical = parsed._replace(
            scheme="https",
            netloc=("www.rednote.com" if host.endswith("rednote.com") else "www.xiaohongshu.com"),
            path=path,
            fragment="",
        )
        return "xiaohongshu", canonical.geturl()

    raise ValueError("Enter a supported Instagram, Threads, or RedNote URL.")


def export_source_url(url: str) -> str:
    """Keep share credentials out of saved metadata and downloaded packages."""
    return urlparse(url)._replace(query="", fragment="").geturl()


def note_id_from_url(url: str) -> str | None:
    match = re.search(r"/(?:explore|discovery/item)/([A-Za-z0-9_-]+)", urlparse(url).path)
    return match.group(1) if match else None


def public_comments_from_state(state: dict, note_id: str) -> dict:
    """Capture only comment bodies already embedded in this public note page."""
    note_state = (state.get('note') or {}).get('noteDetailMap') or {}
    entry = note_state.get(note_id) or {}
    note = entry.get('note') or {}
    comments = []
    seen = set()

    def visit(value, depth=0):
        if depth > 8 or len(comments) >= MAX_IMPORTED_COMMENTS:
            return
        if isinstance(value, list):
            for row in value:
                visit(row, depth + 1)
        elif isinstance(value, dict):
            text = value.get('content') or value.get('text')
            if isinstance(text, str):
                text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()
                comment_key = str(value.get('id') or value.get('commentId') or text)
                if text and comment_key not in seen:
                    seen.add(comment_key)
                    row = {'text': text[:MAX_COMMENT_LENGTH]}
                    likes = value.get('likeCount', value.get('likes'))
                    if isinstance(likes, str) and likes.isdigit():
                        likes = int(likes)
                    if isinstance(likes, int) and not isinstance(likes, bool) and 0 <= likes <= 1_000_000:
                        row['likes'] = likes
                    comments.append(row)
            for key in ('list', 'comments', 'subComments', 'replies', 'data'):
                if key in value:
                    visit(value[key], depth + 1)

    for container in (entry, note):
        for key in ('comments', 'commentList', 'comment'):
            if key in container:
                visit(container[key])
    return {
        'schema': COMMENT_EXPORT_SCHEMA, 'noteId': note_id,
        'source': 'public-page-snapshot',
        'coverage': 'partial-page-snapshot' if comments else 'not-exposed-in-page',
        'capturedCount': len(comments), 'comments': comments,
    }


def validate_comment_import(data: object, expected_note_id: str | None, authorized: bool) -> dict | None:
    """Accept user-supplied, permissioned comment examples; never fetch comments."""
    if data is None:
        return None
    if not authorized:
        raise ValueError("Confirm that you have permission to include this comment export.")
    if not isinstance(data, dict) or data.get("schema") != COMMENT_EXPORT_SCHEMA:
        raise ValueError(f"Comment export schema must be {COMMENT_EXPORT_SCHEMA}.")
    note_id = data.get("noteId")
    if not expected_note_id or note_id != expected_note_id:
        raise ValueError("The comment export noteId must match the RedNote URL.")
    rows = data.get("comments")
    if not isinstance(rows, list) or len(rows) > MAX_IMPORTED_COMMENTS:
        raise ValueError(f"The export must contain no more than {MAX_IMPORTED_COMMENTS} comments.")
    comments = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("Each comment must contain plain-text text.")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", row["text"]).strip()
        if not text:
            continue
        if len(text) > MAX_COMMENT_LENGTH:
            raise ValueError(f"Comment text must be no longer than {MAX_COMMENT_LENGTH} characters.")
        clean = {"text": text}
        likes = row.get("likes")
        if isinstance(likes, int) and not isinstance(likes, bool) and 0 <= likes <= 1_000_000:
            clean["likes"] = likes
        comments.append(clean)
    return {
        "schema": COMMENT_EXPORT_SCHEMA,
        "noteId": note_id,
        "source": "user-supplied-authorized-export",
        "importedAt": datetime.now(timezone.utc).isoformat(),
        "comments": comments,
    }


def post_metadata(platform: str, job: Job, info: dict | None, media: list[Path]) -> dict:
    info = info or {}
    description = info.get("description") if isinstance(info.get("description"), str) else ""
    title = info.get("title") if isinstance(info.get("title"), str) else ""
    raw_tags = info.get("tags") or []
    tags = [str(tag)[:100] for tag in raw_tags if isinstance(tag, (str, int, float))][:80]
    files = []
    for path in media:
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError:
            continue
        files.append({
            "name": path.name,
            "bytes": size,
            "sha256": digest.hexdigest(),
            "mediaType": path.suffix.lower().lstrip("."),
        })
    note_id = note_id_from_url(job.url)
    captured_comments = job.comment_import or info.get('_harvest_public_comments') or {}
    return {
        "schema": "reel-harvest.post/1",
        "platform": platform,
        "postId": note_id or str(info.get("id") or "")[:120],
        "sourceUrl": export_source_url(job.url),
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "title": title[:500],
        "text": description[:12000],
        "textSource": "public-page-metadata" if description or title else "unavailable-from-extractor",
        "tags": tags,
        "publishedDate": str(info.get("upload_date") or "")[:10],
        "media": files,
        "expectedImageCount": info.get('_harvest_expected_images'),
        "commentStatus": "user-supplied-export" if job.comment_import else captured_comments.get('coverage', 'not-exposed-in-page'),
        "commentCount": len(captured_comments.get('comments', [])),
    }


def redact_query_urls(text: str) -> str:
    return re.sub(
        r"(https?://(?:www\.)?(?:rednote|xiaohongshu)\.com/[^\s?]+)\?[^\s]+",
        r"\1?[query removed]",
        text,
        flags=re.IGNORECASE,
    )


def cleanup_old_jobs() -> None:
    """Retain original downloads. Cleanup requires an explicit user action."""


def is_profile_url(url: str) -> bool:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return len(parts) == 1 and parts[0].lower() not in {"p", "reel", "reels", "tv", "stories"}


class RedNoteRedirectHandler(HTTPRedirectHandler):
    """Follow only RedNote share-link redirects to avoid fetching arbitrary hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        parsed = urlparse(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not is_rednote_host(host):
            raise ValueError("RedNote share links must redirect to an HTTPS RedNote page.")
        return super().redirect_request(req, fp, code, msg, headers, target)


def resolve_rednote_share_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    opener = build_opener(RedNoteRedirectHandler())
    try:
        with opener.open(request, timeout=20) as response:
            final_url = response.geturl()
    except HTTPError as exc:
        raise RuntimeError(f"Could not resolve this RedNote share link (HTTP {exc.code}).") from exc
    platform, resolved = normalized_source_url(final_url)
    if platform != "xiaohongshu" or urlparse(resolved).hostname in {"xhslink.com", "www.xhslink.com"}:
        raise RuntimeError("The RedNote share link did not resolve to a note. Copy its full rednote.com or xiaohongshu.com URL and retry.")
    return resolved


def staged_media(target: Path) -> tuple[list[Path], int]:
    files: list[Path] = []
    total_bytes = 0
    for candidate in target.rglob("*"):
        if candidate.suffix.lower() not in {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            continue
        try:
            if not candidate.is_file():
                continue
            total_bytes += candidate.stat().st_size
            files.append(candidate)
        except FileNotFoundError:
            # gallery-dl may atomically rename a temporary file during this scan.
            continue
    return files, total_bytes


def download_profile_with_gallery_dl(job: Job, target: Path, session_browser: str | None) -> None:
    job.phase = "gathering"
    job.note("Instagram scan started — media will appear in staging as it is found")
    extensions = "'jpg', 'jpeg', 'png', 'webp', 'heic'" if job.media_type == "photo" else "'mp4', 'webm', 'mov'"
    command = ([sys.executable, "--gallery-dl-worker"] if getattr(sys, "frozen", False) else [
        sys.executable, "-m", "gallery_dl",
    ]) + [
        "--destination",
        str(target),
        "--download-archive",
        str(target / ".download-archive"),
        "--retries",
        "5",
        "--sleep",
        "3-6",
        "--sleep-request",
        "2",
        "--sleep-retries",
        "exp=30",
        "--sleep-429",
        "120",
        "--filter",
        f"extension in ({extensions})",
    ]
    if session_browser:
        command.extend(["--cookies-from-browser", session_browser])
    command.append(job.url)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    prior_count = -1
    while process.poll() is None:
        media, collected_bytes = staged_media(target)
        job.current_item = len(media)
        job.downloaded_bytes = collected_bytes
        if job.current_item != prior_count:
            prior_count = job.current_item
            size_gb = job.downloaded_bytes / (1024 ** 3)
            job.note(f"{job.current_item} media files saved to staging — {size_gb:.2f} GB collected")
        time.sleep(1)
    _, stderr = process.communicate()
    if process.returncode:
        detail = stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "Profile enumeration failed.")


def download_rednote_photos(ydl: YoutubeDL, job: Job, target: Path, info: dict) -> None:
    candidates = []
    seen_paths = set()
    image_urls = info.get('_harvest_image_urls')
    if image_urls is None:
        image_urls = [thumbnail.get('url') for thumbnail in info.get('thumbnails') or []]
    for image_url in image_urls:
        if not image_url:
            continue
        parsed = urlparse(image_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host.endswith(".xhscdn.com"):
            continue
        if parsed.path in seen_paths:
            continue
        seen_paths.add(parsed.path)
        candidates.append(image_url)

    if not candidates:
        raise RuntimeError("RedNote did not expose downloadable note images. Try Video for a video note.")
    job.total_items = len(candidates)

    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(info.get("title") or "RedNote note"))
    title = title.strip(" .")[:70] or "RedNote note"
    note_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or "note"))
    extension_by_type = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}

    for index, image_url in enumerate(candidates, start=1):
        job.phase = "downloading"
        job.note(f"Downloading RedNote image {index} of {len(candidates)}")
        image_request = Request(image_url, headers={"Referer": job.url})
        with ydl.urlopen(image_request) as response:
            content_type = response.headers.get_content_type().lower()
            extension = extension_by_type.get(content_type)
            if not extension:
                raise RuntimeError(f"RedNote returned an unsupported image type: {content_type or 'unknown'}.")
            image_bytes = response.read()
        output = target / f"{title} [{note_id}] {index:02d}{extension}"
        output.write_bytes(image_bytes)
        job.current_item = index
        job.downloaded_bytes += len(image_bytes)


@contextmanager
def capture_process_guard():
    """Serialize capture across the GUI and CLI processes, not only threads."""
    if MIN_SECONDS_BETWEEN_JOBS <= 0:
        yield
        return
    import os
    with (BASE_DIR / '.capture.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def reserve_job_slot(job: Job) -> None:
    """Apply the same persisted 45-second pacing to every Harvest download path."""
    global last_job_started
    if MIN_SECONDS_BETWEEN_JOBS > 0:
        try:
            prior = float((BASE_DIR / '.capture-clock').read_text())
            if 0 <= prior <= time.time() + 60:
                last_job_started = max(last_job_started, prior)
        except (OSError, ValueError):
            pass
    wait_for = max(0, MIN_SECONDS_BETWEEN_JOBS - (time.time() - last_job_started))
    while wait_for > 0:
        job.state = "cooldown"
        job.phase = "cooldown"
        job.note(f"Respecting a cooldown — starting in {int(wait_for) + 1}s")
        time.sleep(min(1, wait_for))
        wait_for = max(0, MIN_SECONDS_BETWEEN_JOBS - (time.time() - last_job_started))
    last_job_started = time.time()
    if MIN_SECONDS_BETWEEN_JOBS > 0:
        (BASE_DIR / '.capture-clock').write_text(str(last_job_started))


def rednote_download_block_reason(platform: str) -> str | None:
    """Keep legacy RedNote requests outside profile capture and website pauses."""
    if platform != 'xiaohongshu':
        return None
    workspace = getattr(app, 'browser_workspace', None)
    if workspace is not None:
        try:
            cooldown = workspace._online_cooldown_error()
            if cooldown:
                return str(cooldown.get('error') or 'The website cooldown is active.')
            if workspace._clone_busy():
                return 'Pause the profile clone and wait for its current operation before starting another RedNote download.'
            return None
        except Exception:
            return 'The RedNote capture/pause state could not be checked. Try again after the app is ready.'
    # A standalone legacy server must also retain the saved website-wide pause.
    queue = BASE_DIR / 'clone_queue.json'
    if not queue.is_file():
        return None
    try:
        import math
        saved = json.loads(queue.read_text(encoding='utf-8'))
        deadlines = [saved.get('safety', {}).get('siteCooldownUntil', 0)]
        deadlines.extend(row.get('cooldownUntil', 0) for row in saved.get('jobs', []) if isinstance(row, dict))
        until = max(float(value) for value in deadlines)
        if not math.isfinite(until) or until < 0:
            raise ValueError('Invalid cooldown state')
        remaining = math.ceil(until - time.time())
        if remaining > 0:
            return f'The website cooldown is active for another {remaining} seconds. Saved archives remain available.'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'The saved RedNote website pause could not be checked. Open the app capture controls before retrying.'
    return None


@contextmanager
def rednote_download_queue_guard(platform: str):
    """Reserve the same queue boundary used by full-profile start/resume/retry."""
    workspace = getattr(app, 'browser_workspace', None) if platform == 'xiaohongshu' else None
    lock = getattr(workspace, '_profile_batch_lock', None)
    if lock is None:
        yield
    else:
        with lock:
            yield


def run_job(job: Job, destination: Path, session_browser: str | None) -> None:
    with worker_lock, capture_process_guard():
        blocked = rednote_download_block_reason(job.platform)
        if blocked:
            job.state = "failed"
            job.phase = "failed"
            job.error = blocked
            job.note("Download blocked by the RedNote capture or website pause")
            return
        reserve_job_slot(job)
        job.state = "running"
        job.phase = "preparing"
        job.started_at = time.time()
        job.note(f"Preparing {job.platform} download request")
        target = DOWNLOAD_DIR / job.id
        target.mkdir(parents=True, exist_ok=True)
        extractor_errors: list[str] = []
        info: dict | None = None

        class JobLogger:
            def debug(self, message: str) -> None:
                pass

            def warning(self, message: str) -> None:
                pass

            def error(self, message: str) -> None:
                extractor_errors.append(message)

        def hook(data: dict) -> None:
            status = data.get("status")
            info = data.get("info_dict") or {}
            if status == "downloading":
                downloaded = data.get("downloaded_bytes", 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                job.progress = (downloaded / total * 100) if total else job.progress
                job.phase = "downloading"
                job.note(f"Downloading {info.get('title') or 'video'}")
            elif status == "finished":
                job.current_item += 1
                job.progress = 100
                job.note("Processing downloaded media")

        options = {
            "outtmpl": str(target / "%(uploader)s - %(title).80s [%(id)s].%(ext)s"),
            "format": "best[ext=mp4]/best",
            "noplaylist": False,
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "sleep_interval": 3,
            "max_sleep_interval": 6,
            "sleep_interval_requests": 2.0,
            "retries": 0 if job.platform == 'xiaohongshu' else 3,
            "extractor_retries": 0 if job.platform == 'xiaohongshu' else 3,
            "file_access_retries": 3,
            "fragment_retries": 5,
            "ratelimit": 2 * 1024 * 1024,
            "download_archive": str(target / ".download-archive"),
            "progress_hooks": [hook],
            "logger": JobLogger(),
        }
        if job.platform == "threads":
            options["format"] = (
                "best[ext=jpg]/best[ext=jpeg]/best[ext=png]/best[ext=webp]/best[ext=heic]"
                if job.media_type == "photo" else "best[ext=mp4]"
            )
        if session_browser:
            options["cookiesfrombrowser"] = (session_browser, None, None, None)
            job.note(f"Reading your local {session_browser.title()} session")

        try:
            # Recheck after any queue/pacing wait, immediately before networking.
            blocked = rednote_download_block_reason(job.platform)
            if blocked:
                raise RuntimeError(blocked)
            if job.platform == "xiaohongshu" and urlparse(job.url).hostname in {"xhslink.com", "www.xhslink.com"}:
                job.note("Resolving RedNote share link")
                job.url = resolve_rednote_share_url(job.url)
                blocked = rednote_download_block_reason(job.platform)
                if blocked:
                    raise RuntimeError(blocked)

            if job.platform == "instagram" and (is_profile_url(job.url) or job.media_type == "photo"):
                download_profile_with_gallery_dl(job, target, session_browser)
            elif job.platform == "xiaohongshu" and job.media_type == "photo":
                with YoutubeDL(options) as ydl:
                    ydl.add_info_extractor(RedNoteIE())
                    info = ydl.extract_info(job.url, download=False, process=False, ie_key=RedNoteIE.ie_key())
                    if not info:
                        raise RuntimeError("RedNote did not return note details.")
                    download_rednote_photos(ydl, job, target, info)
            elif job.platform == "threads":
                with YoutubeDL(options) as ydl:
                    ydl.add_info_extractor(ThreadsIE())
                    info = ydl.extract_info(job.url, download=True, ie_key=ThreadsIE.ie_key())
            else:
                with YoutubeDL(options) as ydl:
                    if job.platform == "xiaohongshu":
                        ydl.add_info_extractor(RedNoteIE())
                        info = ydl.extract_info(job.url, download=True, ie_key=RedNoteIE.ie_key())
                    else:
                        info = ydl.extract_info(job.url, download=True)

            media, collected_bytes = staged_media(target)
            job.downloaded_bytes = collected_bytes
            if not media:
                detail = extractor_errors[-1] if extractor_errors else f"{job.platform} returned no downloadable media."
                raise RuntimeError(
                    f"No downloadable media was available. {detail}"
                )
            job.files = [p.name for p in media]
            metadata = post_metadata(job.platform, job, info, media)
            note_id = metadata["postId"] or job.id
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", note_id)[:80]
            metadata_path = target / f"{safe_id}.metadata.json"
            comments_path = target / f"{safe_id}.comments.json"
            job.post_title = metadata["title"] or None
            job.post_text = metadata["text"]
            job.post_text_available = bool(metadata["text"] or metadata["title"])
            job.comment_count = metadata["commentCount"]
            job.comment_status = metadata["commentStatus"]
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            comments = job.comment_import or (info or {}).get('_harvest_public_comments') or {
                "schema": COMMENT_EXPORT_SCHEMA,
                "noteId": metadata["postId"],
                "source": "not-collected",
                "status": "No comments exposed in the public page snapshot or supplied by import.",
                "comments": [],
            }
            comments_path.write_text(json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
            candidates = derive_trait_candidates(metadata, comments)
            job.trait_candidate_count = candidates["candidateCount"]
            candidates_path = target / f"{safe_id}.trait-candidates.json"
            candidates_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
            job.phase = "copying"
            job.note(f"Moving {len(media)} media file{'s' if len(media) != 1 else ''} to your folder")
            saved = []
            for media_file in [*media, metadata_path, comments_path, candidates_path]:
                output = destination / media_file.name
                counter = 2
                while output.exists():
                    output = destination / f"{media_file.stem} ({counter}){media_file.suffix}"
                    counter += 1
                shutil.copy2(media_file, output)
                saved.append(output.name)
            job.files = saved
            archive = target / f"{job.platform}-media.zip"
            job.phase = "packaging"
            job.note("Creating ZIP archive")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for media_file in [*media, metadata_path, comments_path, candidates_path]:
                    zf.write(media_file, media_file.name)
            job.archive = str(archive)
            job.state = "complete"
            job.phase = "complete"
            job.progress = 100
            job.note(f"Finished — {len(media)} media file{'s' if len(media) != 1 else ''} saved to {destination}")
        except Exception as exc:
            job.state = "failed"
            job.phase = "failed"
            error_text = str(exc)
            logged_errors = " ".join(extractor_errors)
            site_name = {"instagram": "Instagram", "threads": "Threads", "xiaohongshu": "RedNote"}.get(job.platform, job.platform)
            if session_browser == "chrome" and "dpapi" in logged_errors.lower():
                error_text = (
                    "Chrome released its cookie file, but Windows could not decrypt the cookies (DPAPI/app-bound encryption). "
                    f"Use Firefox: sign into {site_name} there, fully close Firefox, then select the Firefox session in this app."
                )
            elif session_browser and "failed to load cookies" in f"{error_text} {logged_errors}".lower():
                error_text = (
                    f"Could not read {session_browser.title()} cookies because the browser has them locked. "
                    f"Fully close every {session_browser.title()} window, wait a few seconds, then retry. "
                    f"Alternatively, sign into {site_name} in Firefox and select Firefox here."
                )
            job.error = redact_query_urls(error_text) if job.platform == "xiaohongshu" else error_text
            job.note("Download could not be completed")


@app.get("/")
def index():
    return render_template("index.html")


@app.post('/api/bridge/observations')
def bridge_observations():
    # This receives observations, never browser credentials or remote fetch jobs.
    origin = request.headers.get('Origin')
    if request.remote_addr not in {'127.0.0.1', '::1'} or (origin and origin != request.host_url.rstrip('/')):
        return jsonify(error='Use the local bridge client, not a remote web page.'), 403
    if request.content_length is None or request.content_length > 2_000_000:
        return jsonify(error='Observation batch too large'), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, list) or len(payload) > 500:
        return jsonify(error='Expected at most 500 observations'), 400
    try:
        records = [accept_observation(row, BASE_DIR / 'bridge-inbox') for row in payload]
        return jsonify(development_pack(records)), 201
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.get('/bridge')
def bridge_index():
    return render_template('bridge.html')


def _local_browser_bridge_allowed():
    if request.remote_addr not in {'127.0.0.1', '::1'} or request.headers.get('Origin'):
        return False
    expected = getattr(app, 'bridge_token', None)
    supplied = request.headers.get('Authorization', '')
    return bool(expected) and secrets.compare_digest(supplied, f'Bearer {expected}')


@app.get('/api/bridge/browser/status')
def browser_bridge_status():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    workspace = getattr(app, 'browser_workspace', None)
    if workspace is None:
        return jsonify(error='Harvest browser window is unavailable'), 503
    current = workspace.current_page()
    return jsonify(
        browserControl=True,
        currentPage=current.get('url', ''),
        downloadsPath=str(DOWNLOAD_DIR),
        exportsPath=str(BASE_DIR / 'development-exports'),
        jobs=[job.public() for job in list(jobs.values())[-20:]],
    )


@app.post('/api/bridge/browser/<action>')
def browser_bridge_action(action):
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    workspace = getattr(app, 'browser_workspace', None)
    if workspace is None:
        return jsonify(error='Harvest browser window is unavailable'), 503
    if request.content_length is not None and request.content_length > 64_000:
        return jsonify(error='Request too large'), 413
    payload = request.get_json(silent=True) or {}
    try:
        if action == 'navigate':
            result = workspace.navigate(payload.get('url', ''))
        elif action == 'capture':
            result = workspace.capture_post()
        elif action == 'scroll-comments':
            result = workspace.scroll_comments()
        elif action == 'profile-preview':
            result = workspace.profile_preview()
        elif action == 'clone-start':
            if payload.get('confirmPermission') is not True:
                raise ValueError('Confirm permission before saving a profile, its media and comments.')
            result = workspace.start_profile_clone(payload.get('url', ''), payload.get('options'))
        elif action == 'single-start':
            if payload.get('confirmPermission') is not True:
                raise ValueError('Confirm permission before saving a post, its media and comments.')
            result = workspace.start_single_post(payload.get('url', ''))
        elif action == 'clone-status':
            result = workspace.profile_clone_status(payload.get('jobId'))
        elif action == 'clone-pause':
            result = workspace.pause_profile_clone(payload.get('jobId', ''))
        elif action == 'clone-pacing':
            result = workspace.configure_profile_clone_pacing(payload.get('jobId', ''), payload.get('postSeconds'), payload.get('mediaSeconds'))
        elif action == 'clone-batch':
            result = workspace.automatic_profile_batch(payload.get('jobId', ''))
        elif action == 'clone-stop':
            result = workspace.stop_profile_clone(payload.get('jobId', ''))
        elif action == 'clone-resume':
            result = workspace.resume_profile_clone(payload.get('jobId', ''))
        elif action == 'clone-retry':
            result = workspace.retry_profile_clone(payload.get('jobId', ''), payload.get('postId'), payload.get('postIds'))
        elif action == 'profile-batch-start':
            if payload.get('confirmPermission') is not True:
                raise ValueError('Confirm permission before saving profile posts and media.')
            result = workspace.start_profile_batch(payload.get('profileName',''),payload.get('postUrls',[]),confirm_permission=True)
        elif action == 'profile-batch-stop':
            result = workspace.stop_profile_batch(payload.get('jobId',''))
        elif action == 'download-open-media':
            if payload.get('confirmPermission') is not True:
                raise ValueError('Confirm permission to download this media.')
            result = workspace.download_open_media(payload.get('mediaType', 'video'))
        elif action == 'download-link':
            if payload.get('confirmPermission') is not True:
                raise ValueError('Confirm permission to download this media.')
            result = workspace.download(payload.get('url', ''), payload.get('mediaType', 'video'))
        elif action == 'eval-js':
            result = workspace.eval_js(payload.get('script', ''))
        elif action == 'open-downloads':
            result = workspace.open_downloads()
        elif action == 'current-page':
            result = workspace.current_page()
        elif action == 'focus-controls':
            result = workspace.focus_controls()
        elif action == 'explorer-open':
            result = workspace.open_explorer(payload.get('url'))
        elif action == 'explorer-navigate':
            result = workspace.explorer_navigate(payload.get('url',''))
        elif action == 'explorer-status':
            result = workspace.explorer_status()
        elif action == 'explorer-language':
            result = workspace.explorer_language(payload)
        elif action == 'research-status':
            result = workspace.research_status()
        elif action == 'research-search':
            result = workspace.research_search(payload.get('query', ''), payload.get('cursor', 0), payload.get('limit', 30))
        elif action == 'research-library':
            result = workspace.research_library(payload.get('kind', 'saved'), payload.get('profileUrl'), payload.get('cursor', 0), payload.get('limit', 30))
        elif action == 'research-post':
            result = workspace.research_post(payload.get('url', ''), payload.get('cursor', 0), payload.get('limit', 30))
        elif action == 'research-profile':
            result = workspace.research_profile(payload.get('url', ''))
        elif action == 'research-job-start':
            result = workspace.research_job_start(payload.get('kind', 'saved'), payload.get('profileUrl'))
        elif action == 'research-job-status':
            result = workspace.research_job_status(payload.get('jobId'))
        elif action == 'research-job-pause':
            result = workspace.research_job_pause(payload.get('jobId', ''))
        elif action == 'research-job-resume':
            result = workspace.research_job_resume(payload.get('jobId', ''))
        elif action == 'restore-windows':
            result = workspace.restore_windows()
        elif action == 'close-app':
            result = workspace._request_shutdown()
        elif action == 'job':
            job_id = str(payload.get('jobId', ''))
            job = jobs.get(job_id)
            result = job.public() if job else {'error': 'Download not found.'}
        else:
            return jsonify(error='Unsupported Harvest browser action'), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        return jsonify(error='Harvest browser action failed.'), 500
    return jsonify(result), (400 if isinstance(result, dict) and result.get('error') else 200)


def _archive_store():
    from master_service import archive_store
    return archive_store


@app.get('/api/bridge/archive/profiles')
def bridge_archive_profiles():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    return jsonify(profiles=_archive_store().list_profiles())


@app.get('/api/bridge/archive/profile/<profile_id>')
def bridge_archive_profile(profile_id):
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    try:
        record = _archive_store().get_profile(profile_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(record) if record else (jsonify(error='Profile not found'), 404)


@app.get('/api/bridge/archive/post/<post_id>')
def bridge_archive_post(post_id):
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    try:
        record = _archive_store().get_post(post_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(record) if record else (jsonify(error='Post not found'), 404)


@app.get('/api/bridge/archive/selection')
def bridge_archive_selection():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    from master_service import get_current_selection
    return jsonify(get_current_selection())


@app.get('/api/bridge/archive/context')
def bridge_archive_context():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    from master_service import build_context_pack
    profile_id = request.args.get('profileId') or None
    post_ids = request.args.get('postIds')
    try:
        return jsonify(build_context_pack(profile_id, post_ids.split(',') if post_ids else None))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get('/api/bridge/diagnostics')
def bridge_diagnostics():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    workspace = getattr(app, 'browser_workspace', None)
    try:
        release=json.loads((Path(getattr(sys,'_MEIPASS',BASE_DIR))/'release.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        release={'buildId':'development','verifiedPackage':False}
    return jsonify(schema='rednote.diagnostics/1', browserControl=workspace is not None,
        release=release,
        windows=workspace._window_health() if workspace else {},
        currentPage=workspace.current_page().get('url', '') if workspace else '',
        embeddedMcp='/mcp', archiveAvailable=not app.config.get('MASTER_IMPORT_ERROR'),
        masterImportError=app.config.get('MASTER_IMPORT_ERROR'),
        mcpImportError=app.config.get('MCP_IMPORT_ERROR'),
        v2ImportError=app.config.get('V2_IMPORT_ERROR'),
        cloneJobs=workspace.profile_clone_status() if workspace else {'jobs': []})


@app.get('/api/bridge/browser/captures')
def browser_bridge_captures():
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    directory = BASE_DIR / 'development-exports'
    rows = []
    if directory.exists():
        for path in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                pack = row.get('pack', row)
                evidence = (pack.get('evidence') or [{}])[0]
                rows.append({
                    'id': path.name,
                    'sourceUrl': evidence.get('sourceUrl'),
                    'coverage': row.get('commentCoverage', evidence.get('coverage')),
                    'comments': len(row.get('comments', evidence.get('comments', []))),
                    'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                })
            except (OSError, ValueError, TypeError):
                continue
    return jsonify(captures=rows)


@app.get('/api/bridge/browser/captures/<capture_id>')
def browser_bridge_read_capture(capture_id):
    if not _local_browser_bridge_allowed():
        return jsonify(error='Local Harvest MCP authorization required'), 403
    if Path(capture_id).name != capture_id or not capture_id.endswith('.json'):
        return jsonify(error='Invalid capture ID'), 400
    directory = (BASE_DIR / 'development-exports').resolve()
    path = (directory / capture_id).resolve()
    if path.parent != directory or not path.is_file():
        return jsonify(error='Capture not found'), 404
    if path.stat().st_size > 2_000_000:
        return jsonify(error='Capture exceeds the 2 MB read limit'), 413
    try:
        return jsonify(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return jsonify(error='Capture could not be read'), 422


@app.post('/api/bridge/download')
def bridge_download():
    if request.remote_addr not in {'127.0.0.1','::1'} or request.headers.get('Origin'):
        return jsonify(error='Local job client required'),403
    payload=request.get_json(silent=True) or {}
    if payload.get('authorized') is not True:
        return jsonify(error='Explicit download authorization required'),400
    try:
        platform,url=normalized_source_url(str(payload.get('url','')))
        media_type=payload.get('mediaType','video')
        if platform!='xiaohongshu' or media_type not in {'video','photo'}:
            raise ValueError('Choose a Rednote post and supported media type')
    except ValueError as exc:
        return jsonify(error=str(exc)),400
    with rednote_download_queue_guard(platform):
        blocked = rednote_download_block_reason(platform)
        if blocked:
            return jsonify(error=blocked),409
        job=Job(id=uuid.uuid4().hex,url=url,platform=platform,media_type=media_type)
        with jobs_lock:jobs[job.id]=job
    threading.Thread(target=run_job,args=(job,DOWNLOAD_DIR,None),daemon=True).start()
    return jsonify(job.public()),202


@app.get('/api/bridge/status')
def bridge_status():
    if request.remote_addr not in {'127.0.0.1','::1'}:
        return jsonify(error='Local client required'),403
    return jsonify(schema='noir.harvest-host/1',browserControl=getattr(app, 'browser_workspace', None) is not None,
        downloadsPath=str(DOWNLOAD_DIR),exportsPath=str(BASE_DIR/'development-exports'),
        jobs=[job.public() for job in list(jobs.values())[-20:]])


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    if payload.get("authorized") is not True:
        return jsonify(error="Confirm that you have permission to download this content."), 400
    try:
        platform, url = normalized_source_url(str(payload.get("url", "")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    try:
        comment_import = payload.get("commentImport")
        if comment_import is not None and platform != "xiaohongshu":
            raise ValueError("Comment exports are currently supported only for RedNote posts.")
        comment_import = validate_comment_import(
            comment_import,
            note_id_from_url(url),
            payload.get("commentsAuthorized") is True,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    folder_token = str(payload.get("folderToken", ""))
    destination = folder_tokens.get(folder_token)
    if not destination or not destination.is_dir():
        return jsonify(error="Choose a valid destination folder first."), 400

    session_browser = str(payload.get("sessionBrowser", "")).lower() or None
    if session_browser not in {None, "chrome", "edge", "firefox"}:
        return jsonify(error="Choose Chrome, Edge, Firefox, or no browser session."), 400

    media_type = str(payload.get("mediaType", "video")).lower()
    if media_type not in {"video", "photo"}:
        return jsonify(error="Choose photo or video."), 400
    with rednote_download_queue_guard(platform):
        blocked = rednote_download_block_reason(platform)
        if blocked:
            return jsonify(error=blocked), 409
        cleanup_old_jobs()
        job = Job(id=uuid.uuid4().hex, url=url, platform=platform, media_type=media_type, comment_import=comment_import)
        with jobs_lock:
            jobs[job.id] = job
    threading.Thread(target=run_job, args=(job, destination, session_browser), daemon=True).start()
    return jsonify(job.public()), 202


@app.post("/api/select-folder")
def select_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Choose where collected media should be saved")
        root.destroy()
    except Exception as exc:
        return jsonify(error=f"Could not open the folder picker: {exc}"), 500
    if not selected:
        return jsonify(error="No folder was selected."), 400
    destination = Path(selected).resolve()
    token = uuid.uuid4().hex
    folder_tokens[token] = destination
    return jsonify(token=token, name=destination.name, path=str(destination))


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = jobs.get(job_id)
    return (jsonify(job.public()), 200) if job else (jsonify(error="Job not found."), 404)


@app.get("/api/jobs/<job_id>/download")
def download_job(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.archive:
        return jsonify(error="Download is not ready."), 404
    return send_file(job.archive, as_attachment=True, download_name=f"{job.platform}-media.zip")


try:
    from master_service import master_bp
    app.register_blueprint(master_bp)
except Exception as e:
    app.config['MASTER_IMPORT_ERROR'] = type(e).__name__

try:
    from app_mcp import create_mcp_blueprint
    app.register_blueprint(create_mcp_blueprint())
except Exception as e:
    app.config['MCP_IMPORT_ERROR'] = type(e).__name__

try:
    from rednote_v2.api import create_blueprint
    v2_blueprint = create_blueprint(BASE_DIR)
    app.register_blueprint(v2_blueprint)
except Exception as e:
    app.config['V2_IMPORT_ERROR'] = type(e).__name__


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
