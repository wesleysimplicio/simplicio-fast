import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .snapshot import Snapshot, build_snapshot
from .users.http import serve
from .users.repository import JsonUserRepository
from .users.service import UserService


def main() -> None:
    parser = argparse.ArgumentParser(prog="simplicio-fast")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("root", nargs="?", default=".")
    build.add_argument("-o", "--output", default=".simplicio-fast/project.sfast")
    query = commands.add_parser("query")
    query.add_argument("term")
    query.add_argument("-s", "--snapshot", default=".simplicio-fast/project.sfast")
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    if args.command == "build":
        print(json.dumps(asdict(build_snapshot(Path(args.root), Path(args.output))), indent=2))
    elif args.command == "query":
        with Snapshot(Path(args.snapshot)) as snapshot:
            print(json.dumps([asdict(item) for item in snapshot.find(args.term)], indent=2))
    elif args.command == "serve":
        service = UserService(JsonUserRepository(Path("data/users.json")))
        print(f"simplicio-fast listening on http://127.0.0.1:{args.port}")
        serve(service, port=args.port)


if __name__ == "__main__":
    main()
