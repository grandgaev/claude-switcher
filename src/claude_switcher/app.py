"""Textual TUI orchestrator for Claude Code accounts."""
from __future__ import annotations

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


ACCENT = "#d97757"
MUTED = "#888579"
DANGER = "#e76f51"


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

    def show(self, account: Account | None) -> None:
        self._account = account
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
        self.update(text)


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
        Binding("l", "language", "language"),
        Binding("f5", "refresh", "refresh"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, manager: AccountManager | None = None) -> None:
        super().__init__()
        self.manager = manager or AccountManager()
        self._accounts: list[Account] = []
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
                    t("ui.col.org"),
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
            marker = Text("●", style=ACCENT) if a.is_current else Text("○", style=MUTED)
            name = Text(a.name, style=f"bold {ACCENT}" if a.is_current else "white")
            email = Text(a.email or "—", style="white" if a.email else MUTED)
            org = Text(a.organization or "—", style=MUTED)
            updated = Text(humanize_age(a.saved_at), style=MUTED)
            table.add_row(marker, name, email, org, updated, key=a.name)

        if self._accounts:
            target_row = min(cursor_row, len(self._accounts) - 1) if cursor_row >= 0 else 0
            table.move_cursor(row=target_row)

        self._update_detail_for_row(table.cursor_row)
        self.query_one(SnapshotList).populate(snapshots)

    def _update_detail_for_row(self, row: int) -> None:
        panel = self.query_one(DetailPanel)
        if 0 <= row < len(self._accounts):
            panel.show(self._accounts[row])
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
            t("ui.col.org"),
            t("ui.col.updated"),
        )


def run() -> None:
    AccountsApp().run()


if __name__ == "__main__":
    run()
