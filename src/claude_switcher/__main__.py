"""Entry point: ``python -m claude_switcher``."""
from __future__ import annotations

import argparse
import sys

from .app import AccountsApp
from .core import AccountManager, SwitcherError, humanize_age
from .i18n import LANGUAGE_CHOICES, current_choice, current_lang, init, set_language, t
from .warming import format_eta


def _force_utf8_stdout() -> None:
    """Windows console defaults to cp1251 — that breaks non-ASCII output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()

    parser = argparse.ArgumentParser(
        prog="claude-switcher",
        description="Multi-account orchestrator for Claude Code (auth-only, Windows).",
    )
    parser.add_argument(
        "--lang",
        choices=LANGUAGE_CHOICES,
        help="Override interface language for this run (auto/en/ru).",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("ui", help="launch TUI (default)")
    sub.add_parser("list", help="list saved accounts")
    sub.add_parser("status", help="show active account")
    p_switch = sub.add_parser("switch", help="switch to an account")
    p_switch.add_argument("name")
    p_save = sub.add_parser("save", help="save current auth as an account")
    p_save.add_argument("name")
    p_warm = sub.add_parser(
        "warm",
        help="ping Haiku 4.5 with one or more accounts and show limits",
    )
    p_warm.add_argument(
        "names",
        nargs="*",
        help="account names to warm (defaults to every saved account)",
    )
    p_lang = sub.add_parser("lang", help="get or set persistent language (auto|en|ru)")
    p_lang.add_argument("choice", nargs="?", choices=LANGUAGE_CHOICES)

    args = parser.parse_args(argv)

    # Initialise i18n using the manager's backup_dir as the settings location.
    mgr = AccountManager()
    settings_path = mgr.backup_dir / ".settings.json"
    init(settings_path)
    if args.lang:
        set_language(args.lang, persist=False)

    if args.cmd in (None, "ui"):
        AccountsApp(mgr).run()
        return 0

    if args.cmd == "list":
        accounts = mgr.list_accounts()
        if not accounts:
            print(t("cli.no_accounts"))
            return 0
        for a in accounts:
            mark = "*" if a.is_current else " "
            email = a.email or "—"
            print(f"{mark} {a.name:<20} {email:<32} {humanize_age(a.saved_at)}")
        return 0

    if args.cmd == "status":
        cur = mgr.current_account_name()
        print(cur or t("cli.unknown"))
        return 0

    if args.cmd == "switch":
        try:
            switched, prev = mgr.switch_account(args.name)
        except SwitcherError as e:
            print(f"{t('cli.error_prefix')}{e}", file=sys.stderr)
            return 1
        if not switched:
            print(t("cli.already_active", name=args.name))
        else:
            print(t("cli.switched", prev=prev or t("cli.unknown"), name=args.name))
        return 0

    if args.cmd == "save":
        try:
            mgr.save_account(args.name)
        except SwitcherError as e:
            print(f"{t('cli.error_prefix')}{e}", file=sys.stderr)
            return 1
        print(t("cli.saved", name=args.name))
        return 0

    if args.cmd == "warm":
        names = args.names or [a.name for a in mgr.list_accounts() if a.has_credentials]
        if not names:
            print(t("cli.no_accounts"))
            return 0
        exit_code = 0
        for name in names:
            try:
                snap = mgr.warm_account(name)
            except SwitcherError as e:
                print(f"{name:<20} {t('cli.error_prefix')}{e}", file=sys.stderr)
                exit_code = 1
                continue
            if not snap.ok:
                print(f"{name:<20} FAIL  {snap.error}")
                exit_code = 1
                continue
            parts = []
            if snap.five_hour and snap.five_hour.used_pct is not None:
                parts.append(
                    f"5h {snap.five_hour.used_pct}%/{format_eta(snap.five_hour.reset_at)}"
                )
            if snap.weekly and snap.weekly.used_pct is not None:
                parts.append(
                    f"week {snap.weekly.used_pct}%/{format_eta(snap.weekly.reset_at)}"
                )
            if snap.weekly_opus and snap.weekly_opus.used_pct is not None:
                parts.append(
                    f"opus {snap.weekly_opus.used_pct}%/{format_eta(snap.weekly_opus.reset_at)}"
                )
            print(f"{name:<20} OK    {' · '.join(parts) or '(no rate-limit headers)'}")
        return exit_code

    if args.cmd == "lang":
        if args.choice is None:
            print(f"choice={current_choice()}  resolved={current_lang()}")
            return 0
        resolved = set_language(args.choice, persist=True)
        print(f"choice={args.choice}  resolved={resolved}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
