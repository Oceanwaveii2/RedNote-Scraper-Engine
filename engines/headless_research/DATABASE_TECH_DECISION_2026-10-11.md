# Red Engine database technology decision and revised work list

**Research date:** 2026-10-11. **Scope:** capture archive, Red analytical engine and future Scatter authored engine. This is a technology decision and work queue, not a database migration or an approval of the UI prototypes.

## Observed workload and current boundary

The current v2 mirror is a 40,771,584-byte SQLite database with 1 profile, 247 posts, 10,712 comments and 18,295 asset records. The separate Red source-text index is about 7.4 MB and the Red study store about 27 MB. Archive media bytes live in files, not in a graph database. The 2026-10-11 [capture audit](CAPTURE_FOUNDATION_AUDIT_2026-10-11.md) verified the recorded JSON and local media, but all 247 posts remain marked partial, 19 captured comments lack their parent source record, and local comment-attachment/avatar coverage is sparse. Those gaps cannot be repaired by swapping database products.

Python in the active Reel Harvest virtual environment and the shell both report SQLite **3.50.4**. `rednote_v2/store.py` sets `journal_mode=DELETE`. The installed version is in the range affected by SQLite's rare concurrent WAL-reset corruption bug; SQLite documents fixes in 3.51.3 and backports 3.50.7/3.44.6. The [release history](https://www.sqlite.org/changes.html) shows 3.54.0 as the newest release on the research date. Upgrading the runtime is worth evaluating, but journal mode must stay unchanged until the packaged native runtime, readers, backup procedure and concurrent access are verified. See the [SQLite WAL documentation](https://www.sqlite.org/wal.html#walresetbug).

## Technology choice

| Role | Choice now | Reason and gate for change |
| --- | --- | --- |
| Captured source of truth | Keep SQLite plus original JSON and media files. Preserve acquisition receipts and source row/revision hashes. | Existing IDs, relations and hashes already work. Use SQLite's [online backup API](https://www.sqlite.org/backup.html) to make consistent DB snapshots; also copy and independently verify media bytes on a separate volume before claiming disaster recovery. Do not write analytical tables into the captured archive. |
| Source search | Keep the disposable FTS5 trigram index and exact-text fallback for one- and two-character Chinese terms. Version the index against a source snapshot/manifest rather than the mutable live DB file hash alone. | [FTS5 trigram](https://www.sqlite.org/fts5.html#trigramidx) indexes substring/LIKE search, but MATCH cannot find fewer than three Unicode characters. Evaluate a Chinese segmenter, normalized aliases and semantic retrieval against a labelled source-query set only after exact retrieval is measured. |
| Red study/finding state | Keep a separate SQLite store behind engine repository interfaces. Add explicit schema migrations and constraints for new tables; consider [STRICT tables](https://www.sqlite.org/stricttables.html) for new schemas after compatibility review. | One local writer and this archive size do not justify a database server. Immutable study pins, append-only receipts and evidence links matter more than a new engine. |
| Analytical aggregates | Add an optional **DuckDB/Parquet sidecar** only if repeatable study queries exceed measured latency or need large cross-profile scans. Build it from a frozen SQLite backup, never the live writer, and label every export with source snapshot ID and query version. | DuckDB can [query attached SQLite](https://duckdb.org/docs/current/core_extensions/sqlite.html) and [read Parquet with column/filter pushdown](https://duckdb.org/docs/current/data/parquet/overview.html). It is a rebuildable analytical projection, not source authority. |
| Multiuser/server database | Defer PostgreSQL until concurrent writers, remote collaboration, replication or measured operational limits require it. | PostgreSQL 18 offers [logical replication](https://www.postgresql.org/docs/current/logical-replication.html) and [full-text search](https://www.postgresql.org/docs/current/textsearch.html), but introduces a managed server, migration and backup burden that this local workload has not earned. |
| Vector/graph database | Defer. A semantic index, if justified, must retain source IDs, exact text, model version and rebuild instructions. Do not create an authoritative Red graph store. | The Red graph is a read-only UI projection of source relations. Embeddings can retrieve candidates but cannot certify identity, completeness or findings. A vector service or Neo4j-style graph would duplicate authority without solving the capture gaps. |

The DuckDB and PostgreSQL conclusions are architectural inferences from the measured local workload and their documented capabilities. They are not performance benchmarks. No new database dependency is accepted until the relevant gate below is measured.

## Adjusted work list, in order

### P0 — make the data recoverable and source pins stable

1. Copy the **media bytes** and databases to a separate volume; restore a sampled archive into an isolated path and verify DB integrity, imported JSON hashes and sampled media hashes. The existing snapshot has DB/JSON copies and a media manifest only.
2. Replace live SQLite-file-byte hashes as study revision authority with a manifest of stable row/content hashes, source import revisions, coverage snapshot ID and media hashes. Keep file byte hashes as diagnostics. On a capture change, create a new manifest and report changed/new/missing IDs; never silently move old study pins.
3. Reconcile the legacy-only profile, post and 500 comments through an explicit mapping report. Preserve ambiguous IDs as separate entities until reviewed. Make import reversible and idempotent.
4. Define the capture-to-projection receipt: capture event ID, source ID, post completion state, JSON revision, media verification, import watermark and retry key. Reconnect capture and derived processing through this receipt rather than an implicit UI refresh.

### P1 — remove measured capture and query bottlenecks

5. Rebuild the packaged native app with compact queue checkpoints and explicit worker health. Instrument per-phase duration, checkpoint bytes and frequency, memory and recovery time. Try incremental counters or an append-only checkpoint journal only if measurement shows material savings; retain atomic recovery. Do not auto-resume the user-stopped job.
6. Resume only a selected capture segment when explicitly initiated through the app-owned browser. Compare post/comment/media coverage before and after and record challenge/rate-limit outcomes. Preserve configured pacing until a controlled trial supports a change.
7. Move Red study reads to a consistent archive snapshot or manifest-backed projection. Benchmark exact Chinese search, thread navigation, source-link resolution and saved measurements on the present data and a larger representative fixture. Use `EXPLAIN QUERY PLAN` and targeted indexes before introducing DuckDB.
8. Measure the 19 missing-parent cases and unavailable discussion assets separately from DB corruption; expose coverage and missingness in every research result. Full video/audio validation is a separate bounded I/O job.

### P2 — technology experiments with pass/fail decisions

9. Upgrade SQLite in an isolated package/runtime build to a fixed release, run archive open/read/write/concurrency, backup/restore and crash-recovery checks, then decide whether WAL offers a measured benefit. Retain rollback mode until this passes; do not change the live archive in a version probe.
10. Prototype a read-only DuckDB query over a **copied** SQLite snapshot, then a Parquet export, using three saved analytical queries. Compare elapsed time, peak memory, export freshness and result parity with SQLite. Keep it only if it materially improves the target workload.
11. Build a labelled Chinese retrieval set from archived text. Compare current FTS5 exact matches with candidate segmentation/translation and optional semantic retrieval; measure false positives and source citation fidelity. Add a vector index only if it improves recall without losing exact source traceability.
12. Reassess PostgreSQL only after documented need for multiple concurrent writers, remote collaboration or scale beyond measured local limits. If triggered, migrate a derived copy first and prove ID/hash parity and rollback.

### P3 — engine acceptance and UI binding later

13. Implement reviewed findings, source-backed model jobs and Scatter authored persistence behind repository interfaces. Compare source/projection counts and restore results before declaring either engine ready. The Red graph remains a read-only visualization; UI binding and product cutover are separate decisions.

## Evidence limits

This research used official SQLite, DuckDB and PostgreSQL documentation via Firecrawl. The storage recommendations use the 2026-10-11 local archive audit and source inspection. No new engine, SQLite upgrade, DuckDB prototype, PostgreSQL service, vector index, archive restore or capture resume was performed as part of this decision.
