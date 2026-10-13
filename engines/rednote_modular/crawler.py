"""Red Note Profile Crawler & Incremental Synchronization Engine.

Coordinates URL type detection, progressive pagination, duplicate prevention,
checkpoints, and rate-limited profile ingestion.
"""
from __future__ import annotations
import re
import time
import json
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlsplit

from .parser import RedNoteParser
from .media_manager import WorkspaceManager
from .database import RedNoteDatabase
from .topic_grouper import analyze_topics, suggest_class_and_character

class RedNoteCrawler:
    def __init__(self, workspace_manager: WorkspaceManager, database: RedNoteDatabase):
        self.workspace_manager = workspace_manager
        self.database = database
        self.rate_limit_cooldown = 45.0  # seconds between downloads
        self.post_pacing_seconds = 60.0  # seconds between post visits
        self._last_request_time = 0.0

    @staticmethod
    def detect_url_type(url: str) -> Dict[str, str]:
        """Classifies input URL into profile, post, explore, or invalid."""
        clean = str(url or "").strip()
        parsed = urlsplit(clean)
        if parsed.scheme not in ("http", "https"):
            # Check for local schemes like rednote://
            if clean.startswith("rednote://profile/"):
                return {"type": "local_profile", "id": clean.replace("rednote://profile/", "").strip("/")}
            if clean.startswith("rednote://post/"):
                return {"type": "local_post", "id": clean.replace("rednote://post/", "").strip("/")}
            return {"type": "unknown", "id": ""}

        path = parsed.path
        # Profile detection: /user/profile/{id}
        profile_match = re.search(r'/user/profile/([a-f0-9]{24})', path)
        if profile_match:
            return {"type": "profile", "id": profile_match.group(1)}

        # Post detection: /discovery/item/{id} or /explore/{id}
        post_match = re.search(r'/(?:discovery/item|explore)/([a-f0-9]{24})', path)
        if post_match:
            return {"type": "post", "id": post_match.group(1)}

        return {"type": "generic_page", "id": ""}

    def ingest_structured_post(self, raw_post: Dict[str, Any], profile_id: str = "") -> Dict[str, Any]:
        """Ingests a single post into workspace, database, asset card, and taxonomy."""
        parsed = RedNoteParser.parse_post_payload(raw_post, profile_id)
        post_id = parsed["postId"]
        p_id = parsed["profileId"] or profile_id or "default_profile"

        # Auto-suggest taxonomy & topic grouping (Section 11)
        topic_suggestions = analyze_topics(parsed["description"], parsed["hashtags"])
        class_char = suggest_class_and_character(parsed["description"], parsed["hashtags"])

        # Create Asset Card (Section 07 & 08)
        card_id = f"card_{post_id}"
        card_record = {
            "card_id": card_id,
            "post_id": post_id,
            "primary_class": class_char["suggestedClass"],
            "subtype": "Standard",
            "character_association": class_char["suggestedCharacter"],
            "suggested_topics": [t["topic"] for t in topic_suggestions],
            "scatter_status": "ready_for_export",
            "simulation_status": "unlinked",
            "imported_assets": parsed["mediaFiles"],
            "notes": f"Auto-inducted from Red Note post {post_id}",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Store in workspace filesystem
        self.workspace_manager.store_post_record(p_id, post_id, parsed)

        # Store in local SQLite
        self.database.upsert_post({
            "post_id": post_id,
            "profile_id": p_id,
            "source_url": parsed["postUrl"],
            "title": parsed["title"],
            "caption": parsed["description"],
            "hashtags": parsed["hashtags"],
            "media_type": parsed["mediaType"],
            "cover_image": parsed["coverImage"],
            "likes_count": parsed["likes"],
            "favorites_count": parsed["favorites"],
            "shares_count": parsed["shares"],
            "comments_count": parsed["commentCount"],
            "created_at": parsed["creationDate"],
            "crawled_at": parsed["scrapeTimestamp"]
        })
        self.database.upsert_asset_card(card_record)

        parsed["assetCard"] = card_record
        parsed["topicSuggestions"] = topic_suggestions
        return parsed

    def check_incremental_update(self, profile_id: str, new_post_ids: List[str]) -> Dict[str, Any]:
        """Section 18: Checks known posts for updates and identifies new un-downloaded posts."""
        existing_posts = {p["post_id"] for p in self.database.get_all_posts() if p.get("profile_id") == profile_id}
        new_items = [pid for pid in new_post_ids if pid not in existing_posts]
        existing_items = [pid for pid in new_post_ids if pid in existing_posts]
        return {
            "profileId": profile_id,
            "totalScanned": len(new_post_ids),
            "newPostsToDownload": new_items,
            "knownPostsToVerify": existing_items,
            "syncTime": datetime.now(timezone.utc).isoformat()
        }
