"""Reusable modal screens for the orchestrator UI."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from .i18n import LangChoice, current_choice, t


class _BoxModal(ModalScreen):
    """Shared container with title + body + button row."""

    title_text: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label(self.title_text, id="modal-title")
            yield from self.compose_body()
            with Horizontal(id="modal-buttons"):
                yield from self.compose_buttons()

    def compose_body(self) -> ComposeResult:  # pragma: no cover - overridden
        return ()

    def compose_buttons(self) -> ComposeResult:  # pragma: no cover - overridden
        return ()


class TextPromptModal(_BoxModal):
    """Prompt for free-form text. Returns the trimmed string or None on cancel."""

    BINDINGS = [("escape", "cancel", "")]

    def __init__(
        self,
        title: str,
        message: str,
        placeholder: str = "",
        initial: str = "",
        confirm_label: str = "",
    ) -> None:
        super().__init__()
        self.title_text = title
        self._message = message
        self._placeholder = placeholder
        self._initial = initial
        self._confirm_label = confirm_label or t("modal.ok")

    def compose_body(self) -> ComposeResult:
        yield Static(self._message, id="modal-message")
        yield Input(value=self._initial, placeholder=self._placeholder, id="modal-input")

    def compose_buttons(self) -> ComposeResult:
        yield Button(t("modal.cancel"), id="cancel")
        yield Button(self._confirm_label, id="ok", variant="primary", classes="-primary")

    def on_mount(self) -> None:
        self.query_one("#modal-input", Input).focus()

    @on(Button.Pressed, "#ok")
    def _confirm(self) -> None:
        value = self.query_one("#modal-input", Input).value.strip()
        self.dismiss(value or None)

    @on(Input.Submitted, "#modal-input")
    def _submit(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(_BoxModal):
    """Yes/No confirmation. Returns True/False."""

    BINDINGS = [("escape", "cancel", "")]

    def __init__(
        self,
        title: str,
        message: str,
        confirm_label: str = "",
        cancel_label: str = "",
        danger: bool = False,
    ) -> None:
        super().__init__()
        self.title_text = title
        self._message = message
        self._confirm_label = confirm_label or t("modal.ok")
        self._cancel_label = cancel_label or t("modal.cancel")
        self._danger = danger

    def compose_body(self) -> ComposeResult:
        yield Static(self._message, id="modal-message")

    def compose_buttons(self) -> ComposeResult:
        yield Button(self._cancel_label, id="cancel")
        variant = "error" if self._danger else "primary"
        cls = "-danger" if self._danger else "-primary"
        yield Button(self._confirm_label, id="ok", variant=variant, classes=cls)

    def on_mount(self) -> None:
        self.query_one("#ok", Button).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpModal(_BoxModal):
    """Static help text."""

    BINDINGS = [("escape", "close", ""), ("q", "close", "")]

    def __init__(self) -> None:
        super().__init__()
        self.title_text = t("modal.help.title")

    def compose_body(self) -> ComposeResult:
        yield Static(t("modal.help.body"), id="help-text", markup=True)

    def compose_buttons(self) -> ComposeResult:
        yield Button(t("modal.help.close"), id="ok", variant="primary", classes="-primary")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class LanguageModal(_BoxModal):
    """Pick interface language: auto, en, ru. Returns the chosen LangChoice or None."""

    BINDINGS = [("escape", "cancel", "")]

    def __init__(self) -> None:
        super().__init__()
        self.title_text = t("modal.lang.title")

    def compose_body(self) -> ComposeResult:
        yield Static(t("modal.lang.body"), id="modal-message")
        current = current_choice()
        with RadioSet(id="lang-set"):
            yield RadioButton(t("modal.lang.option.auto"), value=(current == "auto"), id="lang-auto")
            yield RadioButton(t("modal.lang.option.en"), value=(current == "en"), id="lang-en")
            yield RadioButton(t("modal.lang.option.ru"), value=(current == "ru"), id="lang-ru")

    def compose_buttons(self) -> ComposeResult:
        yield Button(t("modal.cancel"), id="cancel")
        yield Button(t("modal.lang.confirm"), id="ok", variant="primary", classes="-primary")

    def on_mount(self) -> None:
        self.query_one("#lang-set", RadioSet).focus()

    def _selected(self) -> LangChoice:
        rs = self.query_one("#lang-set", RadioSet)
        idx = rs.pressed_index
        return ("auto", "en", "ru")[idx] if 0 <= idx <= 2 else "auto"

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self._selected())

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
