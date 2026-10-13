# RedNote data foundation change manifest — 2026-10-11

The source files below were copied to `00_ADMIN/Backups/RedEngineCapture20261011` before editing. Original captured JSON, media and legacy database files were not rewritten by the audit/index tools. Opening the packaged native app did rewrite the v2 SQLite file at the byte level; the before/after hashes and unchanged record counts are recorded in `CAPTURE_FOUNDATION_AUDIT_2026-10-11.md`.

| File | Before SHA-256 | After SHA-256 | Change |
| --- | --- | --- | --- |
| `01_Mining_App/Reel_Harvest/profile_clone.py` | `0AA397FA3BF5EE599414CFEB6BFE7FE31629B3248139C466EF0AA056A6B1B73A` | `D971E40FAA8B9ED8A173D55B522DF0DF62F819C3D44EA47D04200E2AA26D95CA` | Compact atomic queue checkpoints. |
| `01_Mining_App/Reel_Harvest/rednote_engine/server.py` | `C57AA13600868CAF90B64F375F5B739CC319C90B3ED86FD8EBEE20452A24A759` | `1A6EE148C30EED1A558D08427A8D0BEF76B8571DBEBF4338D26553390747C352` | Report native host separately; explicit owned derived-worker option. |
| `01_Mining_App/Reel_Harvest/rednote_v2/api.py` | `CFC8FBB786A1FF9055B7A31A6D2851AB50136FE37C771B0D98DFB1D47DB4E205` | `FFB3E8CE24ECB51A8204A690D1D0E4D41590D2F9B26AFE6ABE76E8DDB6AB8C31` | Expose derived-worker attachment in future native builds. |
| `Red_Engine/README.md` | `D733E012E31CFC83C96F587BD3AE5A8EDF9E53CC48D430CBE941DAD1FF9DEA45` | `0A0758A9DF923B4DB44886DBD6642A64B8FDDCDE487FBC3C2D16BC37D477E050` | Document commands, boundaries and audit. |

New source: `Red_Engine/archive_foundation.py` (`B9691589FD9C7E769664FD5921E153CDC0949D8D2C549E88D06E0BF503CCF5C0`). New derived artifacts: `data/source-index.sqlite`, two dated audit JSON reports, `data/media-decode-2026-10-11.json`, and `data/snapshots/20261011T081429Z` with database backups, imported JSON and a media manifest. The index is disposable; the snapshot and audit reports should be retained.

Four changed Python files parsed successfully. Both snapshot SQLite backups passed `PRAGMA quick_check`; 248 imported JSON copies passed SHA-256 checks. All 1,357 saved images loaded and the first frame of all 11 videos decoded. A live exact-text index query returned three captured matches for a four-character Chinese term. The read-only service at port 5182 reported the native app browser reachable while correctly keeping its own `captureAvailable=false`. The native app's derived route returned a validation error to an intentionally invalid request, confirming worker attachment without queuing work. The user-stopped clone remained paused; no capture or derived job was started.
