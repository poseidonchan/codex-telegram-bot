# tgcodex-bot

Telegram bot that wraps the `codex` CLI to provide a stateful, private, per-chat Codex session with:

- Telegram allowlist
- Per-chat persistent session id (resume across bot restarts)
- Local + SSH machines
- Buffered "block streaming" output with typing indicator
- Inline approval UI for command execution (accept once / accept similar / reject)

## Requirements

- Python >= 3.10
- A Telegram bot token (via @BotFather)
- The `codex` CLI installed and available on each machine you run on
- Optional: `asyncssh` for SSH machines (installed by default on non-Windows)

## Quickstart

1. Install:

```bash
python -m pip install -e .
```

2. Configure:

- Option A (wizard): run:

```bash
tgcodex-bot setup --config config.yaml
```

- Option B (manual): copy `config.example.yaml` to `config.yaml` and edit it.

Then set your bot token in the env var referenced by `telegram.token_env`.

Example:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC...keep_this_secret"
```

3. Validate config:

```bash
tgcodex-bot validate-config --config config.yaml
```

4. Start:

```bash
# Background (detached). Writes PID/log files to ./.tgcodex-bot/ next to config.yaml.
tgcodex-bot start --config config.yaml

# Foreground
tgcodex-bot run --config config.yaml
```

## CLI Usage

The CLI entrypoint is `tgcodex-bot`.

### Setup Wizard

This wizard writes a starter `config.yaml`:

```bash
tgcodex-bot setup --config config.yaml
```

It will prompt you for:

- `telegram.token_env` (env var name which holds your bot token)
- `telegram.allowed_user_ids` (Telegram user IDs allowed to use the bot)
- `state.db_path` (SQLite file for bot state)
- `codex.bin` (path/name of the `codex` binary)
- Local machine `default_workdir` and `allowed_roots`
- Optional SSH machine definitions

### Validate Config

```bash
tgcodex-bot validate-config --config config.yaml
```

### Run In Background (Detached)

```bash
tgcodex-bot start --config config.yaml
tgcodex-bot status --config config.yaml
tgcodex-bot stop --config config.yaml
```

By default `start` writes:

- PID file: `./.tgcodex-bot/config.yaml.pid`
- Log file: `./.tgcodex-bot/config.yaml.log`

Tip:

```bash
tail -f .tgcodex-bot/config.yaml.log
```

### Run In Foreground

```bash
tgcodex-bot run --config config.yaml
```

## Telegram Usage

After the bot is running, open a private chat with your bot and send:

- `/start` (sanity check)
- `/menu` (command list)

### Command Reference

- `/start`: bot health check
- `/menu`: show available commands
- `/status`: show current machine/workdir/session and token telemetry
- `/botstatus`: show bot version/config basics
- `/new`: clear current session (next message starts fresh)
- `/rename <title>`: set the current session title
- `/resume`: pick a recent session to resume
- `/machine <name>`: switch machine (clears session)
- `/cd <path>`: change working directory (restricted by `allowed_roots`)
- `/approval <untrusted|on-request|on-failure|never>`: update approval policy
- `/reasoning`: toggle reasoning output (if model emits it)
- `/plan`: toggle plan mode (preprends a “plan first” instruction)
- `/compact`: compact the active session and continue in a new one
- `/model [slug] [effort]`: pick a model (and thinking level if supported)
- `/skills`: list available Codex skills on the active machine
- `/mcp`: list MCP servers configured for Codex
- `/exit`: cancel an active run and clear session state

Normal (non-command) text messages are sent to the `codex` CLI.

## Configuration

See `config.example.yaml` for a full reference.

Key fields:

- `telegram.allowed_user_ids`: only these users can interact with the bot
- `machines.default`: initial machine name (new chats)
- `machines.defs.<name>.allowed_roots`: filesystem allowlist for `/cd` and path resolution
- `machines.defs.<ssh>.codex_bin`: override path to `codex` on a remote machine (useful with nvm/Homebrew PATH issues)

Notes:

- Prefer absolute paths in config, or run the bot from the directory where `config.yaml` lives.
- In YAML, `~` expansion can be surprising; if you literally want a tilde path, quote it.

## Approvals

When Codex emits an execution approval request, the bot shows inline buttons:

- Accept once
- Accept similar (stores a trusted prefix for this session; not offered in `untrusted`)
- Reject

Policy meanings:

- `untrusted`: always ask (and keep Codex sandbox read-only; write actions are proxy-approved)
- `on-request`: only ask when Codex explicitly requests approval
- `on-failure`: ask after a failure
- `never`: never ask

## State And Sessions

The bot stores per-chat state in SQLite (`state.db_path`), including:

- current machine + workdir
- active Codex session id
- trusted prefixes for approvals
- a small session index for `/resume`

## Troubleshooting

### “No response” In Telegram

1. Verify the bot is running:

```bash
tgcodex-bot status --config config.yaml
```

2. Check logs:

```bash
tail -n 200 .tgcodex-bot/config.yaml.log
```

3. Verify allowlist:

- Your Telegram user id must be in `telegram.allowed_user_ids`.
- If it’s wrong, the bot will reply `Unauthorized` (or log denials).

4. Ensure only one instance is polling:

- Running two instances with the same token will cause `getUpdates` conflicts.

5. If remote machine is down:

- If your chat is set to an SSH machine that can’t be reached, switch to local:
  - `/machine local`

### `codex` Not Found

- Ensure `codex` is installed and in `PATH` for the machine you’re using.
- For SSH machines, consider setting `machines.defs.<name>.codex_bin` to an absolute path.

## Security Notes

- Keep your bot token secret (use env vars, do not commit it).
- Do not commit your `config.yaml` if it contains personal IDs/hostnames.
- Runtime logs are written to `./.tgcodex-bot/` and should not be committed.

## Notes

- This project expects the `codex` CLI to be installed and available on each machine.
- Network and filesystem sandboxing are controlled by `codex` settings; this bot adds an additional workdir allowlist via `allowed_roots`.
