# Claude Switcher

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()

> A keyboard-driven TUI to juggle multiple Claude Code accounts on the same machine — without losing project history, memory, or settings.

`[`[English](#english)`]` · `[`[Русский](#русский)`]`

---

## English

### Why?

Claude Code stores your authentication in two places:

- `~/.claude/.credentials.json` — the live OAuth tokens
- a few fields inside `~/.claude.json` (`oauthAccount`, `userID`, `customApiKeyResponses`)

Everything else in `~/.claude/` and `~/.claude.json` — project history, memory (`CLAUDE.md`, `memory/`), todos, MCP servers, IDE settings, tips — is **shared state you do not want to lose** when you sign into a second account.

Existing approaches (creating a separate `HOME` per account, or copying the whole `.claude/` directory) sacrifice that shared state. Claude Switcher does the opposite: **it moves only authentication**, atomically, and leaves everything else completely untouched.

### What does it do?

- **Saves** the live authentication as a named bundle (`work-main`, `personal`, `client-acme`, …).
- **Switches** between saved accounts with one key — the swap is two atomic file writes.
- **Auto-saves** the current bundle on every switch, so rotated refresh tokens never get lost.
- **Safety snapshots** of the live auth are taken before every switch / restore; the last 20 are kept.
- **Detects** the active account by `oauthAccount.accountUuid` (stable across token rotation), with fallback to email / userID / raw credentials.
- **Hot reload** — start a new Claude Code session and it picks up the new account immediately. No restart of anything else; no project rebuild; your active sessions are unaffected because all their state is shared.
- **Localized** — English and Russian, autodetect from system locale, switchable on the fly.
- **Read-mostly UI** — full keyboard, vim-like cursor, instant feedback, no scary dialogs.

### Install

#### Option A — pipx (recommended, isolated)

```bash
pipx install git+https://github.com/grandgaev/claude-switcher.git
claude-switcher        # or shorter: cswitch
```

#### Option B — pip

```bash
pip install git+https://github.com/grandgaev/claude-switcher.git
claude-switcher
```

#### Option C — standalone `.exe` (Windows, no Python required)

Grab `claude-switcher.exe` from the [latest release](https://github.com/grandgaev/claude-switcher/releases/latest) and run it from anywhere.

#### Option D — from source

```bash
git clone https://github.com/grandgaev/claude-switcher.git
cd claude-switcher
pip install -e .
claude-switcher
```

### Usage

#### TUI

```bash
claude-switcher          # or: cswitch
```

| Key       | Action                                |
| --------- | ------------------------------------- |
| `↑` / `↓` | Navigate accounts                     |
| `Enter`   | Switch to highlighted account         |
| `s`       | Save current auth as a new account    |
| `r`       | Rename selected                       |
| `d`       | Delete selected (active is protected) |
| `b`       | Restore from a safety snapshot        |
| `w`       | Warm up selected (ping Haiku 4.5)     |
| `W`       | Warm up every saved account           |
| `l`       | Change interface language             |
| `F5`      | Refresh                               |
| `?`       | Help                                  |
| `q`       | Quit                                  |

The account list shows two extra columns: **5h** (session window) and **Week** (weekly window) with `% used · resets in …`. They populate after the first warm-up and survive restarts (cached in `~/.claude-accounts/.warmup-cache.json`).

#### CLI (scriptable)

```bash
claude-switcher list                   # all accounts
claude-switcher status                 # which one is active
claude-switcher switch work-main       # apply named account
claude-switcher save personal          # snapshot current auth
claude-switcher warm                   # ping Haiku 4.5 with every account
claude-switcher warm work-main         # ping just one
claude-switcher lang en                # persist language
claude-switcher --lang ru list         # one-off override
```

### Warm-up

`w` (or `claude-switcher warm`) sends one short `hi` to `claude-haiku-4-5` using the saved OAuth token of the account, then captures the `anthropic-ratelimit-unified-*` response headers so the TUI can show:

- **5h session** — how much of the 5-hour window is spent and when it resets.
- **Weekly** — same for the 7-day window (with the optional Opus weekly bucket if Anthropic returns one).

If the access token is close to expiring, it is refreshed via `https://console.anthropic.com/v1/oauth/token` before the ping and the new token is written back to the bundle (and to live `~/.claude/.credentials.json` if the warmed account is currently active).

### How it works

```
~/.claude-accounts/
  work-main.account.json     ← JSON bundle: credentials + oauthAccount/userID/…
  personal.account.json
  .settings.json             ← language preference
  .safety-snapshots/
    20260609-143012_before-switch-to-personal.account.json
    …
```

A **switch** is:
1. Take a safety snapshot of the live auth.
2. Auto-save the previous account if it was detected (captures rotated tokens).
3. Atomically write `.credentials.json` and merge whitelisted fields into `.claude.json`.

Everything else in `.claude.json` (`projects`, `mcpServers`, `tipsHistory`, `numStartups`, …) and everything inside `~/.claude/` (`memory/`, `todos/`, `projects/`, `CLAUDE.md`) is **never read or written** during a switch.

### FAQ

**Q: Will I lose my project history when I switch accounts?**
No. Project history lives in `~/.claude.json` under `projects` and inside `~/.claude/projects/` — neither is touched. The same applies to memory, todos, and MCP servers.

**Q: My Claude Code is running. Do I need to restart it after switching?**
A new chat session will use the new account immediately. An already-open session will continue to use the credentials it was started with until its OAuth token expires.

**Q: What if I have multiple OAuth tokens that rotate?**
Every switch auto-saves the previous account first, so the freshest refresh token always ends up in the saved bundle.

**Q: Is my data sent anywhere?**
No. The tool is fully local. Tokens never leave your disk; the only files written are inside `~/.claude-accounts/`.

---

## Русский

### Зачем?

Claude Code хранит твою авторизацию в двух местах:

- `~/.claude/.credentials.json` — текущие OAuth-токены
- несколько полей в `~/.claude.json` (`oauthAccount`, `userID`, `customApiKeyResponses`)

Всё остальное в `~/.claude/` и `~/.claude.json` — история проектов, память (`CLAUDE.md`, `memory/`), todos, MCP-серверы, настройки IDE, подсказки — это **общее состояние, которое ты не хочешь терять** при логине во второй аккаунт.

Существующие подходы (отдельный `HOME` под каждый аккаунт или копирование всей папки `.claude/`) убивают это общее состояние. Claude Switcher делает наоборот: **переключает только авторизацию**, атомарно, и не трогает больше ничего.

### Что умеет

- **Сохраняет** живую авторизацию под именем (`work-main`, `personal`, `client-acme`, …).
- **Переключается** между сохранёнными аккаунтами одной клавишей — это две атомарные записи в файл.
- **Авто-сохраняет** текущий аккаунт перед каждым переключением, чтобы ротированные refresh-токены не терялись.
- **Safety snapshots** живой авторизации создаются перед каждым switch/restore; хранятся последние 20.
- **Детектит** активный аккаунт по `oauthAccount.accountUuid` (стабилен при ротации токенов), fallback на email / userID / raw credentials.
- **Горячее переключение** — запусти новую сессию Claude Code, и она сразу подхватит новый аккаунт. Ничего перезапускать не нужно; ни проектная сборка, ни активные сессии не страдают, потому что их состояние общее.
- **Локализация** — английский и русский, автодетект по системной локали, переключение на лету.
- **Удобный UI** — полностью клавиатурный, vim-like курсор, мгновенный фидбэк, никаких пугающих диалогов.

### Установка

#### Вариант A — pipx (рекомендуется, изолированно)

```bash
pipx install git+https://github.com/grandgaev/claude-switcher.git
claude-switcher        # короткое имя: cswitch
```

#### Вариант B — pip

```bash
pip install git+https://github.com/grandgaev/claude-switcher.git
claude-switcher
```

#### Вариант C — готовый `.exe` (Windows, Python не нужен)

Скачай `claude-switcher.exe` из [последнего релиза](https://github.com/grandgaev/claude-switcher/releases/latest) и запусти откуда угодно.

#### Вариант D — из исходников

```bash
git clone https://github.com/grandgaev/claude-switcher.git
cd claude-switcher
pip install -e .
claude-switcher
```

### Использование

#### TUI

```bash
claude-switcher          # или: cswitch
```

| Клавиша   | Действие                                 |
| --------- | ---------------------------------------- |
| `↑` / `↓` | Навигация по аккаунтам                   |
| `Enter`   | Переключиться на выделенный              |
| `s`       | Сохранить текущую авторизацию            |
| `r`       | Переименовать выделенный                 |
| `d`       | Удалить выделенный (активный защищён)    |
| `b`       | Восстановить из safety-snapshot          |
| `w`       | Прогреть выделенный (ping Haiku 4.5)     |
| `W`       | Прогреть все сохранённые аккаунты        |
| `l`       | Сменить язык интерфейса                  |
| `F5`      | Обновить                                 |
| `?`       | Помощь                                   |
| `q`       | Выход                                    |

В списке аккаунтов появляются две дополнительные колонки: **5ч** (сессия) и **Неделя** (недельный лимит) с форматом `% использовано · сброс через …`. Заполняются после первого прогрева и переживают перезапуск — кешируются в `~/.claude-accounts/.warmup-cache.json`.

#### CLI (скриптовый)

```bash
claude-switcher list                   # все аккаунты
claude-switcher status                 # кто сейчас активен
claude-switcher switch work-main       # применить аккаунт
claude-switcher save personal          # сохранить текущую авторизацию
claude-switcher warm                   # прогреть все аккаунты
claude-switcher warm work-main         # прогреть один
claude-switcher lang ru                # сохранить язык
claude-switcher --lang en list         # разовое переопределение
```

### Прогрев

`w` (или `claude-switcher warm`) отправляет короткое `hi` к `claude-haiku-4-5` под сохранённым OAuth-токеном, потом ловит заголовки `anthropic-ratelimit-unified-*` чтобы показать:

- **Сессия 5ч** — сколько съедено за 5-часовое окно и когда оно обнулится.
- **Неделя** — то же для 7-дневного окна (и отдельно weekly Opus, если Anthropic его вернёт).

Если access-token близок к истечению, он рефрешится через `https://console.anthropic.com/v1/oauth/token` перед пингом и записывается обратно в бандл (а если аккаунт сейчас активный — ещё и в живой `~/.claude/.credentials.json`).

### Как это устроено

```
~/.claude-accounts/
  work-main.account.json     ← JSON-бандл: credentials + oauthAccount/userID/…
  personal.account.json
  .settings.json             ← выбранный язык
  .safety-snapshots/
    20260609-143012_before-switch-to-personal.account.json
    …
```

**Переключение** — это:
1. Snapshot живой авторизации.
2. Авто-сохранение предыдущего аккаунта если он был определён (захватывает свежие токены).
3. Атомарная запись `.credentials.json` и merge whitelisted-полей в `.claude.json`.

Всё остальное в `.claude.json` (`projects`, `mcpServers`, `tipsHistory`, `numStartups`, …) и всё внутри `~/.claude/` (`memory/`, `todos/`, `projects/`, `CLAUDE.md`) **не читается и не пишется** во время переключения.

### FAQ

**В: Потеряется ли история проектов при переключении?**
Нет. История лежит в `~/.claude.json` под ключом `projects` и в папке `~/.claude/projects/` — ничего из этого не трогается. То же относится к памяти, todos и MCP-серверам.

**В: Claude Code запущен. Нужно его перезапускать после переключения?**
Новая сессия чата сразу возьмёт новый аккаунт. Уже открытая сессия продолжит работать с теми токенами, с которыми стартовала, пока её OAuth-токен не истечёт.

**В: А если refresh-токены ротируются?**
Каждое переключение начинается с авто-сохранения предыдущего аккаунта, так что самый свежий refresh-токен всегда оседает в сохранённом бандле.

**В: Данные куда-нибудь отправляются?**
Нет. Утилита полностью локальная. Токены не покидают диск; всё что пишется — в `~/.claude-accounts/`.

---

## Contributing

PRs welcome. The code is small and well-typed:

- `core.py` — the manager (no UI deps)
- `warming.py` — OAuth ping + rate-limit header parser (stdlib only)
- `app.py` — Textual TUI
- `i18n.py` — translations; add a language by appending to `TRANSLATIONS` and updating `LANGUAGE_CHOICES`
- `modals.py` — modal screens

Run the local TUI:

```bash
pip install -e .
claude-switcher
```

## License

MIT — see [LICENSE](LICENSE).
