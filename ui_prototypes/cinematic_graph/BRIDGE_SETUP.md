# Embedded UI review bridges

Open http://127.0.0.1:5186/ and choose **AI review**.

1. Select **ChatGPT · Codex** or **Gemini · Antigravity**.
2. Click **Check connection**. A transport/catalog is not proof of model inference.
3. Inspect the metadata, select a model, and confirm sending the UI layout and review question.
4. Click **Review UI layout**. Recommendations appear in the panel; they are not applied automatically.

## Installed

Both supplied source packages are vendored in `bridge_kits`. Originals and ZIPs are preserved. `ui_bridge.py` hosts provider adapters; `bridge-ui.js` provides the panel. Python standard library HTTP routes run only on loopback 127.0.0.1:5186. Mutations require same-origin JSON with bounded request size. Windows exclusive port binding prevents multiple preview servers sharing the same port.

Only IDs, types, geometry, graph connections and design tokens are supplied. No source captions/comments, media, drafts, actor/override JSON, credentials, archive files, history or code are exposed. No dynamic editing/mining tools are registered. Reviews are text/geometry based; screenshot vision is not installed. Responses stay in process memory. `bridge-telemetry.jsonl` records operation/provider/model/status/time only.

## Codex result

Real account handshake and live model catalog succeeded. A GPT-6.1-Sol request completed and returned layout recommendations. The review identified overlaps and proposed clearer branch lanes, standardized media framing and dedicated cable gutters. Evidence: `RedNote_Graph_Audio_Nodes_20261009/codex-review-verified.png`.

Codex authentication stays with the official local app-server. Account access/limits still apply. The kit uses one request at a time, read-only workspace, no MCP/application tools, and no shell/edit features. Runtime configuration is not an OS security boundary.

## Gemini result and blocker

The actual `language_server.exe agentapi --help` command succeeds. The wrapper at `.gemini/antigravity/bin/agentapi.bat` invokes that subcommand. The ordinary language-server help initially caused an incorrect transport diagnosis; the agentapi-specific probe corrected it.

However, an actual new-conversation request fails with **ANTIGRAVITY_LS_ADDRESS is not set**. The supplied kit does not establish the required running-session/sidecar environment. Its `account()` method claims signed in from executable presence, its model list is hardcoded, and `cancel()` only stops transcript polling. The host corrects these claims: unknown authentication, toolkit-declared model labels, inference unverified, and **Stop waiting** instead of remote cancellation.

The supported sidecar/session setup needs completion in Antigravity. Do not recover credentials from processes or copy session tokens. The user's authorized help request was stored for Gemini in the shared project inbox: message `64a58240-0fd5-408d-abbc-51fc486cd779`, seq 4, delivered to store, not yet read at dispatch. It requests transport/auth/model/cancellation guidance without source content.

Neither original `example.py` is an application installer. The integration imports the clients and runs explicit review requests through a background worker. The standalone generic example was not presented as a working install.

## Sources

- https://learn.chatgpt.com/docs/app-server
- https://www.antigravity.google/docs/sidecars

## Remaining work

- Complete supported Gemini session transport after peer guidance; prove a completed inference turn.
- Add explicit screenshot attachment and vision review if requested, with a visible preview of precisely what is sent.
- Improve cancellation/timeout behavior in the Gemini client using a documented provider interrupt operation.
- Apply selected design recommendations in a separate UI pass. The current adapters do not generate or apply patches.

Mining remains stopped; collector engine and archives are unchanged.
