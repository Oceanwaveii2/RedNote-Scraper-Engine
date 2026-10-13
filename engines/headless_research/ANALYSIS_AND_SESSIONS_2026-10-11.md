# Red Engine analysis and AI-session receipt — 2026-10-11

## Boundary

This work operates on a pinned, read-only captured RedNote study. The analytical engine is separate from Studio 2.5 and its presentation graph. The captured archive was not edited. The study is `study-ed066c5c0e15471aabc92c25c7ef0e53`; the saved marketing run is `measure-e763f60e65e7498baec7b0759c05c693` (`captured-marketing/1`). Open these receipts through `study_cli.py measurement` and `study_cli.py finding`, which recheck the source pins.

## Captured marketing measurements

The denominator is **247 captured posts, 10,712 captured comments and 18,295 asset records**. These are archive records, not a sample of all RedNote activity or distinct people. Concept counts mean a captured record contained at least one literal preset term; concepts can overlap within a record.

| Preset concept | Posts with a literal match | Comments with a literal match |
| --- | ---: | ---: |
| Audience language | 56 | 1,463 |
| Body presentation | 6 | 416 |
| Fashion | 120 | 19 |
| Glamour | 21 | 167 |
| Makeup | 7 | 11 |

The full receipt retains each original-language term, exact mention count, the count of distinct captured author IDs in matching comments, and example source IDs and hashes. “Audience language” is a retrieval label for terms such as `老婆`, `姐妹`, `百合`, `wife` and `yuri`; a hit does **not** verify a writer's gender, sexual orientation or relationship. “Body presentation” gathers literal terms such as `身材` and `曲线`; it is not visual assessment. “Fashion,” “glamour” and “makeup” likewise describe text matches, not the pictured scene, sentiment or purchase intent.

The archive has 236 photo posts and 11 video posts. The median captured comment count per post is 27; 246 posts have at least one captured comment; 251 comments contain question-mark punctuation. These are descriptive counts, not engagement rates, intent classification or sentiment scores. Asset roles include 1,325 post images, 11 videos, 11,489 comment attachments and 5,454 avatars. Verified local files exist for all 1,325 post-image and 11 video records, but for only 18 comment attachments and 10 avatars. The media measurement reads metadata and file verification; it does not interpret images, video or audio.

For the cited post `b825618a-e2a2-500e-90f7-86ff639e7bc1`, `media-context` returns nine verified post-image files and 496 comment-attachment records, of which 18 have verified files. The model evidence package included media descriptors but no media bytes. Any claim about a visual feature needs a separate sourced media review.

## Proposed cited finding and counterevidence

Finding `finding-b293d33955014febaad703a74bae57af` is **proposed**. Its narrow claim is that the selected post text frames the work as game-inspired cosplay and two selected comments use brief enthusiastic language. It explicitly declines to infer demographics, shopping, response rates or attraction from this sample.

The archived source URL for this post and both comments is <https://www.rednote.com/discovery/item/62370bd2000000000102b6b4>. The pin hashes below identify the captured revision used by the finding; a live page may have changed.

- Supporting post `b825618a-e2a2-500e-90f7-86ff639e7bc1`, pinned hash `258c01eca3452d6ed7e1762ee006b4385d747785aa1061443dbf95b75880190d`: “一直很喜欢《杀手47》里的修女刺杀队 / 斗胆在楼梯间cos了一下” (the stored quote contains the original newline).
- Supporting comment `442ed125-c9ba-5a4f-99e8-dee28a7c4738`, pinned hash `3b9e0e693508ca34fae89ff6274abf7b01baf04a56255cfb19aa7a02c738181a`: “我的xp啊啊啊啊啊啊”. This is a source-text observation, not a verified demographic attribute.
- Limiting counterexample `7dc0eaa4-5922-58de-a46d-a9dd9c673e23`, pinned hash `93d165b6bc235694c306b540995d87e6443830afbc5d6ae4a26108242506ce32`: “精准拿捏”. This short reply contains no explicit shopping, body-feature or aspiration statement, weakening a broader marketing reading of the exchange.

An exact study-scoped counter-search for `购买` returned zero captured text matches. The search is recorded with the finding. It is not proof that purchasing is absent from uncaptured content, images or differently worded comments. The two selected comments are not representative of 10,712 captured comments; the capture itself is partial. The finding can be approved, rejected or sent for changes only by a named human reviewer with a rationale. No review decision was made here.

## Real provider runs and evidence checks

| Provider | Job | Saved output hash | State | Exact source observations |
| --- | --- | --- | --- | ---: |
| Signed-in Codex, `gpt-6.1-sol` | `analysis-cb49ad8d78c840a7ba3a9480136b6104` | `2ab30b043e4390d9df0c57ca6bb8b9f0132e4a13161eedcc8c295ea626fd9a82` | Awaiting human review | 4 |
| Antigravity Gemini, `flash` | `analysis-088f151a00e2476988f5dc0ef9616e31` | `d5c5cb1b7ebd05452f45aed5f79ba423a19749b4355b518c64e1ea079edb8068` | Awaiting human review | 3 |

Both jobs used saved context `context-21619eebcb5b4e3a95d123e21b81f8e2` from the pinned study. The engine accepted output only after checking the selected source IDs, exact quote substrings, context freshness, structure and output hash. These mechanical checks do not verify every interpretive sentence; human review remains required. The completed Gemini transcript contained no tool-call steps. Its summary includes an awkward coverage phrase (“460 captured out of expected 460”); that wording should be corrected or rejected in review rather than treated as a verified statistic.

Gemini's first delivery attempt failed because Antigravity's Windows `agentapi` accepted only the first line of a multi-line positional prompt. The failed job event preserves an unknown provider outcome. An explicit retry sent the full JSON-escaped single-line request and saved the validated output. `gemini-project.json` contains only the project UUID. The installed bridge kit discovers the active local sidecar session without storing its token in Red Engine files. A status probe alone does not prove current model entitlement; the completed turn verifies that this session processed the bounded context.

## Recovery and remaining work

Running jobs now have a SQLite lease and heartbeat. Cancellation of a running Codex job requests bridge interruption; Gemini cancellation stops local waiting and records the remote outcome as unknown. Restart recovery marks expired leases interrupted or cancelled and never automatically resends a possibly delivered request. `job-recover` found no stale jobs after the two completed runs. Failed or interrupted jobs require explicit `job-retry` and dispatch, with the pinned context checked again.

Python syntax compilation passed for the changed modules. The CLI directly verified the saved marketing receipt, post media context, proposed finding, real Codex output and real Gemini output. The currently running port-5188 process predates these HTTP route changes; an attempted owned-service restart was rejected by automatic execution policy. The new CLI commands are live, while the new HTTP routes require a permitted service restart. Studio's feature prototype is not yet bound to the review controls.
