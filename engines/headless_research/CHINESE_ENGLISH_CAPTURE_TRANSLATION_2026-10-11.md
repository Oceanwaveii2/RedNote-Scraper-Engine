# Chinese-to-English capture and translation status — 2026-10-11

## Source rule

The normally rendered Chinese page and media are the source. The archive retains Chinese post text, comments, ordered inline comment media, Chinese transcript segments, OCR boxes and Chinese OCR lines. English is stored as a separate derived record with its engine, revision, creation time and source hash. A changed source invalidates display of its old English translation; no translation overwrites the original.

## Active build

`2.0.0-candidate-r15` added:

- GPT translation of post text and visible comment batches with only context-matching, reviewed glossary entries. The glossary and prompt revision are included in the translation revision and the live Browse translation cache key.
- The Hy-MT2 option was subsequently withdrawn at the user's direction because it does not work for this workflow. It must not be presented or called by the active app; GPT remains the translation provider.
- English OCR jobs for saved images and the first sampled video frame. Each English line keeps the source line and original OCR geometry. The archive UI presents Chinese and English together.
- Batches of up to 12 Chinese subtitle segments per GPT request. English subtitle and OCR derivatives are bound to the exact Chinese transcript or OCR content, rather than only to the media file hash.
- A source-revision check before committing translated post/comment text or translated transcript/OCR output. The UI hides stale translations after a new capture.

Source compilation, TypeScript compilation, frontend build and native packaging completed. Native `r15` diagnostics showed the archive available and no V2/master import errors; the selected-note capture checkpoint remained paused. A real GPT translation run and an OCR/subtitle job were not performed in this change. Existing older English subtitle tracks tied only to a media hash must be regenerated before they are shown as current.

`2.0.0-candidate-r16` removed the local translation choice from the archive UI, rejects `provider=local` at the API, and corrected the legacy Browse toolbar's misleading "Tencent local" label to GPT. The unused Hy-MT2 source file is preserved as history but has no active call path. The r16 frontend and native package built; startup diagnostics reported the existing archive available with no V2 or master import error. No real GPT translation or OCR job was run in this change.

## Next capture-quality work

Sample text overlays from more than the first video frame with bounded frame/time provenance; measure OCR recall on Chinese captions and mixed-language posts; keep Chinese original, confidence and source asset for every English line. Review translation quality for slang, names, emoji, and line breaks with a small bilingual sample before any automatic batch translation of the archive.
