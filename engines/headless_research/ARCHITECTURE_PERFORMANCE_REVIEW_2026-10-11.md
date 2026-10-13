# Red Engine and Scatter architecture review

**Status:** Source and design review only, 2026-10-11. No throughput benchmark or live site test was run for this review. The two supplied Scatter handoff ZIPs were treated as design evidence, not instructions or proof of implementation.

## Implemented boundary

- The Red Engine has a headless, read-only study layer (`study_core.py`, `study_storage.py`), durable study/job state, lexical measurements, cited proposed findings, and fixed-context Codex/Gemini dispatch. Its current provider adapters deliberately disable or reject tool calls.
- The RedNote native app has staged source and package for a separate background research WebView, paged search/Saved/Favorites/post reads, optional on-demand commenter profile reads, and durable account-library scan cursors. The staged package is not the running app. The feature has no live site or foreground-focus acceptance evidence yet.
- The 5186 Scatter board is a UI prototype. `prototype_graph/app-pass2.js` persists nodes in browser localStorage; decorative music flow declares `engine_status:'not_connected'`. `rednote_v2/store.py` exports `graphEditorImplemented:false`. There is no active `scatter.sqlite`, `/api/scatter/v1` authoring service, revisioned semantic graph command path, or integrated narrative worker in this checkout.

## Highest value redesigns

| Priority | Finding | Change |
| --- | --- | --- |
| P0 | `StudyEngine._pinned_records` re-captures and re-hashes the entire study for search, measurement, marketing, and media-context reads. `SqliteArchive.capture` materializes all selected comments and assets and rejects studies after 100,000 comments. | Pin per-entity digests and a study membership index once. Stream archive rows in bounded batches; refresh only changed source IDs using capture receipts. Preserve explicit source freshness and missingness. |
| P0 | Study search scans case-folded text in Python despite a derived FTS5 index. | Search the indexed source projection, intersect result IDs with pinned study membership, then verify and return exact original-language excerpts. Keep a scan fallback for short Chinese queries and measure both paths. |
| P0 | Red Engine providers cannot call the new live-research tools: Codex uses `enable_tools=False`; Gemini rejects `tool_calls`. | Add a Red Engine-owned tool broker with archive and live capabilities, observation receipts, citation checking, cancellation, and per-provider queues. Keep the existing fixed-context analysis path as a separate mode. |
| P1 | Background list checkpoints rewrite an ever-growing JSON array on each page. This creates increasing write cost and memory use on a large Saved/Favorites collection. | Store discovered items in an indexed append-only SQLite table keyed by `(job_id, kind, source_id)`. Keep only cursor, site end marker, and lease state in the job checkpoint. Do not impose a job-wide item cap; retain bounded response pages and resource budgets. |
| P1 | Research navigation uses synchronous waits and the Red research service uses `ThreadingHTTPServer`; there is no single owner for shared site-action scheduling across capture, manual Browse and research. | Give live reads one serial browser actor with a request queue, monotonic leases, cancellation, and a shared cooldown budget. Manual Browse keeps its own page state. Do not emulate user behavior to bypass a site gate. |
| P1 | The derived text index is rebuilt wholesale after hashing the complete mirror and legacy databases before and after. | Use immutable snapshot/import receipts and incremental projections for routine updates. Retain periodic full-hash reconciliation as an audit, not on every research query or small capture delta. |
| P1 | Scatter UI localStorage is presentation state, not semantic authority. Connections, music flow and profile branches cannot be safely consumed as engine instructions. | Build an isolated `scatter.sqlite` engine with typed node/port/edge contracts, expected revision and idempotent command IDs, source refs into the immutable archive, branch/form validation and a separate layout projection. Start with one four-card headless slice before binding the board. |
| P2 | Commenter profile links can point to live pages, but a link or avatar alone cannot establish demographics. | Keep profile traversal opt-in and one profile at a time. Save an observation with field-level source/time/coverage, link it to the archived comment and author ID, and require review before any demographic claim enters a finding. Do not bulk-clone commenter profiles. |

## Delivery sequence

1. Measure current capture, study search, source-pinning and UI memory/decoder behavior at small, 68-card and profile scale. Establish latency and memory targets from this machine.
2. Build the incremental source projection and study membership/hash index; move large research checkpoints to indexed storage.
3. Add the Red Engine research tool broker and observation ledger; verify Codex and Gemini tool transport separately. Prove background site use leaves the user's foreground app and manual Browse URL untouched.
4. Build Scatter's headless semantic graph revision store and source adapter. Bind a four-card, two-branch actor transformation fixture before adding prose or music execution.
5. Bind UI commands to accepted engine APIs. Keep board geometry and creative styling replaceable without changing data or engine records.

**Acceptance boundary:** Package builds, syntax checks, UI snapshots, and MCP registration do not establish live site behavior, graph semantics, provider tool execution, or performance. Those need separate direct evidence.
