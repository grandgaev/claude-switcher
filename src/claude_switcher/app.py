"""Textual TUI orchestrator for Claude Code accounts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)

from .core import (
    Account,
    AccountManager,
    Snapshot,
    SwitcherError,
    human_size,
    humanize_age,
)
from .i18n import LangChoice, current_choice, set_language, t
from .modals import ConfirmModal, HelpModal, LanguageModal, TextPromptModal
from .warming import LimitWindow, WarmupSnapshot, format_eta


ACCENT = "#d97757"
MUTED = "#888579"
DANGER = "#e76f51"
WARN = "#e0a800"
OK_COLOR = "#7fb069"


def _usage_color(used_pct: int | None) -> str:
    if used_pct is None:
        return MUTED
    if used_pct >= 90:
        return DANGER
    if used_pct >= 60:
        return WARN
    return OK_COLOR


def _cell_for_window(window: LimitWindow | None) -> Text:
    """Render the per-account 5h / weekly cell as a single short string."""
    if window is None:
        return Text("—", style=MUTED)
    used = window.used_pct
    eta = format_eta(window.reset_at)
    if used is None and window.status is None:
        return Text("—", style=MUTED)
    if used is None:
        label = f"{window.status or '—'} · {eta}"
        return Text(label, style=WARN if window.status not in (None, "ok") else MUTED)
    color = _usage_color(used)
    return Text(f"{used}% · {eta}", style=color)


def _format_warmed_cell(snapshot: WarmupSnapshot | None) -> Text:
    if snapshot is None:
        return Text("—", style=MUTED)
    if not snapshot.ok:
        return Text("err", style=DANGER)
    local = _to_local(snapshot.checked_at)
    if local is None:
        return Text("—", style=MUTED)
    return Text(_humanize_since(local), style=MUTED)


def _humanize_since(when_local: datetime) -> str:
    delta = datetime.now(when_local.tzinfo) - when_local
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return when_local.strftime("%H:%M")
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{seconds // 60}м назад"
    if seconds < 86400:
        return f"{seconds // 3600}ч назад"
    days = seconds // 86400
    if days < 30:
        return f"{days}д назад"
    return when_local.strftime("%Y-%m-%d")


def _summary_for_notification(snapshot: WarmupSnapshot) -> str:
    parts: list[str] = []
    if snapshot.five_hour and snapshot.five_hour.used_pct is not None:
        parts.append(
            t(
                "ui.notify.summary.session",
                pct=snapshot.five_hour.used_pct,
                eta=format_eta(snapshot.five_hour.reset_at),
            )
        )
    if snapshot.weekly and snapshot.weekly.used_pct is not None:
        parts.append(
            t(
                "ui.notify.summary.weekly",
                pct=snapshot.weekly.used_pct,
                eta=format_eta(snapshot.weekly.reset_at),
            )
        )
    if not parts:
        return t("ui.notify.summary.empty")
    return " · ".join(parts)


class StatusBar(Static):
    """Top bar showing the active account."""

    current_name: reactive[str | None] = reactive(None)
    live_summary: reactive[str] = reactive("")
    snapshot_count: reactive[int] = reactive(0)

    def render(self) -> Text:
        text = Text()
        text.append(f"{t('ui.status.active')}  ", style=MUTED)
        if self.current_name:
            text.append(self.current_name, style=f"bold {ACCENT}")
            if self.live_summary and self.live_summary != self.current_name:
                text.append(f"  ({self.live_summary})", style=MUTED)
        elif self.live_summary:
            text.append(f"{t('ui.status.not_saved')}  ", style=f"italic {DANGER}")
            text.append(self.live_summary, style="white")
        else:
            text.append(t("ui.status.no_auth"), style=f"italic {DANGER}")
        text.append("    ·  ", style=MUTED)
        text.append(f"{t('ui.status.snapshots')}: ", style=MUTED)
        text.append(str(self.snapshot_count), style=f"bold {ACCENT}")
        return text


class DetailPanel(Static):
    """Right-hand pane: details for the focused account."""

    def __init__(self) -> None:
        super().__init__()
        self._account: Account | None = None
        self._warmup: WarmupSnapshot | None = None

    def show(self, account: Account | None, warmup: WarmupSnapshot | None = None) -> None:
        self._account = account
        self._warmup = warmup
        self.refresh_panel()

    def refresh_panel(self) -> None:
        if self._account is None:
            self.update(Text(t("ui.detail.empty"), style=MUTED))
            return
        a = self._account
        text = Text()
        text.append(f"{t('ui.detail.title')}\n\n", style=f"bold {ACCENT}")
        rows = [
            (t("ui.detail.name"), a.name, "name"),
            (t("ui.detail.email"), a.email or "—", "email"),
            (t("ui.detail.org"), a.organization or "—", "org"),
            (t("ui.detail.uuid"), a.account_uuid or "—", "uuid"),
            (t("ui.detail.userid"), a.user_id or "—", "userid"),
            (t("ui.detail.creds"),
             t("ui.detail.creds_yes") if a.has_credentials else t("ui.detail.creds_no"),
             "creds"),
            (t("ui.detail.saved"),
             a.saved_at.strftime("%Y-%m-%d %H:%M") if a.saved_at.year > 1 else "—",
             "saved"),
            (t("ui.detail.age"), humanize_age(a.saved_at), "age"),
            (t("ui.detail.bundle"),
             f"{human_size(a.bundle_size)} · {a.bundle_path.name}", "bundle"),
            (t("ui.detail.status"),
             t("ui.detail.status_active") if a.is_current else t("ui.detail.status_saved"),
             "status"),
        ]
        highlight_keys = {"name", "email", "status"} if a.is_current else set()
        for label, value, key in rows:
            text.append(f"  {label:<12}", style=MUTED)
            style = f"bold {ACCENT}" if key in highlight_keys else "white"
            text.append(f"{value}\n", style=style)

        text.append(f"\n{t('ui.detail.section.usage')}\n", style=f"bold {ACCENT}")
        self._append_usage(text)
        self.update(text)

    def _append_usage(self, text: Text) -> None:
        snap = self._warmup
        if snap is None:
            text.append(f"  {t('ui.detail.usage.pending')}\n", style=MUTED)
            return
        checked_local = _to_local(snap.checked_at)
        checked_str = checked_local.strftime("%Y-%m-%d %H:%M") if checked_local else t(
            "ui.detail.usage.never"
        )
        text.append(f"  {t('ui.detail.usage.checked'):<14}", style=MUTED)
        text.append(f"{checked_str}\n", style="white")
        if not snap.ok:
            text.append(
                f"  {t('ui.detail.usage.error', error=snap.error or '—')}\n",
                style=DANGER,
            )
            return
        windows = [
            (t("ui.detail.usage.session"), snap.five_hour),
            (t("ui.detail.usage.weekly"), snap.weekly),
            (t("ui.detail.usage.weekly_opus"), snap.weekly_opus),
        ]
        for label, window in windows:
            if window is None:
                continue
            text.append(f"  {label:<14}", style=MUTED)
            text.append(f"{_format_window_line(window)}\n",
                        style=_usage_color(window.used_pct))


def _format_window_line(window: LimitWindow) -> str:
    used = window.used_pct
    eta = format_eta(window.reset_at)
    if used is not None:
        return t("ui.detail.usage.value", used=used, eta=eta)
    if window.status:
        if window.reset_at:
            return t("ui.detail.usage.value_status", status=window.status, eta=eta)
        return t("ui.detail.usage.value_status_only", status=window.status)
    return t("ui.detail.usage.value_unknown")


def _to_local(dt: datetime | None) -> datetime | None:
    if dt is None or dt == datetime.min:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


class SnapshotList(ListView):
    """ListView wrapper that holds Snapshot objects per item."""

    def populate(self, snapshots: Iterable[Snapshot]) -> None:
        self.clear()
        for snap in snapshots:
            label = Text()
            label.append(snap.created.strftime("%m-%d %H:%M:%S"), style=ACCENT)
            label.append(f"  {snap.summary:<28}", style="white")
            label.append(f"  {_short_label(snap.label)}", style=MUTED)
            item = ListItem(Label(label))
            item.snapshot = snap  # type: ignore[attr-defined]
            self.append(item)


def _short_label(snapshot_label: str) -> str:
    if "_" in snapshot_label:
        return snapshot_label.split("_", 1)[1]
    return snapshot_label


# Translation keys for the footer bindings — used to refresh labels after
# a language change.
_BINDING_KEYS: dict[str, str] = {
    "save": "ui.binding.save",
    "rename": "ui.binding.rename",
    "delete": "ui.binding.delete",
    "restore": "ui.binding.snapshot",
    "refresh": "ui.binding.refresh",
    "warm": "ui.binding.warm",
    "warm_all": "ui.binding.warm_all",
    "language": "ui.binding.lang",
    "help": "ui.binding.help",
    "quit": "ui.binding.quit",
}


class AccountsApp(App):
    """Main TUI."""

    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("s", "save", "save"),
        Binding("r", "rename", "rename"),
        Binding("d", "delete", "delete"),
        Binding("b", "restore", "snapshot"),
        Binding("w", "warm", "warm"),
        Binding("W", "warm_all", "warm all"),
        Binding("l", "language", "language"),
        Binding("f5", "refresh", "refresh"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, manager: AccountManager | None = None) -> None:
        super().__init__()
        self.manager = manager or AccountManager()
        self._accounts: list[Account] = []
        self._warmups: dict[str, WarmupSnapshot] = {}
        self.title = t("ui.app.title")
        self.sub_title = t("ui.app.subtitle")

    # ---- composition ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        with Horizontal(id="main"):
            with Vertical(id="left-pane"):
                table = DataTable(id="accounts", zebra_stripes=True)
                table.cursor_type = "row"
                table.add_columns(
                    t("ui.col.marker"),
                    t("ui.col.name"),
                    t("ui.col.email"),
                    t("ui.col.session"),
                    t("ui.col.weekly"),
                    t("ui.col.warmed"),
                    t("ui.col.updated"),
                )
                yield table
            with Vertical(id="right-pane"):
                yield DetailPanel()
                yield Label(t("ui.snapshot_title"), id="snapshot-title")
                yield SnapshotList(id="snapshot-list")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_binding_labels()
        self.refresh_all()

    # ---- data refresh ----

    def refresh_all(self) -> None:
        self._accounts = self.manager.list_accounts()
        self._warmups = self.manager.warmup_snapshots()
        snapshots = self.manager.list_snapshots()
        current = next((a.name for a in self._accounts if a.is_current), None)

        bar = self.query_one(StatusBar)
        bar.current_name = current
        bar.live_summary = self.manager.live_summary()
        bar.snapshot_count = len(snapshots)
        bar.refresh()

        table = self.query_one("#accounts", DataTable)
        cursor_row = table.cursor_row
        table.clear()
        for a in self._accounts:
            warmup = self._warmups.get(a.name)
            marker = Text("●", style=ACCENT) if a.is_current else Text("○", style=MUTED)
            name = Text(a.name, style=f"bold {ACCENT}" if a.is_current else "white")
            email = Text(a.email or "—", style="white" if a.email else MUTED)
            session_cell = _cell_for_window(warmup.five_hour if warmup and warmup.ok else None)
            weekly_cell = _cell_for_window(warmup.weekly if warmup and warmup.ok else None)
            warmed_cell = _format_warmed_cell(warmup)
            updated = Text(humanize_age(a.saved_at), style=MUTED)
            table.add_row(
                marker, name, email, session_cell, weekly_cell, warmed_cell, updated,
                key=a.name,
            )

        if self._accounts:
            target_row = min(cursor_row, len(self._accounts) - 1) if cursor_row >= 0 else 0
            table.move_cursor(row=target_row)

        self._update_detail_for_row(table.cursor_row)
        self.query_one(SnapshotList).populate(snapshots)

    def _update_detail_for_row(self, row: int) -> None:
        panel = self.query_one(DetailPanel)
        if 0 <= row < len(self._accounts):
            account = self._accounts[row]
            panel.show(account, self._warmups.get(account.name))
        else:
            panel.show(None)

    @on(DataTable.RowHighlighted, "#accounts")
    def _on_row_highlight(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail_for_row(event.cursor_row)

    @on(DataTable.RowSelected, "#accounts")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_switch()

    # ---- helpers ----

    def _selected_account(self) -> Account | None:
        table = self.query_one("#accounts", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._accounts):
            return self._accounts[row]
        return None

    def _notify_error(self, exc: Exception) -> None:
        self.notify(str(exc), title=t("ui.notify.error"), severity="error", timeout=6)

    def _notify_ok(self, message: str) -> None:
        self.notify(message, title=t("ui.notify.done"), severity="information", timeout=4)

    def _apply_binding_labels(self) -> None:
        """Refresh Footer descriptions in the active language."""
        # Walk all known bindings on the app and patch description by action.
        try:
            keymap = self._bindings.key_to_bindings  # type: ignore[attr-defined]
        except AttributeError:
            return
        for bindings in keymap.values():
            for b in bindings:
                key = _BINDING_KEYS.get(b.action)
                if key:
                    try:
                        b.description = t(key)
                    except AttributeError:
                        pass
        try:
            self.query_one(Footer).refresh()
        except Exception:
            pass

    # ---- actions ----

    @work(exclusive=True, thread=True)
    def _switch_worker(self, name: str) -> None:
        try:
            switched, prev = self.manager.switch_account(name)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.call_from_thread(self._notify_error, exc)
            return
        if not switched:
            msg = t("ui.notify.already_active", name=name)
        elif prev:
            msg = t("ui.notify.switched_with_prev", prev=prev, name=name)
        else:
            msg = t("ui.notify.switched_first", name=name)
        self.call_from_thread(self._notify_ok, msg)
        self.call_from_thread(self.refresh_all)

    def action_switch(self) -> None:
        account = self._selected_account()
        if not account:
            self._notify_error(SwitcherError(t("ui.notify.select_account")))
            return
        if account.is_current:
            self._notify_ok(t("ui.notify.already_active", name=account.name))
            return

        current = self.manager.current_account_name()
        if current is None and self.manager.has_live_auth():
            self.push_screen(
                ConfirmModal(
                    t("modal.confirm_switch.title"),
                    t("modal.confirm_switch.body"),
                    confirm_label=t("modal.confirm_switch.confirm"),
                    danger=False,
                ),
                lambda ok: self._switch_worker(account.name) if ok else None,
            )
            return
        self._switch_worker(account.name)

    @work(exclusive=True, thread=True)
    def _save_worker(self, name: str) -> None:
        try:
            self.manager.save_account(name)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover
            self.call_from_thread(self._notify_error, exc)
            return
        self.call_from_thread(self._notify_ok, t("ui.notify.saved", name=name))
        self.call_from_thread(self.refresh_all)

    def action_save(self) -> None:
        current = self.manager.current_account_name()
        initial = current or ""
        live = self.manager.live_summary() or "—"

        def _after(name: str | None) -> None:
            if not name:
                return
            try:
                self.manager.validate_name(name)
            except SwitcherError as exc:
                self._notify_error(exc)
                return
            existing = {a.name for a in self._accounts}
            if name in existing:
                self.push_screen(
                    ConfirmModal(
                        t("modal.overwrite.title"),
                        t("modal.overwrite.body", name=name),
                        confirm_label=t("modal.overwrite.confirm"),
                        danger=True,
                    ),
                    lambda ok: self._save_worker(name) if ok else None,
                )
            else:
                self._save_worker(name)

        self.push_screen(
            TextPromptModal(
                t("modal.save.title"),
                t("modal.save.body", live=live),
                placeholder=t("modal.save.placeholder"),
                initial=initial,
                confirm_label=t("modal.save.confirm"),
            ),
            _after,
        )

    @work(exclusive=True, thread=True)
    def _delete_worker(self, name: str) -> None:
        try:
            self.manager.delete_account(name)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover
            self.call_from_thread(self._notify_error, exc)
            return
        self.call_from_thread(self._notify_ok, t("ui.notify.deleted", name=name))
        self.call_from_thread(self.refresh_all)

    def action_delete(self) -> None:
        account = self._selected_account()
        if not account:
            return
        if account.is_current:
            self._notify_error(SwitcherError(t("err.cannot_delete_active")))
            return
        hint = account.email or t("ui.snapshot.no_email")
        self.push_screen(
            ConfirmModal(
                t("modal.delete.title"),
                t("modal.delete.body", name=account.name, hint=hint),
                confirm_label=t("modal.delete.confirm"),
                danger=True,
            ),
            lambda ok: self._delete_worker(account.name) if ok else None,
        )

    @work(exclusive=True, thread=True)
    def _rename_worker(self, old: str, new: str) -> None:
        try:
            self.manager.rename_account(old, new)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover
            self.call_from_thread(self._notify_error, exc)
            return
        self.call_from_thread(self._notify_ok, t("ui.notify.renamed", old=old, new=new))
        self.call_from_thread(self.refresh_all)

    def action_rename(self) -> None:
        account = self._selected_account()
        if not account:
            return
        self.push_screen(
            TextPromptModal(
                t("modal.rename.title"),
                t("modal.rename.body", name=account.name),
                initial=account.name,
                confirm_label=t("modal.rename.confirm"),
            ),
            lambda new: self._rename_worker(account.name, new) if new else None,
        )

    @work(exclusive=True, thread=True)
    def _restore_worker(self, snapshot_name: str) -> None:
        try:
            self.manager.restore_snapshot(snapshot_name)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover
            self.call_from_thread(self._notify_error, exc)
            return
        self.call_from_thread(self._notify_ok, t("ui.notify.restored", name=snapshot_name))
        self.call_from_thread(self.refresh_all)

    def action_restore(self) -> None:
        snap_list = self.query_one(SnapshotList)
        if snap_list.index is None or snap_list.index < 0:
            self._notify_error(SwitcherError(t("ui.notify.select_snapshot")))
            return
        try:
            item = snap_list.children[snap_list.index]
        except IndexError:
            return
        snapshot: Snapshot | None = getattr(item, "snapshot", None)
        if snapshot is None:
            return
        self.push_screen(
            ConfirmModal(
                t("modal.restore.title"),
                t(
                    "modal.restore.body",
                    hint=snapshot.summary,
                    when=snapshot.created.strftime("%Y-%m-%d %H:%M:%S"),
                ),
                confirm_label=t("modal.restore.confirm"),
                danger=True,
            ),
            lambda ok: self._restore_worker(snapshot.name) if ok else None,
        )

    def action_refresh(self) -> None:
        self.refresh_all()
        self._notify_ok(t("ui.notify.list_refreshed"))

    @work(exclusive=False, thread=True)
    def _warm_worker(self, name: str) -> None:
        try:
            snapshot = self.manager.warm_account(name)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.call_from_thread(self._notify_error, exc)
            return

        if snapshot.ok:
            summary = _summary_for_notification(snapshot)
            self.call_from_thread(
                self._notify_ok, t("ui.notify.warmed", name=name, summary=summary)
            )
        else:
            self.call_from_thread(
                self._notify_error,
                SwitcherError(
                    t("ui.notify.warm_failed", name=name, error=snapshot.error or "—")
                ),
            )
        self.call_from_thread(self.refresh_all)

    def action_warm(self) -> None:
        account = self._selected_account()
        if not account:
            self._notify_error(SwitcherError(t("ui.notify.select_account")))
            return
        if not account.has_credentials:
            self._notify_error(SwitcherError(t("err.no_credentials_to_warm", name=account.name)))
            return
        self._notify_ok(t("ui.notify.warming", name=account.name))
        self._warm_worker(account.name)

    @work(exclusive=True, thread=True)
    def _warm_all_worker(self, names: list[str]) -> None:
        ok = 0
        for name in names:
            try:
                snapshot = self.manager.warm_account(name)
            except SwitcherError:
                continue
            except Exception:  # pragma: no cover - defensive
                continue
            if snapshot.ok:
                ok += 1
            self.call_from_thread(self.refresh_all)
        self.call_from_thread(
            self._notify_ok,
            t("ui.notify.warm_all_done", ok=ok, total=len(names)),
        )

    def action_warm_all(self) -> None:
        names = [a.name for a in self._accounts if a.has_credentials]
        if not names:
            self._notify_error(SwitcherError(t("ui.notify.select_account")))
            return
        self._notify_ok(t("ui.notify.warming_all", count=len(names)))
        self._warm_all_worker(names)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_language(self) -> None:
        self.push_screen(
            LanguageModal(),
            lambda choice: self.apply_language_change(choice) if choice else None,
        )

    def apply_language_change(self, choice: LangChoice) -> None:
        """Apply a new language choice and re-render every translated piece of UI."""
        if choice == current_choice():
            return
        resolved = set_language(choice)
        self.title = t("ui.app.title")
        self.sub_title = t("ui.app.subtitle")
        self._rebuild_table_columns()
        self.query_one("#snapshot-title", Label).update(t("ui.snapshot_title"))
        self._apply_binding_labels()
        self.refresh_all()
        name = t(f"modal.lang.name.{choice}") if choice == "auto" else t(f"modal.lang.name.{resolved}")
        self._notify_ok(t("ui.notify.language_set", lang=name))

    def _rebuild_table_columns(self) -> None:
        table = self.query_one("#accounts", DataTable)
        table.clear(columns=True)
        table.add_columns(
            t("ui.col.marker"),
            t("ui.col.name"),
            t("ui.col.email"),
            t("ui.col.session"),
            t("ui.col.weekly"),
            t("ui.col.warmed"),
            t("ui.col.updated"),
        )


def run() -> None:
    AccountsApp().run()


if __name__ == "__main__":
    run()
