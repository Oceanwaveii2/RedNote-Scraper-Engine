# Scatter headless graph store

This directory is the packaged source for the graph API, revision store, story
queue, and document store. It is started independently from the board preview.
Set `SCATTER_DATA_DIR` to the existing graph data directory and
`REDNOTE_MIRROR_DB`, `REDNOTE_STUDY_DB`, and `REDNOTE_INDEX_DB` to the existing
source databases. No database or captured media is committed to this repository.
`Start-ScatterV3.ps1` starts the API with those paths on this workstation; it
does not start the provider worker or send a model request.

The historical runs and project IDs described below belong to the existing
local data store. Packaging the source does not itself revalidate a provider
session or convert the visual prototype board into a semantic graph.

`graph.py` owns the authored semantic graph. `data/scatter.sqlite` stores immutable revision snapshots and an atomic head pointer. The prototype board is a separate UI artifact and is not read or written here.

Node kinds: `actor`, `transformation`, `source`, `music`, `music_processor`, `junction`, `story`, `group`, and `output`. Ports have `in` or `out` direction and one of `actor`, `reference`, `music`, `story`, or `control` types. Edges must terminate on existing, compatible ports. A single-input port rejects a second edge unless its definition sets `multiple: true`.

All changes use `apply(project_id, expected_revision, command_id, command)`. A repeated command ID with the same content returns its original revision. A stale expected revision is rejected. `restore_revision` creates a new head revision containing an older snapshot; history is not deleted. Source references carry archive/entity IDs and a content hash, but the archive binding and media resolution are a separate future integration.

Supported actions: `add_node`, `remove_node`, `update_node_data`, `set_node_source`, `set_node_ports`, `add_edge`, `remove_edge`, `batch`, and `restore_revision`. A `batch` contains 1–200 ordinary graph commands in `commands`; it validates all pins and connections, then saves one revision or none. This lets callers add an arbitrary new branch and its wires atomically. Board positions, zoom, color, and drag state are UI layout data and intentionally absent from these semantic revisions.

`source_adapter.py` resolves Red Note references through the Red Engine's pinned study API, reading the mirror without writing to it. A source reference includes `archive_id: rednote-v2`, `study_id`, `entity_kind`, `entity_id`, `content_hash`, and optional `source_revision`. `pin-source` generates a current reference; attaching it through `add_node` or `set_node_source` requires that it still resolves as pinned. Existing graph revisions remain readable if the archive later changes. `sources` reports such changes without editing or deleting the authored graph. A source link is evidence, not an automatic character fact or reviewed finding.

`inspect` reports transformation-chain completeness, actor-parent mismatch, actor cycles, and stale source references. Intermediate authoring revisions are allowed; this inspection reports what remains incomplete. It does not execute transformations or generate prose.

`ScatterGraphStore.story_output_command(output_id, title, from_node, from_port)` builds a single revisioned batch for `Prose Engine → Story LLM renderer → Story output`, optionally connected to an upstream story port. The renderer stores GPT, Gemini and Grok enable switches, one active provider, and the `story_render` session role, separate from `data_analysis`. These are typed graph settings only: Prose processing and independent provider dispatch still require runtime adapters. Provider credentials and live session IDs are not stored in graph revisions.

`create-rainee-chain` creates a five-node headless slice: Rainee 01 → Big Lip transformation → Big Lip Variant → Horse transformation → Horse Rainee. The four actor-typed edges, source-free character identities, and local override data are in immutable semantic revisions. The created sample is `scatter-215deb2d14e946edb321e737a72eb80a` at revision 9 in the current local store; `inspect` reported `structurally_complete`. This fixture has no board geometry, media bindings, narrative execution, or automatic archive promotion.

Use `python graph_cli.py create "Title"`, `python graph_cli.py show PROJECT_ID`, or `python graph_cli.py apply PROJECT_ID EXPECTED_REVISION COMMAND_ID command.json`.

From the project root, `python engines/scatter_graph/graph_cli.py inspect PROJECT_ID` and `python engines/scatter_graph/graph_cli.py sources PROJECT_ID` read a revision. `python engines/scatter_graph/graph_cli.py pin-source STUDY_ID post ENTITY_ID` prints a source reference that can be attached in a command JSON. `--db PATH` can be used for a separate local store.

## Branch and story execution layer

`execution.py` compiles a selected output by walking its incoming typed edges back to its one original protagonist. Each output node declares `data.branch_id` and `data.provider` (`codex` or `gemini`), with optional `data.model`. Add any number of branch IDs: they are data, not a fixed three-branch enum. A branch request compiles each of its outputs independently. Shared upstream inputs are included in each packet; private paths do not leak into sibling stories.

A readable profile group is a semantic `group` node, not a board folder. Connect captured `source` nodes whose pinned `source.entity_kind` is `profile` to its reference input port, then connect its reference output to downstream branch nodes. The compiler lists all connected member IDs and includes their current pinned records as distinct evidence entries. It rejects an empty group, a stale pin, a missing original protagonist, a cross-branch path, or a graph cycle. Grouping does not merge profile identities. A source post/comment can also feed downstream nodes through separate reference connections. A future deep-mining node can be added through the same typed-edge contract without changing the branch selector.

Example semantic ports (UI placement and appearance remain separate):

```json
{"id":"lips-group","kind":"group","title":"Dudu Lips","ports":{"profiles_in":{"direction":"in","type":"reference","multiple":true},"context_out":{"direction":"out","type":"reference"}},"data":{"branch_id":"big-lips"}}
{"id":"lips-story-a","kind":"output","title":"Big Lips story A","ports":{"actor_in":{"direction":"in","type":"actor"},"context_in":{"direction":"in","type":"reference","multiple":true},"music_in":{"direction":"in","type":"music","multiple":true}},"data":{"branch_id":"big-lips","provider":"gemini","model":"flash"}}
```

The original protagonist must have `data.role: original_protagonist`; downstream form/actor nodes and output connections carry it into the selected output path. `source_adapter.py` reads bounded text/metadata for pinned mirror records and excludes local media bytes. Captured content is untrusted provider input, never an instruction. `providers.py` sends a queued packet only on an explicit `run` command, with tools disabled for Codex and a text-only Antigravity turn for Gemini. There is no automatic provider fallback. Provider text is saved as a **proposed draft** for human review; dispatch never edits the graph, archive, or authored story.

Commands:

```text
python engines/scatter_graph/graph_cli.py compile-output PROJECT_ID OUTPUT_ID
python engines/scatter_graph/graph_cli.py branches PROJECT_ID
python engines/scatter_graph/graph_cli.py compile-branch PROJECT_ID BRANCH_ID
python engines/scatter_graph/graph_cli.py queue-output PROJECT_ID OUTPUT_ID
python engines/scatter_graph/graph_cli.py queue-branch PROJECT_ID BRANCH_ID
python engines/scatter_graph/graph_cli.py show-run RUN_ID
python engines/scatter_graph/graph_cli.py cancel-queued RUN_ID
python engines/scatter_graph/graph_cli.py run RUN_ID
```

`queue-*` freezes a bounded packet in `data/scatter-runs.sqlite` and records its SHA-256 hash, graph revision, selected provider and model. `run` is the older direct external dispatch step; use `story_cli.py` for staged runs. A queued run is not a completed model turn. The current graph UI at port 5186 still uses its own local prototype state. Its explicit node bindings are stored separately in `data/scatter-ui.sqlite`; visual wires and positions are not semantic graph revisions. The active Red Engine/Scatter archive must have real pinned profile records before a group-backed story can be queued. Remote Gemini cancellation is unavailable; an uncertain submitted turn must be inspected rather than automatically retried.

### Staged story worker

`story_pipeline.py` adds a durable interpretation → human choice → causal plan → linked draft path. It uses `story_cli.py` and the same frozen run packets. A branch queue creates separate runs for all of its story outputs; **each output** proposes exactly three short interpretations. Selecting one creates a child run. The worker plans and writes only that choice. It stores stage artifacts, provider receipts and ordered events in the run database. `context.py` performs the initial authored-operation conflict and mandatory-context checks and evaluates declared symbolic preconditions/effects. See [research mapping](RESEARCH_TO_ENGINE_2026-10-11.md) for exact scope and gaps.

```text
python engines/scatter_graph/story_cli.py queue-branch PROJECT_ID BRANCH_ID
python engines/scatter_graph/story_cli.py worker --loop
python engines/scatter_graph/story_cli.py events RUN_ID --after 0
python engines/scatter_graph/story_cli.py artifact RUN_ID interpretations
python engines/scatter_graph/story_cli.py select PARENT_RUN_ID CANDIDATE_ID EXPECTED_REVISION
python engines/scatter_graph/story_cli.py artifact CHILD_RUN_ID draft
```

The worker is an explicit headless process, not an HTTP request handler. `story_cli.py worker` without `--loop` processes at most one available stage. Do not run the older `graph_cli.py run` for a staged story; that command remains a direct single-draft transport. Queueing a branch does not call the model. The staged queue commits frozen runs, worker membership and queue events together. A provider response is saved with its receipt before JSON parsing and validation, so malformed replies remain inspectable as `interpretations_response`, `plan_response` or `draft_response` artifacts.

`story_cli.py input-summary RUN_ID` (also `GET /api/scatter/v1/input-summary?run_id=...`) reports exact frozen profile/post counts, machine reading aids, media states, graph size, context size and interpretation prompt bytes. It counts only what entered that run. Post coverage counters are metadata; comments and media pixels are not silently treated as processed content. The queue now checks the mandatory context budget before registering runs, so a too-large selection fails early and can be split into separate outputs.

### Selected evidence, music, documents and recovery

A profile source node can declare `data.selected_post_refs` as up to 100 pinned post references. Every selected post must belong to that profile and the same study. The frozen packet includes those post records, their coverage/inventory, verified local media state and current machine reading aids. Unavailable media is explicitly marked; bytes are not copied into the packet. A profile with no selected posts contributes only its profile record.

The same profile node may declare up to 200 `data.selected_comment_refs`. Every comment must be pinned in the same study and belong to one of that node's selected posts. Comment bodies, captured author/avatar identifiers and current machine reading aids then enter a separate `selected_comments` packet section. The engine never expands a comment tree implicitly or clones a commenter's profile. Future wider research can select a bounded subset and split outputs when the mandatory context budget is reached.

A connected `music_processor` needs exactly one upstream `music` node and `data` such as `{"mode":"expression","influence":0.5,"time_range_ms":[0,30000],"notes":"cadence only"}`. Modes are `expression`, `interpretation`, and `in_story`. This is a scoped story instruction, not audio signal processing. Gain/playback controls remain separate.

Provider draft blocks remain proposed artifacts. `documents.py` accepts a completed draft only against the unchanged graph revision and creates an authored document with stable block IDs. `edit`, `insert`, `remove`, `lock`, and `unlock` create immutable document revisions. Locked blocks reject edits and removal. Concurrent updates require the caller's expected revision. A changed graph head requires a new review rather than silently applying a stale draft.

If the symbolic plan check reports issues, the run stops at `waiting_for_resolution`. A reviewer may submit a corrected plan with `story_cli.py resolve-plan RUN_ID EXPECTED_GRAPH_REVISION COMMAND_ID plan.json` or `POST /api/scatter/v1/resolve-plan`. The provider plan and issue report remain intact; a valid correction is saved as `plan_resolved` and queues only the draft stage. This does not automatically repair or approve story content. Document edit commands are idempotent when the same command ID, parent revision and command content are repeated; accepting one draft run again returns its existing document.

`story_attempts` records the worker PID, stage, and provider receipt. On an interrupted active stage, `story_cli.py interrupted` lists uncertain runs. After confirming the worker has stopped, `mark-interrupted RUN_ID STAGE` moves a run to `outcome_unknown`; it never resubmits it. Inspect the provider account and local receipt before deciding what to do next. Queued stages, including a corrected plan's draft stage, can be cancelled with `StoryPipeline.cancel_queued`.

`api.py` is a separate loopback server on `127.0.0.1:5189`. It exposes graph reads/updates, branch/output queueing, candidate selection, plan resolution, document actions, run artifacts, events and an SSE stream. POST requests accept only the Studio preview origins on ports 5185/5186. It does not execute provider calls inside HTTP requests; `story_cli.py worker --loop` is a separate process. The prototype board's narrow engine panel can bind individual visual node IDs to existing semantic nodes and queue a bound output. **This does not convert visual wires, localStorage or mock source cards into semantic evidence.**

The live source-backed interpretation gate below passed for Codex. Human candidate selection, plan/draft output, authored document acceptance, a completed Gemini response and full board-to-engine mapping remain open operational gates. The later durability and resolution edits described above have not had an end-to-end provider run.

### Engine preparation for the UI pass

The loopback API now exposes `GET /api/scatter/v1/source-catalog` with `study_id`, `kind`, `cursor`, and `limit` for a paged, status-labelled picker of pinned mirror records. It returns references and membership IDs, not media bytes. The UI must still present selected posts and comments for review and pin them through graph commands.

`authored_sources.py` stores custom profile, post, and comment JSON in a separate SQLite database with immutable revisions. `GET /custom-sources`, `/custom-source`, and `/custom-pin`, plus `POST /custom-source` and `/custom-revision`, provide the future import UI with a durable data boundary. A custom source reference uses `archive_id: scatter-authored/1`, `study_id: authored`, its entity ID, revision and content hash. Old revisions remain readable after edits. This store does not clone a Red Note page or certify user-entered data as captured evidence. Custom image/video bytes and file ingestion still require a separate media workflow.

The same headless operations are available through `python engines/scatter_graph/source_cli.py create profile "Title" source.json`, `revise RECORD_ID EXPECTED_REVISION COMMAND_ID source.json`, `read`, `list`, and `pin`. `catalog STUDY_ID KIND` pages pinned mirror membership for selecting captured sources. No command in this CLI starts capture or contacts a model.

`BoardBindings.plan_edges` previews the typed graph commands for a complete set of explicitly mapped visual wires. `POST /api/scatter/v1/edge-plan` returns adds/removes, the graph revision and a binding hash; `POST /edge-apply` requires those expected values plus a command ID. It rejects unbound endpoints, missing or incompatible ports, overfilled single inputs, stale graph heads, and more than 200 changes. Only edges with this board's generated IDs are replaced; unrelated semantic edges remain intact. No board wire is imported automatically: the forthcoming UI pass must assign stable visual edge IDs and choose semantic ports, then show the plan before commit.

### Live source-backed slice (2026-10-11)

After the initial implementation, a fresh Red Engine study `study-685a0001c3cc4aaaa74373c89368ccd1` pinned Ling_ZU and two selected posts. Older studies returned `source_changed` and were not used. The sample semantic project `scatter-50d74973438347d6ac316028292574b3` at revision 1 links Rainee core, the selected Ling_ZU profile/posts, a readable group and one regular-branch Codex output. Both selected posts reported `verified_local` media state; the packet is roughly 12 KB. It is a separate engine project, not a conversion of the prototype board.

The first live Codex run `scatter-run-1b2b5b1a40cf46a085f38dd6a3380311` returned a candidate whose card role cited an unselected graph ID. Validation rejected it, exposing a lost-receipt and worker-loop flaw. The worker now retains raw stage artifacts with receipts before validation, states the allowed card IDs in the provider prompt, and survives a failed job. A new live run `scatter-run-f36f043482464bbc9095525c69f2b8ca` produced three valid interpretations and is `waiting_for_selection`. Its saved receipt includes a Codex thread and turn ID using model `gpt-6.1-sol`. No plan or draft was generated because candidate selection belongs to the human.

A separate Gemini project `scatter-d2e79854ad524be79f667f30a9c4d62d` kept the Codex graph revision unchanged. The first Gemini launch through `agentapi.bat` exited immediately; Scatter now calls the native `language_server.exe` with an explicit command-size limit. The next Gemini run `scatter-run-6d0ad3e672224ce2817ccaa7bfe85422` created a conversation but received no completed transcript within 240 seconds. It is `outcome_unknown`; metadata linked it to the matching Antigravity conversation, whose local transcript was still pending when inspected. No automatic retry occurred. This is a transport observation, not a verified Gemini interpretation or confirmed credit diagnosis.
