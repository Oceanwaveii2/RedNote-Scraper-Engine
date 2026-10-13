# Review of 67 partial comment trees — 2026-10-11

The row-by-row [classification](data/partial-comment-tree-review-2026-10-11.csv) was read from the current v2 mirror without changing it. It includes the source post link, captured and reported counts, visibility diagnostics, and missing parent references.

| Category | Posts | Reported count gap | Interpretation |
| --- | ---: | ---: | --- |
| Visible end reached; source count still larger | 63 | 187 | `atBottom=true`, no reported unexpanded replies or blocked branches, and loading stopped. This is a counter discrepancy or content not exposed in the saved web view; the evidence cannot distinguish deleted, app-only, gated, or otherwise hidden comments. |
| Capture paused before visibility proof | 3 | 23 | No recorded end-of-list/reply-control diagnostics. Keep partial. |
| Scroll budget ended before bottom | 1 | 717 | `atBottom=false` after 300 rounds. This is the clearest candidate for a later paced continuation; it does not prove 717 reachable replies. |

Across these posts, 19 captured replies reference a parent source ID absent from their captured post. That is a parent-coverage issue, not proof that a visible “view more” control was missed. No row was upgraded to complete and no unavailable source was bypassed. The source-count gaps total 927, but the number is a comparison with site-reported counters rather than a verified count of retrievable comments.

The prior targeted 10-second-paced retry recovered no new comment text. A later retained-note-link attempt received a site `source_unavailable` page and stopped at `needs_attention`, preserving the captured archive. Follow-up capture should prioritize the single scroll-budget tree when source access is available, then only revisit count-gap posts if a fresh visibility trace shows a concrete reply control or additional comments.
