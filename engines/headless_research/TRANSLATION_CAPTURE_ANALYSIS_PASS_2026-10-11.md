# Translation, capture, and analysis pass — 2026-10-11

This is an engine/data pass. The captured mirror is the original-language source; machine English text and media extraction are derived, unreviewed reading aids. The Studio graph prototype did not participate.

## 1. Reviewed GPT translation sample

The signed-in native GPT path completed two batches totaling six posts and twelve comments. The sampled derivative rows are in `Red Note Gemini/data_v2/rednote-v2.sqlite`; a pre-batch SQLite backup and the sample receipt are under `00_ADMIN/Backups/RedTranslationBatch-20261011/`. Source hashes were checked. The English is usable for finding candidates but has unresolved idiom and joke choices: `老婆` and `妈咪` are context-dependent, and the homophone joke `没铝` becomes literal “No aluminum!!”. No glossary or human translation approval was applied.

## 2. Video text and subtitles

The saved 9.68-second clip yielded Chinese OCR at 0 and 9,533 ms. GPT translated `在想昨晚我喝多了` as “Thinking I drank too much last night” and `为什么要向我的高跟鞋求婚` as “Why propose to my high heels”. Native subtitles detected a short `字幕by索兰娅` segment, rendered in English as “Subtitles by 索兰娅”. This is sparse coverage of one clip, not a complete bilingual transcript or scene description. The native subtitle failure was traced to the installed `faster-whisper`/PyAV call mismatch; the app environment now pins `av>=14,<19` and native build r21 completed the jobs.

## 3. Partial comment capture

The retained queue has 67 posts with partial comment-tree coverage: 63 source-count gaps, three paused captures, and one scroll-budget stop. The reported gaps total 927, but source counters can differ from currently visible comments. The targeted retry retained 10-second post and media pacing and deferred bulk comment attachment download. Of three low-gap posts, one was revisited and stayed 9/10; two disappeared from bounded profile-card discovery. No new comment text was recovered in that attempt, and none was marked complete. Native build r22 then tried the saved first-party link for one missing note, with loaded note-ID and author checks, but Red Note returned an unavailable page. The queue stopped at `needs_attention` / `source_unavailable`, preserving all prior content. The fallback has not produced a successful capture receipt.

## 4. Discussion attachment classification

The latest archived comments contain 11,540 emoji occurrences (76 distinct source URLs) and 10,714 `author_avatar` image occurrences (5,107 distinct URLs). Of these, 131 emoji and 125 avatar occurrences are saved; the others retain remote references. All 10,714 comments in this inventory have an author-avatar URL. These are discussion assets, not missing primary post media. The mirror's 1,325 post images and 11 post videos are marked verified. Keep commenter profile links available for on-demand live visits; this pass did not clone commenter profiles or bulk fetch avatars.

## 5. Red Engine linkage

Study `study-1aedc35dc7704828b79995778f4adbe6` pins one profile, two posts, 479 comments, and 919 assets. It can expose current source-hash-checked machine translations, OCR, and transcript rows as unreviewed reading aids. Context `context-122522317a0844589d3d9513fda3cf3f` includes selected English aids while retaining original Chinese for citations; inventory receipt `measure-68d323974fd542ffbfcfb0dfa7b4e43f` is deterministic. A real Codex job, `analysis-6b46af31d9e3462681f797e52203f52e`, reached `awaiting_review` and did not approve its own output.

Proposed finding `finding-cd7c4f5538bd4f679810cb0ec6ecfe86` pins the video asset, OCR derivative, timestamp, exact Chinese frame line, and an exact archived comment quote. It currently reports `evidence_current: true`; it remains **proposed**, with machine OCR unreviewed and two sampled frames. The engine rechecks source and derivative hashes when the context or finding is read. The live HTTP research service at port 5188 exposes the derived-reading route; the mirror remains read-only to Red Engine. An older proposed finding still reads after the additive evidence-schema migration.

## Remaining review

Human review is needed for translation nuance, subtitle/OCR accuracy, the proposed finding, and the 67 partial comment trees. A source-count gap alone must not be treated as proof that a hidden reply is obtainable. Do not bulk download commenter profile pages to address discussion-asset coverage.
