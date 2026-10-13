# Implementation Guide for Next AI / Engineer
**Package:** `SCATTER_REDNOTE_UPGRADE_PACKAGE_2026-10-10.zip`  
**Target:** Noir Fiction Engine / RedNote Engine & Scatter Subsystem

---

## Instructions for the Next AI / Engineer

You have received this package to execute the engineering upgrades for the RedNote Engine and Scatter Note Graph. 
Follow these exact instructions and constraints.

### 1. Architectural Guardrails (Non-Negotiable)
* **Do NOT modify `rednote.sqlite` during story generation.** Use it as read-only via `ArchivePort`.
* **All authored fiction data belongs in `scatter.sqlite`** using the schema in `02_SCATTER_SCHEMA.sql`.
* **Preserve Cultural Logic:** Never apply hardcoded Western realism checks to non-Western cultural branches. Always evaluate events against the branch's declared `cultural_regime` (see Section 4.3 of `01_MASTER_ARCHITECTURE_SPECIFICATION.md`).
* **Preserve UI Art:** Do not replace or discard the Rete v2 UI canvas, custom styles, or Star Rail / black-and-green aesthetic. Wire backend adapters to the existing UI interfaces.

### 2. Immediate Execution Steps

#### Step 1: Database Setup
1. Create `scatter.sqlite` in the configured application directory.
2. Run the migration script `02_SCATTER_SCHEMA.sql` (found in the original handoff `programmer_guide/` directory).
3. Validate table creation: `scatter_projects`, `scatter_revisions`, `scatter_commands`, `scatter_runs`, `scatter_candidates`, `scatter_plans`, `scatter_documents`, `scatter_block_locks`, `scatter_issues`.

#### Step 2: Implement `ArchivePort` Adapter
1. In `scatter_engine/archive_port.py`, implement the interface:
   - `resolve_post(post_id: str) -> Optional[SourcePacket]`
   - `get_comments(post_id: str, max_depth: int) -> List[CommentTree]`
   - `get_media_assets(post_id: str) -> List[MediaAsset]`
2. Connect to the existing `rednote.sqlite` database. If an item is missing, return `SOURCE_NOT_CAPTURED` instead of crashing or inventing data.

#### Step 3: Implement the 4-Tier Comment Engine
1. In `scatter_engine/comments.py`, add classification logic:
   - Identify `creator_reply` vs `audience_reaction`.
   - Extract market and aesthetic tags (`#ootd`, `butt_meme`, `lucky_horse`).
   - Implement the `elevate_comment(comment_id, elevation_type, canon_lock)` command in `scatter.sqlite`.

#### Step 4: Implement Neuro-Symbolic Cultural Guard
1. In `scatter_engine/planner/guard.py`:
   - Read the incoming branch's `cultural_regime`.
   - If set to `CHINESE_FOLK_CARNIVAL_REDNOTE`, allow the horse avatar, lucky flatulence, and public butt-patting interactions as culturally coherent actions.
   - Halt execution and emit `waiting_for_resolution` only when internal branch consistency is broken (e.g., reverting to human form without an author-defined transformation event).

#### Step 5: Background Worker & SSE Streaming
1. Run multi-stage generation in a detached background thread (`NarrativeWorker`).
2. Expose `/api/scatter/v1/runs/{id}/events` via Server-Sent Events (SSE).
3. Record each step event into `scatter_run_events` to support resilient replay on reconnect.
