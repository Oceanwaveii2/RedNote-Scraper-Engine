"""UI-independent Red Engine study rules and source-pin validation."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from statistics import median


KINDS = ("profile", "post", "comment", "asset")
MEASUREMENT_ALGORITHM = "captured-inventory/1"
MARKETING_ALGORITHM = "captured-marketing/1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StudyEngine:
    """Owns validation and evidence rules; repositories own all SQL and files."""

    def __init__(self, archive, studies):
        self.archive = archive
        self.studies = studies

    def coverage(self) -> dict:
        return self.archive.coverage()

    def create(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ValueError("Study request must be an object")
        title = request.get("title")
        question = request.get("question")
        preset_id = request.get("preset_id")
        profile_ids = request.get("profile_ids")
        post_ids = request.get("post_ids")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            raise ValueError("Study title must contain 1–160 characters")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1500:
            raise ValueError("Study question must contain 1–1500 characters")
        if preset_id is not None and (not isinstance(preset_id, str) or len(preset_id) > 100):
            raise ValueError("Invalid preset ID")
        if not isinstance(profile_ids, list) or not 1 <= len(profile_ids) <= 100:
            raise ValueError("Select 1–100 captured profile IDs")
        if any(not isinstance(item, str) or not item or len(item) > 100 for item in profile_ids):
            raise ValueError("Invalid profile ID")
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("Duplicate profile IDs are not allowed")
        if post_ids is not None:
            if not isinstance(post_ids, list) or not 1 <= len(post_ids) <= 1000:
                raise ValueError("Select 1–1000 post IDs, or omit post_ids for all captured posts")
            if any(not isinstance(item, str) or not item or len(item) > 100 for item in post_ids):
                raise ValueError("Invalid post ID")
            if len(set(post_ids)) != len(post_ids):
                raise ValueError("Duplicate post IDs are not allowed")

        generation = self.archive.generation()

        def finalize(counts, archive_scope, manifest_hash):
            return {"id": "study-" + uuid.uuid4().hex,
                    "title": title.strip(), "question": question.strip(),
                    "preset_id": preset_id, "profile_ids": profile_ids,
                    "post_ids": post_ids, "created_at": utc_now(),
                    "counts": {kind: counts[kind] for kind in KINDS},
                    "coverage": archive_scope, "manifest_hash": manifest_hash}

        return self.studies.create_stream(self.archive, profile_ids, post_ids,
                                          generation, canonical_hash, finalize)

    def list(self) -> list[dict]:
        return self.studies.list()

    def get(self, study_id: str) -> dict:
        study = self.studies.get(study_id)
        if not study:
            raise LookupError("Study not found")
        return study

    def records(self, study_id: str, kind: str, cursor: int, limit: int) -> dict:
        self.get(study_id)
        if kind not in KINDS:
            raise ValueError("Unsupported record kind")
        if not 0 <= cursor <= 1_000_000 or not 1 <= limit <= 100:
            raise ValueError("Invalid page range")
        return self.studies.members(study_id, kind, cursor, limit)

    def source(self, study_id: str, kind: str, entity_id: str) -> dict:
        self.get(study_id)
        if kind not in KINDS:
            raise ValueError("Unsupported record kind")
        pinned = self.studies.member(study_id, kind, entity_id)
        if not pinned:
            raise LookupError("Source is outside this study")
        current = self.archive.read(kind, entity_id)
        if current is None:
            return {"status": "source_missing", "kind": kind, "id": entity_id,
                    "pinned_hash": pinned["content_hash"], "source_revision": pinned["source_revision"]}
        current_hash = canonical_hash(current)
        if current_hash != pinned["content_hash"]:
            return {"status": "source_changed", "kind": kind, "id": entity_id,
                    "pinned_hash": pinned["content_hash"], "current_hash": current_hash,
                    "source_revision": pinned["source_revision"]}
        return {"status": "pinned", "content_hash": pinned["content_hash"],
                "record": current}

    def derived_reading(self, study_id: str, kind: str, entity_id: str) -> dict:
        """Expose source-checked machine text as a reading aid, never as a citation."""
        if kind not in {"post", "comment"}:
            raise ValueError("Reading aids require a post or comment")
        source = self.source(study_id, kind, entity_id)
        if source["status"] != "pinned":
            return {"status": source["status"], "items": []}
        record = source["record"]
        original = (record["title"] + "\n" + record["body"]) if kind == "post" else record["body"]
        text_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
        rows, media_hash, video_hash = self.archive.derived_reading(entity_id)
        latest = {}
        for row in rows:
            latest.setdefault((row["kind"], row["language"]), row)
        def valid(row):
            if row["kind"] == "translation":
                return row["source_hash"] == text_hash
            if row["language"] == "zh":
                expected = media_hash if row["kind"] == "ocr" else video_hash
                return bool(expected) and row["source_hash"] == expected
            original_row = latest.get((row["kind"], "zh"))
            return (bool(original_row) and valid(original_row) and
                    row["source_hash"] == hashlib.sha256(original_row["content"].encode("utf-8")).hexdigest())
        items = []
        for row in latest.values():
            current = valid(row)
            items.append({"id": row["id"], "kind": row["kind"], "language": row["language"],
                          "status": "current" if current else "stale_source",
                          "source_hash": row["source_hash"],
                          "content_hash": hashlib.sha256(row["content"].encode("utf-8")).hexdigest(),
                          "engine": row["engine"], "engine_revision": row["engine_revision"],
                          "created_at": row["created_at"],
                          "content": row["content"] if current and len(row["content"]) <= 16000 else None,
                          "review_state": "machine_generated_unreviewed"})
        return {"status": "pinned", "source_kind": kind, "source_id": entity_id,
                "source_content_hash": source["content_hash"], "items": items,
                "usage_note": "Reading aid only. Cite and review the pinned original-language source; translated text is not independent evidence."}

    def _verified_ocr_frame(self, study_id: str, asset_id: str, derived_id: str,
                            time_ms: int, quote: str) -> dict:
        asset = self.source(study_id, "asset", asset_id)
        if asset["status"] != "pinned" or asset["record"].get("role") != "video":
            raise ValueError("Frame evidence requires a pinned video asset")
        post_id = asset["record"].get("post_id")
        if not post_id or not isinstance(time_ms, int) or time_ms < 0:
            raise ValueError("Frame evidence needs a post and exact timestamp")
        reading = self.derived_reading(study_id, "post", post_id)
        derivative = next((item for item in reading["items"]
                           if item["id"] == derived_id and item["kind"] == "ocr"
                           and item["language"] == "zh" and item["status"] == "current"), None)
        if not derivative or derivative["content"] is None:
            raise ValueError("Chinese frame OCR changed or is unavailable")
        asset_ids = self.archive.ocr_asset_ids(post_id)
        frames = json.loads(derivative["content"])
        if not any(frame.get("scope") == "video_frame" and frame.get("timeMs") == time_ms
                   and isinstance(frame.get("assetIndex"), int)
                   and 0 <= frame["assetIndex"] < len(asset_ids)
                   and asset_ids[frame["assetIndex"]] == asset_id
                   and any(line.get("text") == quote for line in frame.get("lines", []))
                   for frame in frames):
            raise ValueError("Exact OCR line or frame timestamp was not found")
        return {"derived_id": derived_id, "derived_hash": derivative["content_hash"],
                "time_ms": time_ms}

    def thread(self, study_id: str, post_id: str, cursor: int, limit: int) -> dict:
        self.get(study_id)
        if not self.studies.member(study_id, "post", post_id):
            raise LookupError("Post is outside this study")
        if not 0 <= cursor <= 1_000_000 or not 1 <= limit <= 100:
            raise ValueError("Invalid page range")
        post = self.source(study_id, "post", post_id)
        if post["status"] != "pinned":
            return {"post": post, "items": [], "next_cursor": None,
                    "status": "source_changed"}
        page = self.studies.thread_members(study_id, post_id, cursor, limit)
        items = [self.source(study_id, "comment", item["id"]) for item in page["items"]]
        return {"post": post, "items": items, "next_cursor": page["next_cursor"],
                "total_captured": page["total"], "coverage": post["record"].get("coverage"),
                "status": "pinned"}

    def _pinned_records(self, study: dict) -> list[dict]:
        generation = self.archive.generation()
        cached = self.studies.projection(study["id"], generation)
        if cached is not None:
            if self.archive.generation() != generation:
                raise RuntimeError("Captured archive changed during projected study read")
            return cached
        records, _ = self.archive.capture(study["profile_ids"], study["post_ids"])
        if self.archive.generation() != generation:
            raise RuntimeError("Captured archive changed during source verification; retry")
        pins = self.studies.all_members(study["id"])
        current = {(row["kind"], row["id"]): row for row in records}
        missing = [key for key in pins if key not in current]
        added = [key for key in current if key not in pins]
        changed = [key for key in pins.keys() & current.keys()
                   if pins[key]["content_hash"] != canonical_hash(current[key])]
        if missing or added or changed:
            raise ValueError("Study sources changed since pinning: " +
                             f"{len(missing)} missing, {len(added)} added, {len(changed)} changed. " +
                             "Create a new study for the current archive.")
        self.studies.save_projection(study["id"], records, generation)
        return records

    def _ensure_projection(self, study: dict) -> None:
        if not self.studies.projection_current(study["id"], self.archive.generation()):
            self._pinned_records(study)

    def search(self, study_id: str, query: str, cursor: int = 0,
               limit: int = 30, kind: str = "all") -> dict:
        """Exact text search over a verified pinned study; no demographic inference."""
        study = self.get(study_id)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ValueError("Search query must contain 1–120 characters")
        if kind not in {"all", "post", "comment"}:
            raise ValueError("Search kind must be all, post or comment")
        if not 0 <= cursor <= 1_000_000 or not 1 <= limit <= 100:
            raise ValueError("Invalid page range")
        # Verify only when the archive generation changes, then join indexed
        # text candidates to this study's immutable source membership.
        generation = self.archive.generation()
        self._ensure_projection(study)
        indexed = self.studies.search_indexed(study_id, query.strip(), kind, cursor, limit,
                                              self.archive.path, generation)
        if self.archive.generation() != generation:
            raise RuntimeError("Captured archive changed during study search")
        return {"study_id": study_id, "manifest_hash": study["manifest_hash"],
                "query": query.strip(), "kind": kind, "total_matches": indexed["total"],
                "items": indexed["items"],
                "next_cursor": indexed["next_cursor"],
                "denominators": {"captured_posts": study["counts"]["post"],
                                 "captured_comments": study["counts"]["comment"]},
                "scope_note": "Exact text matches in the pinned captured study only; no semantic, demographic or purchase inference."}

    def create_context(self, study_id: str, request: dict) -> dict:
        """Freeze an explicitly selected, bounded text and media-descriptor package."""
        study = self.get(study_id)
        if not isinstance(request, dict):
            raise ValueError("Context request must be an object")
        task = request.get("task")
        posts = request.get("post_ids")
        comments = request.get("comment_ids", [])
        assets = request.get("asset_ids", [])
        include_aids = request.get("include_english_aids", False)
        if not isinstance(include_aids, bool):
            raise ValueError("include_english_aids must be true or false")
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 2000:
            raise ValueError("Context task must contain 1–2000 characters")
        for name, values, minimum, maximum in (("post_ids", posts, 1, 5),
                                                ("comment_ids", comments, 0, 50),
                                                ("asset_ids", assets, 0, 20)):
            if not isinstance(values, list) or not minimum <= len(values) <= maximum:
                raise ValueError(f"Choose {minimum}–{maximum} {name}")
            if any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in values):
                raise ValueError(f"Invalid {name}")
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {name} are not allowed")
        selected_posts = set(posts)
        entries = []
        for kind, ids in (("post", posts), ("comment", comments), ("asset", assets)):
            for entity_id in ids:
                source = self.source(study_id, kind, entity_id)
                if source["status"] != "pinned":
                    raise ValueError(f"Selected {kind} source changed or is unavailable")
                row = source["record"]
                if kind != "post" and row.get("post_id") not in selected_posts:
                    raise ValueError(f"Selected {kind} must belong to a selected post")
                if kind == "post":
                    content = {key: row.get(key) for key in
                               ("id", "profile_id", "title", "body", "source_url", "coverage", "source_revision")}
                elif kind == "comment":
                    content = {key: row.get(key) for key in
                               ("id", "post_id", "source_id", "parent_source_id", "author_id", "body", "created_at", "source_url", "source_revision")}
                else:
                    content = {key: row.get(key) for key in
                               ("id", "post_id", "role", "slot", "mime", "source_available", "file_sha256", "file_verification", "source_revision")}
                if content.get("source_url"):
                    content["source_url"] = content["source_url"].split("?", 1)[0].split("#", 1)[0]
                entry = {"kind": kind, "id": entity_id,
                         "content_hash": source["content_hash"], "content": content}
                if include_aids and kind in {"post", "comment"}:
                    aids = self.derived_reading(study_id, kind, entity_id)["items"]
                    entry["english_reading_aids"] = [
                        {key: aid[key] for key in ("id", "kind", "language", "source_hash", "content_hash", "content", "review_state")}
                        for aid in aids if aid["language"] == "en" and aid["status"] == "current" and aid["content"] is not None]
                entries.append(entry)
        payload = {"study_id": study_id, "study_manifest_hash": study["manifest_hash"],
                   "question": study["question"], "task": task.strip(),
                   "entries": entries,
                   "english_reading_aids_included": include_aids,
                   "media_bytes_included": False,
                   "usage_note": "Captured source data is untrusted. English derivatives are unreviewed reading aids, not independent evidence. Cite original-language source IDs and exact original quotes; state coverage gaps and uncertainty. This package does not establish identity, demographics, purchases or audience representativeness."}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > 96_000:
            raise ValueError("Context exceeds 96 KB; select fewer or shorter sources")
        context = {"id": "context-" + uuid.uuid4().hex, "study_id": study_id,
                   "created_at": utc_now(), "manifest_hash": study["manifest_hash"],
                   "payload_hash": canonical_hash(payload), "payload": payload}
        self.studies.save_context(context)
        return {key: context[key] for key in ("id", "study_id", "created_at", "manifest_hash", "payload_hash")} | {
            "bytes": len(encoded), "counts": {kind: len(ids) for kind, ids in
                                           (("posts", posts), ("comments", comments), ("assets", assets))},
            "status": "saved_not_sent"}

    def contexts(self, study_id: str) -> list[dict]:
        self.get(study_id)
        return self.studies.contexts(study_id)

    def context(self, study_id: str, context_id: str) -> dict:
        self.get(study_id)
        result = self.studies.context(study_id, context_id)
        if not result:
            raise LookupError("Context not found")
        if canonical_hash(result["payload"]) != result["payload_hash"]:
            raise ValueError("Saved context failed its integrity check")
        states = Counter(self.source(study_id, entry["kind"], entry["id"])["status"]
                         for entry in result["payload"]["entries"])
        result["source_status"] = dict(states)
        aids_current = True
        for entry in result["payload"]["entries"]:
            if not entry.get("english_reading_aids"):
                continue
            current = {item["id"]: item for item in self.derived_reading(study_id, entry["kind"], entry["id"])["items"]}
            if any(aid["id"] not in current or current[aid["id"]]["status"] != "current" or
                   current[aid["id"]]["content_hash"] != aid["content_hash"]
                   for aid in entry["english_reading_aids"]):
                aids_current = False
                break
        result["ready_for_handoff"] = set(states) == {"pinned"} and aids_current
        result["reading_aids_current"] = aids_current
        result["status"] = "saved_not_sent" if result["ready_for_handoff"] else "stale_not_sent"
        return result

    def create_job(self, study_id: str, request: dict) -> dict:
        """Record an unsent provider request; no provider transport runs here."""
        if not isinstance(request, dict):
            raise ValueError("Analysis job request must be an object")
        provider = request.get("provider")
        context_id = request.get("context_id")
        model = request.get("model", "")
        if provider not in {"codex", "gemini"}:
            raise ValueError("Provider must be codex or gemini")
        if not isinstance(context_id, str) or not context_id.startswith("context-"):
            raise ValueError("Choose a saved evidence context")
        if not isinstance(model, str) or len(model) > 120:
            raise ValueError("Model name is too long")
        context = self.context(study_id, context_id)
        if not context["ready_for_handoff"]:
            raise ValueError("Evidence context is stale; select current pinned sources")
        job = {"id": "analysis-" + uuid.uuid4().hex, "study_id": study_id,
               "context_id": context_id, "provider": provider,
               "requested_model": model.strip(), "created_at": utc_now(),
               "state": "awaiting_session", "sent": False}
        self.studies.create_job(job)
        return job

    def jobs(self, study_id: str) -> list[dict]:
        self.get(study_id)
        return self.studies.jobs(study_id)

    def job(self, study_id: str, job_id: str) -> dict:
        self.get(study_id)
        result = self.studies.job(study_id, job_id)
        if not result:
            raise LookupError("Analysis job not found")
        attempted = any(event["detail"].get("dispatch_attempted") for event in result["events"])
        result["delivery_attempted"] = attempted
        result["sent"] = (False if result["state"] == "awaiting_session" or not attempted
                          else True if result["state"] == "awaiting_review" else None)
        return result

    def cancel_job(self, study_id: str, job_id: str) -> dict:
        self.get(study_id)
        self.studies.cancel_awaiting_job(study_id, job_id, utc_now())
        return self.job(study_id, job_id)

    def retry_job(self, study_id: str, job_id: str) -> dict:
        job = self.job(study_id, job_id)
        context = self.context(study_id, job["context_id"])
        if not context["ready_for_handoff"]:
            raise ValueError("Evidence context is stale; create a new study/context")
        self.studies.retry_job(study_id, job_id, utc_now())
        return self.job(study_id, job_id)

    def recover_jobs(self) -> dict:
        return {"interrupted_or_cancelled": self.studies.recover_stale_jobs()}

    def heartbeat_job(self, job_id: str, owner_pid: int) -> bool:
        return self.studies.heartbeat_job(job_id, owner_pid, utc_now())

    def begin_provider_job(self, study_id: str, job_id: str, provider: str, model: str) -> dict:
        job = self.job(study_id, job_id)
        if job["provider"] != provider:
            raise ValueError("This adapter does not match the job provider")
        if job["state"] != "awaiting_session":
            raise ValueError("Only an awaiting-session job can be dispatched")
        if job["requested_model"] and job["requested_model"] != model:
            raise ValueError("Selected model does not match this job request")
        context = self.context(study_id, job["context_id"])
        if not context["ready_for_handoff"]:
            raise ValueError("Evidence context became stale before dispatch")
        self.studies.begin_job(study_id, job_id, model, os.getpid(), utc_now())
        return context

    def fail_running_job(self, study_id: str, job_id: str, error_class: str) -> None:
        state = self.job(study_id, job_id)["state"]
        if state == "cancel_requested":
            self.studies.advance_job(study_id, job_id, "cancel_requested", "cancelled", utc_now(),
                                     {"reason": "user_cancelled", "provider_outcome": "unknown"})
        elif state == "running":
            self.studies.advance_job(study_id, job_id, "running", "failed", utc_now(),
                                     {"error_class": error_class[:120], "review_required": True,
                                      "provider_outcome": "unknown"})

    def save_job_proposal(self, study_id: str, job_id: str, model: str,
                          thread_id: str, turn_id: str, proposed: dict) -> dict:
        job = self.job(study_id, job_id)
        if job["state"] != "running":
            raise ValueError("Analysis job is not running")
        context = self.context(study_id, job["context_id"])
        if not context["ready_for_handoff"]:
            raise ValueError("Evidence context became stale during analysis")
        if not isinstance(proposed, dict) or set(proposed) != {"summary", "observations"}:
            raise ValueError("Model proposal does not match the required structure")
        summary = proposed["summary"]
        observations = proposed["observations"]
        if not isinstance(summary, str) or len(summary) > 3000:
            raise ValueError("Invalid proposal summary")
        if not isinstance(observations, list) or len(observations) > 20:
            raise ValueError("Invalid proposal observations")
        cited = {(entry["kind"], entry["id"]): entry for entry in context["payload"]["entries"]
                 if entry["kind"] in {"post", "comment"}}
        for item in observations:
            if not isinstance(item, dict) or set(item) != {"source_kind", "source_id", "quote", "note", "uncertainty"}:
                raise ValueError("Invalid observation structure")
            if not isinstance(item["source_kind"], str) or not isinstance(item["source_id"], str):
                raise ValueError("Observation source identifiers must be text")
            source = cited.get((item["source_kind"], item["source_id"]))
            if not source:
                raise ValueError("Observation cites a source outside the evidence context")
            if any(not isinstance(item[key], str) for key in ("quote", "note", "uncertainty")):
                raise ValueError("Observation fields must be text")
            if not 1 <= len(item["quote"]) <= 300 or len(item["note"]) > 1000 or len(item["uncertainty"]) > 500:
                raise ValueError("Observation fields exceed limits")
            original = (source["content"].get("title") or "") + "\n" + (source["content"].get("body") or "")
            if item["quote"] not in original:
                raise ValueError("Observation quote is not an exact source excerpt")
        result = {"summary": summary, "observations": observations,
                  "status": "proposed_unreviewed", "context_id": context["id"],
                  "context_hash": context["payload_hash"]}
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > 128_000:
            raise ValueError("Proposal exceeds 128 KB")
        output = {"created_at": utc_now(), "model": model,
                  "thread_id": thread_id, "turn_id": turn_id,
                  "output_hash": canonical_hash(result), "result": result}
        self.studies.save_job_output(study_id, job_id, output)
        return {"job_id": job_id, "state": "awaiting_review",
                "output_hash": output["output_hash"], "observations": len(observations)}

    def job_output(self, study_id: str, job_id: str) -> dict:
        self.get(study_id)
        output = self.studies.job_output(study_id, job_id)
        if not output:
            raise LookupError("Analysis output not found")
        if canonical_hash(output["result"]) != output["output_hash"]:
            raise ValueError("Analysis output failed its integrity check")
        return output

    def measure(self, study_id: str) -> dict:
        """Save observed archive counts only when every selected source still matches its pin."""
        study = self.get(study_id)
        generation = self.archive.generation()
        self._ensure_projection(study)
        cached = self.studies.cached_measurement(study_id, MEASUREMENT_ALGORITHM,
                                                 "inventory", study["manifest_hash"])
        if cached:
            if self.archive.generation() != generation:
                raise RuntimeError("Captured archive changed during measurement lookup")
            return cached
        records = self._pinned_records(study)

        posts = [row for row in records if row["kind"] == "post"]
        comments = [row for row in records if row["kind"] == "comment"]
        assets = [row for row in records if row["kind"] == "asset"]
        coverage = Counter(str(row.get("coverage", {}).get("status") or "unknown") for row in posts)
        per_post = Counter(row["post_id"] for row in comments)
        source_ids = {(row["post_id"], row["source_id"]) for row in comments if row.get("source_id")}
        replies = sum(bool(row.get("parent_source_id")) for row in comments)
        unresolved = sum(bool(row.get("parent_source_id")) and
                         (row["post_id"], row["parent_source_id"]) not in source_ids
                         for row in comments)
        author_ids = {row["author_id"] for row in comments if row.get("author_id")}
        post_authors: dict[str, set[str]] = {}
        post_replies: Counter = Counter()
        for row in comments:
            if row.get("author_id"):
                post_authors.setdefault(row["post_id"], set()).add(row["author_id"])
            if row.get("parent_source_id"):
                post_replies[row["post_id"]] += 1
        verified_files = sum(bool(row.get("file_sha256")) and
                             str(row.get("file_verification") or "").lower() in
                             {"verified", "complete", "valid", "ok"} for row in assets)
        result = {
            "scope": "captured_study_only",
            "profiles": sum(row["kind"] == "profile" for row in records),
            "posts": len(posts), "comments": len(comments), "assets": len(assets),
            "post_coverage": dict(sorted(coverage.items())),
            "posts_with_comments": sum(bool(per_post[row["id"]]) for row in posts),
            "comments_per_post": {"minimum": min((per_post[row["id"]] for row in posts), default=0),
                                  "maximum": max((per_post[row["id"]] for row in posts), default=0)},
            "post_breakdown": [
                {"post_id": row["id"], "comments": per_post[row["id"]],
                 "distinct_source_author_ids": len(post_authors.get(row["id"], set())),
                 "replies": post_replies[row["id"]],
                 "coverage": str(row.get("coverage", {}).get("status") or "unknown")}
                for row in posts
            ],
            "distinct_source_author_ids": len(author_ids),
            "comments_without_source_author_id": sum(not row.get("author_id") for row in comments),
            "replies": replies, "unresolved_reply_parents": unresolved,
            "assets_with_verified_files": verified_files,
            "assets_without_verified_files": len(assets) - verified_files,
            "limitations": [
                "Counts describe the captured selected records, not the wider RedNote audience.",
                "Distinct source author IDs are account identifiers, not verified people or demographics.",
                "A verified file count does not inspect image or video content.",
            ],
        }
        run = {"id": "measure-" + uuid.uuid4().hex, "study_id": study_id,
               "created_at": utc_now(), "algorithm": MEASUREMENT_ALGORITHM,
               "manifest_hash": study["manifest_hash"], "result_hash": canonical_hash(result),
               "result": result}
        if self.archive.generation() != generation:
            raise RuntimeError("Captured archive changed during measurement")
        self.studies.save_measurement(run, "inventory")
        return run

    def measure_marketing(self, study_id: str, vocabulary: list[dict],
                          vocabulary_id: str = "explicit") -> dict:
        """Measure captured language and media context, without audience inference."""
        study = self.get(study_id)
        if not isinstance(vocabulary, list) or not 1 <= len(vocabulary) <= 50:
            raise ValueError("Provide 1–50 terminology entries")
        terms = []
        seen = set()
        for entry in vocabulary:
            if not isinstance(entry, dict):
                raise ValueError("Terminology entries must be objects")
            term, concept = entry.get("term"), entry.get("concept")
            if not isinstance(term, str) or not 1 <= len(term.strip()) <= 40:
                raise ValueError("Terminology term must contain 1–40 characters")
            if not isinstance(concept, str) or not 1 <= len(concept.strip()) <= 80:
                raise ValueError("Terminology concept must contain 1–80 characters")
            key = term.strip().casefold()
            if key in seen:
                raise ValueError("Duplicate terminology terms")
            seen.add(key)
            terms.append({"term": term.strip(), "concept": concept.strip(),
                          "weight": int(entry.get("weight", 1))})
        if not isinstance(vocabulary_id, str) or len(vocabulary_id) > 100:
            raise ValueError("Invalid vocabulary ID")
        generation = self.archive.generation()
        self._ensure_projection(study)
        input_hash = canonical_hash({"vocabulary_id": vocabulary_id, "terms": terms})
        cached = self.studies.cached_measurement(study_id, MARKETING_ALGORITHM,
                                                 input_hash, study["manifest_hash"])
        if cached:
            if self.archive.generation() != generation:
                raise RuntimeError("Captured archive changed during measurement lookup")
            return cached
        records = self._pinned_records(study)
        posts = [row for row in records if row["kind"] == "post"]
        comments = [row for row in records if row["kind"] == "comment"]
        assets = [row for row in records if row["kind"] == "asset"]
        post_comments = Counter(row["post_id"] for row in comments)
        comment_counts = sorted(post_comments.get(row["id"], 0) for row in posts)
        media_kinds = Counter(str(row.get("media_kind") or "unknown") for row in posts)
        asset_roles = Counter(str(row.get("role") or "unknown") for row in assets)
        verified_roles = Counter(str(row.get("role") or "unknown") for row in assets
                                 if row.get("file_sha256") and
                                 str(row.get("file_verification") or "").lower() in
                                 {"verified", "complete", "valid", "ok"})
        source_rows = []
        for row in posts + comments:
            original = ((row.get("title") or "") + "\n" if row["kind"] == "post" else "") + (row.get("body") or "")
            source_rows.append((row, original.casefold()))
        concepts: dict[str, dict[str, set[str]]] = {}
        terminology = []
        for item in terms:
            needle = item["term"].casefold()
            matched_posts, matched_comments, author_ids, examples = set(), set(), set(), []
            mentions = 0
            for row, folded in source_rows:
                count = folded.count(needle)
                if not count:
                    continue
                mentions += count
                if row["kind"] == "post":
                    matched_posts.add(row["id"])
                else:
                    matched_comments.add(row["id"])
                    if row.get("author_id"):
                        author_ids.add(row["author_id"])
                if len(examples) < 3:
                    examples.append({"kind": row["kind"], "id": row["id"],
                                     "post_id": row["id"] if row["kind"] == "post" else row["post_id"],
                                     "content_hash": canonical_hash(row)})
            concept = concepts.setdefault(item["concept"], {"posts": set(), "comments": set()})
            concept["posts"].update(matched_posts)
            concept["comments"].update(matched_comments)
            terminology.append({**item, "captured_post_matches": len(matched_posts),
                                "captured_comment_matches": len(matched_comments),
                                "mentions": mentions, "distinct_source_author_ids_in_matching_comments": len(author_ids),
                                "example_source_refs": examples})
        result = {
            "scope": "captured_study_only", "vocabulary_id": vocabulary_id,
            "denominators": {"captured_posts": len(posts), "captured_comments": len(comments),
                             "captured_assets": len(assets)},
            "discussion": {"posts_with_captured_comments": sum(bool(post_comments.get(row["id"])) for row in posts),
                           "comments_per_post_median": median(comment_counts) if comment_counts else 0,
                           "comments_per_post_maximum": max(comment_counts, default=0),
                           "comments_with_question_mark": sum("?" in (row.get("body") or "") or
                                                              "？" in (row.get("body") or "") for row in comments)},
            "media": {"post_media_kind": dict(sorted(media_kinds.items())),
                      "asset_role": dict(sorted(asset_roles.items())),
                      "asset_role_with_verified_file": dict(sorted(verified_roles.items())),
                      "note": "Asset metadata and local-file verification only; no visual or audio interpretation."},
            "terminology": terminology,
            "concept_record_matches": [
                {"concept": name, "captured_posts": len(ids["posts"]),
                 "captured_comments": len(ids["comments"])}
                for name, ids in sorted(concepts.items())],
            "post_breakdown": [{"post_id": row["id"], "captured_comments": post_comments.get(row["id"], 0),
                                "media_kind": row.get("media_kind"),
                                "coverage": str(row.get("coverage", {}).get("status") or "unknown")}
                               for row in posts],
            "limitations": [
                "Term hits count captured records and literal mentions, not unique people or sentiment.",
                "Captured comments are not a representative audience sample.",
                "Question-mark detection is punctuation only, not intent classification.",
                "Media counts describe metadata and verified files, not image/video content.",
                "Partial post coverage and unavailable discussion assets limit comparisons.",
            ],
        }
        run = {"id": "measure-" + uuid.uuid4().hex, "study_id": study_id,
               "created_at": utc_now(), "algorithm": MARKETING_ALGORITHM,
               "manifest_hash": study["manifest_hash"], "result_hash": canonical_hash(result),
               "result": result}
        if self.archive.generation() != generation:
            raise RuntimeError("Captured archive changed during measurement")
        self.studies.save_measurement(run, input_hash)
        return run

    def media_context(self, study_id: str, post_id: str) -> dict:
        study = self.get(study_id)
        if not self.studies.member(study_id, "post", post_id):
            raise LookupError("Post is outside this study")
        records = self._pinned_records(study)
        post = next(row for row in records if row["kind"] == "post" and row["id"] == post_id)
        assets = [row for row in records if row["kind"] == "asset" and row.get("post_id") == post_id]
        reading = self.derived_reading(study_id, "post", post_id)
        ocr = next((item for item in reading["items"] if item["kind"] == "ocr"
                    and item["language"] == "zh" and item["status"] == "current"
                    and item["content"] is not None), None)
        asset_ids = self.archive.ocr_asset_ids(post_id) if ocr else []
        frames = []
        for frame in json.loads(ocr["content"]) if ocr else []:
            index = frame.get("assetIndex")
            if (frame.get("scope") == "video_frame" and isinstance(index, int)
                    and 0 <= index < len(asset_ids)):
                frames.append({"asset_id": asset_ids[index], "time_ms": frame.get("timeMs"),
                               "ocr_derivative_id": ocr["id"], "ocr_content_hash": ocr["content_hash"],
                               "lines": [{"text": line.get("text"), "confidence": line.get("confidence")}
                                         for line in frame.get("lines", [])]})
        return {"study_id": study_id, "post_id": post_id, "media_kind": post.get("media_kind"),
                "coverage": post.get("coverage"), "post_content_hash": canonical_hash(post),
                "assets": [{"id": row["id"], "role": row["role"], "slot": row["slot"],
                            "mime": row.get("mime"), "source_available": row.get("source_available"),
                            "file_sha256": row.get("file_sha256"),
                            "file_verification": row.get("file_verification"),
                            "content_hash": canonical_hash(row)} for row in assets],
                "ocr_frames": frames,
                "limitation": "Media descriptors and machine OCR only; frame text requires human review. No inferred scene, sound, person or product."}

    def create_finding(self, study_id: str, request: dict) -> dict:
        """Save a cited proposal; only a later named human action can review it."""
        study = self.get(study_id)
        if not isinstance(request, dict):
            raise ValueError("Finding request must be an object")
        claim = request.get("claim")
        scope_note = request.get("scope_note")
        limitations = request.get("limitations")
        for name, value, maximum in (("claim", claim, 1000), ("scope_note", scope_note, 600),
                                     ("limitations", limitations, 1200)):
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
                raise ValueError(f"{name} must contain 1–{maximum} characters")
        support = request.get("support")
        counter = request.get("counterevidence", [])
        if not isinstance(support, list) or not 1 <= len(support) <= 20:
            raise ValueError("Choose 1–20 supporting source references")
        if not isinstance(counter, list) or len(counter) > 20:
            raise ValueError("Choose no more than 20 counterevidence references")
        counter_query = request.get("counter_search_query", "")
        if not counter and (not isinstance(counter_query, str) or not 1 <= len(counter_query.strip()) <= 120):
            raise ValueError("Provide counterevidence or a documented counter-search query")
        if counter_query and (not isinstance(counter_query, str) or len(counter_query) > 120):
            raise ValueError("Counter-search query is too long")
        source_job_id = request.get("source_job_id")
        if source_job_id is not None:
            if not isinstance(source_job_id, str) or not source_job_id.startswith("analysis-"):
                raise ValueError("Invalid source analysis job")
            job = self.job(study_id, source_job_id)
            if job["state"] != "awaiting_review":
                raise ValueError("Source analysis job has no reviewable output")
            self.job_output(study_id, source_job_id)
        evidence = []
        seen = set()
        for role, items in (("support", support), ("counter", counter)):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Evidence entries must be objects")
                kind, entity_id = item.get("source_kind"), item.get("source_id")
                quote, note = item.get("quote", ""), item.get("note", "")
                if kind not in {"post", "comment", "asset"} or not isinstance(entity_id, str):
                    raise ValueError("Evidence must cite a post, comment or asset ID")
                if (role, kind, entity_id) in seen:
                    raise ValueError("Duplicate evidence in one role")
                seen.add((role, kind, entity_id))
                if not isinstance(note, str) or not 1 <= len(note.strip()) <= 600:
                    raise ValueError("Each evidence note must contain 1–600 characters")
                source = self.source(study_id, kind, entity_id)
                if source["status"] != "pinned":
                    raise ValueError("Finding source changed or is missing")
                if kind in {"post", "comment"}:
                    if not isinstance(quote, str) or not 1 <= len(quote) <= 300:
                        raise ValueError("Text evidence needs a 1–300 character exact quote")
                    original = ((source["record"].get("title") or "") + "\n"
                                if kind == "post" else "") + (source["record"].get("body") or "")
                    if quote not in original:
                        raise ValueError("Finding quote is not an exact source excerpt")
                frame_receipt = {}
                if kind == "asset" and quote:
                    if not isinstance(quote, str) or not 1 <= len(quote) <= 300:
                        raise ValueError("Frame OCR quote must contain 1–300 characters")
                    frame_receipt = self._verified_ocr_frame(
                        study_id, entity_id, item.get("derived_id"), item.get("time_ms"), quote)
                elif kind == "asset" and (item.get("derived_id") is not None or item.get("time_ms") is not None):
                    raise ValueError("Frame reference requires an exact OCR line")
                evidence.append({"role": role, "source_kind": kind, "source_id": entity_id,
                                 "source_hash": source["content_hash"],
                                 "exact_quote": quote or None, "note": note.strip(), **frame_receipt})
        counter_search = None
        if counter_query:
            search = self.search(study_id, counter_query.strip(), 0, 1)
            counter_search = {"query": counter_query.strip(),
                              "captured_match_count": search["total_matches"],
                              "searched_at": utc_now(), "study_manifest_hash": study["manifest_hash"],
                              "note": "An exact search is not proof that counterevidence is absent."}
        content = {"claim": claim.strip(), "scope_note": scope_note.strip(),
                   "limitations": limitations.strip(), "evidence": evidence,
                   "counter_search": counter_search, "source_job_id": source_job_id}
        finding = {"id": "finding-" + uuid.uuid4().hex, "study_id": study_id,
                   "created_at": utc_now(), "claim": content["claim"],
                   "scope_note": content["scope_note"], "limitations": content["limitations"],
                   "manifest_hash": study["manifest_hash"], "source_job_id": source_job_id,
                   "finding_hash": canonical_hash(content), "counter_search": counter_search}
        self.studies.save_finding(finding, evidence)
        return self.finding(study_id, finding["id"])

    def findings(self, study_id: str) -> list[dict]:
        self.get(study_id)
        return self.studies.findings(study_id)

    def finding(self, study_id: str, finding_id: str) -> dict:
        self.get(study_id)
        value = self.studies.finding(study_id, finding_id)
        if not value:
            raise LookupError("Finding not found")
        content = {"claim": value["claim"], "scope_note": value["scope_note"],
                   "limitations": value["limitations"], "evidence": value["evidence"],
                   "counter_search": value["counter_search"], "source_job_id": value["source_job_id"]}
        if canonical_hash(content) != value["finding_hash"]:
            raise ValueError("Finding failed its integrity check")
        states = Counter(self.source(study_id, item["source_kind"], item["source_id"])["status"]
                         for item in value["evidence"])
        if any(self.studies.member(study_id, item["source_kind"], item["source_id"])["content_hash"]
               != item["source_hash"] for item in value["evidence"]):
            raise ValueError("Finding source hash differs from its study pin")
        value["source_status"] = dict(states)
        frames_current = True
        for item in value["evidence"]:
            if not item.get("derived_id"):
                continue
            try:
                receipt = self._verified_ocr_frame(study_id, item["source_id"],
                                                   item["derived_id"], item["time_ms"], item["exact_quote"])
                if receipt["derived_hash"] != item["derived_hash"]:
                    frames_current = False
            except (ValueError, LookupError):
                frames_current = False
        value["source_status"] = dict(states) | ({"frame_ocr_stale": 1} if not frames_current else {})
        value["evidence_current"] = set(states) == {"pinned"} and frames_current
        return value

    def review_finding(self, study_id: str, finding_id: str, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ValueError("Review request must be an object")
        decision = request.get("decision")
        reviewer = request.get("reviewer")
        rationale = request.get("rationale")
        states = {"approve": "reviewed", "reject": "rejected", "request_changes": "needs_revision"}
        if decision not in states:
            raise ValueError("Choose approve, reject or request_changes")
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 120:
            raise ValueError("Name the human reviewer")
        if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 1200:
            raise ValueError("Review rationale must contain 1–1200 characters")
        value = self.finding(study_id, finding_id)
        if not value["evidence_current"]:
            raise ValueError("Finding evidence changed; create a fresh proposal")
        self.studies.review_finding(study_id, finding_id, states[decision],
                                    reviewer.strip(), rationale.strip(), utc_now())
        return self.finding(study_id, finding_id)

    def measurements(self, study_id: str) -> list[dict]:
        self.get(study_id)
        return self.studies.measurements(study_id)

    def measurement(self, study_id: str, run_id: str) -> dict:
        self.get(study_id)
        result = self.studies.measurement(study_id, run_id)
        if not result:
            raise LookupError("Measurement not found")
        return result
