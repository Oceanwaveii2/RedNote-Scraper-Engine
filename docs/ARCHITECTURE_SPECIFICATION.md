# Scatter & RedNote Engine: Architectural Specification & Upgrade Blueprint (v1.0)
**Date:** 2026-10-10  
**Target:** Noir Fiction Engine / RedNote Acquisition / Scatter Note Graph Subsystem  
**Authority:** Derived from `SCATTER_REDNOTE_BUILD_HANDOFF_2026-10-09.zip`, `AGENTS.md`, and Live Code Audits.

---

## 1. Executive Summary & Purpose

This package provides the complete, unified architectural design, flaw audit, and implementation roadmap for:
1. **RedNote Ingestion Engine (`Reel_Harvest` / `profile_clone.py` / `rednote_engine`):** High-fidelity data scraping, session management, anti-bot safeguards, and comment tree harvesting.
2. **Scatter Fiction Graph Engine (`scatter.sqlite` / Rete v2):** Non-destructive authored narrative graphs, multi-branch storytelling, and real-time execution.
3. **Cultural Ontology & Neuro-Symbolic State Machine:** Cultural regime registers solving the "Normal vs. Surreal" context trap (e.g. Chinese Year of the Horse zodiac festive lore vs. Western literary realism).
4. **Narrative AI Stack Integration:** Pragmatic synthesis of DeepMind Dramatron, ConWriter, Re3, DOC, and PLOTTER into a clean 3-stage worker.
5. **Audience-to-Canon Pipeline:** 4-tier comment elevation converting viral audience engagement and humor into verified in-world dialogue and canon facts.

---

## 2. Flaw & Error Audit (Identified Risks & Direct Solutions)

During rigorous second-pass analysis of the handoff contracts, existing code, and workflows, five critical edge-case flaws were identified. All upgrades in this specification directly resolve them:

### Flaw 1: The "Western Realism" Precondition Failure (Cultural Trap)
* **Error:** Conventional neuro-symbolic planners (e.g., standard ConWriter rules) evaluate world states against universal physical realism. An event where Rainee attends a billionaire luxury gala in horse form while farting fortune red envelopes and firecrackers would be rejected as an "illegal hallucinated state."
* **Fix:** Introduced **Branch-Scoped Cultural Reality Registers (`cultural_regime`)**. Precondition validation is evaluated relative to the active branch's cultural regime (e.g., `CHINESE_FOLK_CARNIVAL_REDNOTE`), where zodiac mascots, "patting the horse butt for wealth" (摸马屁招财), and festive absurdity are socially valid and coherent.

### Flaw 2: Cross-Database Coupling & Archive Mutation
* **Error:** Early scripts attempted to write Scatter authoring nodes directly into `rednote.sqlite` or modify archive records during fiction generation, corrupting source evidence.
* **Fix:** Hard isolation. `rednote.sqlite` is strictly read-only to narrative generation. All authored state, revisions, commands, candidates, and plans live in the isolated `scatter.sqlite` database, referencing archive records strictly by `archiveInstanceId` + `entityId` + `sha256` hash.

### Flaw 3: Audience Comment Conflation & Dialogue Promotion Gaps
* **Error:** Viewers' jokes or comments about a character's aesthetic or curves ("huge ass booty") were either blindly treated as factual truths about the creator, or completely discarded as unverified noise.
* **Fix:** A **4-Tier Comment Elevation Pipeline**. Audience sentiment is categorized as market/audience cues by default. Explicit `elevate_comment` operations allow the author or engine to promote witty comments or reply chains into in-world dialogue, background gossip, or locked canon traits with strict speaker provenance.

### Flaw 4: Synchronous Request-Thread AI Execution & Client Collisions
* **Error:** `master_service.py` and `prototype_graph/server.py` invoked generation directly on Flask/HTTP worker threads, risking dropped connections, gateway timeouts, and state collisions on single-turn local bridge clients.
* **Fix:** Implemented an asynchronous background `NarrativeWorker` with a durable state machine (`queued` -> `interpreting` -> `planning` -> `writing` -> `validating`), monotonic SSE streaming on `/api/scatter/v1/runs/{id}/events` with `Last-Event-ID` reconnection, and serialized per-provider request queues.

### Flaw 5: In-Flight Graph Edits Overwriting Author Work
* **Error:** If an author modified node text while a background generation was running, the returning story draft would overwrite the author's new edits without warning.
* **Fix:** Strict optimistic concurrency via `expectedRevision`. Late-arriving runs against an outdated graph are flagged as `stale` and preserved as sibling artifacts rather than overwriting head document state.

---

## 3. Unified Architecture & Software Stack

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             REDNOTE ARCHIVE SUBSYSTEM                            │
│  (Authoritative Acquisition • ProfileCloneService • Port 5182 Headless Engine)   │
│  • Profiles, Post Carousels (1-9 images), Keyframe MP4s, Threaded Comments       │
│  • Storage: rednote.sqlite + Content-Addressed Media (clone_media/<sha256>.<ext>)│
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                         ArchivePort (Bounded Evidence Read)
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            SCATTER ENGINE & RETE CANVAS                          │
│  (Authored Fiction State • scatter.sqlite • Port 5186 Gateway • Multi-Branch)    │
│  • Graph Nodes & Edges (Branch Scopes: Core Trunk vs. Year of the Horse Branch)  │
│  • Actor Releases (Rainee identity vs. Form transformations)                     │
│  • Authored Overrides & Comment Elevation Primitives                             │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                          ContextCompiler (Budget & Omission Checks)
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         NARRATIVE WORKER & AI TECH STACK                         │
│  Stage 1: Proposal (Dramatron) ──► 3 Compact Branch Loglines & Card Role Beats   │
│  Stage 2: Plan (PLOTTER + ConWriter) ──► Causal Graph + Cultural Reality Checks  │
│  Stage 3: Prose (Re3 + DOC) ──► Stable Document Blocks & Passage Lock Protection │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             RICH DOCUMENT OUTPUT NODE                            │
│  • In-Place Candidate Selector (Choose 1 of 3) • Block Locks (text/fact/endpoint)│
│  • Real-Time SSE Status Stream • Reversible Command Undo/Redo Log                │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Subsystem Specifications

### 4.1. RedNote Acquisition Engine Upgrades
1. **Isolated Browser Profiles:** Persistent Chromium `user-data-dir` storing session cookies safely without leaking tokens to disk logs.
2. **Proactive Health Probes:** `/api/engine/session/health` endpoint detecting login decay or captcha challenges prior to queue execution.
3. **Adaptive Behavioral Pacing:** Replaces static `25s`/`1.5s` timers with Gaussian randomized scrolling, viewport pauses, and jitter. Automatic pause on HTTP 429 into `waiting_for_user_verification`.
4. **Full Carousel & Video Stream Capture:** Scrapes all 9 photos in high-res CDN formats; extracts high-bitrate MP4 streams and generates keyframe triplets (start, middle, end) with SHA-256 digests.
5. **Hierarchical Comment Tree Harvesting:** Recursively navigates "View more replies" (`展开更多回复`) to preserve nested parent-child comment threads, reply-to user IDs, and like counts.

### 4.2. Scatter Engine & Schema Specifications
* Authored database uses `scatter.sqlite` conforming to `02_SCATTER_SCHEMA.sql`.
* **Non-Evictable Context:** The Context Compiler guarantees that core actor identity, author overrides, selected candidate rules, and locked endpoints cannot be dropped. If token capacity is exceeded, it returns `CONTEXT_BUDGET_EXCEEDED` rather than silently cutting facts.
* **Durable Event Streaming:** Runs stream step events via Server-Sent Events (SSE). Reconnection via `Last-Event-ID` retrieves missed events from `scatter_run_events`.

### 4.3. Cultural Regime & State Transition Machine
* Branch nodes store `cultural_regime` metadata.
* **Chinese Folk Carnival Regime (`CHINESE_FOLK_CARNIVAL_REDNOTE`):**
  * Allows zodiac mascot manifestations in luxury venues.
  * Treats "patting the horse butt" as an auspicious wealth ritual (`摸马屁招财`), setting social reactions to reverence, celebration, and selfies rather than panic.
  * Evaluates flatulence emissions of red envelopes and firecrackers as legitimate blessings.
* The state machine halts **only** on genuine continuity defects (e.g. unprovoked identity shifts or unmotivated location jumps).

### 4.4. The 4-Tier Comment Pipeline
* **Tier 0 (Disambiguation):** Separates creator replies from general audience comments and pinned posts.
* **Tier 1 (Audience Sentiment):** Extracts market reception tags (`#ootd`, `butt_meme`, `luxury_envy`) stored as unverified audience cues.
* **Tier 2 (Dialogue Banter Extraction):** Groups high-wit reply threads into structured banter snippets for ambient scene dialogue.
* **Tier 3 (Canon Elevation):** The author or engine executes `elevate_comment`, converting a comment into a locked character trait, dialogue line, or plot goal.

---

## 5. Phased Build & Acceptance Roadmap

1. **Phase 1 (M0 - M1): Infrastructure, Schema & Gateway**
   * Apply `02_SCATTER_SCHEMA.sql` to `scatter.sqlite`.
   * Stand up loopback gateway with `Origin` and `X-RedNote-Action: explicit` guards.
   * Run contract test fixtures.
2. **Phase 2 (M2 - M3): ArchivePort & Context Compiler**
   * Connect `ArchivePort` to existing `rednote.sqlite`.
   * Bind 4 real asset cards to Rainee's protagonist definition.
   * Implement token budgeting and override conflict detection.
3. **Phase 3 (M4): 3-Candidate Proposal Flow**
   * Wire local signed-in Codex/Gemini bridges.
   * Generate 3 compact Dramatron-style candidate interpretations for the branch.
4. **Phase 4 (M5): Causal Plotter & Cultural Guard**
   * Implement ConWriter + PLOTTER event graph with branch-scoped cultural registers.
   * Enforce locked transformation endpoint (Year of the Horse fixture).
   * Render linked story prose with stable block IDs.
5. **Phase 5 (M6 - M7): Rich Output Node & Music Influence**
   * Enable in-place candidate selection, block locking, and stale-run detection in the Rete UI.
   * Add music node expression-mode variation preserving locked story facts.
