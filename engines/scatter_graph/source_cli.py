"""Headless operator for revisioned custom source data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from authored_sources import AuthoredSources
from source_adapter import RedNoteSourceAdapter


def _document(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Source document must contain a JSON object")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("create")
    add.add_argument("kind", choices=["profile", "post", "comment"])
    add.add_argument("title")
    add.add_argument("json_file")
    add.add_argument("--id")
    revise = commands.add_parser("revise")
    revise.add_argument("record_id")
    revise.add_argument("expected_revision", type=int)
    revise.add_argument("command_id")
    revise.add_argument("json_file")
    read = commands.add_parser("read")
    read.add_argument("record_id")
    read.add_argument("--revision", type=int)
    listed = commands.add_parser("list")
    listed.add_argument("--kind", choices=["profile", "post", "comment"])
    listed.add_argument("--cursor", type=int, default=0)
    listed.add_argument("--limit", type=int, default=50)
    pin = commands.add_parser("pin")
    pin.add_argument("record_id")
    pin.add_argument("--revision", type=int)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("study_id")
    catalog.add_argument("kind", choices=sorted(RedNoteSourceAdapter.KINDS))
    catalog.add_argument("--cursor", type=int, default=0)
    catalog.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    store = AuthoredSources()
    if args.command == "create":
        result = store.create(args.kind, args.title, _document(args.json_file), args.id)
    elif args.command == "revise":
        result = store.revise(args.record_id, args.expected_revision,
                              args.command_id, _document(args.json_file))
    elif args.command == "read":
        result = store.read(args.record_id, args.revision)
    elif args.command == "list":
        result = store.list(args.kind, args.cursor, args.limit)
    elif args.command == "pin":
        result = store.pin(args.record_id, args.revision)
    else:
        result = RedNoteSourceAdapter().catalog(args.study_id, args.kind,
                                                args.cursor, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
