# Argon review handoff — Scatter engine replacement

Date: 2026-10-14. Review the `codex/scatter-v3-engine` branch and draft PR #1
in `Oceanwaveii2/RedNote-Scraper-Engine`. **This is an engine and UI prototype,
not a complete product or a full-profile analysis result.** This branch is a replacement candidate;
the old Scatter files on `main` must remain until the replacement reaches its
integration and performance gates. Git history is the rollback path.

## Source and ownership

- `engines/scatter_graph/` now packages the revisioned semantic graph, typed
  ports and edges, source pins, authored-source store, story queue, document
  revisions, loopback API, and worker source. The previous code lived only in
  `D:/0/Noir Fiction Engine/Scatter_Engine`, outside this repository.
- `engines/headless_research/` remains the Red Engine study and capture-index
  integration. `source_adapter.py` imports its pinned-source API from the repo.
- The user archive, study DB, source index, graph DBs, media, model sessions,
  credentials, and browser storage are **not** committed. `SCATTER_DATA_DIR`,
  `REDNOTE_MIRROR_DB`, `REDNOTE_STUDY_DB`, and `REDNOTE_INDEX_DB` select them.
- `ui_prototypes/cinematic_graph/` is the board preview, not the semantic
  authority. Visual cards only enter the authored graph through explicit
  binding and reviewed wire plans. Graph-local Ignore/override state must not
  mutate Mirror Storage. Keep custom node designs and the Core, Big Lips, and
  Horse branch hierarchy.
- The old `scatter_pipeline_ext.py` still serves `/api/scatter/*` on the Red
  Engine and the board's `/pipeline/*` proxy. `spatial_engine_v2_ext.ts` is a
  standalone prototype. They are migration targets, not replacements already
  removed. Audit every caller before deleting them on the final PR.

## Startup on the current Windows workstation

From the repository root, run `./Start-ScatterV3.ps1`. Parameters allow
explicit Python, data, mirror, study, and index paths. It starts only the
graph API on `127.0.0.1:5189`. Health is
`GET http://127.0.0.1:5189/api/scatter/v1/health`. Starting the separate
`engines/scatter_graph/story_cli.py worker --loop` can contact Codex or Gemini,
so do that only for an explicitly authorized provider run. Start the board
preview with `./Start-ScatterBoard.ps1` on this workstation, or run
`python ui_prototypes/cinematic_graph/server.py` with explicit
`SCATTER_PRIVATE_ASSET_ROOT` and `SCATTER_VIDEO_INPUTS_JSON` elsewhere. It
serves port 5186; Mirror Storage on 5185 and Red Engine on 5188 are separate.
The script's private-media defaults are workstation-specific parameters.
Override them on another machine. No private media bytes are committed.

On another machine, point all four DB parameters at that machine's real stores.
Do not use the workstation defaults from the script as a portable fixture.
Never copy or push the live DBs or media as part of a code review.

## Verified slice and evidence limits

The checks below used copied `scatter.sqlite`, `scatter-runs.sqlite`, and
`scatter-ui.sqlite` in a temporary directory. The live graph databases were
not written. Source pins read the existing archive, study, and index stores.

| Gate | Observation |
| --- | --- |
| Python syntax and imports | `compileall` passed for the packaged service and modified research adapters. Imports resolved against the repository modules. |
| API startup | Port 5189 health reported `scatter-graph`, schema `scatter.graph/1`, and graph/runs DB presence. |
| Historical project read | A copied source-backed graph read at revision 1 with 4 nodes and 3 edges. `graph_cli.py inspect` reported `structurally_complete` and one matching source pin. |
| Queue without dispatch | One output queued from the copied store, its frozen input summary was read, and the queued run was cancelled. No worker was started and no model call was made. |
| Frozen input coverage | 1 selected profile, 2 selected posts, 0 selected comment bodies, 2 verified-local media states, 12,017 packet bytes, 13,447 interpretation prompt bytes. This does not mean it processed the whole profile or image pixels. |
| Board preview | Port 5186 loaded in an isolated Playwright browser. At overview it reported 41 nodes and 24 edges, 29 visible proxies, 0 mounted full cards, 32-card budget. Focusing the Big Lip card reported 9 visible nodes, 15 visible edges, 8 mounted cards and 0.37 zoom. |
| Render timing sample | At that focus point the small sample reported 1.9 ms paint p50, 111.8 ms p95, 10 painted frames. This is **not** a sustained benchmark; startup/mount work affected the tail. JS heap reported about 7 MB at the focus point. |
| Offline dependency | The isolated board got 503 for `/mirror/api/v2/profiles` because port 5185 was offline. This check does not verify Mirror Cabinet live behavior. |
| Offline regression suite | `python -m unittest discover -s tests -p test_scatter_engine_v3.py -v` passed 5/5: revision replay/conflict/restore, atomic edge rejection, Rainee fixture shape, reviewed binding plan, and queue/summary/cancel without provider. |
| Packaged API integration | `test_scatter_packaged_integration.py` passed 2/2 against temporary SQLite backups of graph DBs and read-only archive/study/index stores. It checked historical project read, queue/summary/cancel, telemetry redaction, and origin rejection. No worker or provider ran. |
| Browser and media pass | With a private asset root configured, board JS and one private image returned 200; a private MP4 and video slot 4 returned 206 for a byte range; path traversal returned 404. Fresh Playwright navigation returned zero console errors and warnings. Studio 5185 remained offline. |
| Board-to-engine binding | Loading the historical project in the prototype UI worked. The wire plan reported **0 bound forward wires and 33 skipped unbound or reverse wires**. Current visual cards are not a complete authored graph. |
| Narrow render sample | Focusing Big Lip with private assets available showed 41 total nodes, 24 edges, 8 mounted cards, 9 visible nodes, and 15 visible edges. A 13-frame paint sample showed 0.8 ms p50 and 13.5 ms p95. This is not a sustained pan/zoom/drag benchmark. |

Local graph API telemetry is append-only JSONL under
`$SCATTER_DATA_DIR/telemetry/scatter-events.jsonl`. It records build label,
route, method, response bytes, status, elapsed time, queue counts/packet bytes,
and worker stage state/timing. It omits prompts, source text, and credentials.
The API also writes `api.stdout.log` and `api.stderr.log` there. The board's
`window.scatterCanvasEngine.metrics()` exposes mounted-card count, visible
counts, 240-frame bounded paint samples, zoom, and available JS heap. The
verification process was running from uncommitted source, so its health build
label was the previous Git HEAD; do not use that label as proof of the new
commit. Re-run from the pushed commit and capture a fresh health label.

## Argon review instructions

Inspect both `engines/scatter_graph/` and `ui_prototypes/cinematic_graph/` in
this branch. Use the board to see the existing custom card designs and Core,
Big Lips, Horse layout. Saved mock cards are design fixtures, not captured
evidence. The prior source-backed interpretation included one selected profile,
two posts, and zero comment bodies; it did not analyze the whole profile. Record
exact source scope and exclusions before interpreting results. Keep private
media, local DBs, and model credentials outside Git.

1. Check that every path in the packaged graph service resolves within this
   repo or through the explicit data environment variables. Inspect the
   updated Codex bridge owner/cancel semantics and Gemini path separately.
2. Trace source pins from the frozen study to `compile_output`. Confirm
   revision checks and negative/Ignore exclusions cannot silently change an
   existing run packet. Check group traversal and large-profile memory use.
3. Audit the old pipeline routes and the board's `/pipeline/*` proxy. Migrate
   remaining callers to the revisioned graph/story service before proposing
   deletion of the old Scatter modules on `main`.
4. Measure a sustained board workload with 15, 100, 1,000, and larger cards,
   including pan, zoom, drag, connections, custom card content, memory, and
   startup. The current 41-node sample is not a 50,000-node claim. Preserve
   hit targets and each bespoke node layout while reducing DOM work.
5. Complete explicit card-to-semantic-node mapping and source pinning. Compare
   graph revision and binding hash before applying typed wires; no board
   localStorage JSON or mock source card should become evidence by default.
6. Verify a real Codex and Gemini worker run only with the user's live-session
   authorization. Capture dispatch receipt, stage artifact, failure state,
   cancellation, restart recovery, and the no-automatic-retry rule. Grok is not
   wired. Keep data analysis and story rendering sessions distinct.
7. Review telemetry for accidental personal content and silent write failures.
   Add durable stage metrics where needed without logging prompt bodies,
   archive text, media bytes, credentials, or full source URLs.
8. Check the offline Mirror Cabinet path and the board startup script on
   Windows. Review drag/drop, custom cards, and source links in a live Studio
   session separately; the offline 503 is not a successful Cabinet read.

## Release gate

Do not merge or delete the old Scatter files on `main` until the replacement
owns their live UI and API callers, a copied-data migration check passes, and
a real board plus provider run has reviewable receipts. For rollback, stop the
new API, return clients to the prior routes, and revert the merge; captured
archive databases remain independently owned and should not be rolled back by
Scatter. Record exact commit IDs and telemetry samples in the PR before asking
the user to switch their live application.
