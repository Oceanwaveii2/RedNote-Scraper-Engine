# Ling_ZU captured-profile report — 2026-10-11

**Status:** Analytical report complete; three new findings are **proposed and awaiting human review**. This report describes the captured archive, not the current live RedNote profile or a representative audience survey.

## Scope and method

The source is the pinned [ling_zi profile](https://www.rednote.com/user/profile/6082a828000000000101ef8d), profile ID `e40558fa-b06b-54d7-ba90-59abd43446f6`, in Red Engine study `study-ed066c5c0e15471aabc92c25c7ef0e53` (manifest `8a369e4b3a738b5e3853fa803d7edd6673bc53251d699eefc2857d91a9427960`). The snapshot bio says `摄影师` (“photographer”) and `还在成长` (“still growing”). Its follower figures are a dated capture, so this report does not use them as current metrics.

The study pins **247 posts, 10,712 comments and 18,295 asset records**. All 247 posts have `partial` capture status; 19 comment-parent references are unresolved. It cannot establish full-profile completeness, unique audience size, exposure, reach, conversion or current platform state. The profile's own capture metadata says enumeration was still running at its recorded capture time.

The fresh deterministic receipt is `measure-0d937a342aec475aa8dab874d2cca18d`, algorithm `captured-marketing/1`, result hash `9a0551e3c31c81dc5e369a16d888eaff1509f36ec4b07875952d12cce5e7504f`. It counts literal matches in pinned post titles/bodies and captured comments. A record can match several terms and concepts. Percentages below use the captured-post or captured-comment denominator, not impressions or people. The previous broader preset receipt is `measure-e763f60e65e7498baec7b0759c05c693`.

| Text pattern in this report's vocabulary | Captured posts | Captured comments | Reading limit |
| --- | ---: | ---: | --- |
| `老婆`, `姐妹`, `wife` | 56 / 247 (22.7%) | 1,463 / 10,712 (13.7%) | Forms of address, not verified gender, relationship or attraction |
| `身材`, `曲线`, `练腰`, `有氧`, `肌肉` | 5 / 247 (2.0%) | 329 / 10,712 (3.1%) | Text discussion, not a measured body or medical result |
| `链接`, `哪里买`, `求同款` | 0 | 106 / 10,712 (1.0%) | Item-source language, not purchases or sales |
| `妆容`, `穿搭` | 125 / 247 (50.6%) | 22 / 10,712 (0.2%) | Literal fashion/makeup terms; no image interpretation |
| `购买` | 0 | 0 | Only this exact purchase term was checked |

The 1,463 affectionate-address comment records include 1,402 with `老婆`; those 1,402 records contain 504 distinct **source author IDs**, not 504 verified people. The 106 item-source records include 100 with `链接`. Exact phrase counts miss synonyms, paraphrases, images and audio; neither the zero `购买` count nor any positive count is a semantic classification.

## Cited observations and counterevidence

**Affectionate address.** Under [a post about a strong woman](https://www.rednote.com/discovery/item/678710360000000019033e58), comment `74e8778f-139e-5dfb-9c7b-97a7bf63a879` says `老婆的赞美惊心动魄`. Under [a fashion/transition post](https://www.rednote.com/discovery/item/627504ea0000000021035d00), comment `dafce3a2-f580-56d5-9226-b0b9f07b9d3d` says `姐妹我好爱，我晚画你`, and comment `f103dfa8-15cb-5a58-b2d0-64960504fb7b` uses `我可爱的wife`. Counterexample: `ddd049eb-42f0-5b4f-9824-f7576ef65a66` says `老婆我这号被限流了`, using the same address in an account-reach discussion. These examples support a language pattern, not a claim about commenters' sex, orientation or real-world relationships. Proposed finding: `finding-8ed84689f3164fe8af8b033ae8b160c9`.

**Body and fitness discussion has mixed responses.** The archived [“挑战回复所有评论💚” post](https://www.rednote.com/discovery/item/664d4e640000000015009110) says `夏天来了 还没刷脂成功` (“summer is here; fat loss is not yet successful”). Comment `26e8905c-0743-5ddb-8ea5-bceef4085ae0` asks for `练腰的教程` (a waist-training tutorial); `8e590679-cb4c-5209-9108-c1bb1ab247dc` asks `仅有氧刷脂会掉肌肉吗` (whether aerobic exercise alone while cutting fat loses muscle). Counterexample `1609bb02-9a5d-5e31-9a7f-a1d2a83910b6` expresses doubt that the depicted body is real and alleges editing. That is **a commenter allegation**, not evidence that an image was edited. The pattern suggests fitness questions are worth indexing separately from generic compliments; the archive does not verify training advice or visual appearance. Proposed finding: `finding-53bb7c24e6a5476ea30c893e067f8729`.

**Requests for item sources occur, without a sales trail.** Comment `10d5b8b7-4d1a-5b28-b45b-37dacbd5160e` asks for a pants link under [an outdoor/green clothing post](https://www.rednote.com/discovery/item/68482dce000000002001ede9). Comment `1c140c9a-0927-578f-8aaf-72f4755fc002` asks for a dress link under [a daily outfit post](https://www.rednote.com/discovery/item/62f4d989000000000900afc3). Comment `113be776-d493-596a-9fa4-55374aa711d5` asks for an underwear link under [another outfit post](https://www.rednote.com/discovery/item/65b707cb000000000a031d9a). A different request, `姐姐出一期妆容吧` (“please make a makeup tutorial”), appears as comment `0fdca2a5-f7aa-599c-90bb-69df77733f19` under [a styling post](https://www.rednote.com/discovery/item/6348df5e000000000603efea). These are observable requests; no captured transaction or purchase is established. Proposed finding: `finding-e8330966428b4f2faed9f68e7d675127`.

## Media and marketing interpretation

The broader saved receipt counts 236 photo posts and 11 video posts; 246 posts have at least one captured comment, with a median of 27 captured comments per post. These are **capture counts**, not engagement rates. Local verification exists for all 1,325 post-image records and 11 video records, but only 18 of 11,489 comment attachments and 10 of 5,454 avatar records. This report did not inspect image or video pixels or audio, and therefore makes no claim about pictured bodies, product brands, performance, expressions or music.

For a future editorial or marketing study, the archive supports three distinct **research lanes**: affectionate language; body/fitness questions and disagreement; and requests for item sources. Keep these as observed comment categories with their original Chinese terms and exact source links. Do not turn them into demographic targeting or conversion estimates. A later source review could examine whether requested item links received replies and whether media actually shows the item or technique named in a comment.

## Review and reproducibility

The three finding records above each have pinned supporting quotes, limiting counterevidence, and a study-scoped exact `购买` counter-search. Each returned `evidence_current: true`; **none is human-approved**. Review can approve, reject, or request changes with a named reviewer and rationale. The earlier cosplay finding `finding-b293d33955014febaad703a74bae57af` is also proposed and remains separate from this profile-wide report.

Use `python Red_Engine/study_cli.py measurement study-ed066c5c0e15471aabc92c25c7ef0e53 measure-0d937a342aec475aa8dab874d2cca18d` for the full terminology receipt and `python Red_Engine/study_cli.py finding STUDY_ID FINDING_ID` for each exact source hash and review state. The mirror database is opened read-only; measurement and findings are stored separately in Red Engine's study database. This report interprets the captured snapshot and does not change the mirror.
