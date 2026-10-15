"""Headless narrative queue operator. No browser or UI dependency."""

import argparse
import json
import time

from execution import ScatterExecution
from story_pipeline import StoryPipeline
from documents import StoryDocuments
from gemini_recovery import GeminiRecovery


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    queue = commands.add_parser("queue-branch")
    queue.add_argument("project_id")
    queue.add_argument("branch_id")
    single = commands.add_parser("queue-output")
    single.add_argument("project_id")
    single.add_argument("output_id")
    worker = commands.add_parser("worker")
    worker.add_argument("--loop", action="store_true")
    select = commands.add_parser("select")
    select.add_argument("parent_run_id")
    select.add_argument("candidate_id")
    select.add_argument("expected_revision", type=int)
    resolve = commands.add_parser("resolve-plan")
    resolve.add_argument("run_id")
    resolve.add_argument("expected_revision", type=int)
    resolve.add_argument("command_id")
    resolve.add_argument("plan_file")
    events = commands.add_parser("events")
    events.add_argument("run_id")
    events.add_argument("--after", type=int, default=0)
    artifact = commands.add_parser("artifact")
    artifact.add_argument("run_id")
    artifact.add_argument("stage", choices=["interpretations", "interpretations_raw",
                                            "interpretations_response", "plan", "plan_raw",
                                            "plan_response", "plan_review", "plan_resolved", "draft", "draft_raw",
                                            "draft_response"])
    status = commands.add_parser("status")
    status.add_argument("run_id")
    input_summary = commands.add_parser("input-summary")
    input_summary.add_argument("run_id")
    commands.add_parser("interrupted")
    recovery = commands.add_parser("mark-interrupted")
    recovery.add_argument("run_id")
    recovery.add_argument("stage", choices=["interpreting", "planning", "writing"])
    timeout_fix = commands.add_parser("reclassify-timeout")
    timeout_fix.add_argument("run_id")
    accept = commands.add_parser("accept-draft")
    accept.add_argument("run_id")
    accept.add_argument("expected_graph_revision", type=int)
    doc = commands.add_parser("document")
    doc.add_argument("document_id")
    edit = commands.add_parser("edit-document")
    edit.add_argument("document_id")
    edit.add_argument("expected_revision", type=int)
    edit.add_argument("command_id")
    edit.add_argument("command_json")
    gemini_inspect = commands.add_parser("gemini-inspect")
    gemini_inspect.add_argument("run_id")
    gemini_attach = commands.add_parser("gemini-attach")
    gemini_attach.add_argument("run_id")
    gemini_attach.add_argument("conversation_id")
    gemini_recover = commands.add_parser("gemini-recover")
    gemini_recover.add_argument("run_id")
    args = parser.parse_args()
    execution = ScatterExecution()
    pipeline = StoryPipeline(execution)
    documents = StoryDocuments(execution)
    recovery = GeminiRecovery(pipeline)
    if args.command == "queue-branch":
        result = pipeline.queue_branch(args.project_id, args.branch_id)
    elif args.command == "queue-output":
        result = pipeline.queue_output(args.project_id, args.output_id)
    elif args.command == "worker":
        if args.loop:
            while True:
                try:
                    result = pipeline.process_next()
                except Exception as exc:
                    print(json.dumps({"state": "worker_error", "error_class": type(exc).__name__,
                                      "message": str(exc)}, ensure_ascii=False), flush=True)
                    time.sleep(1)
                    continue
                if result["state"] == "idle":
                    time.sleep(1)
                else:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
        else:
            result = pipeline.process_next()
    elif args.command == "select":
        result = pipeline.select(args.parent_run_id, args.candidate_id,
                                 args.expected_revision)
    elif args.command == "resolve-plan":
        from pathlib import Path
        result = pipeline.resolve_plan(args.run_id, args.expected_revision, args.command_id,
                                       json.loads(Path(args.plan_file).read_text(encoding="utf-8")))
    elif args.command == "events":
        result = pipeline.events(args.run_id, args.after)
    elif args.command == "artifact":
        result = pipeline.artifact(args.run_id, args.stage)
    elif args.command == "input-summary":
        result = pipeline.input_summary(args.run_id)
    elif args.command == "interrupted":
        result = pipeline.interrupted()
    elif args.command == "mark-interrupted":
        result = pipeline.mark_interrupted(args.run_id, args.stage)
    elif args.command == "reclassify-timeout":
        result = pipeline.reclassify_timeout(args.run_id)
    elif args.command == "accept-draft":
        result = documents.create_from_run(args.run_id, args.expected_graph_revision)
    elif args.command == "document":
        result = documents.read(args.document_id)
    elif args.command == "edit-document":
        result = documents.apply(args.document_id, args.expected_revision,
                                 args.command_id, json.loads(args.command_json))
    elif args.command == "gemini-inspect":
        result = recovery.inspect(args.run_id)
    elif args.command == "gemini-attach":
        result = recovery.attach(args.run_id, args.conversation_id)
    elif args.command == "gemini-recover":
        result = recovery.recover_interpretations(args.run_id)
    else:
        result = execution.read_run(args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
