# Argon Scatter v3 bundle review

**Status:** Candidate code received and inspected. It has **not** been merged into the current Scatter branch or activated against the live archive.

The user supplied `RedNote-Scraper-Engine-scatter-v3-fixed.zip`, `scatter-v3-logs-and-telemetry.zip`, and `scatter-v3-code-and-logs-bundle.zip` on October 14, 2026 (Pacific time). The fixed ZIP embeds a Git checkout at commit `5f62c8529fe30ad831aed0f23e908ac419c1ca34`, made from an `8c10667` baseline. The current review branch was already at `52afc10` when this bundle arrived. The histories and working trees are not a drop-in replacement for one another.

## What the supplied evidence supports

- The archive contains a new `engines/scatter_graph/` implementation, an additional `engines/scatter_engine/` implementation, a board wire planner, source adapter, tests, and capture code changes. Its report describes this as a **prototype**.
- The included unittest log says **175 tests passed** in an offline environment. The log shows nine `test_scatter_v3_engine.py` cases among broader scraper/engine tests. This is evidence of that packaged run, not proof of a live workstation or provider session.
- The included wire/evidence log reports an offline plan of **18 forward wires and 15 skipped wires** from a 41-card layout, and a Core packet with one profile, two posts, one excluded post, three comments, and two local media states. The report identifies deterministic local provider receipts, with `real_provider_session_dispatched: false`.
- Static syntax inspection of 128 packaged Python files found no syntax errors. Syntax inspection of the board wire script found a JavaScript error (below). The reported spatial benchmarks exercise a synthetic Node workload; they do not measure sustained interaction with the custom board in a browser.

## Blockers before integration

1. **The board wire script does not parse.** `ui_prototypes/cinematic_graph/assets/scatter-edge-link.js` has literal newlines inside two `].join('...')` string literals. `node --check` fails at line 63. Because `index.html` loads this file, its planner buttons cannot run in the browser as packaged. The 18-wire count came from offline planning, not this UI path.
2. **The board API is split across two different SQLite graph stores.** In the supplied `ui_prototypes/cinematic_graph/server.py`, `/api/scatter/v1/*` routes through `ScatterV1Gateway`. That gateway reads `/graph` and `/bindings` from `engines/scatter_engine/graph.py` (`scatter.sqlite`) but forwards `/wire-plan`, `/apply-wire-plan`, `/command`, and `/compile-preview` to `ScatterV3EngineAPI` (`scatter_v3.sqlite`). A UI project load, binding, wire apply, and compilation therefore do not share one authoritative graph/revision. Fix this contract before using the board to edit real work.
3. **The launcher selects a different data root.** The supplied `Start-ScatterV3.ps1` defaults `DataDir` to `$PSScriptRoot` and starts a new `scatter_v3.sqlite` there. It no longer accepts the existing graph, mirror, study, and index DB parameters from the current `Start-ScatterV3.ps1`. The new source adapter then looks for `rednote-v2.sqlite` or `rednote.sqlite` under that root, rather than the user's existing capture store. Running it as written can produce a convincing local fixture run without reading the intended archive.
4. **Build labels are stale.** The supplied `engines/scatter_graph/api.py` hard-codes `VERIFIED_BASELINE_BUILD = "2d35b2a"`, although the embedded Argon commit is `5f62c852...`. The JSONL telemetry repeats `2d35b2a`. Those records cannot establish which source commit actually served a request. Build identity should come from the running checkout and be captured once at startup.
5. **The archive rolls back current UI/runtime fixes.** It contains older copies of the prototype title/status, `engine-bridge.js`, board startup script, and private-media handling. The current branch's prototype labeling and safe private asset configuration must be preserved. Do a file-by-file port onto the current branch rather than replacing the tree.
6. **Do not publish the full fixed ZIP as source.** It contains `.git` internals, logs, `scatter.sqlite`, `scatter_v3.sqlite`, `scraper/rednote.sqlite`, and a research checkpoint DB. Their presence in the ZIP does not mean they should become tracked source or be used as live archive data. Keep any useful test fixture explicitly synthetic and outside the production data path.

## Directive scope

The supplied `ARGON_INSTRUCTION_DIRECTIVE.md` is byte-identical to the copy inside the bundle (SHA-256 `4d5285d971e7bd3788255fd135c880edee407b884315be71f493e3ad97b5d3b3`). Its title and target are **Art Equation Studio 2.5** and its Vello, document, DCC, and 374-check UI work. It is valuable as context about Argon's working process; it is **not** a Scatter Engine specification or a verified Scatter acceptance result. The Scatter communication brief and actual Scatter source contracts should govern the next pass.

## Recommended porting sequence

1. Start from the current `codex/scatter-v3-engine` branch and preserve its data-path parameters, private-media launcher, prototype labels, and existing graph database. Keep the supplied ZIP separate.
2. Compare the new graph/schema and `engines/scatter_engine/` code with the current graph API. Choose **one** authoritative revision store and one API contract before moving any UI route. Define a migration for existing project IDs, source pins, and runs on a copied database.
3. Repair the board script syntax, then map card bindings against that one store. Plan and apply typed wires with a visible revision and binding hash. Recheck the 18/15 classification against the current board and review each skipped reason.
4. Port source selection, Ignore exceptions, comments, group traversal, and paged compilation only after confirming they read the real mirror in read-only mode. Report packet counts from the selected source records, not from fixtures or design cards.
5. Repeat the offline tests on the merged branch, then separately check the live Studio Cabinet, private media, browser interactions, real provider receipts, cancellation, restart recovery, and sustained rendering. Keep the old `main` Scatter path until those gates are reviewable.

## Evidence references

The packaged report is `logs/SCATTER_V3_ENGINE_AUDIT_AND_FIX_REPORT_2026-10-14.md`. The other key records are `logs/unittest_175_tests_run.log`, `logs/wire_plan_and_evidence_audit.log`, `logs/spatial_engine_benchmarks.log`, and `logs/scatter-events.jsonl` inside the supplied ZIPs. This review used those as claims to check against source, not as instructions. No live provider, Studio, or capture operation was performed during this review.
