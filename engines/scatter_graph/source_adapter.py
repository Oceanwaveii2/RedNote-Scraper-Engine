"""Read-only bridge from authored Scatter references to pinned Red Engine evidence."""

from __future__ import annotations

import sys
from paths import RESEARCH

if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from study_core import StudyEngine
from study_storage import SqliteArchive, SqliteStudies
try:
    from .authored_sources import AuthoredSources, ARCHIVE_ID as AUTHORED_ARCHIVE_ID
except ImportError:
    from authored_sources import AuthoredSources, ARCHIVE_ID as AUTHORED_ARCHIVE_ID


class RedNoteSourceAdapter:
    ARCHIVE_ID = "rednote-v2"
    KINDS = {"profile", "post", "comment", "asset"}
    FIELDS = {
        "profile": ("id", "name", "nickname", "bio", "description", "source_url", "coverage", "source_revision"),
        "post": ("id", "profile_id", "title", "body", "media_kind", "source_url", "coverage", "inventory", "source_revision"),
        "comment": ("id", "post_id", "parent_source_id", "author_id", "author", "avatar_id",
                    "body", "likes", "created_at", "source_url", "source_revision"),
        "asset": ("id", "post_id", "role", "slot", "mime", "source_available", "file_sha256", "file_verification", "source_revision"),
    }

    def __init__(self, archive=None, studies=None):
        self.engine = StudyEngine(archive or SqliteArchive(), studies or SqliteStudies())

    def pin(self, study_id: str, kind: str, entity_id: str) -> dict:
        if kind not in self.KINDS:
            raise ValueError("Unsupported archive entity kind")
        result = self.engine.source(study_id, kind, entity_id)
        if result["status"] != "pinned":
            raise ValueError(f"Source is {result['status']}; a current pin is required")
        return {"archive_id": self.ARCHIVE_ID, "study_id": study_id,
                "entity_kind": kind, "entity_id": entity_id,
                "content_hash": result["content_hash"],
                "source_revision": result.get("record", {}).get("source_revision")}

    def catalog(self, study_id: str, kind: str, cursor: int = 0,
                limit: int = 50) -> dict:
        """Page study membership for a source picker without reading media bytes."""
        page = self.engine.records(study_id, kind, cursor, limit)
        items = []
        for member in page["items"]:
            reference = {"archive_id": self.ARCHIVE_ID, "study_id": study_id,
                         "entity_kind": kind, "entity_id": member["id"],
                         "content_hash": member["content_hash"],
                         "source_revision": member.get("source_revision")}
            status = self.resolve(reference)["status"]
            items.append({"id": member["id"], "post_id": member.get("post_id"),
                          "status": status, "reference": reference})
        return {"study_id": study_id, "kind": kind, "items": items,
                "total": page["total"], "next_cursor": page["next_cursor"]}

    def resolve(self, reference: dict) -> dict:
        if not isinstance(reference, dict) or reference.get("archive_id") != self.ARCHIVE_ID:
            raise ValueError("Unknown archive reference")
        kind = reference.get("entity_kind")
        study_id = reference.get("study_id")
        entity_id = reference.get("entity_id")
        expected = reference.get("content_hash")
        if (kind not in self.KINDS or not all(isinstance(value, str) and value
                                               for value in (study_id, entity_id, expected))
                or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)):
            raise ValueError("Source reference needs a study, entity kind, ID and SHA-256")
        try:
            result = self.engine.source(study_id, kind, entity_id)
        except LookupError:
            return {"status": "pin_missing", "archive_id": self.ARCHIVE_ID,
                    "study_id": study_id, "entity_kind": kind, "entity_id": entity_id}
        status = result["status"]
        if status == "pinned" and result["content_hash"] != expected:
            status = "reference_changed"
        return {"status": status, "archive_id": self.ARCHIVE_ID,
                "study_id": study_id, "entity_kind": kind, "entity_id": entity_id,
                "expected_hash": expected,
                "current_hash": result.get("content_hash", result.get("current_hash"))}

    def read_pinned(self, reference: dict) -> dict:
        """Read selected captured evidence; never read local media bytes or credentials."""
        state = self.resolve(reference)
        if state["status"] != "pinned":
            raise ValueError(f"Source is {state['status']}")
        kind = reference["entity_kind"]
        result = self.engine.source(reference["study_id"], kind, reference["entity_id"])
        if result["status"] != "pinned" or result["content_hash"] != reference["content_hash"]:
            raise ValueError("Source changed while preparing the output packet")
        record = result["record"]
        content = {key: record.get(key) for key in self.FIELDS[kind] if key in record}
        if content.get("source_url"):
            content["source_url"] = content["source_url"].split("?", 1)[0].split("#", 1)[0]
        if kind == "post":
            archive = self.engine.archive
            _, media_hash, video_hash = archive.derived_reading(reference["entity_id"])
            reading = self.engine.derived_reading(reference["study_id"], kind, reference["entity_id"])
            content["media"] = {"verified_media_hash": media_hash,
                                "verified_video_hash": video_hash,
                                "status": "verified_local" if media_hash else "unavailable_or_unverified",
                                "reading_aids": reading["items"]}
        elif kind == "comment":
            reading = self.engine.derived_reading(reference["study_id"], kind, reference["entity_id"])
            content["reading_aids"] = reading["items"]
        return {"kind": kind, "id": reference["entity_id"],
                "study_id": reference["study_id"], "content_hash": reference["content_hash"],
                "content": content}


class ScatterSourceAdapter:
    """Route pinned mirror and authored references without mixing their storage."""

    def __init__(self, mirror=None, authored=None):
        self.mirror = mirror or RedNoteSourceAdapter()
        self.authored = authored or AuthoredSources()

    def _store(self, reference):
        if not isinstance(reference, dict):
            raise ValueError("Source reference must be an object")
        archive_id = reference.get("archive_id")
        if archive_id == AUTHORED_ARCHIVE_ID:
            return self.authored
        if archive_id == self.mirror.ARCHIVE_ID:
            return self.mirror
        raise ValueError("Unknown source archive")

    def resolve(self, reference):
        return self._store(reference).resolve(reference)

    def read_pinned(self, reference):
        return self._store(reference).read_pinned(reference)

    def catalog(self, study_id, kind, cursor=0, limit=50):
        return self.mirror.catalog(study_id, kind, cursor, limit)

    def pin(self, study_id, kind, entity_id):
        return self.mirror.pin(study_id, kind, entity_id)
