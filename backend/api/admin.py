"""
Manage who may sign in to a hosted Doosra, from a shell on the server.

    python -m api.admin invite alice@example.com
    python -m api.admin revoke alice@example.com
    python -m api.admin list

It uses the same workspace database as the app (DATABASE_URL, or the SQLite
file beside the cricket database).
"""

from __future__ import annotations

import argparse
import re
import sys

from . import workspace

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.admin", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("invite", "revoke"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        invites = workspace.list_invites()
        for i in invites:
            print(f"{i['email']:40} {'signed in' if i['accepted'] else 'invited'}  {i['created'][:10]}")
        if not invites:
            print("No invites.")
        return 0
    if not EMAIL.match(args.email):
        print(f"'{args.email}' doesn't look like an email address.", file=sys.stderr)
        return 2
    if args.cmd == "invite":
        workspace.add_invite(args.email, "cli")
        print(f"Invited {args.email.strip().lower()}.")
        return 0
    if not workspace.remove_invite(args.email):
        print(f"{args.email} has no invite.", file=sys.stderr)
        return 1
    print(f"Revoked {args.email.strip().lower()}; their session stops working immediately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
