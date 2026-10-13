"""Red Note Structured Content Ingestion Parser.

Extracts all 25+ attributes defined in Section 02 of the Codex Directive,
preserving original Chinese text and separating derived translations.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from .translation_engine import default_translator
from .emoji_cache import replace_emojis_with_unicode

class RedNoteParser:
    @staticmethod
    def parse_post_payload(raw_data: Dict[str, Any], profile_id: str = "") -> Dict[str, Any]:
        """Parses a raw note payload into the canonical 25+ field structured schema."""
        post_id = str(raw_data.get("id") or raw_data.get("postId") or "").strip()
        user_info = raw_data.get("user") or {}
        p_id = profile_id or str(user_info.get("userId") or raw_data.get("profileId") or "").strip()
        author_name = str(user_info.get("nickname") or raw_data.get("author") or "Unknown").strip()
        display_name = str(user_info.get("displayName") or author_name).strip()
        avatar = str(user_info.get("avatar") or raw_data.get("avatar") or "").strip()
        
        source_url = raw_data.get("sourceUrl") or raw_data.get("url") or f"https://www.rednote.com/discovery/item/{post_id}"
        title_cn = str(raw_data.get("title") or "").strip()
        desc_cn = str(raw_data.get("description") or raw_data.get("desc") or raw_data.get("text") or raw_data.get("caption") or "").strip()

        # Extract hashtags and mentions
        hashtags = list(dict.fromkeys(
            re.findall(r'#([^\s#]+)', desc_cn) +
            [t.get("name") if isinstance(t, dict) else str(t) for t in raw_data.get("tags", [])] +
            [t.get("name") if isinstance(t, dict) else str(t) for t in raw_data.get("tagList", [])]
        ))
        mentions = list(dict.fromkeys(re.findall(r'@([^\s@]+)', desc_cn)))

        # Clean text without modifying original
        title_en = default_translator.translate_or_lookup(title_cn)
        desc_en = default_translator.translate_or_lookup(desc_cn)

        # Image and video extraction
        images = []
        if "_harvest_image_urls" in raw_data:
            images = [u for u in raw_data["_harvest_image_urls"] if u]
        elif "images" in raw_data:
            images = [str(img) if isinstance(img, str) else img.get("url") for img in raw_data["images"]]
        elif "imageList" in raw_data:
            for img in raw_data["imageList"]:
                if isinstance(img, dict):
                    url = img.get("urlDefault") or img.get("urlPre") or next((i.get("url") for i in img.get("infoList", []) if i.get("url")), None)
                    if url: images.append(url)

        # Local media files if already saved
        media_files = raw_data.get("mediaFiles", [])

        videos = []
        if "videos" in raw_data:
            videos = raw_data["videos"]
        elif "video" in raw_data and isinstance(raw_data["video"], dict):
            streams = (raw_data["video"].get("media") or {}).get("stream") or {}
            for group in streams.values():
                for row in group if isinstance(group, list) else [group]:
                    if isinstance(row, dict) and row.get("masterUrl"):
                        videos.append({"url": row.get("masterUrl"), "width": row.get("width"), "height": row.get("height")})

        media_type = "video" if videos or any(str(f).endswith(('.mp4', '.mov')) for f in media_files) else ("image_carousel" if len(images) > 1 or len(media_files) > 1 else "single_image")
        cover_image = images[0] if images else (media_files[0] if media_files else "")

        # Comments parsing
        raw_comments = raw_data.get("comments") or raw_data.get("_harvest_public_comments") or []
        comments = []
        for c in raw_comments:
            if isinstance(c, dict):
                text_cn = c.get("text") or c.get("content") or ""
                if not text_cn: continue
                comments.append({
                    "commentId": c.get("id") or c.get("commentId") or f"c_{len(comments)+1}",
                    "username": c.get("author") or c.get("username") or "RedNote User",
                    "avatar": c.get("avatar") or "",
                    "textChinese": text_cn,
                    "textEnglish": default_translator.translate_or_lookup(text_cn),
                    "textRendered": replace_emojis_with_unicode(text_cn),
                    "likes": int(c.get("likes") or c.get("likeCount") or 0),
                    "isReply": bool(c.get("reply") or c.get("parentCommentId")),
                    "replies": c.get("replies", [])
                })

        now = datetime.now(timezone.utc).isoformat()
        return {
            "postId": post_id,
            "profileId": p_id,
            "authorName": author_name,
            "displayName": display_name,
            "avatarUrl": avatar,
            "postUrl": source_url,
            "originalSourceUrl": source_url,
            "title": title_cn,
            "titleEnglish": title_en,
            "description": desc_cn,
            "descriptionEnglish": desc_en,
            "descriptionRendered": replace_emojis_with_unicode(desc_cn),
            "hashtags": hashtags,
            "mentions": mentions,
            "creationDate": raw_data.get("createdAt") or raw_data.get("created_at") or "",
            "updateDate": raw_data.get("updatedAt") or "",
            "mediaType": media_type,
            "images": images,
            "mediaFiles": media_files,
            "videos": videos,
            "coverImage": cover_image,
            "likes": int(raw_data.get("likes") or raw_data.get("likes_count") or 0),
            "favorites": int(raw_data.get("favorites") or raw_data.get("favorites_count") or 0),
            "shares": int(raw_data.get("shares") or raw_data.get("shares_count") or 0),
            "commentCount": int(raw_data.get("reportedComments") or len(comments)),
            "comments": comments,
            "scrapeTimestamp": now,
            "sourceMetadata": {
                "extractor": "RedNoteIE/CodexModular",
                "verifiedDOM": True,
                "coverage": raw_data.get("commentCoverage", "visible-DOM-only")
            }
        }
