"""Master Red Note Service Extensions.

Integrates the local SQLite relational database, profile reconstruction,
non-destructive curation selection sets, LLM context packs, and Scatter handoffs
directly into the Reel Harvest Flask application.
"""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, Response, abort, jsonify, request, send_file, send_from_directory

import app as harvest_app
from rednote.database import RedNoteDatabase, ClosingConnection
from rednote.taxonomy import CLASSES, CHARACTER_ASSOCIATIONS, TOPIC_CATEGORIES
from profile_archive import ProfileArchiveStore, sanitize, _write

master_bp = Blueprint("master_rednote", __name__)

DB_PATH = harvest_app.BASE_DIR / "rednote.sqlite"
db = RedNoteDatabase(DB_PATH)
archive_store = ProfileArchiveStore(harvest_app.BASE_DIR)
SELECTION_PATH = harvest_app.BASE_DIR / "master_selection.json"

def get_current_selection():
    return archive_store._read(SELECTION_PATH, {"profileId": None, "postIds": [], "excludedPostIds": []})

def save_current_selection(payload):
    profile_id = payload.get("profileId")
    profile = archive_store.get_profile(profile_id) if profile_id else None
    if profile_id and not profile:
        raise ValueError("Profile not found")
    valid = {p["post_id"] for p in (profile or {}).get("posts", [])}
    excluded = [p for p in payload.get("excludedPostIds", []) if p in valid]
    included = [p for p in payload.get("postIds", []) if p in valid and p not in excluded]
    selection = {"profileId": profile_id, "postIds": included, "excludedPostIds": excluded, "updatedAt": datetime.now(timezone.utc).isoformat()}
    _write(SELECTION_PATH, selection)
    return selection

def build_context_pack(profile_id=None, post_ids=None):
    selected = get_current_selection()
    profile_id = profile_id or selected.get("profileId")
    profile = archive_store.get_profile(profile_id) if profile_id else None
    if post_ids is None:
        post_ids = selected.get("postIds", []) if profile_id == selected.get("profileId") else [p["post_id"] for p in (profile or {}).get("posts", [])]
    posts = [p for p in (archive_store.get_post(post_id) for post_id in post_ids) if p and (not profile_id or p["profile_id"] == profile_id)]
    return {"schema": "rednote.context-pack/2", "generatedAt": datetime.now(timezone.utc).isoformat(),
            "profile": {k: v for k, v in (profile or {}).items() if k not in {"posts", "raw_metadata"}},
            "includedNotesCount": len(posts), "totalCommentsCount": sum(len(p.get("comments", [])) for p in posts),
            "posts": posts, "selection": selected, "providerUpload": False,
            "coverage": (profile or {}).get("manifest") or (profile or {}).get("coverage", {})}

def get_db():
    conn = sqlite3.connect(str(DB_PATH), factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn

@master_bp.get("/api/master/profiles")
def get_profiles():
    return jsonify(profiles=archive_store.list_profiles(), selection=get_current_selection())

@master_bp.get("/api/master/profiles/<profile_id>")
def get_profile_detail(profile_id: str):
    profile = archive_store.get_profile(profile_id)
    if not profile:
        return jsonify(error="Profile not found"), 404
    return jsonify(profile={k: v for k, v in profile.items() if k != "posts"}, posts=profile["posts"])

@master_bp.get("/api/master/stats")
def get_stats():
    with get_db() as conn:
        profile_count = conn.execute("SELECT COUNT(*) FROM rednote_profiles").fetchone()[0]
        post_count = conn.execute("SELECT COUNT(*) FROM rednote_posts").fetchone()[0]
        video_count = conn.execute("SELECT COUNT(*) FROM rednote_posts WHERE media_type = 'video' OR cover_image LIKE '%.mp4'").fetchone()[0]
        comment_count = conn.execute("SELECT COUNT(*) FROM rednote_comments").fetchone()[0]
        card_count = conn.execute("SELECT COUNT(*) FROM rednote_asset_cards").fetchone()[0]
        set_count = conn.execute("SELECT COUNT(*) FROM rednote_selection_sets").fetchone()[0]
        return jsonify(
            stats={
                "total_profiles": profile_count,
                "total_posts": post_count,
                "total_videos": video_count,
                "total_photos": post_count - video_count,
                "total_comments": comment_count,
                "total_cards": card_count,
                "total_sets": set_count,
            }
        )

@master_bp.get("/api/master/posts")
def get_posts():
    q = (request.args.get("q") or "").strip()
    profile_id = request.args.get("profileId") or get_current_selection().get("profileId")
    profile = archive_store.get_profile(profile_id) if profile_id else None
    posts = (profile or {}).get("posts", [])
    if q:
        posts = [p for p in posts if q.casefold() in (p.get("title", "") + " " + p.get("caption", "") + " " + p["post_id"]).casefold()]
    return jsonify(posts=posts)

@master_bp.get("/api/master/posts/<post_id>")
def get_post_detail(post_id: str):
    post = archive_store.get_post(post_id)
    if not post:
        return jsonify(error="Post not found"), 404
    with get_db() as conn:
        card = conn.execute("SELECT * FROM rednote_asset_cards WHERE post_id = ?", (post_id,)).fetchone()
    comments = [{**c, "comment_id": c["id"], "parent_comment_id": c.get("parentId"), "username": c.get("author"), "text_chinese": c.get("text"), "text_english": c.get("textEnglish"), "likes_count": c.get("likes", 0)} for c in post.get("comments", [])]
    return jsonify(post=post, comments=comments, media=post.get("media", []), coverage=post.get("coverage", {}), assetCard=dict(card) if card else None)

@master_bp.route("/api/master/selection", methods=["GET", "POST"])
def master_selection():
    if request.method == "GET":
        return jsonify(selection=get_current_selection())
    try:
        return jsonify(selection=save_current_selection(request.get_json(silent=True) or {}))
    except ValueError as error:
        return jsonify(error=str(error)), 400

@master_bp.get("/api/master/context-pack")
def get_context_pack():
    ids = request.args.get("postIds")
    return jsonify(pack=build_context_pack(request.args.get("profileId"), ids.split(",") if ids is not None else None))

@master_bp.get("/api/master/cards")
def get_cards():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*, p.title as post_title, p.cover_image, p.profile_id 
            FROM rednote_asset_cards c
            LEFT JOIN rednote_posts p ON c.post_id = p.post_id
            ORDER BY c.created_at DESC
        """).fetchall()
        return jsonify(cards=[dict(r) for r in rows], classes=CLASSES, characters=CHARACTER_ASSOCIATIONS)

@master_bp.get("/api/master/sets")
def get_selection_sets():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM rednote_selection_sets ORDER BY updated_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["included_post_ids"] = json.loads(d.get("included_post_ids") or "[]")
            d["excluded_post_ids"] = json.loads(d.get("excluded_post_ids") or "[]")
            result.append(d)
        return jsonify(sets=result)

@master_bp.post("/api/master/sets")
def save_selection_set():
    payload = request.get_json(silent=True) or {}
    set_id = payload.get("set_id") or f"set_{uuid.uuid4().hex[:8]}"
    name = payload.get("name") or "Curated Set"
    profile_id = payload.get("profile_id")
    inc = json.dumps(payload.get("included_post_ids", []), ensure_ascii=False)
    exc = json.dumps(payload.get("excluded_post_ids", []), ensure_ascii=False)
    prompt = payload.get("prompt", "")
    target = payload.get("scatter_target", "Character SuperNode")
    now = datetime.now(timezone.utc).isoformat()
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO rednote_selection_sets (set_id, name, profile_id, included_post_ids, excluded_post_ids, prompt, scatter_target, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(set_id) DO UPDATE SET
                name=excluded.name,
                included_post_ids=excluded.included_post_ids,
                excluded_post_ids=excluded.excluded_post_ids,
                prompt=excluded.prompt,
                scatter_target=excluded.scatter_target,
                updated_at=excluded.updated_at
        """, (set_id, name, profile_id, inc, exc, prompt, target, now, now))
        conn.commit()
    return jsonify(success=True, set_id=set_id, message="Selection set saved.")

@master_bp.post("/api/master/llm/context-pack")
def generate_context_pack():
    payload = request.get_json(silent=True) or {}
    return jsonify(pack=build_context_pack(payload.get("profile_id"), payload.get("post_ids")))

@master_bp.post("/api/master/scatter/send")
def send_to_scatter():
    payload = request.get_json(silent=True) or {}
    post_ids = payload.get("post_ids", [])
    prompt = payload.get("prompt", "")
    destination = payload.get("destination", "Character SuperNode")
    
    handoff_record = {
        "handoffId": f"scatter_handoff_{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "destinationSuperNode": destination,
        "operatorPrompt": prompt,
        "selectedPostsCount": len(post_ids),
        "postIds": post_ids,
        "status": "saved_to_local_scatter_inbox",
        "context": build_context_pack(payload.get("profile_id"), post_ids),
    }
    
    inbox = harvest_app.BASE_DIR / "scatter_inbox"
    inbox.mkdir(exist_ok=True)
    out_file = inbox / f"{handoff_record['handoffId']}.json"
    out_file.write_text(json.dumps(handoff_record, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return jsonify(success=True, handoff=handoff_record, message="Selected local source context saved to the Scatter inbox.")

@master_bp.get("/media/<path:filename>")
def serve_media_file(filename: str):
    media_dir = harvest_app.BASE_DIR / "static" / "media"
    return send_from_directory(media_dir, filename)

@master_bp.get("/api/master/media/<path:filename>")
def serve_archived_media(filename):
    path = archive_store.local_path(filename)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, as_attachment=False)

@master_bp.get("/api/master/archive/<profile_id>/<path:filename>")
def serve_profile_archive(profile_id, filename):
    if not archive_store.get_profile(profile_id):
        abort(404)
    directory = (archive_store.root / profile_id).resolve()
    path = (directory / filename).resolve()
    try:
        path.relative_to(directory)
    except ValueError:
        abort(404)
    if path.suffix == ".html" and path.is_file():
        content = path.read_text(encoding="utf-8")
        def rewrite(match):
            from urllib.parse import unquote
            attribute, value = match.groups()
            candidate = (path.parent / unquote(value)).resolve()
            try:
                relative = candidate.relative_to(archive_store.base_dir).as_posix()
            except ValueError:
                return attribute + '=""'
            return attribute + '="' + archive_store.media_url(relative) + '"'
        content = re.sub(r'(src|poster)="([^"]+)"', rewrite, content)
        return Response(content, mimetype="text/html")
    return send_from_directory(archive_store.root / profile_id, filename)
