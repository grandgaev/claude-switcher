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
| `e`       | Export all accounts + settings to one file |
| `i`       | Import accounts + settings from a file |
| `F5`      | Refresh                               |
| `?`       | Help                                  |
| `q`       | Quit                                  |

The account list shows two extra columns: **5h** (session window) and **Week** (weekly window) with `% used · resets in …`. They populate after the first warm-up and survive restarts (cached in `~/.claude-accounts/.warmup-cache.json`). Every saved account is warmed automatically every 5 minutes so these numbers stay live without pressing `w`/`W` — if a window's reset time has passed and no fresh warm-up has landed yet, the cell shows **stale** instead of a frozen, possibly-wrong percentage.

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
claude-switcher export accounts.cswitchconfig       # bundle everything into one file
claude-switcher import accounts.cswitchconfig       # load it on this (or another) machine
claude-switcher import accounts.cswitchconfig --overwrite  # replace name conflicts too
```

### Warm-up

`w` (or `claude-switcher warm`) sends one short `hi` to `claude-haiku-4-5` using the saved OAuth token of the account, then captures the `anthropic-ratelimit-unified-*` response headers so the TUI can show:

- **5h session** — how much of the 5-hour window is spent and when it resets.
- **Weekly** — same for the 7-day window (with the optional Opus weekly bucket if Anthropic returns one).

If the access token is close to expiring, it is refreshed via `https://console.anthropic.com/v1/oauth/token` before the ping and the new token is written back to the bundle (and to live `~/.claude/.credentials.json` if the warmed account is currently active).

Every saved account with credentials is also warmed automatically every 5 minutes while the TUI is open, so usage % and reset timers stay current without manual `w`/`W` presses. This does make a real (tiny) network request per account every 5 minutes — expected behavior, not a background data leak (see the FAQ below).

### Export / Import — moving your accounts to another machine

`e` (or `claude-switcher export <path>`) bundles every saved account plus your language setting into a single file — any extension works, but the app suggests `.cswitchconfig` so it's recognizable at a glance. Safety snapshots are not included; they're an internal undo log, not a profile you'd want to carry around.

`i` (or `claude-switcher import <path>`) loads that file back — on the same machine or a different one, Windows/Linux/macOS interchangeably, since the format is plain cross-platform JSON. Accounts whose name already exists are skipped by default; pass `--overwrite` on the CLI, or confirm the on-screen prompt in the TUI, to replace them instead. The language setting from the file is applied immediately.

```
{
  "format": "claude-switcher-config",
  "format_version": 1,
  "exported_at": "2026-07-09T12:00:00",
  "generator": "claude-switcher/0.2.0",
  "settings": { "lang": "ru" },
  "accounts": [ { "name": "work-main", "saved_at": "...", "credentials_text": "...", "config_fields": {...} } ]
}
```

**Security note:** this file contains plaintext OAuth tokens, exactly like the `.account.json` bundles it's built from. Treat it like a secret — don't commit it, don't attach it to a support ticket, and move it between machines over a channel you'd trust with a password (scp, an encrypted USB drive, a password manager's file storage — not a public paste or an unencrypted email).

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
Only for warm-up. Warming an account (manually with `w`/`W`, automatically every 5 minutes, or via `claude-switcher warm`) sends a real 1-token request to Anthropic's API using that account's own OAuth token — that's how the usage %/reset timers are obtained; there's no way to read them without asking Anthropic. Nothing else the tool does touches the network: switching, saving, exporting, and importing are purely local file operations inside `~/.claude-accounts/`.

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
| `e`       | Экспортировать все аккаунты + настройки в один файл |
| `i`       | Импортировать аккаунты + настройки из файла |
| `F5`      | Обновить                                 |
| `?`       | Помощь                                   |
| `q`       | Выход                                    |

В списке аккаунтов появляются две дополнительные колонки: **5ч** (сессия) и **Неделя** (недельный лимит) с форматом `% использовано · сброс через …`. Заполняются после первого прогрева и переживают перезапуск — кешируются в `~/.claude-accounts/.warmup-cache.json`. Каждый сохранённый аккаунт автоматически прогревается раз в 5 минут, чтобы эти цифры оставались живыми без нажатия `w`/`W` — если время сброса окна уже прошло, а свежего прогрева ещё не было, ячейка показывает **устар.** вместо замороженного и, возможно, неверного процента.

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
claude-switcher export accounts.cswitchconfig       # собрать всё в один файл
claude-switcher import accounts.cswitchconfig       # загрузить на этой (или другой) машине
claude-switcher import accounts.cswitchconfig --overwrite  # заодно перезаписать конфликты имён
```

### Прогрев

`w` (или `claude-switcher warm`) отправляет короткое `hi` к `claude-haiku-4-5` под сохранённым OAuth-токеном, потом ловит заголовки `anthropic-ratelimit-unified-*` чтобы показать:

- **Сессия 5ч** — сколько съедено за 5-часовое окно и когда оно обнулится.
- **Неделя** — то же для 7-дневного окна (и отдельно weekly Opus, если Anthropic его вернёт).

Если access-token близок к истечению, он рефрешится через `https://console.anthropic.com/v1/oauth/token` перед пингом и записывается обратно в бандл (а если аккаунт сейчас активный — ещё и в живой `~/.claude/.credentials.json`).

Пока открыт TUI, каждый сохранённый аккаунт с credentials автоматически прогревается раз в 5 минут, так что проценты использования и таймеры сброса остаются актуальными без ручных нажатий `w`/`W`. Это означает реальный (крошечный) сетевой запрос на аккаунт раз в 5 минут — так и задумано, это не скрытая утечка данных (см. FAQ ниже).

### Экспорт / импорт — перенос аккаунтов на другую машину

`e` (или `claude-switcher export <path>`) собирает все сохранённые аккаунты и язык интерфейса в один файл — подойдёт любое расширение, но приложение предлагает `.cswitchconfig`, чтобы файл было легко узнать. Safety-snapshots туда не попадают — это внутренний журнал отмены действий, а не профиль для переноса.

`i` (или `claude-switcher import <path>`) загружает такой файл обратно — на этой же машине или на другой, хоть Windows, хоть Linux, хоть macOS, потому что формат — обычный кроссплатформенный JSON. Аккаунты, чьё имя уже занято, по умолчанию пропускаются; передай `--overwrite` в CLI или подтверди запрос в TUI, чтобы перезаписать их. Язык из файла применяется сразу.

```
{
  "format": "claude-switcher-config",
  "format_version": 1,
  "exported_at": "2026-07-09T12:00:00",
  "generator": "claude-switcher/0.2.0",
  "settings": { "lang": "ru" },
  "accounts": [ { "name": "work-main", "saved_at": "...", "credentials_text": "...", "config_fields": {...} } ]
}
```

**Важно про безопасность:** этот файл содержит OAuth-токены в открытом виде — точно так же, как и бандлы `.account.json`, из которых он собран. Обращайся с ним как с секретом: не коммить его, не прикладывай к тикету в поддержку и переноси между машинами по каналу, которому доверил бы пароль (scp, зашифрованная флешка, файловое хранилище менеджера паролей — но не публичный paste и не письмо без шифрования).

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
Только для прогрева. Прогрев аккаунта (вручную `w`/`W`, автоматически раз в 5 минут или через `claude-switcher warm`) отправляет настоящий запрос на 1 токен к API Anthropic под OAuth-токеном этого аккаунта — именно так добываются проценты использования и таймеры сброса, иначе их не узнать. Всё остальное, что делает утилита — переключение, сохранение, экспорт и импорт — это чисто локальные файловые операции внутри `~/.claude-accounts/`, без сети.

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
