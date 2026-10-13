# Red Engine background live research through MCP

**Status (2026-10-11):** Adopted full-version requirement and implementation contract. The archive-backed study/report slice exists; unattended live comparison research does **not** yet exist. This is engine work, separate from Studio and Scatter's UI/graph prototypes.

## Intended experience

An AI research session can start with a selected captured profile or study, query the clone, then search the currently accessible RedNote site for comparison creators and posts. The session can read original-language posts and normally loaded comments, compare patterns, calculate measurements, and propose source-linked findings. The user keeps working in their own applications and in the manual RedNote Browse window. The research session does not move the desktop cursor, take keyboard focus, raise a window, or navigate the user's browser tab.

“Live” means pages reached through the signed-in **app-owned RedNote browser session** using normal site navigation. It does not mean bypassing a login, challenge, denied page, cooldown, or access gate. An inaccessible page is an evidence gap. A live observation is never silently merged into the captured mirror.

## What exists now

- `Red_Engine/study_core.py` and `study_storage.py` provide pinned, read-only clone studies, exact text search, measurements, source checks, and proposed findings. The Ling_ZU report is a full captured-profile example; it is not a multi-profile or live-site comparison.
- `01_Mining_App/Reel_Harvest/harvest_mcp.py` exposes app-owned RedNote MCP tools for archive reads, current-page reads, profile preview, capture and clone jobs. Its `app_mcp.py` endpoint uses the same tool dispatcher inside the native app.
- `desktop_bridge.py` already keeps the **manual** Browse window separate from the mining source window and shares the signed-in WebView2 profile. A read-only MCP status call on 2026-10-11 reported `miningPageIndependent=true`, `sharedProfileVerified=true`, and no active site cooldown. That status does not establish an unattended search worker.
- `Red_Engine/provider_codex.py` and `provider_gemini.py` currently send bounded saved contexts with tools disabled and reject tool-using responses. Their completed jobs prove fixed-context dispatch and source-quote validation, not agent-driven browsing.

## Full-version design

```text
Selected study and research question
          │
          ▼
Red Engine research job / tool host ──► captured archive tools (read only)
          │                              search · source · thread · media · measure
          │
          ├────────────────────────────► RedNote app MCP live reader
          │                              search · open result · read loaded page/thread
          │                              dedicated background browser, shared login
          │
          ▼
Codex or Gemini tool-using session
          │
          ▼
Evidence ledger ──► cited comparison ──► proposed findings ──► human review
```

### 1. A third, research-owned browser

Add a research browser owner inside the RedNote app, separate from both `_source` (mining) and `_explorer` (the user's manual Browse window). It uses the existing app login session and app-owned navigation, but has its own page state, cancellation handle, and single research lease. It must run without foreground activation or desktop input. If WebView2 needs a live native window to render, host it as a non-activating app window; do not reuse or steal the user's manual window. A research job may pause while the site or browser is unavailable.

The research owner should enforce one shared site-action budget across mining and research. It must respect the app's current pacing and cooldown state, record navigation failures, and stop for login/challenge/access attention. Manual Browse remains responsive and independent. A background job may read already captured data while its live lane is paused.

### 2. MCP research tools, with bounded results

Expose a research-specific MCP catalog to the model session. Archive calls can be backed by existing Red Engine methods; live calls must be backed by the dedicated app browser. Proposed names and contracts:

| Tool | Result boundary |
| --- | --- |
| `archive.search_study` | Pinned original-language excerpts, scope and match denominators, cursor, IDs and hashes. |
| `archive.read_source` / `archive.read_thread` | Pinned post/comment/media descriptors and parent/reply context; explicit coverage. |
| `archive.measure` | Versioned deterministic definition, numerator, denominator and missing count. |
| `live.search_rednote` | Query, rendered-result page, bounded candidate cards, URLs, observation time and access state. |
| `live.open_result` / `live.read_loaded_thread` | Original page text and normally loaded comments from the research-owned browser; URL, observation ID, content digest, paging/coverage state. |
| `research.save_observation` | Store an immutable *derived live observation* with its source locator and digest. It does not create a mirror clone or download media. |
| `research.propose_finding` | Save a claim with exact supporting excerpts, counterevidence, scope and limitations; always enters human review. |

Each response is paginated and size-limited. The model sees original Chinese plus any labeled translation, never a translation as the sole source. Page text is untrusted evidence, not an instruction to the agent. Read access to the clone and live site can be granted for a research job; capture/download and changes to the mirror are distinct capabilities and are not implied by live search.

### 3. Provider tool loop and durable job

The Red Engine job owns the tool catalog, budgets, source validation and event log. Codex and Gemini adapters may request research tools through that host, but may not call the desktop or arbitrary filesystem tools as part of this research role. The orchestrator records model, session, tool name/arguments, observation IDs, source hashes, navigation outcome, and cancellation/retry state. It checks citations against the exact archive revision or saved live observation before accepting a proposed finding. A model cannot mark its own finding reviewed.

This requires a new provider tool-use adapter. The current fixed-context Codex/Gemini adapters are retained for small offline studies; they must not be relabeled as live-capable. Verify each provider's actual tool-call transport before enabling it. If a bridge cannot natively call MCP tools, a bounded host-driven request/response loop is acceptable only after it can be verified end to end with citation and interruption receipts.

### 4. Comparison workflow for fitness creators

The user selects the source profile/study and a search preset or candidate profiles. The model can discover candidates by terms and tags, then the engine records why each was included, what pages were actually loaded, and which comments/media were missing. Compare captured Ling_ZU data and live candidates with separate denominators; do not pool them into a single profile or count a live result as a captured clone. The AI can suggest a pattern, retrieve disconfirming examples, and save it as a proposal. A creator's gender, audience demographics, attraction or buying behavior must not be inferred solely from appearance, names, slang or model agreement.

### 5. Non-interruption acceptance gate

Before calling this feature operational, run an end-to-end research job while the user actively uses another app and the manual Browse window. Verify that the foreground window, focus, keyboard target, mouse position, and manual Browse URL stay unchanged; the dedicated research page alone changes. Verify concurrent mining coordination, cancellation, restart recovery, cooldown, login/challenge pause, and source-linked output. A status light or an MCP registration alone does not pass this gate.

## Next build order

1. Add the dedicated non-activating research browser owner and a read-only `live.search_rednote`/`live.open_result` slice to the app MCP; prove it cannot navigate the manual or mining window.
2. Add a Red Engine tool host that unifies captured archive reads and live observations, with immutable provenance, bounded pages, per-job budgets and site-action coordination.
3. Upgrade Codex and Gemini sessions to a verified tool loop, preserving the current fixed-context path; expose provider-specific readiness honestly.
4. Run a small multi-profile fitness comparison with selected candidates, counterexamples and explicit coverage; keep findings proposed until human review.
5. Bind Studio's Sessions, Explore, Findings and Review UI to the accepted headless contracts. The analytical mirror map remains a representation of source data.
