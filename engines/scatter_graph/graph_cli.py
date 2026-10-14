"""Small headless CLI for authored Scatter graph revisions."""

import argparse
import json
from pathlib import Path

from graph import ScatterGraphStore
from source_adapter import RedNoteSourceAdapter
from fixtures import build_rainee_chain
from execution import ScatterExecution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("title")
    show = commands.add_parser("show")
    show.add_argument("project_id")
    show.add_argument("--revision", type=int)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("project_id")
    inspect.add_argument("--revision", type=int)
    sources = commands.add_parser("sources")
    sources.add_argument("project_id")
    sources.add_argument("--revision", type=int)
    pin = commands.add_parser("pin-source")
    pin.add_argument("study_id")
    pin.add_argument("kind", choices=sorted(RedNoteSourceAdapter.KINDS))
    pin.add_argument("entity_id")
    commands.add_parser("create-rainee-chain")
    edit = commands.add_parser("apply")
    edit.add_argument("project_id")
    edit.add_argument("expected_revision", type=int)
    edit.add_argument("command_id")
    edit.add_argument("command_json", help="JSON file containing one typed graph command")
    output = commands.add_parser("compile-output")
    output.add_argument("project_id")
    output.add_argument("output_id")
    output.add_argument("--revision", type=int)
    branch = commands.add_parser("compile-branch")
    branch.add_argument("project_id")
    branch.add_argument("branch_id")
    branch.add_argument("--revision", type=int)
    branches = commands.add_parser("branches")
    branches.add_argument("project_id")
    branches.add_argument("--revision", type=int)
    queue = commands.add_parser("queue-output")
    queue.add_argument("project_id")
    queue.add_argument("output_id")
    queue.add_argument("--revision", type=int)
    queue_branch = commands.add_parser("queue-branch")
    queue_branch.add_argument("project_id")
    queue_branch.add_argument("branch_id")
    queue_branch.add_argument("--revision", type=int)
    run = commands.add_parser("run")
    run.add_argument("run_id")
    show_run = commands.add_parser("show-run")
    show_run.add_argument("run_id")
    cancel_run = commands.add_parser("cancel-queued")
    cancel_run.add_argument("run_id")
    args = parser.parse_args()
    store = ScatterGraphStore(args.db) if args.db else ScatterGraphStore()
    if args.command == "create":
        result = store.create(args.title)
    elif args.command == "show":
        result = store.read(args.project_id, args.revision)
    elif args.command == "inspect":
        result = store.inspect(args.project_id, args.revision)
    elif args.command == "sources":
        result = store.source_status(args.project_id, args.revision)
    elif args.command == "pin-source":
        result = RedNoteSourceAdapter().pin(args.study_id, args.kind, args.entity_id)
    elif args.command == "create-rainee-chain":
        result = build_rainee_chain(store)
    elif args.command in {"compile-output", "compile-branch", "branches", "queue-output", "queue-branch", "run", "show-run", "cancel-queued"}:
        engine = ScatterExecution(store)
        if args.command == "compile-output":
            result = engine.compile_output(args.project_id, args.output_id, args.revision)
        elif args.command == "compile-branch":
            result = engine.compile_branch(args.project_id, args.branch_id, args.revision)
        elif args.command == "branches":
            result = engine.branches(args.project_id, args.revision)
        elif args.command == "queue-output":
            result = engine.queue_output(args.project_id, args.output_id, args.revision)
        elif args.command == "queue-branch":
            result = engine.queue_branch(args.project_id, args.branch_id, args.revision)
        elif args.command == "show-run":
            result = engine.read_run(args.run_id)
        elif args.command == "cancel-queued":
            result = engine.cancel_queued(args.run_id)
        else:
            from providers import dispatch
            result = dispatch(args.run_id, engine)
    else:
        result = store.apply(args.project_id, args.expected_revision, args.command_id,
                             json.loads(Path(args.command_json).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
