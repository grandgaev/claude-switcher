"""Lightweight i18n: EN/RU strings + system-locale autodetect.

Usage::

    from .i18n import t, init, set_language
    init(settings_path)          # once, before any UI
    print(t("ui.binding.save"))

Choice is persisted as one of ``"auto" | "en" | "ru"`` in a small
settings JSON file. ``"auto"`` re-evaluates the system locale every
time the app starts.
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Literal

LangChoice = Literal["auto", "en", "ru"]
ResolvedLang = Literal["en", "ru"]

LANGUAGE_CHOICES: tuple[LangChoice, ...] = ("auto", "en", "ru")

# ---------- translations ----------

_EN: dict[str, str] = {
    # app shell
    "ui.app.title": "Claude Switcher",
    "ui.app.subtitle": "auth-only multi-account orchestrator",
    # status bar
    "ui.status.active": "Active",
    "ui.status.not_saved": "not saved",
    "ui.status.no_auth": "no authentication",
    "ui.status.snapshots": "Snapshots",
    # table columns
    "ui.col.marker": "·",
    "ui.col.name": "Account",
    "ui.col.email": "Email",
    "ui.col.org": "Org",
    "ui.col.updated": "Updated",
    "ui.col.session": "5h",
    "ui.col.weekly": "Week",
    "ui.col.warmed": "Warmed",
    # detail panel
    "ui.detail.title": "Account details",
    "ui.detail.name": "Name",
    "ui.detail.email": "Email",
    "ui.detail.org": "Org",
    "ui.detail.uuid": "UUID",
    "ui.detail.userid": "userID",
    "ui.detail.creds": "Credentials",
    "ui.detail.saved": "Saved",
    "ui.detail.age": "Age",
    "ui.detail.bundle": "Bundle",
    "ui.detail.status": "Status",
    "ui.detail.creds_yes": "yes",
    "ui.detail.creds_no": "no",
    "ui.detail.status_active": "active",
    "ui.detail.status_saved": "saved",
    "ui.detail.empty": "No saved accounts yet.",
    "ui.detail.section.usage": "Usage & resets",
    "ui.detail.usage.session": "5h session",
    "ui.detail.usage.weekly": "Weekly",
    "ui.detail.usage.weekly_opus": "Weekly Opus",
    "ui.detail.usage.checked": "Last warm-up",
    "ui.detail.usage.never": "never",
    "ui.detail.usage.pending": "not checked yet — press w",
    "ui.detail.usage.error": "warm-up failed: {error}",
    "ui.detail.usage.value": "{used}% used · resets in {eta}",
    "ui.detail.usage.value_status": "status={status} · resets in {eta}",
    "ui.detail.usage.value_status_only": "status={status}",
    "ui.detail.usage.value_unknown": "no data from server",
    # snapshots
    "ui.snapshot_title": "Safety snapshots",
    "ui.snapshot.no_email": "(no email)",
    "ui.snapshot.no_auth": "(no auth)",
    "ui.live.creds_only": "credentials present (no oauth metadata)",
    "ui.live.userid_only": "userID={prefix}…",
    # notifications
    "ui.notify.error": "Error",
    "ui.notify.done": "Done",
    "ui.notify.already_active": "“{name}” is already active.",
    "ui.notify.switched_with_prev": "Switched: {prev} → {name}. Previous account saved.",
    "ui.notify.switched_first": "Switched to {name}. Previous state went to a safety snapshot.",
    "ui.notify.saved": "Saved as “{name}”.",
    "ui.notify.deleted": "Deleted “{name}”.",
    "ui.notify.renamed": "Renamed: {old} → {new}.",
    "ui.notify.restored": "Restored: {name}.",
    "ui.notify.list_refreshed": "List refreshed.",
    "ui.notify.select_account": "Select an account in the list first.",
    "ui.notify.select_snapshot": "Select a snapshot on the right.",
    "ui.notify.language_set": "Language set: {lang}",
    "ui.notify.warming": "Warming up “{name}”…",
    "ui.notify.warmed": "“{name}” warmed: {summary}",
    "ui.notify.warm_failed": "Warm-up failed for “{name}”: {error}",
    "ui.notify.warming_all": "Warming up {count} account(s)…",
    "ui.notify.warm_all_done": "Warm-up done: {ok}/{total} succeeded.",
    "ui.notify.summary.session": "5h {pct}% · {eta}",
    "ui.notify.summary.weekly": "Week {pct}% · {eta}",
    "ui.notify.summary.empty": "no rate-limit headers in response",
    # bindings (Footer)
    "ui.binding.switch": "switch",
    "ui.binding.save": "save",
    "ui.binding.rename": "rename",
    "ui.binding.delete": "delete",
    "ui.binding.snapshot": "snapshot",
    "ui.binding.refresh": "refresh",
    "ui.binding.warm": "warm",
    "ui.binding.warm_all": "warm all",
    "ui.binding.lang": "language",
    "ui.binding.help": "help",
    "ui.binding.quit": "quit",
    # modals: shared
    "modal.cancel": "Cancel",
    "modal.ok": "OK",
    # save
    "modal.save.title": "Save current authentication",
    "modal.save.body": "Save the current authentication ({live}) under a name.\nOnly .credentials.json and auth fields of .claude.json are saved; project history and memory are shared.",
    "modal.save.placeholder": "e.g. work-main",
    "modal.save.confirm": "Save",
    # overwrite
    "modal.overwrite.title": "Overwrite?",
    "modal.overwrite.body": "Account “{name}” already exists. Overwrite it?",
    "modal.overwrite.confirm": "Overwrite",
    # confirm switch (unknown current)
    "modal.confirm_switch.title": "Confirm switch",
    "modal.confirm_switch.body": "Current ~/.claude state doesn't match any saved account; it will be captured only as a safety snapshot.\n\nProceed?",
    "modal.confirm_switch.confirm": "Switch",
    # delete
    "modal.delete.title": "Delete account?",
    "modal.delete.body": "Backup “{name}” ({hint}) will be deleted. This cannot be undone.\n\nThis only removes the saved authentication; project history and memory stay.",
    "modal.delete.confirm": "Delete",
    # rename
    "modal.rename.title": "Rename",
    "modal.rename.body": "New name for “{name}”.",
    "modal.rename.confirm": "Rename",
    # restore
    "modal.restore.title": "Restore from snapshot?",
    "modal.restore.body": "Auth state will be restored from “{hint}” ({when}).\n\nA fresh safety snapshot will be taken first.",
    "modal.restore.confirm": "Restore",
    # help
    "modal.help.title": "Keyboard shortcuts",
    "modal.help.body": (
        "[b]↑/↓[/]            navigate the list\n"
        "[b]Enter[/]           switch to the highlighted account\n"
        "[b]s[/]               save current authentication\n"
        "[b]r[/]               rename selected\n"
        "[b]d[/]               delete selected\n"
        "[b]b[/]               restore from a safety snapshot\n"
        "[b]w[/]               warm up selected account (ping Haiku 4.5)\n"
        "[b]W / Shift+W[/]     warm up every saved account\n"
        "[b]l[/]               change language\n"
        "[b]F5[/]              refresh\n"
        "[b]?[/]               this screen\n"
        "[b]q / Ctrl+C[/]      quit\n"
        "\n"
        "[#d97757 b]What is switched[/]: only authentication — "
        "[i].credentials.json[/] and the [i]oauthAccount / userID / "
        "customApiKeyResponses[/] fields in [i].claude.json[/].\n"
        "\n"
        "[#7fb069 b]What is shared[/]: project history, "
        "memory (CLAUDE.md, memory/), todos, MCP servers, theme, "
        "IDE settings — never touched.\n"
        "\n"
        "[#d97757 b]Warm-up[/]: sends a 1-token “hi” to "
        "[i]claude-haiku-4-5[/] using the saved OAuth token, then "
        "shows how much of the 5h session window and the weekly "
        "window is already spent and when each one resets.\n"
        "\n"
        "[dim]A safety snapshot of the live auth is taken before "
        "every switch and restore (the last 20 are kept).[/]"
    ),
    "modal.help.close": "Close",
    # language picker
    "modal.lang.title": "Language",
    "modal.lang.body": "Choose interface language. “Auto” follows the system locale.",
    "modal.lang.option.auto": "Auto (system)",
    "modal.lang.option.en": "English",
    "modal.lang.option.ru": "Русский",
    "modal.lang.confirm": "Apply",
    "modal.lang.name.en": "English",
    "modal.lang.name.ru": "Russian",
    "modal.lang.name.auto": "Auto",
    # core errors
    "err.name_invalid": "Name must contain only letters, digits, _, -, . and be 1–64 chars long.",
    "err.nothing_to_save": "Nothing to save: no .credentials.json or auth fields in .claude.json. Sign into Claude Code and try again.",
    "err.account_not_saved": "Account “{name}” isn't saved.",
    "err.cannot_delete_active": "Cannot delete the active account. Switch to another one first.",
    "err.account_not_found": "Account “{name}” not found.",
    "err.account_exists": "Account “{name}” already exists.",
    "err.snapshot_not_found": "Snapshot “{name}” not found.",
    "err.bad_bundle": "Corrupt bundle: {name}",
    "err.no_credentials_to_warm": "Account “{name}” has no credentials saved — nothing to warm up.",
    # humanize age
    "time.now": "just now",
    "time.min": "{n} min ago",
    "time.hour": "{n} h ago",
    "time.day": "{n} d ago",
    "time.month": "{n} mo ago",
    "time.year": "{n} y ago",
    # cli
    "cli.no_accounts": "No saved accounts.",
    "cli.unknown": "(unknown)",
    "cli.already_active": "Already active: {name}",
    "cli.switched": "Switched: {prev} -> {name}",
    "cli.saved": "Saved: {name}",
    "cli.error_prefix": "Error: ",
}

_RU: dict[str, str] = {
    "ui.app.title": "Claude Switcher",
    "ui.app.subtitle": "оркестратор аккаунтов (только авторизация)",
    "ui.status.active": "Активный",
    "ui.status.not_saved": "не сохранён",
    "ui.status.no_auth": "нет авторизации",
    "ui.status.snapshots": "Snapshots",
    "ui.col.marker": "·",
    "ui.col.name": "Аккаунт",
    "ui.col.email": "Email",
    "ui.col.org": "Org",
    "ui.col.updated": "Обновлён",
    "ui.col.session": "5ч",
    "ui.col.weekly": "Неделя",
    "ui.col.warmed": "Прогрев",
    "ui.detail.title": "Детали аккаунта",
    "ui.detail.name": "Имя",
    "ui.detail.email": "Email",
    "ui.detail.org": "Org",
    "ui.detail.uuid": "UUID",
    "ui.detail.userid": "userID",
    "ui.detail.creds": "Credentials",
    "ui.detail.saved": "Сохранён",
    "ui.detail.age": "Возраст",
    "ui.detail.bundle": "Бандл",
    "ui.detail.status": "Статус",
    "ui.detail.creds_yes": "есть",
    "ui.detail.creds_no": "нет",
    "ui.detail.status_active": "активный",
    "ui.detail.status_saved": "сохранённый",
    "ui.detail.empty": "Сохранённых аккаунтов пока нет.",
    "ui.detail.section.usage": "Использование и сбросы",
    "ui.detail.usage.session": "Сессия 5ч",
    "ui.detail.usage.weekly": "Неделя",
    "ui.detail.usage.weekly_opus": "Неделя Opus",
    "ui.detail.usage.checked": "Последний прогрев",
    "ui.detail.usage.never": "никогда",
    "ui.detail.usage.pending": "ещё не прогревался — нажми w",
    "ui.detail.usage.error": "ошибка прогрева: {error}",
    "ui.detail.usage.value": "{used}% использовано · сброс через {eta}",
    "ui.detail.usage.value_status": "статус={status} · сброс через {eta}",
    "ui.detail.usage.value_status_only": "статус={status}",
    "ui.detail.usage.value_unknown": "сервер не вернул данные",
    "ui.snapshot_title": "Safety snapshots",
    "ui.snapshot.no_email": "(без email)",
    "ui.snapshot.no_auth": "(нет auth)",
    "ui.live.creds_only": "credentials есть (без oauth-метаданных)",
    "ui.live.userid_only": "userID={prefix}…",
    "ui.notify.error": "Ошибка",
    "ui.notify.done": "Готово",
    "ui.notify.already_active": "«{name}» уже активен.",
    "ui.notify.switched_with_prev": "Переключено: {prev} → {name}. Прежний аккаунт сохранён.",
    "ui.notify.switched_first": "Переключено на {name}. Прежнее состояние — в safety snapshot.",
    "ui.notify.saved": "Сохранено как «{name}».",
    "ui.notify.deleted": "Удалён «{name}».",
    "ui.notify.renamed": "Переименовано: {old} → {new}.",
    "ui.notify.restored": "Восстановлено: {name}.",
    "ui.notify.list_refreshed": "Список обновлён.",
    "ui.notify.select_account": "Выбери аккаунт в списке.",
    "ui.notify.select_snapshot": "Выбери snapshot в правой панели.",
    "ui.notify.language_set": "Язык: {lang}",
    "ui.notify.warming": "Прогреваю «{name}»…",
    "ui.notify.warmed": "«{name}» прогрет: {summary}",
    "ui.notify.warm_failed": "Прогрев «{name}» провалился: {error}",
    "ui.notify.warming_all": "Прогреваю {count} аккаунт(ов)…",
    "ui.notify.warm_all_done": "Прогрев завершён: {ok}/{total} успешно.",
    "ui.notify.summary.session": "5ч {pct}% · {eta}",
    "ui.notify.summary.weekly": "Неделя {pct}% · {eta}",
    "ui.notify.summary.empty": "сервер не вернул rate-limit заголовков",
    "ui.binding.switch": "переключить",
    "ui.binding.save": "сохранить",
    "ui.binding.rename": "переимен.",
    "ui.binding.delete": "удалить",
    "ui.binding.snapshot": "snapshot",
    "ui.binding.refresh": "обновить",
    "ui.binding.warm": "прогрев",
    "ui.binding.warm_all": "прогрев всех",
    "ui.binding.lang": "язык",
    "ui.binding.help": "помощь",
    "ui.binding.quit": "выход",
    "modal.cancel": "Отмена",
    "modal.ok": "OK",
    "modal.save.title": "Сохранить текущую авторизацию",
    "modal.save.body": "Сохранить текущую авторизацию ({live}) под именем.\nСохраняются только .credentials.json и auth-поля .claude.json; история проектов и память — общие.",
    "modal.save.placeholder": "напр. work-main",
    "modal.save.confirm": "Сохранить",
    "modal.overwrite.title": "Перезаписать?",
    "modal.overwrite.body": "Аккаунт «{name}» уже существует. Перезаписать его?",
    "modal.overwrite.confirm": "Перезаписать",
    "modal.confirm_switch.title": "Подтверди переключение",
    "modal.confirm_switch.body": "Текущее состояние ~/.claude не совпадает ни с одним сохранённым аккаунтом — оно попадёт только в safety snapshot.\n\nПродолжить?",
    "modal.confirm_switch.confirm": "Переключить",
    "modal.delete.title": "Удалить аккаунт?",
    "modal.delete.body": "Будет удалён бэкап «{name}» ({hint}). Это необратимо.\n\nУдаляется только сохранённая авторизация — история проектов и память останутся.",
    "modal.delete.confirm": "Удалить",
    "modal.rename.title": "Переименовать",
    "modal.rename.body": "Новое имя для «{name}».",
    "modal.rename.confirm": "Переименовать",
    "modal.restore.title": "Восстановить из snapshot?",
    "modal.restore.body": "Auth-состояние будет восстановлено из «{hint}» ({when}).\n\nПеред этим будет сделан новый safety snapshot.",
    "modal.restore.confirm": "Восстановить",
    "modal.help.title": "Горячие клавиши",
    "modal.help.body": (
        "[b]↑/↓[/]            навигация по списку\n"
        "[b]Enter[/]           переключиться на выбранный аккаунт\n"
        "[b]s[/]               сохранить текущую авторизацию\n"
        "[b]r[/]               переименовать выбранный\n"
        "[b]d[/]               удалить выбранный\n"
        "[b]b[/]               восстановить из safety-snapshot\n"
        "[b]w[/]               прогреть выбранный аккаунт (ping Haiku 4.5)\n"
        "[b]W / Shift+W[/]     прогреть все сохранённые аккаунты\n"
        "[b]l[/]               сменить язык\n"
        "[b]F5[/]              обновить список\n"
        "[b]?[/]               этот экран\n"
        "[b]q / Ctrl+C[/]      выход\n"
        "\n"
        "[#d97757 b]Что переключается[/]: только авторизация — "
        "[i].credentials.json[/] и поля [i]oauthAccount / userID / "
        "customApiKeyResponses[/] в [i].claude.json[/].\n"
        "\n"
        "[#7fb069 b]Что остаётся общим[/]: история проектов, "
        "память (CLAUDE.md, memory/), todos, MCP-серверы, тема, "
        "настройки IDE — всё это никогда не трогается.\n"
        "\n"
        "[#d97757 b]Прогрев[/]: отправляет короткое «hi» к "
        "[i]claude-haiku-4-5[/] под сохранённым OAuth-токеном, "
        "после чего показывает, сколько съедено за 5-часовую сессию "
        "и за неделю и когда оба лимита обнулятся.\n"
        "\n"
        "[dim]Перед каждым переключением и восстановлением "
        "создаётся snapshot текущей auth в "
        ".claude-accounts/.safety-snapshots/ (хранятся последние 20).[/]"
    ),
    "modal.help.close": "Закрыть",
    "modal.lang.title": "Язык интерфейса",
    "modal.lang.body": "Выбери язык. «Авто» следует системной локали.",
    "modal.lang.option.auto": "Авто (системный)",
    "modal.lang.option.en": "English",
    "modal.lang.option.ru": "Русский",
    "modal.lang.confirm": "Применить",
    "modal.lang.name.en": "английский",
    "modal.lang.name.ru": "русский",
    "modal.lang.name.auto": "авто",
    "err.name_invalid": "Имя может содержать буквы, цифры, _, -, . и быть длиной 1–64.",
    "err.nothing_to_save": "Нечего сохранять: не найдены ни .credentials.json, ни auth-поля в .claude.json. Залогинься в Claude Code и попробуй снова.",
    "err.account_not_saved": "Аккаунт «{name}» не сохранён.",
    "err.cannot_delete_active": "Нельзя удалить активный аккаунт. Сначала переключись на другой.",
    "err.account_not_found": "Аккаунт «{name}» не найден.",
    "err.account_exists": "Аккаунт «{name}» уже существует.",
    "err.snapshot_not_found": "Snapshot «{name}» не найден.",
    "err.bad_bundle": "Битый бандл: {name}",
    "err.no_credentials_to_warm": "У «{name}» не сохранены credentials — прогревать нечего.",
    "time.now": "только что",
    "time.min": "{n} мин назад",
    "time.hour": "{n} ч назад",
    "time.day": "{n} дн назад",
    "time.month": "{n} мес назад",
    "time.year": "{n} г назад",
    "cli.no_accounts": "Сохранённых аккаунтов нет.",
    "cli.unknown": "(неизвестно)",
    "cli.already_active": "Уже активен: {name}",
    "cli.switched": "Переключено: {prev} -> {name}",
    "cli.saved": "Сохранено: {name}",
    "cli.error_prefix": "Ошибка: ",
}

TRANSLATIONS: dict[ResolvedLang, dict[str, str]] = {"en": _EN, "ru": _RU}

# ---------- state ----------

_settings_path: Path | None = None
_choice: LangChoice = "auto"
_resolved: ResolvedLang = "en"


def detect_system_lang() -> ResolvedLang:
    """Best-effort system language detection.

    Honors LANG / LC_ALL env vars, then falls back to ``locale.getlocale``.
    Anything starting with ``ru`` → Russian, otherwise English.
    """
    for var in ("LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        v = os.environ.get(var)
        if v:
            if v.lower().startswith("ru"):
                return "ru"
            if v.lower().startswith(("en", "c", "posix")):
                return "en"
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, locale.Error):
        loc = ""
    if not loc:
        try:
            loc = locale.getdefaultlocale()[0] or ""  # type: ignore[attr-defined]
        except (ValueError, AttributeError, locale.Error):
            loc = ""
    if loc.lower().startswith("ru") or "russian" in loc.lower():
        return "ru"
    return "en"


def init(settings_path: Path | None) -> ResolvedLang:
    """Load saved choice from disk and apply it. Returns the resolved language."""
    global _settings_path
    _settings_path = settings_path
    saved = _load_choice()
    return set_language(saved, persist=False)


def _load_choice() -> LangChoice:
    if not _settings_path or not _settings_path.exists():
        return "auto"
    try:
        data = json.loads(_settings_path.read_text(encoding="utf-8"))
        val = data.get("lang", "auto")
        if val in LANGUAGE_CHOICES:
            return val  # type: ignore[return-value]
    except (OSError, json.JSONDecodeError):
        pass
    return "auto"


def _save_choice(choice: LangChoice) -> None:
    if not _settings_path:
        return
    _settings_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if _settings_path.exists():
        try:
            loaded = json.loads(_settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["lang"] = choice
    tmp = _settings_path.with_name(_settings_path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _settings_path)


def set_language(choice: LangChoice, persist: bool = True) -> ResolvedLang:
    """Apply a language choice and optionally persist it."""
    global _choice, _resolved
    if choice not in LANGUAGE_CHOICES:
        choice = "auto"
    _choice = choice
    if choice == "auto":
        _resolved = detect_system_lang()
    else:
        _resolved = choice  # type: ignore[assignment]
    if persist:
        _save_choice(choice)
    return _resolved


def current_choice() -> LangChoice:
    return _choice


def current_lang() -> ResolvedLang:
    return _resolved


def t(key: str, /, **kwargs: object) -> str:
    """Translate ``key`` using the active language. Falls back to EN, then to the key."""
    table = TRANSLATIONS.get(_resolved, _EN)
    value = table.get(key) or _EN.get(key) or key
    if kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return value
    return value
