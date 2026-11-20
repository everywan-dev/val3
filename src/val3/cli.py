"""Command-line interface: parses arguments and maps errors to exit codes."""
import argparse
import getpass
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import psycopg

from val3 import (Val3Error, __version__, catalogue, hooks, importer, inbox, indexes, init, mcp, mirror,
                  neighbour, pending, status, tty, usage, view)
from val3.backup import backup
from val3.candidate import EVIDENCE_FORMAT, FORMAT, TOOL_TYPES, WITHDRAWAL_EVIDENCE_FORMAT, evidence_items
from val3.config import (CONFIG_FILE, Project, check_vault_name, create_private, find_config, load_project,
                         parse_ref)
from val3.db import connect, create_vault, grant, marks, upgrade_vault, upgraded
from val3.embedding import EmbeddingError
from val3.evaluate import draft, evaluate, load_cases
from val3.extract import ExtractorError, extract, summary
from val3.recheck import own_marks
from val3.search import RECALL_TIMEOUT, find
from val3.views import _one_line, pending_reviews, review_card, review_history, safe, show_entry
from val3.writer import (PERSONS, RETIRED, Withdrawal, add_tool, admission_vectors, after_admission, confirm, decide,
                         describe, index_lines, merge_index, propose_withdrawal, settle, store_vectors, submit,
                         submit_notes, warning, withdraw, withdraws_at_once)


VAULT_HELP = "an own vault of the project: home or specialized (default: home)"
READ_SERVICE_HELP = "read another database: a pg_service.conf entry (default: the project's)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="val3", description="Verified session memory for coding agents.")
    parser.add_argument("--version", action="version", version=f"val3 {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    vault = sub.add_parser("vault", help="create and administer vaults")
    vault_sub = vault.add_subparsers(dest="vault_command", metavar="ACTION", required=True)
    create = vault_sub.add_parser("create", help="create a vault schema in the val3 database")
    create.add_argument("name")
    create.add_argument("--description", help="what the vault holds: one line, at most 300 characters")
    create.add_argument("--parent", help="the vault this one specializes")
    create.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    create.add_argument("--specialized", action="store_true",
                        help="a specialized vault of this project: parent the home vault, added to val3.toml")
    create.set_defaults(func=cmd_vault_create)
    describe_parser = vault_sub.add_parser("describe", help="record a vault's description and parent")
    describe_parser.add_argument("name")
    describe_parser.add_argument("--description", required=True,
                                 help="what the vault holds: one line, at most 300 characters")
    describe_parser.add_argument("--parent", help="the vault this one specializes")
    describe_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    describe_parser.set_defaults(func=cmd_vault_describe)
    list_parser = vault_sub.add_parser("list", help="list the vaults of the database and this role's access")
    list_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    list_parser.set_defaults(func=cmd_vault_list)
    grant_parser = vault_sub.add_parser("grant", help="grant a database role read or write access to a vault")
    grant_parser.add_argument("name")
    grant_parser.add_argument("role")
    mode = grant_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read", action="store_true", help="read only (linked vault)")
    mode.add_argument("--write", action="store_true", help="read and append (home vault)")
    grant_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    grant_parser.set_defaults(func=cmd_vault_grant)
    upgrade = vault_sub.add_parser("upgrade", help="bring a vault to the current schema")
    upgrade.add_argument("name")
    upgrade.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    upgrade.add_argument("--dry-run", action="store_true", help="list every change and change nothing")
    upgrade.set_defaults(func=cmd_vault_upgrade)
    dump_parser = vault_sub.add_parser("dump", help="write a vault to a dump for `val3 vault mirror` elsewhere")
    dump_parser.add_argument("name")
    dump_parser.add_argument("--file", required=True, help="a new file, created private; - for stdout")
    dump_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    dump_parser.set_defaults(func=cmd_vault_dump)
    mirror_parser = vault_sub.add_parser("mirror", help="load or refresh a read-only mirror of another database's "
                                         "vault")
    mirror_parser.add_argument("name")
    origin = mirror_parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--from-file", help="a dump from `val3 vault dump`; - for stdin")
    origin.add_argument("--from-service", help="dump the vault through this pg_service.conf entry")
    mirror_parser.add_argument("--service", help="the database the mirror is in: pg_service.conf entry "
                               "(default: the project's)")
    mirror_parser.set_defaults(func=cmd_vault_mirror)

    save = sub.add_parser("save", help="submit a candidate entry (JSON) to the home vault, or a specialized vault "
                          "with --vault",
                          epilog="candidate format:\n" + FORMAT,
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    save.add_argument("file", nargs="?", default="-", help="candidate JSON file (default: stdin)")
    save.add_argument("--transcript", type=Path, help="session transcript (default: $VAL3_TRANSCRIPT)")
    save.add_argument("--session", help="session id (default: $VAL3_SESSION_ID)")
    save.add_argument("--vault", help=VAULT_HELP)
    save.set_defaults(func=cmd_save)

    confirm_parser = sub.add_parser("confirm", help="record that a live entry still holds, with fresh evidence",
                                    epilog="evidence format:\n" + EVIDENCE_FORMAT,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    confirm_parser.add_argument("ref", help="<vault>#<id> of an own vault")
    confirm_parser.add_argument("file", nargs="?", default="-", help="evidence JSON list file (default: stdin)")
    confirm_parser.add_argument("--transcript", type=Path, help="session transcript (default: $VAL3_TRANSCRIPT)")
    confirm_parser.add_argument("--session", help="session id (default: $VAL3_SESSION_ID)")
    confirm_parser.set_defaults(func=cmd_confirm)

    show = sub.add_parser("show", help="print an entry with its evidence")
    show.add_argument("ref", help="<vault>#<id> of any vault this role can read")
    show.add_argument("--service", help=READ_SERVICE_HELP)
    show.set_defaults(func=cmd_show)

    review = sub.add_parser("review", help="list and decide the review list of an own vault (history only on a vault "
                            "upgraded for ADR 0012)")
    review.add_argument("--tty", action="store_true", help="mark and decide the pending items in a full-screen list")
    review.add_argument("--vault", help=VAULT_HELP)
    review_sub = review.add_subparsers(dest="review_command", metavar="ACTION")
    review_show = review_sub.add_parser("show", help="print a review item's candidate")
    review_show.add_argument("id", type=int)
    review_show.add_argument("--vault", default=argparse.SUPPRESS, help=VAULT_HELP)
    for action in ("approve", "reject"):
        decide_parser = review_sub.add_parser(action, help=f"{action} a review item")
        decide_parser.add_argument("id", type=int)
        decide_parser.add_argument("--by", default=None, help="who decides (default: the current user)")
        decide_parser.add_argument("--vault", default=argparse.SUPPRESS, help=VAULT_HELP)
    review.set_defaults(func=cmd_review)

    withdraw_parser = sub.add_parser("withdraw", help="take an entry or an import batch out of delivery for good",
                                     epilog="evidence format:\n" + WITHDRAWAL_EVIDENCE_FORMAT,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    target = withdraw_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("ref", nargs="?", help="<vault>#<id> of an own vault")
    target.add_argument("--batch", help="an import batch: every entry it admitted")
    cause = withdraw_parser.add_mutually_exclusive_group(required=True)
    cause.add_argument("--wrong", dest="cause", action="store_const", const="wrong",
                       help="the knowledge was never true")
    cause.add_argument("--obsolete", dest="cause", action="store_const", const="obsolete",
                       help="it was true and is no longer needed")
    withdraw_parser.add_argument("--reason", required=True, help="why; shown wherever the entry is shown")
    withdraw_parser.add_argument("--evidence", dest="file", metavar="FILE|-",
                                 help="evidence JSON list file, or - for stdin (not with --batch)")
    withdraw_parser.add_argument("--transcript", type=Path, help="session transcript (default: $VAL3_TRANSCRIPT)")
    withdraw_parser.set_defaults(func=cmd_withdraw, session=None)  # the session id comes from $VAL3_SESSION_ID

    settle_parser = sub.add_parser("settle", help="settle an entry marked \"to settle\" and its close neighbour",
                                   usage="val3 settle REF NEAR (--replace | --drop | --keep)")
    settle_parser.add_argument("ref", help="<vault>#<id> of the entry marked to settle (the newer one)")
    settle_parser.add_argument("near", help="<vault>#<id> of its neighbour (the older one)")
    outcome = settle_parser.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--replace", dest="outcome", action="store_const", const="replace",
                         help="the new entry replaces its neighbour: it supersedes it")
    outcome.add_argument("--drop", dest="outcome", action="store_const", const="drop",
                         help="the new entry repeats it: withdraw the new one as a duplicate")
    outcome.add_argument("--keep", dest="outcome", action="store_const", const="keep",
                         help="both hold: record the pair as distinct, never marked again")
    settle_parser.set_defaults(func=cmd_settle)

    tool = sub.add_parser("tool", help="register external tools")
    tool_sub = tool.add_subparsers(dest="tool_command", metavar="ACTION", required=True)
    add = tool_sub.add_parser("add", help="register a skill, MCP server or CLI that answers derivable questions")
    add.add_argument("name")
    add.add_argument("--type", required=True, choices=TOOL_TYPES)
    add.add_argument("--description", required=True)
    add.add_argument("--question", action="append", default=[], help="a question the tool answers (repeatable)")
    add.add_argument("--environment", default="", help="where it runs; repository URL to clone")
    add.add_argument("--priority", type=int, default=0)
    add.add_argument("--index", action="append", default=[], help="knowledge index (repeatable)")
    add.add_argument("--vault", help=VAULT_HELP)
    add.set_defaults(func=cmd_tool_add)

    recall = sub.add_parser("recall", help="search the project's vaults")
    recall.add_argument("terms", nargs="+")
    recall.add_argument("--limit", type=int, default=10)
    recall.add_argument("--vault", action="append", default=[],
                        help="search this vault instead of the project's (repeatable)")
    recall.add_argument("--service", help=READ_SERVICE_HELP + "; outside a project it needs --vault")
    recall.set_defaults(func=cmd_recall)

    index_parser = sub.add_parser("index", help="list knowledge indexes and their entries, or merge two indexes",
                                  usage="val3 index [NAME | --none | merge FROM INTO] [--vault V] [--service S]")
    index_parser.add_argument("words", nargs="*", help="NAME to list its entries; merge FROM INTO")
    index_parser.add_argument("--none", action="store_true", help="list the entries with no section index")
    index_parser.add_argument("--vault", help="listing: the vault to list instead of the project's; merge: "
                              + VAULT_HELP)
    index_parser.add_argument("--service", help="listing only: " + READ_SERVICE_HELP + "; outside a project it "
                              "needs --vault")
    index_parser.set_defaults(func=cmd_index)

    embed_parser = sub.add_parser("embed", help="store vectors for the entries of an own vault that have none")
    embed_parser.add_argument("--vault", help=VAULT_HELP)
    embed_parser.set_defaults(func=cmd_embed)

    eval_parser = sub.add_parser("eval", help="measure the semantic floor against a labelled set")
    eval_parser.add_argument("file", type=Path, help="labelled set (JSON lines), or a prompt log with --draft")
    eval_parser.add_argument("--draft", action="store_true", help="print an unlabelled set from a prompt log")
    eval_parser.set_defaults(func=cmd_eval)

    backup_parser = sub.add_parser("backup", help="dump the whole val3 database into a private file (pg_dump)")
    backup_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    backup_parser.add_argument("--out", type=Path,
                               help="output file (default: ~/.local/share/val3/backup/<database>-<time>.dump)")
    backup_parser.set_defaults(func=lambda args: print(backup(_service(args), args.out)))

    import_parser = sub.add_parser("import", help="import the knowledge of a validated-memory 2.x adopter")
    import_mode = import_parser.add_mutually_exclusive_group(required=True)
    import_mode.add_argument("--dry-run", action="store_true",
                             help="extract and check quotes; write a manifest, no database writes")
    import_mode.add_argument("--apply", type=Path, metavar="FILE",
                             help="admit a manifest's candidates whose quotes all verified; queue the rest for review")
    import_parser.add_argument("--out", type=Path,
                               help="manifest file (default: ~/.local/share/val3/import/<vault>-<time>-<batch>.json)")
    import_parser.set_defaults(func=cmd_import)

    extract_parser = sub.add_parser("extract", help="extract verified entries from a session transcript now")
    extract_parser.add_argument("transcript", type=Path)
    extract_parser.add_argument("--session", help="session id (default: the transcript file name)")
    extract_parser.set_defaults(func=cmd_extract)

    drain_parser = sub.add_parser("drain", help="extract every queued session now")
    drain_parser.add_argument("--if-idle", action="store_true", help="exit at once if another drainer runs")
    drain_parser.set_defaults(func=cmd_drain)

    queue_parser = sub.add_parser("queue", help="list queued and failed captures")
    queue_parser.set_defaults(func=lambda args: print(pending.describe()))

    init_parser = sub.add_parser("init", help="set this project up (val3.toml, hooks, CLAUDE.md) and check it")
    init_parser.add_argument("--service", help="pg_service.conf entry (required without a val3.toml)")
    init_parser.add_argument("--vault", help="home vault (required without a val3.toml)")
    init_parser.add_argument("--allow-save", action="store_true",
                             help="let the agent call memory_save without asking (never removed by a later run)")
    init_parser.add_argument("--linked", action="append", default=[], help="a linked vault (repeatable)")
    init_parser.add_argument("--project", help="project name (default: the directory name)")
    init_parser.set_defaults(func=cmd_init)

    status_parser = sub.add_parser("status", help="show whether VAL3 is active here and what it delivered lately")
    status_parser.add_argument("--last", type=int, default=5, help="logged prompts to show (default: 5)")
    status_parser.set_defaults(func=cmd_status)

    hook = sub.add_parser("hook", help="entry points for harness hooks (event JSON on stdin)")
    hook.add_argument("event", choices=("session-start", "prompt", "capture"))
    hook.set_defaults(func=lambda args: hooks.run(args.event, sys.stdin))

    view_parser = sub.add_parser("view", help="serve a read-only web view of the project's vaults on 127.0.0.1")
    view_parser.add_argument("--port", type=_port, default=0, help="the port to listen on (default: any free one)")
    view_parser.add_argument("--no-open", action="store_true", help="print the address, do not open a browser")
    view_parser.set_defaults(func=lambda args: view.serve(load_project(Path.cwd()), args.port, not args.no_open))

    mcp_parser = sub.add_parser("mcp", help="serve the memory tools to an MCP client over stdio")
    mcp_parser.add_argument("--call", action="store_true", help=argparse.SUPPRESS)  # one tool call, run by the server
    mcp_parser.set_defaults(func=lambda args: mcp.call_main(sys.stdin, sys.stdout) if args.call
                            else mcp.serve(sys.stdin.buffer, sys.stdout.buffer, Path.cwd()))
    return parser


def _port(text: str) -> int:
    if not text.isdigit() or int(text) > 65535:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}")
    return int(text)


def _target(args):
    """The project as seen from the vault `--vault` names, its home vault by default."""
    project = load_project(Path.cwd())
    return project.writing(args.vault or project.home)


def _service(args) -> str:
    return args.service or load_project(Path.cwd()).service


NO_NOTES = "this database has no vault notes yet: its owner runs val3 vault upgrade <vault>"


def cmd_vault_create(args) -> int | None:
    if args.parent is not None and args.description is None:
        print("val3: error: --parent goes with --description", file=sys.stderr)
        return 2
    project = None
    if args.specialized:
        if args.description is None:
            print("val3: error: --specialized needs --description", file=sys.stderr)
            return 2
        try:
            project = load_project(Path.cwd())
        except Val3Error as exc:
            print(f"val3: error: --specialized runs inside a project: {safe(str(exc))}", file=sys.stderr)
            return 2
        if args.parent not in (None, project.home):
            print("val3: error: --specialized sets the parent to the home vault", file=sys.stderr)
            return 2
        if args.name in project.vaults:
            print(f"val3: error: {args.name} is already declared in {CONFIG_FILE}", file=sys.stderr)
            return 2
        args.parent = project.home
    check_vault_name(args.name)
    with connect(_service(args)) as conn, conn.transaction():
        if (args.description is not None and not catalogue.has_notes(conn)
                and conn.execute("SELECT to_regnamespace('val3_common')").fetchone()[0] is not None):
            raise Val3Error(NO_NOTES)  # a database without val3_common gets the notes with it
        create_vault(conn, args.name)
        if args.description is not None:
            describe(conn, args.name, args.description, args.parent)
        if project is not None and args.service:
            with connect(project.service) as own:
                role = own.execute("SELECT current_user").fetchone()[0]
            if role != conn.execute("SELECT current_user").fetchone()[0]:
                grant(conn, args.name, role, write=True)
    print(f"created vault {args.name}")
    if project is not None:
        line = f"specialized = [{', '.join(json.dumps(v, ensure_ascii=False) for v in (*project.specialized, args.name))}]"
        try:
            edited = init.add_specialized(project.root, args.name) is None
        except Val3Error:
            edited = False
        if edited:
            names = ", ".join(json.dumps(v, ensure_ascii=False) for v in load_project(project.root).specialized)
            print(f"{CONFIG_FILE}: specialized = [{names}]")
        else:
            print(f"val3: warning: {CONFIG_FILE} not changed; add under [vaults]: {line}", file=sys.stderr)
    return None


def cmd_vault_describe(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        if not catalogue.has_notes(conn):
            raise Val3Error(NO_NOTES)
        if catalogue.is_mirror(conn, args.name):
            raise Val3Error(f"vault {args.name} is a mirror and read only")
        if not catalogue.writable(conn, args.name):
            role = conn.execute("SELECT current_user").fetchone()[0]
            raise Val3Error(f"cannot describe vault {args.name}: describing a vault needs write on it "
                            f"(val3 vault grant {args.name} {role} --write)")
        describe(conn, args.name, args.description, args.parent)
    print(f"described vault {args.name}")


def cmd_vault_list(args) -> None:
    with connect(_service(args)) as conn:
        rows = catalogue.vaults(conn)
    if not rows:
        print("no vaults")
    for row in rows:  # names, descriptions and sources are written by other roles: one escaped line each
        line = f"{row.name}  {row.privilege}  " + ("-" if row.entries is None else f"{row.entries} entries")
        if row.parent:
            line += f"  parent {row.parent}"
        if row.mirror_source:
            line += f"  mirror of {row.mirror_source}, synced {row.synced_at.isoformat(timespec='seconds')}"
        print(safe(_one_line(line)))
        if row.description:
            print("    " + safe(_one_line(f"{row.description} (by {row.written_by})")))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_usage(sys.stderr)
        return 2
    try:
        return args.func(args) or 0
    except init.UsageError as exc:
        print(f"val3: error: {safe(str(exc))}", file=sys.stderr)
        return 2
    except Val3Error as exc:
        print(f"val3: error: {safe(str(exc))}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"val3: error: database: {str(exc).strip()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("val3: interrupted", file=sys.stderr)
        return 1


def cmd_vault_grant(args) -> None:
    with connect(_service(args)) as conn:
        if args.write and catalogue.is_mirror(conn, args.name):
            raise Val3Error(f"vault {args.name} is a mirror and read only")
        grant(conn, args.name, args.role, args.write)
    print(f"granted {'write' if args.write else 'read'} on {args.name} to {args.role}")


def cmd_vault_upgrade(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        if catalogue.is_mirror(conn, args.name):  # its rows are the source's: a refresh brings its schema
            raise Val3Error(f"vault {args.name} is a mirror: refresh it from its source with `val3 vault mirror`")
        schema, switch = upgrade_vault(conn, args.name, args.dry_run)
    for line in (schema + switch if args.dry_run else switch):
        print(line)
    if args.dry_run:
        print(f"dry run: vault {args.name} not changed" if schema or switch else f"vault {args.name} is up to date")
    else:
        print(f"upgraded vault {args.name}" if schema or switch else f"vault {args.name} is up to date")


def cmd_vault_dump(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        if args.file == "-":
            counts = mirror.dump(conn, args.name, sys.stdout.buffer)
            sys.stdout.buffer.flush()
        else:
            path = Path(args.file)
            fd = create_private(path)
            try:
                with os.fdopen(fd, "wb") as out:
                    counts = mirror.dump(conn, args.name, out)
            except BaseException:
                path.unlink(missing_ok=True)  # created above: an incomplete dump is never left behind
                raise
    print(f"dumped vault {args.name}: {counts['entry']} entries", file=sys.stderr)


def cmd_vault_mirror(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        # ponytail: read before the load's lock, so a concurrent refresh can skew "new"; display only
        before = (catalogue.last_sync(conn, args.name) or ({},))[0].get("entry", 0)
        if args.from_service:
            source = args.from_service
            with tempfile.TemporaryFile() as stream:
                with connect(args.from_service) as remote:
                    mirror.dump(remote, args.name, stream)
                stream.seek(0)
                counts = mirror.load(conn, args.name, source, stream)
        elif args.from_file == "-":
            source = "stdin"
            counts = mirror.load(conn, args.name, source, sys.stdin.buffer)
        else:
            source = args.from_file
            try:
                stream = open(source, "rb")
            except OSError as exc:
                raise Val3Error(f"cannot read {source}: {exc.strerror}") from None
            with stream:
                counts = mirror.load(conn, args.name, source, stream)
    print(safe(f"mirrored vault {args.name} from {source}: {counts['entry']} entries "
               f"({counts['entry'] - before} new)"))


def _warn(message: str | None) -> None:
    if message:
        print(warning(message), file=sys.stderr)


def cmd_embed(args) -> None:
    project = _target(args)
    with connect(project.service) as conn:
        print(f"embedded {store_vectors(conn, project)} entries")


def cmd_eval(args) -> None:
    if args.draft:
        text = draft(args.file)
        if text:
            print(text)
        return
    project = load_project(Path.cwd())
    cases = load_cases(args.file, project.vaults)
    with connect(project.service) as conn:
        print(evaluate(conn, project, cases))


def _json_input(args, what: str) -> object:
    try:
        text = (sys.stdin.buffer.read().decode("utf-8") if args.file == "-"  # strict, whatever the locale
                else Path(args.file).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Val3Error(f"cannot read {args.file}: {exc.strerror}") from None
    except UnicodeDecodeError:
        raise Val3Error(f"{'standard input' if args.file == '-' else args.file} is not valid UTF-8") from None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise Val3Error(f"{what} is not valid JSON: {exc}") from None


def _transcript_and_session(args) -> tuple[Path | None, str | None]:
    transcript = args.transcript or (Path(os.environ["VAL3_TRANSCRIPT"]) if os.environ.get("VAL3_TRANSCRIPT") else None)
    return transcript, args.session or os.environ.get("VAL3_SESSION_ID")


def cmd_save(args) -> None:
    project = _target(args)
    data = _json_input(args, "candidate")
    if isinstance(data, dict) and "vault" in data:  # only capture routes on it, and strips it first
        raise Val3Error('"vault" is not a candidate key: use val3 save --vault <name>')
    transcript, session = _transcript_and_session(args)
    with connect(project.service) as conn:
        outcome = submit(conn, project, data, transcript, session, (str(inbox.memory_dir(Path.cwd())),), via="save")
        print(outcome, flush=True)
        for line in submit_notes(outcome):
            print(line, file=sys.stderr, flush=True)
        for line in after_admission(conn, project, outcome):
            print(line, file=sys.stderr)


def _new_indexes(outcome) -> None:
    for line in index_lines(outcome):
        print(line, file=sys.stderr)


def _reading(args, vaults: list[str]) -> tuple[Project | None, Project]:
    """Design §4.3: (the project whose own vaults get marks, None with --service; the project a read command
    reads). `vaults` (--vault) replace the project's vaults; --service reads another database."""
    vaults = list(dict.fromkeys(check_vault_name(vault) for vault in vaults))
    if args.service and find_config(Path.cwd()) is None:
        if not vaults:
            raise init.UsageError("--service outside a project needs --vault")
        here, project = None, Project(Path.cwd(), "-", args.service, vaults[0], ())
    else:
        here = project = load_project(Path.cwd())
    if args.service:
        here, project = None, replace(project, service=args.service)
    if vaults:
        project = replace(project, home=vaults[0], specialized=(), linked=tuple(vaults[1:]))
    return here, project


def _readable(conn, vaults) -> None:
    for vault in vaults:
        if not catalogue.readable(conn, vault):
            role = conn.execute("SELECT current_user").fetchone()[0]
            raise Val3Error(f"cannot read vault {vault} (val3 vault grant {vault} {role} --read)")


def cmd_show(args) -> None:
    vault, entry_id = parse_ref(args.ref)
    here = None if args.service else load_project(Path.cwd())
    shown = False
    try:
        with connect(args.service or here.service) as conn:
            _readable(conn, [vault])
            own = here is not None and vault in here.own
            head, changes = own_marks(conn, here, {vault: [entry_id]}) if own else (None, {})
            near = neighbour.open_pairs(conn, vault, [entry_id], here).get(entry_id, []) if own else []
            print(show_entry(conn, vault, entry_id, changes.get((vault, entry_id), ()), head, near))
            shown = True
    finally:
        if here is not None:  # with --service another database is read: not this project's usage
            usage.record(here.name, "show", [vault], [f"{vault}#{entry_id}"] if shown else [],
                         "ok" if shown else "error")


def cmd_settle(args) -> None:
    project = load_project(Path.cwd()).writing(parse_ref(args.ref)[0])
    session = os.environ.get("VAL3_SESSION_ID")
    by = f"agent {session or '-'}" if os.environ.get("CLAUDECODE") else getpass.getuser()
    with connect(project.service) as conn:
        print(settle(conn, project, args.ref, args.near, args.outcome, by, session))


def cmd_confirm(args) -> None:
    project = load_project(Path.cwd())
    vault, entry_id = parse_ref(args.ref)
    project = project.writing(vault)
    data = _json_input(args, "evidence")
    transcript, session = _transcript_and_session(args)
    with connect(project.service) as conn:
        print(confirm(conn, project, f"{vault}#{entry_id}", data, transcript, session,
                      (str(inbox.memory_dir(Path.cwd())),)))


def _person_only() -> None:
    if os.environ.get("CLAUDECODE"):
        raise Val3Error("this decision belongs to a person: run it in a terminal outside the agent session")


def cmd_review(args) -> int | None:
    if args.tty and args.review_command:
        print("val3: error: --tty takes no action", file=sys.stderr)
        return 2
    project = _target(args)
    with connect(project.service) as conn:
        if upgraded(conn, project.home):  # ADR 0012 §7: history only
            if args.tty or args.review_command in ("approve", "reject"):
                raise Val3Error(f"{RETIRED}: `val3 review` lists the history of vault {project.home}")
            print(review_card(conn, project.home, args.id) if args.review_command == "show"
                  else review_history(conn, project.home))
            return None
    if args.tty or args.review_command in ("approve", "reject"):
        _person_only()
    if args.tty and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("val3: error: val3 review --tty needs a terminal", file=sys.stderr)
        return 2
    with connect(project.service) as conn:
        if args.tty:
            tty.walk(conn, project, getpass.getuser(), lambda: admission_vectors(conn, project))
        elif args.review_command is None:
            print(pending_reviews(conn, project.home))
        elif args.review_command == "show":
            print(review_card(conn, project.home, args.id))
        else:
            approve = args.review_command == "approve"
            print(decide(conn, project, args.id, approve, args.by or getpass.getuser()))
            if approve:
                _warn(admission_vectors(conn, project))
    return None


def cmd_withdraw(args) -> int | None:
    reason = " ".join(args.reason.split())  # one line: it is shown inside `show` and on cards
    if not reason:
        print("val3: error: --reason must not be blank", file=sys.stderr)
        return 2
    if args.batch is not None and not args.batch.strip():
        print("val3: error: --batch must not be blank", file=sys.stderr)
        return 2
    if args.batch and args.file:
        print("val3: error: --batch takes no --evidence", file=sys.stderr)
        return 2
    project = load_project(Path.cwd())
    ref = None
    if args.ref:
        vault, entry_id = parse_ref(args.ref)
        project = project.writing(vault)
        ref = f"{vault}#{entry_id}"  # canonical: the text as typed may carry control characters
    evidence = evidence_items(_json_input(args, "evidence")) if args.file else None
    transcript, session = _transcript_and_session(args)
    private = (str(inbox.memory_dir(Path.cwd())),)
    with connect(project.service) as conn:
        w = Withdrawal(ref, args.batch, args.cause, reason, evidence)
        agent = bool(os.environ.get("CLAUDECODE"))
        if upgraded(conn, project.home):  # ADR 0012 §6: nothing is proposed
            if not withdraws_at_once(w) and (agent or not sys.stdin.isatty()):
                raise Val3Error(PERSONS)
            by = f"agent {session or '-'}" if agent else getpass.getuser()
            print(withdraw(conn, project, w, by, transcript, session, private))
        elif not agent:
            print(withdraw(conn, project, w, getpass.getuser(), transcript, session, private))
        elif withdraws_at_once(w):  # design L6, N3
            print(withdraw(conn, project, w, f"agent {session or '-'}", transcript, session, private))
        else:  # the agent proposes; a person approves (design W2)
            print(propose_withdrawal(conn, project, w, session, transcript, private))
    return None


def cmd_import(args) -> int | None:
    if args.apply and args.out:
        print("val3: error: --out goes with --dry-run", file=sys.stderr)
        return 2
    project = load_project(Path.cwd())
    if args.dry_run:
        print(importer.dry_run(project, args.out))
        return None
    _person_only()
    with connect(project.service) as conn:
        print(importer.apply(conn, project, args.apply, getpass.getuser()))
        if project.embedding is not None:  # the admissions stand; `val3 embed` and the drainer store the rest
            try:
                store_vectors(conn, project)
            except (EmbeddingError, psycopg.Error) as exc:
                _warn(f"no vector stored ({exc}); run `val3 embed` later")
    return None


def cmd_tool_add(args) -> None:
    _person_only()
    project = _target(args)
    with connect(project.service) as conn:
        outcome = add_tool(conn, project, args.name, args.type, args.description, args.question,
                           args.environment, args.priority, args.index, getpass.getuser())
        print(outcome)
        _new_indexes(outcome)
        if outcome.status == "admitted":
            _warn(admission_vectors(conn, project))


def cmd_index(args) -> int | None:
    words = args.words
    if (args.none and words) or not (len(words) <= 1 or (len(words) == 3 and words[0] == "merge")):
        print("val3: error: usage: val3 index [NAME | --none | merge FROM INTO] [--vault V] [--service S]", file=sys.stderr)
        return 2
    if len(words) == 3:
        if args.service:
            print("val3: error: --service goes with listing: merge writes the project's database", file=sys.stderr)
            return 2
        project = load_project(Path.cwd())
        with connect(project.service) as conn:
            print(safe(merge_index(conn, project.writing(args.vault or project.home), words[1], words[2],
                                   os.environ.get("VAL3_SESSION_ID"))))
        return None
    here, project = _reading(args, [args.vault] if args.vault else [])
    with connect(project.service) as conn:
        if args.vault or args.service:
            _readable(conn, project.vaults)
        if words or args.none:
            print(indexes.members(conn, project, words[0] if words else None, here))
        else:
            print(indexes.listing(conn, project))
    return None


def cmd_recall(args) -> None:
    here, project = _reading(args, args.vault)
    found = None
    try:
        with connect(project.service) as conn:
            if args.vault or args.service:
                _readable(conn, project.vaults)
            found = find(conn, project, " ".join(args.terms), args.limit, timeout=RECALL_TIMEOUT, words=True)
            backing = {(vault, entry_id): mark for vault in project.vaults for entry_id, mark in
                       marks(conn, vault, [hit.id for hit in found.hits if hit.vault == vault]).items()}
    finally:
        if here is not None:  # with --service another database is read: not this project's usage, as for show
            usage.record(project.name, "recall", project.vaults, [hit.ref for hit in found.hits] if found else [],
                         "error" if found is None else "ok" if found.hits else "empty", limit=args.limit,
                         query=" ".join(args.terms))
    if found.warning:
        print(f"val3: warning: {found.warning}", file=sys.stderr)
    if not found.hits:
        print("no matches")
    for hit in found.hits:
        mark = backing.get((hit.vault, hit.id))
        print(f"{hit.ref}  [{hit.kind}]  {safe(hit.title)}" + (f"  {mark[1:]}" if mark else ""))
        print("    " + safe(" ".join(hit.statement.split())[:200]))


def cmd_extract(args) -> None:
    project = load_project(Path.cwd())
    with pending.drain_lock(), connect(project.service) as conn:
        try:
            counts = extract(conn, project, args.transcript, args.session or args.transcript.stem, Path.cwd())
        except ExtractorError as exc:
            raise Val3Error(str(exc)) from None
    print(f"{args.transcript.name}: {summary(counts)}")


def cmd_drain(args) -> None:
    print(f"drained {pending.drain(wait=not args.if_idle)} queued sessions")


def cmd_status(args) -> int:
    text, code = status.report(Path.cwd(), args.last)
    print(text)
    return code


def cmd_init(args) -> int:
    try:
        lines, code = init.run(Path.cwd(), args.service, args.vault, args.linked, args.project, args.allow_save)
    except init.UsageError as exc:
        print(f"val3: error: {safe(str(exc))}", file=sys.stderr)
        return 2
    except Val3Error as exc:
        for line in getattr(exc, "lines", ()):
            print(safe(line))
        raise
    for line in lines:
        print(safe(line))
    return code
