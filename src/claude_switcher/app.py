"""Textual TUI orchestrator for Claude Code accounts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable

from rich.text import Text
from textual import events, on, work
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
    ImportResult,
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

# RowSelected fires for both mouse clicks and Enter. If less time than this
# has passed since the latest Click, we treat the selection as click-driven
# and require chain >= 2 (double-click) to actually switch.
_CLICK_RECENT_WINDOW = 0.25

# How often to re-render the table so the "resets in …" timers tick down
# without the user pressing F5. Cheap: rereads cached JSON, no network.
_ETA_TICK_SECONDS = 60

# How often to silently re-warm every saved account in the background, so
# usage % and reset timers stay live without the user pressing w/W. This
# does hit the real Anthropic API (one 1-token ping per account), so keep
# it infrequent.
_AUTO_WARM_INTERVAL_SECONDS = 300


def _is_stale(window: LimitWindow | None, now: datetime | None = None) -> bool:
    """A cached window is stale once its reset time has passed — the
    utilization % in it belonged to a window that already rolled over, so
    displaying it as current would be misleading."""
    if window is None or window.reset_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    reset_at = window.reset_at
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    return reset_at <= now


def _crop(value: str, width: int) -> str:
    """Trim ``value`` with an ellipsis so it never wraps onto a new line."""
    if width < 1 or len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[: width - 1] + "…"


def _usage_color(used_pct: int | None) -> str:
    if used_pct is None:
        return MUTED
    if used_pct >= 90:
        return DANGER
    if used_pct >= 60:
        return WARN
    return OK_COLOR


def _cell_for_window(window: LimitWindow | None, width: int) -> Text:
    """Render the per-account 5h / weekly cell as a single short string.

    Cropped to ``width`` ourselves (rather than relying on DataTable's own
    fixed-column cropping) so long or non-English strings never bleed past
    the column and never trigger a horizontal scrollbar.
    """
    if window is None:
        return Text("—", style=MUTED)
    if _is_stale(window):
        return Text(_crop(t("ui.cell.stale"), width), style=MUTED)
    used = window.used_pct
    eta = format_eta(window.reset_at)
    if used is None and window.status is None:
        return Text("—", style=MUTED)
    if used is None:
        label = f"{window.status or '—'} · {eta}"
        return Text(_crop(label, width), style=WARN if window.status not in (None, "ok") else MUTED)
    color = _usage_color(used)
    return Text(_crop(f"{used}% · {eta}", width), style=color)


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
        label_width = 12
        value_width = self._value_width(label_width)
        highlight_keys = {"name", "email", "status"} if a.is_current else set()
        for label, value, key in rows:
            text.append(f"  {_crop(label, label_width):<{label_width}}", style=MUTED)
            style = f"bold {ACCENT}" if key in highlight_keys else "white"
            text.append(f"{_crop(value, value_width)}\n", style=style)

        text.append(f"\n{t('ui.detail.section.usage')}\n", style=f"bold {ACCENT}")
        self._append_usage(text)
        self.update(text)

    def _value_width(self, label_width: int) -> int:
        available = self.size.width - label_width - 2
        return available if available > 8 else 8

    def _append_usage(self, text: Text) -> None:
        snap = self._warmup
        label_width = 18  # fits the longest translated label, e.g. "Последний прогрев"
        value_width = self._value_width(label_width)
        if snap is None:
            text.append(f"  {t('ui.detail.usage.pending')}\n", style=MUTED)
            return
        checked_local = _to_local(snap.checked_at)
        checked_str = checked_local.strftime("%Y-%m-%d %H:%M") if checked_local else t(
            "ui.detail.usage.never"
        )
        text.append(f"  {_crop(t('ui.detail.usage.checked'), label_width):<{label_width}}", style=MUTED)
        text.append(f"{_crop(checked_str, value_width)}\n", style="white")
        if not snap.ok:
            text.append(
                f"  {_crop(t('ui.detail.usage.error', error=snap.error or '—'), value_width + label_width)}\n",
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
            text.append(f"  {_crop(label, label_width):<{label_width}}", style=MUTED)
            stale = _is_stale(window)
            color = MUTED if stale else _usage_color(window.used_pct)
            text.append(f"{_crop(_format_window_line(window, stale), value_width)}\n", style=color)


def _format_window_line(window: LimitWindow, stale: bool = False) -> str:
    if stale:
        return t("ui.detail.usage.stale")
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
            label.append(f"  {_crop(snap.summary, 18):<18}", style="white")
            label.append(f"  {_crop(_short_label(snap.label), 12)}", style=MUTED)
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
    "export": "ui.binding.export",
    "import": "ui.binding.import",
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
        Binding("e", "export", "export"),
        Binding("i", "import", "import"),
        Binding("f5", "refresh", "refresh"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, manager: AccountManager | None = None) -> None:
        super().__init__()
        self.manager = manager or AccountManager()
        self._accounts: list[Account] = []
        self._warmups: dict[str, WarmupSnapshot] = {}
        self._last_click_at: float = 0.0
        self._last_click_chain: int = 0
        self.title = t("ui.app.title")
        self.sub_title = t("ui.app.subtitle")

    # ---- composition ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        with Horizontal(id="main"):
            with Vertical(id="left-pane"):
                # cell_padding=1 (Textual's default) keeps a real gap between
                # columns — cropped cell text usually fills its column
                # exactly, so padding=0 made adjacent cells visually glue
                # together with no space at all. _COLUMN_WIDTHS below sizes
                # each column, including this padding, to the real budget.
                table = DataTable(id="accounts", zebra_stripes=True, cell_padding=1)
                table.cursor_type = "row"
                _add_table_columns(table)
                yield table
            with Vertical(id="right-pane"):
                yield DetailPanel()
                yield Label(t("ui.snapshot_title"), id="snapshot-title")
                yield SnapshotList(id="snapshot-list")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_binding_labels()
        self.refresh_all()
        # Tick the cached ETAs forward without hitting the network.
        self.set_interval(_ETA_TICK_SECONDS, self.refresh_all)
        # Keep usage % and reset timers live without manual w/W presses.
        self.set_interval(_AUTO_WARM_INTERVAL_SECONDS, self._auto_warm_tick)

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
            name = Text(_crop(a.name, _COLUMN_WIDTH_MAP["ui.col.name"]), style=f"bold {ACCENT}" if a.is_current else "white")
            email = Text(_crop(a.email or "—", _COLUMN_WIDTH_MAP["ui.col.email"]), style="white" if a.email else MUTED)
            if a.has_credentials:
                session_cell = _cell_for_window(
                    warmup.five_hour if warmup and warmup.ok else None, _COLUMN_WIDTH_MAP["ui.col.session"]
                )
                weekly_cell = _cell_for_window(
                    warmup.weekly if warmup and warmup.ok else None, _COLUMN_WIDTH_MAP["ui.col.weekly"]
                )
            else:
                # Distinguish "never warmed yet" (—) from "can never be
                # warmed" (no saved credentials at all) so a broken save
                # is obvious in the list, not just on selection.
                session_cell = Text(_crop(t("ui.cell.no_creds"), _COLUMN_WIDTH_MAP["ui.col.session"]), style=DANGER)
                weekly_cell = Text(_crop(t("ui.cell.no_creds"), _COLUMN_WIDTH_MAP["ui.col.weekly"]), style=DANGER)
            updated = Text(_crop(humanize_age(a.saved_at), _COLUMN_WIDTH_MAP["ui.col.updated"]), style=MUTED)
            table.add_row(
                marker, name, email, session_cell, weekly_cell, updated,
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

    @on(events.Click)
    def _track_click_chain(self, event: events.Click) -> None:
        """Remember the latest click's chain count so RowSelected can tell
        a single click from a double click."""
        self._last_click_at = monotonic()
        self._last_click_chain = int(getattr(event, "chain", 1) or 1)

    @on(DataTable.RowSelected, "#accounts")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        # RowSelected fires for BOTH a mouse click and the Enter key. We
        # treat a mouse click as "switch" only when the user double-clicked
        # quickly on the same row; a slow second click on the same row stays
        # a no-op so accidental clicks don't swap the active account.
        if monotonic() - self._last_click_at <= _CLICK_RECENT_WINDOW:
            if self._last_click_chain < 2:
                return  # single click — highlight only
            self._last_click_chain = 0  # consume the chain
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
    def _warm_all_worker(self, names: list[str], notify: bool = True) -> None:
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
        if notify:
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

    def _auto_warm_tick(self) -> None:
        """Silently refresh usage % / reset timers for every saved account.

        Runs every ``_AUTO_WARM_INTERVAL_SECONDS`` so the numbers stay live
        even if the user never presses w/W. No toast on success — only
        surfaces via the table/detail panel; a background auto-warm run
        already in flight is skipped (``exclusive=True`` on the worker).
        """
        names = [a.name for a in self._accounts if a.has_credentials]
        if names:
            self._warm_all_worker(names, notify=False)

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
        self._refresh_localized_ui()
        name = t(f"modal.lang.name.{choice}") if choice == "auto" else t(f"modal.lang.name.{resolved}")
        self._notify_ok(t("ui.notify.language_set", lang=name))

    def _refresh_localized_ui(self) -> None:
        """Re-render every translated piece of UI for the currently active language."""
        self.title = t("ui.app.title")
        self.sub_title = t("ui.app.subtitle")
        self._rebuild_table_columns()
        self.query_one("#snapshot-title", Label).update(t("ui.snapshot_title"))
        self._apply_binding_labels()
        self.refresh_all()

    # ---- export / import ----

    def action_export(self) -> None:
        default_path = str(Path.home() / "claude-switcher-export.cswitchconfig")

        def _after(path: str | None) -> None:
            if path:
                self._export_worker(path)

        self.push_screen(
            TextPromptModal(
                t("modal.export.title"),
                t("modal.export.body", count=len(self._accounts)),
                placeholder=t("modal.export.placeholder"),
                initial=default_path,
                confirm_label=t("modal.export.confirm"),
            ),
            _after,
        )

    @work(exclusive=True, thread=True)
    def _export_worker(self, path: str) -> None:
        try:
            count = self.manager.export_config(path)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except OSError as exc:  # pragma: no cover - defensive
            self.call_from_thread(self._notify_error, exc)
            return
        self.call_from_thread(self._notify_ok, t("ui.notify.exported", count=count, path=path))

    def action_import(self) -> None:
        def _after(path: str | None) -> None:
            if path:
                self._import_worker(path, overwrite=False)

        self.push_screen(
            TextPromptModal(
                t("modal.import.title"),
                t("modal.import.body"),
                placeholder=t("modal.import.placeholder"),
                confirm_label=t("modal.import.confirm"),
            ),
            _after,
        )

    @work(exclusive=True, thread=True)
    def _import_worker(self, path: str, overwrite: bool) -> None:
        try:
            result = self.manager.import_config(path, overwrite=overwrite)
        except SwitcherError as exc:
            self.call_from_thread(self._notify_error, exc)
            return
        except OSError as exc:  # pragma: no cover - defensive
            self.call_from_thread(self._notify_error, exc)
            return

        if result.skipped and not overwrite:
            self.call_from_thread(self._prompt_import_overwrite, path, result)
            return
        self.call_from_thread(self._finish_import, result)

    def _prompt_import_overwrite(self, path: str, result: ImportResult) -> None:
        self.push_screen(
            ConfirmModal(
                t("modal.import_conflict.title"),
                t(
                    "modal.import_conflict.body",
                    count=len(result.skipped),
                    names=", ".join(result.skipped),
                ),
                confirm_label=t("modal.import_conflict.confirm"),
                danger=True,
            ),
            lambda ok: self._import_worker(path, overwrite=True) if ok else self._finish_import(result),
        )

    def _finish_import(self, result: ImportResult) -> None:
        if result.language:
            self._refresh_localized_ui()
        else:
            self.refresh_all()
        self._notify_ok(
            t(
                "ui.notify.imported",
                imported=len(result.imported),
                skipped=len(result.skipped),
                overwritten=len(result.overwritten),
            )
        )

    def _rebuild_table_columns(self) -> None:
        table = self.query_one("#accounts", DataTable)
        table.clear(columns=True)
        _add_table_columns(table)


# Fixed column widths (content only — DataTable adds cell_padding on each
# side on top of this) keep the table from outgrowing the left pane and
# triggering a horizontal scrollbar on common terminal sizes (~100 cols).
# The "warmed" column was dropped: it's redundant with "Last warm-up" in
# the detail panel, and removing it left enough budget for every other
# column to keep a real gap between cells.
_COLUMN_WIDTHS: tuple[tuple[str, int], ...] = (
    ("ui.col.marker", 1),
    ("ui.col.name", 11),
    ("ui.col.email", 14),
    ("ui.col.session", 9),
    ("ui.col.weekly", 9),
    ("ui.col.updated", 7),
)
_COLUMN_WIDTH_MAP: dict[str, int] = dict(_COLUMN_WIDTHS)


def _add_table_columns(table: DataTable) -> None:
    for key, width in _COLUMN_WIDTHS:
        table.add_column(_crop(t(key), width), width=width, key=key)


def run() -> None:
    AccountsApp().run()


if __name__ == "__main__":
    run()
