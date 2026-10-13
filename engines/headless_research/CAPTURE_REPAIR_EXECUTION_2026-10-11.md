# Controlled capture repair and performance update — 2026-10-11

This is a continuation of [CAPTURE_FOUNDATION_AUDIT_2026-10-11.md](CAPTURE_FOUNDATION_AUDIT_2026-10-11.md). The original audit describes the archive before the controlled repair. The mirror is the captured source; this report and the Red Engine index are derived records.

## Changes made

- The native collector now uses a dedicated hidden, app-owned WebView for capture. Closing the visible Mining window no longer drops a running capture. The hidden window shares the app session and does not take the foreground. A website access gate or cooldown still pauses the job; no alternate transport or gate bypass was added.
- The queue keeps atomic, resumable checkpoints. The smaller in-memory capture status endpoint supplies the current note, phase, retained post count, and comment count to Studio every two seconds. The existing red progress bar animates only while capture is active (or when its preview control is used).
- Targeted retries can select up to 30 retained post IDs. Saved source data and files are reused. Requested notes missing from the currently exposed profile cards remain `needs_attention`; this is not treated as successful capture.
- The packaged r9 collector enforces a minimum of 10 seconds between media requests, 25 seconds between posts, and 1.5 seconds of page delay. It upgrades the older five-second trial setting on resume/retry. The current three-post batch was paused before the r9 switch, then resumed at the ten-second media interval.
- Full-profile media reconciliation during an active job is spaced at 180 seconds, with a final reconciliation at paused, complete, partial, failed, or attention states. The prior 30-second active interval repeatedly scanned the whole profile as individual assets were saved. This changes local work, not website request pacing.
- The separate source text index refreshes changed post/comment rows using observation and comment cursors. The latest paused-batch refresh changed three posts in its derived index. Captured source tables were not rewritten by indexing.

## Measured boundaries

- A single streaming study construction of 29,255 records produced the same membership and coverage as the prior path, using 6.7 MB instead of 67.6 MB peak Python allocations and 6.25 instead of 7.50 seconds. SQLite native memory is outside that Python allocation measure. See `data/capture-study-perf-2026-10-11.json`.
- In the earlier eight-run offline queue probe, compact JSON reduced a 1.57 MB checkpoint to about 0.99 MB. Median counter, encoding, and temporary-file write times were 1.64 ms, 17.16 ms, and 4.44 ms. See `data/capture-checkpoint-perf-2026-10-11.json`.
- During the controlled repair, the queue accumulated 276 checkpoints: 50.98 seconds of checkpoint preparation and 10.06 seconds of writes. Seventeen full media reconciliations accounted for 36.21 seconds of that preparation. These are cumulative per-job figures spanning r6–r9, not an isolated before/after benchmark for r9.
- At the batch's resting state, reconciliation found 248 post records, 10,713 retained comments, 19 missing parent references, all 1,352 post-media files present, and 65 of 11,489 comment-owned attachment files present. Per-post avatar memberships were 603 of 7,665; shared avatars may be counted in several posts. One newly discovered post is still an uncaptured stub. See `data/capture-reconciliation-2026-10-11-repair-final.json` and `data/capture-repair-plan-2026-10-11-repair-final.json`.

The three selected notes ended `partial`: they retained 14/15, 9/10, and 10/12 comments reported by the source. Their currently enumerated attachment sets were saved 30/30, 21/21, and 21/21. The missing comments and the 19 absent parent links remain explicit coverage gaps; remote discussion media has not been inferred from local file presence or marked complete. The batch stopped at a selected-note checkpoint rather than continuing the rest of the profile queue.

## Preserved state

The archive and queue have SQLite/queue backups under `00_ADMIN/Backups/RedCaptureRepair-*`; source changes have copies and SHA-256 manifests under `00_ADMIN/Backups/RedCaptureCode-*`, `RedCaptureBatch-*`, `RedCapturePerf-*`, and `RedCapturePacing-*`. The r9 package is under `01_Mining_App/Reel_Harvest/dist/research-2026-10-11-r9`. Its shared data directory remains `D:/0/Noir Fiction Engine/Red Note Gemini`.

## Pacing adjustment after the batch

The user reported that the older app had run at 10 seconds between posts and 10 seconds between media requests, with a separate five-second trial. The next baseline is 10/10. Packaged r10 changes the default/minimum post interval to 10 seconds and adds an atomic pacing change for a paused job. The retained job was changed to 10/10 while paused; no additional capture was started by this adjustment. Its recorded source counts and partial coverage above remain the outcome of the preceding batch. The five-second trial is not the default and remains below the current configured minimum. The live r10 package is `01_Mining_App/Reel_Harvest/dist/research-2026-10-11-r10/Red Note Gemini`.

## Hidden-reply repair trial

The user identified collapsed “view more” comment trees as a capture gap. The r11 collector recognizes additional “view more”, “view all replies”, “remaining replies”, and Chinese equivalents on interactive controls in the comment area. A pre-run SQLite/queue snapshot is in `00_ADMIN/Backups/RedCaptureRepliesBatch-20261011`, and the previous source is in `00_ADMIN/Backups/RedCaptureReplies-20261011`, each with a hash manifest.

A bounded two-note retry ran at 10/10 in the hidden app window without a site cooldown or access gate. One previously partial note remained at 14/15 retained comments with 30/30 exposed attachments saved. The adjacent note rose from 9 to 13 retained comments; its currently reported source count was 14, and 27/28 exposed attachments saved. Both remain `partial` with `source_count_gap`; zero expandable controls were recognized at the final bottom-of-scroll snapshot. The run demonstrates recovered comments, but does not prove that every hidden reply has been found or that the site's count maps one-to-one to available rows.

Resting-state reconciliation now records 10,717 comments, 80 of 11,489 comment-owned attachment files, 19 missing parent references, and 243 posts with a discussion-media gap. All 1,352 post-media files remain present. The derived text index refreshed two changed posts. See `data/capture-reconciliation-2026-10-11-replies.json` and `data/capture-repair-plan-2026-10-11-replies.json`. The active package is r11, and the job is paused at its selected-note checkpoint.
