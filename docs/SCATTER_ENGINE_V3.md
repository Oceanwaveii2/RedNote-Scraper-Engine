# Scatter Engine v3: authority and rendering

**Status: prototype.** The headless graph can revision, queue, summarize, and
cancel against pinned source data. The cinematic board remains a UI prototype
with partial card bindings. A linked project or rendered mock card does not
mean a whole captured profile was processed. Review the exact selected profile,
post, comment, and media counts in each frozen input packet.

This is the implementation boundary for the new Scatter path discussed in the
Gemini handoff. It keeps the RedNote capture system, the Mirror Clone, and the
custom node designs. It does not make a second writable copy of captured data
inside browser OPFS.

## Ownership

| Layer | Authority | Rule |
| --- | --- | --- |
| RedNote capture and media | `rednote-v2.sqlite` and verified media bytes | Captured source is read only to Scatter. Keep/Ignore is resolved from the mirror selection and graph-local exclusions. |
| Authored graph | Packaged `engines/scatter_graph/graph.py` on port 5189; `SCATTER_DATA_DIR` points at the existing SQLite store | Commands use `expected_revision` and immutable revision receipts. Source references remain pinned. |
| Research studies | Headless Red Engine on port 5188 | Research questions, citations, and review do not edit the mirror or graph implicitly. |
| Board rendering | Browser at port 5186 | Positions, hit targets, and visible cards are a projection of the authored graph. Rendering caches are disposable. |
| Story runs and documents | Revisioned Scatter execution and documents | Model dispatch is explicit; model text is a proposal until a person accepts it. |

The five-table SQLite WASM/OPFS design in the handoff is useful as a disposable
read cache, but it must not become a second authority for profiles, source
selections, or graph revisions. The existing Argon
`engines/headless_research/spatial_engine_v2_ext.ts` is a standalone prototype,
not the active board renderer and not a measured 50,000-node product path.

## Implemented in this branch

The complete headless Scatter service source is now included under
`engines/scatter_graph`. `Start-ScatterV3.ps1` accepts explicit paths for the
existing graph, mirror, study, and index stores. The service reads the same
source databases via `engines/headless_research` and does not copy the capture
archive into the repository. API health and timing events are written to a
content-free JSONL log in the selected graph data directory. Story queue and
worker stage transitions record counts, durations, and status, with no prompt
or source text in that telemetry.

The cinematic graph loads `scatter-spatial-core.js` and
`scatter-canvas-engine.js` after its custom node scripts. The spatial core
incrementally indexes variable-size node bounds. The renderer queries the
viewport, draws the relevant wires in one Canvas 2D frame, and retains only
visible SVG connection hit targets. It coalesces pan, zoom, and drag changes in
`requestAnimationFrame`. On boards over 12 nodes, the renderer mounts up to 32
nearby custom HTML cards and represents the rest with selectable canvas proxies
or low-zoom clusters. Visible cards retain their custom designs;
`content-visibility` lets the browser skip work for offscreen interiors.
`Start-ScatterBoard.ps1` starts the preview with a private asset root and local
video manifest. The board server serves these bytes only under `/assets/` and
`/video/` with relative-path checks; neither the assets nor local DBs are part
of this repository. Port 5185 Mirror Storage is a separate runtime dependency.
The wire projection preserves the existing scene-mode routes and folds hidden
post endpoints into their visible profile/output cards. Connection editing still
targets the original authored edge. Hidden grouped posts do not take a card slot
or appear as duplicate overview proxies.
The board also loads `scatter-edge-link.js`: after a user binds visual cards to
semantic nodes, it maps forward board wires to typed ports, asks the graph
service for an add/remove plan, and applies that plan only on a separate click
with the expected graph revision and binding hash. Unbound or reverse board
wires stay visual; the plan reports how many were skipped. A wire's stable ID
comes from its endpoint IDs, not its current coordinates or label.

The mirror adapter now walks every included captured post for every profile in
a group. It no longer silently stops at 60 posts or hydrates only the last
profile. Post rows are fetched in bounded batches, and each batch uses one
comment query instead of one query per post. The compiled board still
materializes every included post, so this is not the final large-profile
context format. Each story node carries its source ID, URL, and capture coverage. Its
comment text remains an explicitly bounded sample of up to ten root comments,
with up to five included in story observations. A complete comment analysis
must query the mirror or research broker separately.
Current Ignore selections also remove previously saved post members from the
compiled profile and are carried into the compilation's exclusion ledger.

`codex_direct` uses the installed, signed-in Codex app-server with tools
disabled for the story rendering turn. The resulting receipt records model,
thread, and turn IDs. Grok and Gemini routes now fail explicitly until real
story adapters exist; no locally constructed draft may be labeled as their
output. Export of user-supplied text has an empty provider list.
The graph preview server now has a same-origin proxy for the headless pipeline;
`pipeline-nodes-ui.js` is still not loaded into the board while its custom
card integration is revised. No visual story button is claimed operational.

## Next integration boundaries

1. Complete card-to-graph mapping and source pinning so every authored visual
   node can be represented in the revisioned graph. Keep coordinates in the
   board layout projection, not in Mirror Storage. The current mapping is
   explicit and partial; it does not auto-import mock cards.
2. Replace whole-board JSON requests with revision IDs plus bounded source
   streaming. Compile branch context deterministically before provider work.
3. Add separately accountable Gemini and Grok story adapters with availability
   probes, cancellation, receipts, restart recovery, and no assumed login.
4. Preserve and review each existing Protagonist, Branch, Profile, Video,
   Music, LLM, Prose, and document template in the bounded DOM path. Measure
   frame time and memory before choosing WebGPU/PixiJS.

No 50,000-node frame-rate or live Grok/Gemini claim follows from the code in
this branch. Those require integrated runtime measurements and real sessions.
