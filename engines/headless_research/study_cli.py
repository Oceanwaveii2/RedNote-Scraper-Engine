"""Headless command-line access to Red Engine study scopes and pinned sources."""

from __future__ import annotations

import argparse
import json
import sys

from study_core import StudyEngine
from study_storage import SqliteArchive, SqliteStudies


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Red Engine study commands")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("coverage")
    commands.add_parser("list")
    create = commands.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--question", required=True)
    create.add_argument("--profile-id", action="append", required=True, dest="profile_ids")
    create.add_argument("--post-id", action="append", dest="post_ids")
    create.add_argument("--preset-id")
    show = commands.add_parser("show")
    show.add_argument("study_id")
    records = commands.add_parser("records")
    records.add_argument("study_id")
    records.add_argument("--kind", choices=("profile", "post", "comment", "asset"), default="post")
    records.add_argument("--cursor", type=int, default=0)
    records.add_argument("--limit", type=int, default=30)
    source = commands.add_parser("source")
    source.add_argument("study_id")
    source.add_argument("kind", choices=("profile", "post", "comment", "asset"))
    source.add_argument("entity_id")
    derived = commands.add_parser("derived-reading")
    derived.add_argument("study_id")
    derived.add_argument("kind", choices=("post", "comment"))
    derived.add_argument("entity_id")
    search = commands.add_parser("search")
    search.add_argument("study_id")
    search.add_argument("query")
    search.add_argument("--kind", choices=("all", "post", "comment"), default="all")
    search.add_argument("--cursor", type=int, default=0)
    search.add_argument("--limit", type=int, default=30)
    thread = commands.add_parser("thread")
    thread.add_argument("study_id")
    thread.add_argument("post_id")
    thread.add_argument("--cursor", type=int, default=0)
    thread.add_argument("--limit", type=int, default=30)
    measure = commands.add_parser("measure")
    measure.add_argument("study_id")
    measurements = commands.add_parser("measurements")
    measurements.add_argument("study_id")
    measurement = commands.add_parser("measurement")
    measurement.add_argument("study_id")
    measurement.add_argument("run_id")
    marketing = commands.add_parser("marketing-measure", help="Save captured language/media metrics for a preset")
    marketing.add_argument("study_id")
    marketing.add_argument("--preset-id", default="")
    media = commands.add_parser("media-context")
    media.add_argument("study_id")
    media.add_argument("post_id")
    finding_create = commands.add_parser("finding-create")
    finding_create.add_argument("study_id")
    finding_create.add_argument("--file", required=True, help="UTF-8 JSON finding request")
    finding_list = commands.add_parser("findings")
    finding_list.add_argument("study_id")
    finding_show = commands.add_parser("finding")
    finding_show.add_argument("study_id")
    finding_show.add_argument("finding_id")
    finding_review = commands.add_parser("finding-review")
    finding_review.add_argument("study_id")
    finding_review.add_argument("finding_id")
    finding_review.add_argument("--decision", choices=("approve", "reject", "request_changes"), required=True)
    finding_review.add_argument("--reviewer", required=True)
    finding_review.add_argument("--rationale", required=True)
    context = commands.add_parser("context-create")
    context.add_argument("study_id")
    context.add_argument("--task", required=True)
    context.add_argument("--post-id", action="append", required=True, dest="post_ids")
    context.add_argument("--comment-id", action="append", default=[], dest="comment_ids")
    context.add_argument("--asset-id", action="append", default=[], dest="asset_ids")
    context.add_argument("--include-english-aids", action="store_true")
    contexts = commands.add_parser("contexts")
    contexts.add_argument("study_id")
    read_context = commands.add_parser("context")
    read_context.add_argument("study_id")
    read_context.add_argument("context_id")
    new_job = commands.add_parser("job-create")
    new_job.add_argument("study_id")
    new_job.add_argument("context_id")
    new_job.add_argument("--provider", choices=("codex", "gemini"), required=True)
    new_job.add_argument("--model", default="")
    jobs = commands.add_parser("jobs")
    jobs.add_argument("study_id")
    job = commands.add_parser("job")
    job.add_argument("study_id")
    job.add_argument("job_id")
    cancel_job = commands.add_parser("job-cancel")
    cancel_job.add_argument("study_id")
    cancel_job.add_argument("job_id")
    retry_job = commands.add_parser("job-retry")
    retry_job.add_argument("study_id")
    retry_job.add_argument("job_id")
    commands.add_parser("job-recover", help="Mark expired running leases; never auto-resend")
    output = commands.add_parser("job-output")
    output.add_argument("study_id")
    output.add_argument("job_id")
    dispatch = commands.add_parser("job-dispatch-codex", help="Explicitly send a saved context to signed-in Codex")
    dispatch.add_argument("study_id")
    dispatch.add_argument("job_id")
    dispatch_gemini = commands.add_parser("job-dispatch-gemini", help="Explicitly send a saved context through a configured Gemini sidecar")
    dispatch_gemini.add_argument("study_id")
    dispatch_gemini.add_argument("job_id")
    commands.add_parser("codex-status", help="Probe account and model catalog without sending study data")
    commands.add_parser("gemini-status", help="Probe configured Antigravity sidecar without sending study data")
    broker = commands.add_parser("research-dispatch", help="Explicit tool-enabled study research session")
    broker.add_argument("study_id")
    broker.add_argument("question")
    broker.add_argument("--provider", choices=("codex", "gemini"), required=True)
    broker.add_argument("--model", default="")
    receipt = commands.add_parser("research-session", help="Read one saved broker session and receipt summary")
    receipt.add_argument("session_id")
    commands.add_parser("research-sessions", help="List recent broker sessions")
    cancel = commands.add_parser("research-cancel", help="Request cancellation of a running broker session")
    cancel.add_argument("session_id")
    commands.add_parser("research-recover", help="Mark sessions with expired worker heartbeats interrupted")
    sc_compile = commands.add_parser("scatter-compile", help="Compile upstream 5-tier JSON overrides for a Scatter graph node")
    sc_compile.add_argument("--board-file", required=True, help="Path to board layout.json")
    sc_compile.add_argument("--target-node", required=True, help="Target output/LLM/Prose node ID")
    sc_exec = commands.add_parser("scatter-execute", help="Run full Scatter pipeline (DAG Compile -> Grok+Gemini -> Prose Conditioner)")
    sc_exec.add_argument("--board-file", required=True, help="Path to board layout.json")
    sc_exec.add_argument("--target-node", required=True, help="Target output node ID")
    sc_exec.add_argument("--routing-mode", default="gemini_then_grok")
    sc_exec.add_argument("--style-guide", default="noir_velvet_kinetic")
    sc_exec.add_argument("--docx-out", default="", help="Optional path to save .docx manuscript")
    args = parser.parse_args()
    if args.command in {"scatter-compile", "scatter-execute"}:
        from pathlib import Path
        from scatter_pipeline_ext import pipeline_instance as scatter_pipe, MirrorCloneAdapter
        from research_service import MIRROR_DB
        board_data = json.loads(Path(args.board_file).read_text(encoding="utf-8"))
        if MIRROR_DB.is_file():
            board_data = MirrorCloneAdapter(sqlite_path=MIRROR_DB).hydrate_board_from_mirror_sqlite(board_data)
        if args.command == "scatter-compile":
            print(json.dumps(scatter_pipe.compile_chain(board_data, args.target_node), ensure_ascii=False, indent=2))
            return
        res = scatter_pipe.execute_full_pipeline(
            board=board_data,
            target_node_id=args.target_node,
            routing_mode=args.routing_mode,
            style_guide_id=args.style_guide,
        )
        if args.docx_out:
            docx_bytes = scatter_pipe.export_run_docx(
                board=board_data,
                target_node_id=args.target_node,
                title=res["title"],
                text=res["final_text"],
                routing_mode=args.routing_mode,
                style_guide_id=args.style_guide,
            )
            Path(args.docx_out).write_bytes(docx_bytes)
            res["docx_saved_to"] = args.docx_out
            res["docx_bytes"] = len(docx_bytes)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if args.command == "research-dispatch":
        from provider_codex import dispatch_research as codex_research
        from provider_gemini import dispatch_research as gemini_research
        runner = codex_research if args.provider == "codex" else gemini_research
        print(json.dumps(runner(args.study_id, args.question, args.model or ("flash" if args.provider == "gemini" else "")),
                         ensure_ascii=False, indent=2))
        return
    if args.command == "research-session":
        from research_broker import ResearchSessionStore
        print(json.dumps(ResearchSessionStore().read(args.session_id), ensure_ascii=False, indent=2))
        return
    if args.command in {"research-sessions", "research-cancel", "research-recover"}:
        from research_broker import ResearchSessionStore
        store = ResearchSessionStore()
        if args.command == "research-sessions":
            result = {"items": store.list()}
        elif args.command == "research-cancel":
            result = store.request_cancel(args.session_id)
        else:
            result = store.recover_interrupted()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "codex-status":
        from provider_codex import status as codex_status
        print(json.dumps(codex_status(), ensure_ascii=False, indent=2))
        return
    if args.command == "gemini-status":
        from provider_gemini import status as gemini_status
        print(json.dumps(gemini_status(), ensure_ascii=False, indent=2))
        return
    engine = StudyEngine(SqliteArchive(), SqliteStudies())
    try:
        if args.command == "coverage":
            result = engine.coverage()
        elif args.command == "list":
            result = {"items": engine.list()}
        elif args.command == "create":
            result = engine.create({"title": args.title, "question": args.question,
                                    "profile_ids": args.profile_ids, "post_ids": args.post_ids,
                                    "preset_id": args.preset_id})
        elif args.command == "show":
            result = engine.get(args.study_id)
        elif args.command == "records":
            result = engine.records(args.study_id, args.kind, args.cursor, args.limit)
        elif args.command == "source":
            result = engine.source(args.study_id, args.kind, args.entity_id)
        elif args.command == "derived-reading":
            result = engine.derived_reading(args.study_id, args.kind, args.entity_id)
        elif args.command == "search":
            result = engine.search(args.study_id, args.query, args.cursor, args.limit, args.kind)
        elif args.command == "measure":
            result = engine.measure(args.study_id)
        elif args.command == "measurements":
            result = {"items": engine.measurements(args.study_id)}
        elif args.command == "measurement":
            result = engine.measurement(args.study_id, args.run_id)
        elif args.command == "marketing-measure":
            from research_service import DEFAULT_ID, preset_connection, read_preset
            study = engine.get(args.study_id)
            preset_id = args.preset_id or study.get("preset_id") or DEFAULT_ID
            with preset_connection() as db:
                preset = read_preset(db, preset_id)
            if not preset:
                raise LookupError("Research preset not found")
            result = engine.measure_marketing(args.study_id, preset["terms"], preset_id)
        elif args.command == "media-context":
            result = engine.media_context(args.study_id, args.post_id)
        elif args.command == "finding-create":
            from pathlib import Path
            result = engine.create_finding(args.study_id,
                json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "findings":
            result = {"items": engine.findings(args.study_id)}
        elif args.command == "finding":
            result = engine.finding(args.study_id, args.finding_id)
        elif args.command == "finding-review":
            result = engine.review_finding(args.study_id, args.finding_id,
                {"decision": args.decision, "reviewer": args.reviewer,
                 "rationale": args.rationale})
        elif args.command == "context-create":
            result = engine.create_context(args.study_id, {"task": args.task,
                "post_ids": args.post_ids, "comment_ids": args.comment_ids,
                "asset_ids": args.asset_ids, "include_english_aids": args.include_english_aids})
        elif args.command == "contexts":
            result = {"items": engine.contexts(args.study_id)}
        elif args.command == "context":
            result = engine.context(args.study_id, args.context_id)
        elif args.command == "job-create":
            result = engine.create_job(args.study_id, {"context_id": args.context_id,
                                                     "provider": args.provider, "model": args.model})
        elif args.command == "jobs":
            result = {"items": engine.jobs(args.study_id)}
        elif args.command == "job":
            result = engine.job(args.study_id, args.job_id)
        elif args.command == "job-cancel":
            result = engine.cancel_job(args.study_id, args.job_id)
        elif args.command == "job-retry":
            result = engine.retry_job(args.study_id, args.job_id)
        elif args.command == "job-recover":
            result = engine.recover_jobs()
        elif args.command == "job-output":
            result = engine.job_output(args.study_id, args.job_id)
        elif args.command == "job-dispatch-codex":
            from provider_codex import dispatch as dispatch_codex
            result = dispatch_codex(args.study_id, args.job_id)
        elif args.command == "job-dispatch-gemini":
            from provider_gemini import dispatch as dispatch_gemini
            result = dispatch_gemini(args.study_id, args.job_id)
        else:
            result = engine.thread(args.study_id, args.post_id, args.cursor, args.limit)
    except (ValueError, LookupError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
