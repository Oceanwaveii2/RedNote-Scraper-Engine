"""Red Note Local Relational Database.

Stores structured metadata in SQLite while leaving large media in the filesystem,
maintaining relationships between profiles, posts, media, comments, asset cards,
and Scatter Graph bridges.
"""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rednote_profiles (
    profile_id TEXT PRIMARY KEY,
    author_name TEXT NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    profile_url TEXT NOT NULL,
    bio TEXT,
    followers_count INTEGER DEFAULT 0,
    following_count INTEGER DEFAULT 0,
    notes_count INTEGER DEFAULT 0,
    likes_count INTEGER DEFAULT 0,
    crawled_at TEXT NOT NULL,
    last_sync_at TEXT NOT NULL,
    raw_metadata TEXT
);

CREATE TABLE IF NOT EXISTS rednote_posts (
    post_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    caption TEXT,
    hashtags TEXT,
    media_type TEXT NOT NULL,
    cover_image TEXT,
    likes_count INTEGER DEFAULT 0,
    favorites_count INTEGER DEFAULT 0,
    shares_count INTEGER DEFAULT 0,
    comments_count INTEGER DEFAULT 0,
    created_at TEXT,
    crawled_at TEXT NOT NULL,
    raw_metadata TEXT,
    FOREIGN KEY(profile_id) REFERENCES rednote_profiles(profile_id)
);

CREATE TABLE IF NOT EXISTS rednote_media (
    media_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    remote_url TEXT,
    file_bytes INTEGER,
    sha256 TEXT,
    width INTEGER,
    height INTEGER,
    order_idx INTEGER DEFAULT 0,
    FOREIGN KEY(post_id) REFERENCES rednote_posts(post_id)
);

CREATE TABLE IF NOT EXISTS rednote_comments (
    comment_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    parent_comment_id TEXT,
    username TEXT,
    avatar_url TEXT,
    text_chinese TEXT NOT NULL,
    text_english TEXT,
    likes_count INTEGER DEFAULT 0,
    emojis_used TEXT,
    timestamp TEXT,
    FOREIGN KEY(post_id) REFERENCES rednote_posts(post_id)
);

CREATE TABLE IF NOT EXISTS rednote_asset_cards (
    card_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    primary_class TEXT NOT NULL,
    subtype TEXT NOT NULL,
    character_association TEXT NOT NULL,
    suggested_topics TEXT,
    scatter_status TEXT DEFAULT 'available',
    simulation_status TEXT DEFAULT 'unlinked',
    imported_assets TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(post_id) REFERENCES rednote_posts(post_id)
);

CREATE TABLE IF NOT EXISTS rednote_ingestion_graph (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL,
    parent_id TEXT,
    provenance_chain TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rednote_scatter_bridge (
    bridge_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    scatter_node_id TEXT NOT NULL,
    trait_target TEXT NOT NULL,
    trait_weight REAL DEFAULT 0.8,
    formula TEXT,
    exported_at TEXT NOT NULL,
    FOREIGN KEY(card_id) REFERENCES rednote_asset_cards(card_id)
);
"""

class ClosingConnection(sqlite3.Connection):
    """Commit/rollback a context transaction and release its file handle."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class RedNoteDatabase:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def upsert_profile(self, profile: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO rednote_profiles (
                    profile_id, author_name, display_name, avatar_url, profile_url,
                    bio, followers_count, following_count, notes_count, likes_count,
                    crawled_at, last_sync_at, raw_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    author_name=excluded.author_name,
                    display_name=excluded.display_name,
                    profile_url=excluded.profile_url,
                    avatar_url=excluded.avatar_url,
                    bio=excluded.bio,
                    followers_count=excluded.followers_count,
                    following_count=excluded.following_count,
                    notes_count=excluded.notes_count,
                    likes_count=excluded.likes_count,
                    last_sync_at=excluded.last_sync_at,
                    raw_metadata=excluded.raw_metadata
            """, (
                profile["profile_id"],
                profile.get("author_name", "Unknown"),
                profile.get("display_name", ""),
                profile.get("avatar_url", ""),
                profile["profile_url"],
                profile.get("bio", ""),
                profile.get("followers_count", 0),
                profile.get("following_count", 0),
                profile.get("notes_count", 0),
                profile.get("likes_count", 0),
                profile.get("crawled_at", datetime.now(timezone.utc).isoformat()),
                datetime.now(timezone.utc).isoformat(),
                json.dumps(profile, ensure_ascii=False)
            ))
            conn.commit()

    def upsert_post(self, post: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO rednote_posts (
                    post_id, profile_id, source_url, title, caption, hashtags,
                    media_type, cover_image, likes_count, favorites_count,
                    shares_count, comments_count, created_at, crawled_at, raw_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    title=excluded.title,
                    caption=excluded.caption,
                    hashtags=excluded.hashtags,
                    likes_count=excluded.likes_count,
                    favorites_count=excluded.favorites_count,
                    shares_count=excluded.shares_count,
                    comments_count=excluded.comments_count,
                    crawled_at=excluded.crawled_at,
                    raw_metadata=excluded.raw_metadata
            """, (
                post["post_id"],
                post["profile_id"],
                post["source_url"],
                post.get("title", ""),
                post.get("caption", ""),
                json.dumps(post.get("hashtags", []), ensure_ascii=False),
                post.get("media_type", "image_carousel"),
                post.get("cover_image", ""),
                post.get("likes_count", 0),
                post.get("favorites_count", 0),
                post.get("shares_count", 0),
                post.get("comments_count", 0),
                post.get("created_at", ""),
                post.get("crawled_at", datetime.now(timezone.utc).isoformat()),
                json.dumps(post, ensure_ascii=False)
            ))
            conn.commit()

    def upsert_asset_card(self, card: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO rednote_asset_cards (
                    card_id, post_id, primary_class, subtype, character_association,
                    suggested_topics, scatter_status, simulation_status,
                    imported_assets, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                    primary_class=excluded.primary_class,
                    subtype=excluded.subtype,
                    character_association=excluded.character_association,
                    suggested_topics=excluded.suggested_topics,
                    scatter_status=excluded.scatter_status,
                    simulation_status=excluded.simulation_status,
                    imported_assets=excluded.imported_assets,
                    notes=excluded.notes
            """, (
                card["card_id"],
                card["post_id"],
                card["primary_class"],
                card["subtype"],
                card["character_association"],
                json.dumps(card.get("suggested_topics", []), ensure_ascii=False),
                card.get("scatter_status", "available"),
                card.get("simulation_status", "unlinked"),
                json.dumps(card.get("imported_assets", []), ensure_ascii=False),
                card.get("notes", ""),
                card.get("created_at", datetime.now(timezone.utc).isoformat())
            ))
            conn.commit()

    def get_all_posts(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT * FROM rednote_posts ORDER BY crawled_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_all_asset_cards(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT * FROM rednote_asset_cards").fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        """Closes any persistent connections if necessary."""
        pass
