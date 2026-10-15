"""Revisioned, authored story documents. Provider drafts remain separate proposals."""

from __future__ import annotations

import json
import sqlite3
import uuid

from execution import ScatterExecution, _hash, _json, _utc


class StoryDocuments:
    def __init__(self, execution: ScatterExecution | None = None):
        self.execution = execution or ScatterExecution()
        with self.execution._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS story_documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, output_id TEXT NOT NULL,
                    head INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS story_document_revisions (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    parent INTEGER, content_hash TEXT NOT NULL, content_json TEXT NOT NULL,
                    source_run_id TEXT, command_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    command_hash TEXT,
                    PRIMARY KEY(document_id,revision), UNIQUE(document_id,command_id));
                CREATE UNIQUE INDEX IF NOT EXISTS story_doc_one_acceptance
                    ON story_document_revisions(source_run_id) WHERE source_run_id IS NOT NULL;
                CREATE TRIGGER IF NOT EXISTS story_doc_revision_no_update
                    BEFORE UPDATE ON story_document_revisions
                    BEGIN SELECT RAISE(ABORT,'Document revisions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS story_doc_revision_no_delete
                    BEFORE DELETE ON story_document_revisions
                    BEGIN SELECT RAISE(ABORT,'Document revisions are immutable'); END;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(story_document_revisions)")}
            if "command_hash" not in columns:
                db.execute("ALTER TABLE story_document_revisions ADD COLUMN command_hash TEXT")

    @staticmethod
    def _validate(blocks):
        if not isinstance(blocks, list) or len(blocks) > 2000:
            raise ValueError("Document requires a bounded block list")
        seen = set()
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("id"), str) or not block["id"]:
                raise ValueError("Every block needs a stable ID")
            if block["id"] in seen or not isinstance(block.get("text"), str):
                raise ValueError("Duplicate block ID or invalid text")
            if not isinstance(block.get("locked", False), bool):
                raise ValueError("Block lock must be boolean")
            seen.add(block["id"])

    def read(self, document_id: str, revision: int | None = None):
        with self.execution._db() as db:
            doc = db.execute("SELECT * FROM story_documents WHERE id=?", (document_id,)).fetchone()
            if doc is None:
                raise LookupError("Document not found")
            number = doc["head"] if revision is None else revision
            row = db.execute("SELECT * FROM story_document_revisions WHERE document_id=? AND revision=?",
                             (document_id, number)).fetchone()
        if row is None:
            raise LookupError("Document revision not found")
        content = json.loads(row["content_json"])
        if _hash(content) != row["content_hash"]:
            raise RuntimeError("Document revision checksum mismatch")
        return {"document_id": document_id, "project_id": doc["project_id"],
                "output_id": doc["output_id"], "revision": number, "head": doc["head"],
                "source_run_id": row["source_run_id"], "content_hash": row["content_hash"],
                "blocks": content["blocks"]}

    def create_from_run(self, run_id: str, expected_graph_revision: int, document_id: str | None = None):
        """Explicitly accept a draft; stale graph heads are rejected, never overwritten."""
        from story_pipeline import StoryPipeline
        run = self.execution.read_run(run_id)
        if run["state"] != "completed":
            raise ValueError("Only a completed draft can become a document")
        current = self.execution.graph.read(run["project_id"])["revision"]
        if current != expected_graph_revision or current != run["revision"]:
            raise ValueError(f"Graph changed; current revision is {current}")
        draft = StoryPipeline(self.execution).artifact(run_id, "draft")["content"]
        blocks = [{**block, "locked": False} for block in draft["blocks"]]
        self._validate(blocks)
        document_id = document_id or "scatter-doc-" + uuid.uuid4().hex
        content = {"schema": "scatter.document/1", "blocks": blocks}
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT document_id FROM story_document_revisions "
                               "WHERE source_run_id=?", (run_id,)).fetchone()
            if prior is not None:
                return self.read(prior["document_id"])
            db.execute("INSERT INTO story_documents VALUES (?,?,?,?,?)",
                       (document_id, run["project_id"], run["output_id"], 0, _utc()))
            db.execute("INSERT INTO story_document_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                       (document_id, 0, None, _hash(content), _json(content), run_id, "initial", _utc(),
                        _hash({"action": "accept-draft", "run_id": run_id})))
        return self.read(document_id)

    def apply(self, document_id: str, expected_revision: int, command_id: str, command: dict):
        if not isinstance(command_id, str) or not command_id or not isinstance(command, dict):
            raise ValueError("Command ID and command are required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected revision is required")
        with self.execution._db() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = db.execute("SELECT head FROM story_documents WHERE id=?", (document_id,)).fetchone()
            if doc is None:
                raise LookupError("Document not found")
            prior = db.execute("SELECT revision,parent,command_hash FROM story_document_revisions "
                               "WHERE document_id=? AND command_id=?",
                               (document_id, command_id)).fetchone()
            if prior is not None:
                if prior["parent"] == expected_revision and prior["command_hash"] == _hash(command):
                    return self.read(document_id, prior["revision"])
                raise ValueError("Command ID has already been used for another edit")
            if doc["head"] != expected_revision:
                raise ValueError(f"Document revision conflict: current head is {doc['head']}")
            row = db.execute("SELECT content_json FROM story_document_revisions WHERE document_id=? AND revision=?",
                             (document_id, expected_revision)).fetchone()
            content = json.loads(row[0])
            blocks = content["blocks"]
            action, block_id = command.get("action"), command.get("block_id")
            index = next((i for i, block in enumerate(blocks) if block["id"] == block_id), None)
            if action in {"edit", "lock", "unlock", "remove"}:
                if index is None:
                    raise ValueError("Target block does not exist")
                if blocks[index]["locked"] and action != "unlock":
                    raise ValueError("Locked block cannot be changed")
                if action == "edit":
                    if not isinstance(command.get("text"), str):
                        raise ValueError("Block text must be a string")
                    blocks[index]["text"] = command["text"]
                elif action in {"lock", "unlock"}:
                    blocks[index]["locked"] = action == "lock"
                else:
                    blocks.pop(index)
            elif action == "insert":
                block = command.get("block")
                if not isinstance(block, dict):
                    raise ValueError("Insert requires a block")
                insert_at = len(blocks) if block_id is None else index
                if insert_at is None:
                    raise ValueError("Insert target does not exist")
                blocks.insert(insert_at, {**block, "locked": block.get("locked", False)})
            else:
                raise ValueError("Unsupported document action")
            self._validate(blocks)
            new_revision = expected_revision + 1
            db.execute("INSERT INTO story_document_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                       (document_id, new_revision, expected_revision, _hash(content), _json(content),
                        None, command_id, _utc(), _hash(command)))
            db.execute("UPDATE story_documents SET head=? WHERE id=? AND head=?",
                       (new_revision, document_id, expected_revision))
        return self.read(document_id)
