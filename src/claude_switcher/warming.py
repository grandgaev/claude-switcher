"""Account warm-up: ping Haiku 4.5 with OAuth bundle and parse rate-limit headers.

Sends a 1-token ``hi`` to ``claude-haiku-4-5`` on behalf of the saved
account, then captures the ``anthropic-ratelimit-unified-*`` headers so the
TUI can show how much of the 5h session window and the weekly window is
already spent and when each one resets.

The warm-up does **not** switch the active account: it works directly off
``_Bundle.credentials_text`` and refreshes the OAuth token in-place when
expired. If the call is made against the live account the freshly
refreshed token is also written back to ``~/.claude/.credentials.json``.
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .i18n import t

# Public Claude Code OAuth client id — same value baked into the CLI.
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
HAIKU_MODEL = "claude-haiku-4-5"
ANTHROPIC_BETA = "oauth-2025-04-20"
ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = "claude-switcher/0.2 (warmup)"
REQUEST_TIMEOUT = 25.0

# Header families we care about. Anthropic exposes both a 5h session window
# and a 7d weekly window for Claude.ai / Claude Code subscriptions. Opus has
# its own weekly bucket when the account hits it.
WINDOW_PREFIXES: dict[str, str] = {
    "five_hour": "anthropic-ratelimit-unified-5h",
    "weekly": "anthropic-ratelimit-unified-7d",
    "weekly_opus": "anthropic-ratelimit-unified-7d_opus",
}


class WarmupError(Exception):
    """Anything that prevented a successful warm-up call."""


@dataclass(frozen=True)
class LimitWindow:
    status: str | None          # allowed / rejected / allowed_warning / …
    utilization: float | None   # 0.0-1.0 — share already spent
    remaining: int | None       # legacy / alternate spelling: 0-100 left
    reset_at: datetime | None   # absolute UTC

    @property
    def used_pct(self) -> int | None:
        if self.utilization is not None:
            return max(0, min(100, int(round(self.utilization * 100))))
        if self.remaining is not None:
            return max(0, min(100, 100 - int(self.remaining)))
        return None


@dataclass(frozen=True)
class WarmupSnapshot:
    checked_at: datetime
    ok: bool
    error: str | None
    five_hour: LimitWindow | None
    weekly: LimitWindow | None
    weekly_opus: LimitWindow | None

    def to_json(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(),
            "ok": self.ok,
            "error": self.error,
            "windows": {
                key: _window_to_json(getattr(self, key))
                for key in ("five_hour", "weekly", "weekly_opus")
            },
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "WarmupSnapshot":
        windows = raw.get("windows") or {}
        return cls(
            checked_at=_parse_iso(raw.get("checked_at")) or datetime.min,
            ok=bool(raw.get("ok")),
            error=raw.get("error"),
            five_hour=_window_from_json(windows.get("five_hour")),
            weekly=_window_from_json(windows.get("weekly")),
            weekly_opus=_window_from_json(windows.get("weekly_opus")),
        )


# ---- public API ----

def warm_credentials(credentials_text: str) -> tuple[WarmupSnapshot, str | None]:
    """Send the ping; refresh OAuth on the fly if needed.

    Returns ``(snapshot, refreshed_text)`` where ``refreshed_text`` is a new
    ``.credentials.json`` string when the access token was rotated and
    ``None`` otherwise. The caller persists it.
    """
    creds = _load_credentials(credentials_text)
    refreshed_text: str | None = None

    if _needs_refresh(creds):
        try:
            creds = _refresh_oauth(creds)
            refreshed_text = json.dumps({"claudeAiOauth": creds}, indent=2, ensure_ascii=False)
        except WarmupError as exc:
            return _failure(str(exc)), None

    access_token = creds.get("accessToken")
    if not access_token:
        return _failure("no access token in bundle"), refreshed_text

    try:
        headers = _call_haiku(access_token)
    except WarmupError as exc:
        return _failure(str(exc)), refreshed_text

    snap = WarmupSnapshot(
        checked_at=datetime.now(timezone.utc),
        ok=True,
        error=None,
        five_hour=_parse_window(headers, WINDOW_PREFIXES["five_hour"]),
        weekly=_parse_window(headers, WINDOW_PREFIXES["weekly"]),
        weekly_opus=_parse_window(headers, WINDOW_PREFIXES["weekly_opus"]),
    )
    return snap, refreshed_text


# ---- HTTP helpers ----

def _call_haiku(access_token: str) -> dict[str, str]:
    body = json.dumps(
        {
            "model": HAIKU_MODEL,
            "max_tokens": 8,
            "system": "You are a no-op endpoint. Reply with a single character.",
            "messages": [{"role": "user", "content": "hi"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        MESSAGES_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": ANTHROPIC_BETA,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_ctx()) as resp:
            # Read & discard body so the socket can be reused / closed cleanly.
            resp.read()
            return _normalize_headers(resp.headers.items())
    except urllib.error.HTTPError as e:
        # The rate-limit response (429) still carries the headers we want.
        try:
            payload = e.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            payload = ""
        if e.code == 429:
            return _normalize_headers(e.headers.items())
        raise WarmupError(f"HTTP {e.code}: {payload or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as e:
        raise WarmupError(f"network: {e}") from e


def _refresh_oauth(creds: dict[str, Any]) -> dict[str, Any]:
    refresh_token = creds.get("refreshToken")
    if not refresh_token:
        raise WarmupError("token expired and no refresh token in bundle")

    body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_CODE_CLIENT_ID,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = e.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            payload = ""
        raise WarmupError(f"refresh HTTP {e.code}: {payload or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as e:
        raise WarmupError(f"refresh network: {e}") from e
    except (ValueError, KeyError) as e:
        raise WarmupError(f"refresh bad response: {e}") from e

    access = data.get("access_token")
    if not access:
        raise WarmupError("refresh response missing access_token")
    expires_in = int(data.get("expires_in") or 0)
    updated = dict(creds)
    updated["accessToken"] = access
    updated["refreshToken"] = data.get("refresh_token") or refresh_token
    if expires_in:
        updated["expiresAt"] = int(time.time() * 1000) + expires_in * 1000
    if data.get("scope") and "scopes" in updated:
        scopes = data["scope"].split() if isinstance(data["scope"], str) else data["scope"]
        if scopes:
            updated["scopes"] = scopes
    return updated


# ---- parsing helpers ----

def _load_credentials(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as e:
        raise WarmupError(f"bundle has no parseable credentials ({e})") from e
    if not isinstance(payload, dict):
        raise WarmupError("credentials.json must be an object")
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise WarmupError("credentials.json missing claudeAiOauth section")
    return dict(oauth)


def _needs_refresh(creds: dict[str, Any]) -> bool:
    exp = creds.get("expiresAt")
    if not isinstance(exp, (int, float)):
        return False
    # ``expiresAt`` is stored in milliseconds; refresh 5 minutes early so the
    # token can't expire mid-flight.
    return (exp / 1000.0) - time.time() < 300


def _parse_window(headers: dict[str, str], prefix: str) -> LimitWindow | None:
    status = headers.get(f"{prefix}-status")
    utilization_raw = headers.get(f"{prefix}-utilization")
    remaining_raw = headers.get(f"{prefix}-remaining")
    reset_raw = headers.get(f"{prefix}-reset")
    if status is None and utilization_raw is None and remaining_raw is None and reset_raw is None:
        return None
    return LimitWindow(
        status=status,
        utilization=_to_float(utilization_raw),
        remaining=_to_int(remaining_raw),
        reset_at=_to_reset(reset_raw),
    )


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _to_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _to_reset(raw: str | None) -> datetime | None:
    """Reset values can be epoch seconds, ISO timestamps, or HTTP-dates."""
    if not raw:
        return None
    raw = raw.strip()
    # Epoch seconds (most common from Anthropic).
    try:
        n = float(raw)
        if n > 10_000_000_000:  # likely milliseconds
            n /= 1000.0
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO-8601.
    try:
        if raw.endswith("Z"):
            raw_iso = raw[:-1] + "+00:00"
        else:
            raw_iso = raw
        return datetime.fromisoformat(raw_iso)
    except ValueError:
        return None


def _normalize_headers(items) -> dict[str, str]:
    return {k.lower(): v for k, v in items}


def _failure(message: str) -> WarmupSnapshot:
    return WarmupSnapshot(
        checked_at=datetime.now(timezone.utc),
        ok=False,
        error=message,
        five_hour=None,
        weekly=None,
        weekly_opus=None,
    )


def _ssl_ctx() -> ssl.SSLContext:
    # Default verification — Anthropic uses a normal public CA.
    return ssl.create_default_context()


def _window_to_json(w: LimitWindow | None) -> dict[str, Any] | None:
    if w is None:
        return None
    return {
        "status": w.status,
        "utilization": w.utilization,
        "remaining": w.remaining,
        "reset_at": w.reset_at.isoformat() if w.reset_at else None,
    }


def _window_from_json(raw: Any) -> LimitWindow | None:
    if not isinstance(raw, dict):
        return None
    return LimitWindow(
        status=raw.get("status"),
        utilization=raw.get("utilization"),
        remaining=raw.get("remaining"),
        reset_at=_parse_iso(raw.get("reset_at")),
    )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---- formatting helpers used by the UI ----

def format_eta(reset_at: datetime | None, now: datetime | None = None) -> str:
    if reset_at is None:
        return t("time.eta.unknown")
    now = now or datetime.now(timezone.utc)
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    delta = reset_at - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return t("time.eta.now")
    if seconds < 3600:
        return t("time.eta.min", n=max(1, seconds // 60))
    if seconds < 86400:
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        return t("time.eta.hour_min", h=hours, m=minutes) if minutes else t("time.eta.hour", n=hours)
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    return t("time.eta.day_hour", d=days, h=hours) if hours else t("time.eta.day", n=days)
