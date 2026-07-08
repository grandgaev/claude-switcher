"""Account management — auth-only model.

A "switch" only moves authentication state. Projects, history, memory,
todos, settings and MCP config in ``~/.claude`` are SHARED across all
accounts and never touched by this tool.

What we actually move:
  • ``~/.claude/.credentials.json`` — full file
  • subset of ``~/.claude.json`` fields listed in ``AUTH_FIELDS``
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .i18n import LANGUAGE_CHOICES, current_choice, set_language, t
from .warming import WarmupSnapshot, warm_credentials


VALID_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
MAX_SAFETY_SNAPSHOTS = 20
BUNDLE_SUFFIX = ".account.json"
BUNDLE_VERSION = 1
WARMUP_CACHE_FILENAME = ".warmup-cache.json"

# Portable export/import file — a single JSON file (any extension; the app
# suggests ``.cswitchconfig``) bundling every saved account plus the
# language setting, so a user can move their whole set of profiles to
# another machine, on any OS, in one step.
CONFIG_FORMAT = "claude-switcher-config"
CONFIG_FORMAT_VERSION = 1

# Whitelist of fields in ~/.claude.json that carry identity / auth.
# Everything else (projects, mcpServers, tipsHistory, numStartups, …) stays.
AUTH_FIELDS: tuple[str, ...] = (
    "oauthAccount",
    "userID",
    "customApiKeyResponses",
)


class SwitcherError(Exception):
    """Domain error surfaced to the UI."""


@dataclass(frozen=True)
class Account:
    name: str
    bundle_path: Path
    saved_at: datetime
    email: str | None
    account_uuid: str | None
    organization: str | None
    user_id: str | None
    has_credentials: bool
    bundle_size: int
    is_current: bool


@dataclass(frozen=True)
class Snapshot:
    name: str
    path: Path
    created: datetime
    size_bytes: int
    label: str
    summary: str  # short human-readable identity hint (email / "no auth")


@dataclass(frozen=True)
class ImportResult:
    imported: tuple[str, ...]
    skipped: tuple[str, ...]
    overwritten: tuple[str, ...]
    language: str | None


@dataclass
class _Bundle:
    """In-memory representation of an account bundle file."""
    version: int = BUNDLE_VERSION
    name: str = ""
    saved_at: str = ""
    credentials_text: str | None = None
    config_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "_Bundle":
        return cls(
            version=raw.get("version", BUNDLE_VERSION),
            name=raw.get("name", ""),
            saved_at=raw.get("saved_at", ""),
            credentials_text=raw.get("credentials_text"),
            config_fields=raw.get("config_fields") or {},
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "saved_at": self.saved_at,
            "credentials_text": self.credentials_text,
            "config_fields": self.config_fields,
        }


class AccountManager:
    """Backend for the orchestrator.

    Layout under ``backup_dir``:
        <name>.account.json              # one file per account
        .safety-snapshots/
            <timestamp>_<label>.account.json
    """

    def __init__(
        self,
        claude_dir: Path | None = None,
        claude_config: Path | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        home = Path.home()
        self.claude_dir = Path(claude_dir or home / ".claude")
        self.claude_config = Path(claude_config or home / ".claude.json")
        self.backup_dir = Path(backup_dir or home / ".claude-accounts")
        self.safety_dir = self.backup_dir / ".safety-snapshots"
        self.warmup_cache_path = self.backup_dir / WARMUP_CACHE_FILENAME
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.safety_dir.mkdir(parents=True, exist_ok=True)

    # ---- file paths ----

    @property
    def credentials_path(self) -> Path:
        return self.claude_dir / ".credentials.json"

    def _bundle_path(self, name: str) -> Path:
        return self.backup_dir / f"{name}{BUNDLE_SUFFIX}"

    # ---- introspection ----

    def list_accounts(self) -> list[Account]:
        live = self._read_live_bundle()
        live_uuid = _peek(live.config_fields.get("oauthAccount"), "accountUuid")
        live_email = _peek(live.config_fields.get("oauthAccount"), "emailAddress")
        live_user = live.config_fields.get("userID")
        live_creds = live.credentials_text

        accounts: list[Account] = []
        for path in sorted(self.backup_dir.glob(f"*{BUNDLE_SUFFIX}")):
            name = path.name[: -len(BUNDLE_SUFFIX)]
            if not name or name.startswith("."):
                continue
            try:
                bundle = _read_bundle(path)
            except (OSError, json.JSONDecodeError):
                continue
            oauth = bundle.config_fields.get("oauthAccount") or {}
            uuid = _peek(oauth, "accountUuid")
            email = _peek(oauth, "emailAddress")
            org = _peek(oauth, "organizationName") or _peek(oauth, "organizationUuid")
            user_id = bundle.config_fields.get("userID")

            is_current = False
            if live_uuid and uuid and live_uuid == uuid:
                is_current = True
            elif not live_uuid and live_email and email and live_email == email:
                is_current = True
            elif not live_uuid and not live_email and live_user and user_id and live_user == user_id:
                is_current = True
            elif not (live_uuid or live_email or live_user):
                if live_creds is not None and bundle.credentials_text == live_creds:
                    is_current = True

            saved_at = _parse_iso(bundle.saved_at) or _file_mtime(path)
            accounts.append(
                Account(
                    name=name,
                    bundle_path=path,
                    saved_at=saved_at,
                    email=email,
                    account_uuid=uuid,
                    organization=org,
                    user_id=user_id,
                    has_credentials=bool(bundle.credentials_text),
                    bundle_size=path.stat().st_size,
                    is_current=is_current,
                )
            )
        return accounts

    def current_account_name(self) -> str | None:
        for a in self.list_accounts():
            if a.is_current:
                return a.name
        return None

    def live_summary(self) -> str:
        """Short text describing who is currently authenticated, regardless of save state."""
        live = self._read_live_bundle()
        oauth = live.config_fields.get("oauthAccount") or {}
        email = _peek(oauth, "emailAddress")
        if email:
            return email
        uid = live.config_fields.get("userID")
        if uid:
            return t("ui.live.userid_only", prefix=str(uid)[:8])
        if live.credentials_text:
            return t("ui.live.creds_only")
        return ""

    def has_live_auth(self) -> bool:
        live = self._read_live_bundle()
        return bool(live.credentials_text) or bool(live.config_fields)

    # ---- validation ----

    def validate_name(self, name: str) -> None:
        if not VALID_NAME_RE.match(name):
            raise SwitcherError(t("err.name_invalid"))

    # ---- main operations ----

    def save_account(self, name: str) -> None:
        self.validate_name(name)
        live = self._read_live_bundle()
        if not live.credentials_text:
            # config_fields (oauthAccount/userID) can linger in ~/.claude.json
            # even after .credentials.json is gone or was never written this
            # session. Saving that alone would produce a bundle that looks
            # like an account but can never be warmed up or switched to
            # without wiping the live credentials — reject it instead.
            raise SwitcherError(t("err.nothing_to_save"))
        live.name = name
        live.saved_at = datetime.now().isoformat(timespec="seconds")
        _write_bundle(self._bundle_path(name), live)

    def switch_account(self, name: str) -> tuple[bool, str | None]:
        """Apply account ``name`` to live state. Returns (switched, previous_name)."""
        target_path = self._bundle_path(name)
        if not target_path.exists():
            raise SwitcherError(t("err.account_not_saved", name=name))
        target = _read_bundle(target_path)
        if not target.credentials_text:
            # Applying this bundle would delete the live .credentials.json
            # (see _apply_bundle) with no way back short of logging into
            # Claude Code again. Refuse rather than silently log the user out.
            raise SwitcherError(t("err.no_credentials_to_switch", name=name))

        previous = self.current_account_name()
        if previous == name:
            # Still refresh saved copy with latest live state (credentials rotate).
            self.save_account(name)
            return False, previous

        self.safety_snapshot(f"before-switch-to-{name}")

        if previous:
            try:
                # Auto-save in case live credentials drifted from the saved copy.
                self.save_account(previous)
            except SwitcherError:
                # Live state for `previous` has no credentials right now (it
                # may have gone stale between switches) — nothing new to
                # capture, so leave its existing saved bundle untouched
                # rather than overwrite it with a broken one.
                pass

        self._apply_bundle(target)
        return True, previous

    def delete_account(self, name: str) -> None:
        path = self._bundle_path(name)
        if not path.exists():
            raise SwitcherError(t("err.account_not_found", name=name))
        if self.current_account_name() == name:
            raise SwitcherError(t("err.cannot_delete_active"))
        path.unlink()
        self._forget_warmup(name)

    def rename_account(self, old: str, new: str) -> None:
        self.validate_name(new)
        if old == new:
            return
        old_path = self._bundle_path(old)
        new_path = self._bundle_path(new)
        if not old_path.exists():
            raise SwitcherError(t("err.account_not_found", name=old))
        if new_path.exists():
            raise SwitcherError(t("err.account_exists", name=new))
        bundle = _read_bundle(old_path)
        bundle.name = new
        _write_bundle(new_path, bundle)
        old_path.unlink()
        cache = self._read_warmup_cache()
        if old in cache:
            cache[new] = cache.pop(old)
            _atomic_write_text(
                self.warmup_cache_path,
                json.dumps(cache, indent=2, ensure_ascii=False),
            )

    # ---- portable export/import ----

    def export_config(self, path: Path) -> int:
        """Write every saved account bundle + language choice into one file.

        Safety snapshots are excluded — they're an internal safety net, not
        a "profile" to carry to another machine. Returns the number of
        accounts written.
        """
        accounts: list[dict[str, Any]] = []
        for p in sorted(self.backup_dir.glob(f"*{BUNDLE_SUFFIX}")):
            name = p.name[: -len(BUNDLE_SUFFIX)]
            if not name or name.startswith("."):
                continue
            try:
                accounts.append(_read_bundle(p).to_json())
            except (OSError, json.JSONDecodeError):
                continue

        payload = {
            "format": CONFIG_FORMAT,
            "format_version": CONFIG_FORMAT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "generator": f"claude-switcher/{__version__}",
            "settings": {"lang": current_choice()},
            "accounts": accounts,
        }
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False))
        return len(accounts)

    def import_config(self, path: Path, overwrite: bool = False) -> ImportResult:
        """Load accounts + language choice from a file written by ``export_config``.

        Accounts whose name already exists are skipped unless ``overwrite``
        is set, in which case they're replaced with the imported bundle.
        """
        source = Path(path).expanduser()
        if not source.is_file():
            raise SwitcherError(t("err.config_not_found", path=str(source)))
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SwitcherError(t("err.bad_config", path=str(source))) from e
        if not isinstance(raw, dict) or raw.get("format") != CONFIG_FORMAT:
            raise SwitcherError(t("err.bad_config", path=str(source)))

        imported: list[str] = []
        skipped: list[str] = []
        overwritten: list[str] = []
        for entry in raw.get("accounts") or []:
            if not isinstance(entry, dict):
                continue
            bundle = _Bundle.from_json(entry)
            name = bundle.name
            if not name or not VALID_NAME_RE.match(name):
                continue
            target = self._bundle_path(name)
            if target.exists() and not overwrite:
                skipped.append(name)
                continue
            was_existing = target.exists()
            _write_bundle(target, bundle)
            (overwritten if was_existing else imported).append(name)

        language: str | None = None
        settings = raw.get("settings")
        if isinstance(settings, dict):
            lang = settings.get("lang")
            if lang in LANGUAGE_CHOICES:
                set_language(lang)
                language = lang

        return ImportResult(
            imported=tuple(imported),
            skipped=tuple(skipped),
            overwritten=tuple(overwritten),
            language=language,
        )

    # ---- warm-up ----

    def warm_account(self, name: str) -> WarmupSnapshot:
        """Ping Haiku 4.5 with the account's OAuth bundle.

        Rotated tokens are written back to the bundle, and to the live
        ``.credentials.json`` when the account is currently active. The
        resulting snapshot is cached so the TUI can show timers between
        runs.
        """
        path = self._bundle_path(name)
        if not path.exists():
            raise SwitcherError(t("err.account_not_saved", name=name))
        bundle = _read_bundle(path)
        if not bundle.credentials_text:
            raise SwitcherError(t("err.no_credentials_to_warm", name=name))

        was_active = self.current_account_name() == name
        snapshot, refreshed_text = warm_credentials(bundle.credentials_text)

        if refreshed_text and refreshed_text != bundle.credentials_text:
            bundle.credentials_text = refreshed_text
            _write_bundle(path, bundle)
            if was_active:
                self.claude_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(self.credentials_path, refreshed_text)

        self._update_warmup_cache(name, snapshot)
        return snapshot

    def get_warmup_snapshot(self, name: str) -> WarmupSnapshot | None:
        cache = self._read_warmup_cache()
        raw = cache.get(name)
        if not raw:
            return None
        try:
            return WarmupSnapshot.from_json(raw)
        except (KeyError, ValueError, TypeError):
            return None

    def warmup_snapshots(self) -> dict[str, WarmupSnapshot]:
        cache = self._read_warmup_cache()
        out: dict[str, WarmupSnapshot] = {}
        for name, raw in cache.items():
            try:
                out[name] = WarmupSnapshot.from_json(raw)
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _read_warmup_cache(self) -> dict[str, Any]:
        if not self.warmup_cache_path.exists():
            return {}
        try:
            data = json.loads(self.warmup_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _update_warmup_cache(self, name: str, snapshot: WarmupSnapshot) -> None:
        cache = self._read_warmup_cache()
        cache[name] = snapshot.to_json()
        # Drop entries for accounts that no longer exist on disk.
        live_names = {p.name[: -len(BUNDLE_SUFFIX)]
                      for p in self.backup_dir.glob(f"*{BUNDLE_SUFFIX}")
                      if not p.name.startswith(".")}
        cache = {k: v for k, v in cache.items() if k in live_names}
        _atomic_write_text(
            self.warmup_cache_path,
            json.dumps(cache, indent=2, ensure_ascii=False),
        )

    # ---- safety snapshots ----

    def safety_snapshot(self, label: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean = re.sub(r"[^A-Za-z0-9_.\-]", "_", label)[:80]
        path = self.safety_dir / f"{ts}_{clean}{BUNDLE_SUFFIX}"
        live = self._read_live_bundle()
        live.name = f"snapshot:{clean}"
        live.saved_at = datetime.now().isoformat(timespec="seconds")
        _write_bundle(path, live)
        self._prune_snapshots()
        return path

    def list_snapshots(self) -> list[Snapshot]:
        items: list[Snapshot] = []
        for path in self.safety_dir.glob(f"*{BUNDLE_SUFFIX}"):
            try:
                bundle = _read_bundle(path)
            except (OSError, json.JSONDecodeError):
                continue
            stat = path.stat()
            label = path.name[: -len(BUNDLE_SUFFIX)]
            oauth = bundle.config_fields.get("oauthAccount") or {}
            summary = _peek(oauth, "emailAddress") or (
                t("ui.snapshot.no_auth") if not bundle.credentials_text
                else t("ui.snapshot.no_email")
            )
            items.append(
                Snapshot(
                    name=path.name,
                    path=path,
                    created=datetime.fromtimestamp(stat.st_mtime),
                    size_bytes=stat.st_size,
                    label=label,
                    summary=summary,
                )
            )
        items.sort(key=lambda s: s.created, reverse=True)
        return items

    def restore_snapshot(self, snapshot_file_name: str) -> None:
        path = self.safety_dir / snapshot_file_name
        if not path.is_file():
            raise SwitcherError(t("err.snapshot_not_found", name=snapshot_file_name))
        self.safety_snapshot(f"before-restore")
        bundle = _read_bundle(path)
        self._apply_bundle(bundle)

    def _forget_warmup(self, name: str) -> None:
        cache = self._read_warmup_cache()
        if name in cache:
            cache.pop(name, None)
            _atomic_write_text(
                self.warmup_cache_path,
                json.dumps(cache, indent=2, ensure_ascii=False),
            )

    def _prune_snapshots(self) -> None:
        snaps = self.list_snapshots()
        for old in snaps[MAX_SAFETY_SNAPSHOTS:]:
            try:
                old.path.unlink()
            except OSError:
                pass

    # ---- read/write live state ----

    def _read_live_bundle(self) -> _Bundle:
        bundle = _Bundle()
        if self.credentials_path.exists():
            try:
                bundle.credentials_text = self.credentials_path.read_text(encoding="utf-8")
            except OSError:
                bundle.credentials_text = None
        if self.claude_config.exists():
            try:
                cfg = json.loads(self.claude_config.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cfg = {}
            if isinstance(cfg, dict):
                bundle.config_fields = {
                    f: cfg[f] for f in AUTH_FIELDS if f in cfg
                }
        return bundle

    def _apply_bundle(self, bundle: _Bundle) -> None:
        """Write bundle's auth state onto disk atomically.

        Merge whitelisted fields into ``.claude.json`` preserving everything
        else; then replace ``.credentials.json``.
        """
        # 1) merge into ~/.claude.json
        cfg: dict[str, Any] = {}
        if self.claude_config.exists():
            try:
                loaded = json.loads(self.claude_config.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg = loaded
            except (OSError, json.JSONDecodeError):
                cfg = {}
        for f in AUTH_FIELDS:
            if f in bundle.config_fields:
                cfg[f] = bundle.config_fields[f]
            else:
                cfg.pop(f, None)
        _atomic_write_text(
            self.claude_config,
            json.dumps(cfg, indent=2, ensure_ascii=False),
        )

        # 2) replace ~/.claude/.credentials.json
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        if bundle.credentials_text is None:
            if self.credentials_path.exists():
                try:
                    self.credentials_path.unlink()
                except OSError:
                    pass
        else:
            _atomic_write_text(self.credentials_path, bundle.credentials_text)


# ---- module helpers ----

def _peek(obj: Any, key: str) -> str | None:
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _read_bundle(path: Path) -> _Bundle:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SwitcherError(t("err.bad_bundle", name=path.name))
    return _Bundle.from_json(raw)


def _write_bundle(path: Path, bundle: _Bundle) -> None:
    payload = json.dumps(bundle.to_json(), indent=2, ensure_ascii=False)
    _atomic_write_text(path, payload)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _file_mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.min


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024
    return f"{n} B"


def humanize_age(when: datetime) -> str:
    if when == datetime.min:
        return "—"
    delta = datetime.now() - when
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return t("time.now")
    if seconds < 3600:
        return t("time.min", n=seconds // 60)
    if seconds < 86400:
        return t("time.hour", n=seconds // 3600)
    days = seconds // 86400
    if days < 30:
        return t("time.day", n=days)
    if days < 365:
        return t("time.month", n=days // 30)
    return t("time.year", n=days // 365)
