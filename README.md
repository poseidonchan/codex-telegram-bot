# codex-telegram-bot (`tgcodex-bot`)

Run the `codex` CLI from Telegram with per-chat sessions, machine selection (local/SSH), and an approvals UI for command execution.

This is designed for private, single-operator use: you allowlist your Telegram user ID(s), then you can run Codex from your phone or desktop Telegram client.

## Features

- Private by default: Telegram user allowlist.
- Stateful: persists a per-chat Codex session ID (resume across restarts).
- Multi-machine: run on local or SSH targets.
- Safer execution UX: inline approvals for exec requests (accept once / accept similar / reject).
- Telegram-friendly output: buffered streaming with typing indicator.

## Requirements

- Python >= 3.10
- A Telegram bot token (create via @BotFather)
- The `codex` CLI installed and available on each machine you run on
- Optional: `asyncssh` for SSH machines (installed by default on non-Windows)

## Install

From the repo (recommended):

```bash
python -m pip install -e .
```

Or directly from GitHub:

```bash
python -m pip install "git+https://github.com/poseidonchan/codex-telegram-bot.git"
```

## Quickstart

1. Create a config using the setup wizard:

```bash
tgcodex-bot setup --config config.yaml
```

2. Export your bot token into the env var you chose in the wizard (default: `TELEGRAM_BOT_TOKEN`):

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC...keep_this_secret"
```

3. Validate config:

```bash
tgcodex-bot validate-config --config config.yaml
```

4. Start the bot:

```bash
# Detached background process (recommended for servers)
tgcodex-bot start --config config.yaml

# Foreground (useful for debugging)
tgcodex-bot run --config config.yaml
```

5. In Telegram, open a private chat with your bot and send:

```text
/start
/menu
```

## Configuration

For a full reference, see `config.example.yaml`.

Minimal example:

```yaml
telegram:
  token_env: TELEGRAM_BOT_TOKEN
  allowed_user_ids: [123456789]

state:
  db_path: tgcodex.sqlite3

codex:
  bin: codex
  args: []
  model: null
  sandbox: workspace-write
  approval_policy: untrusted
  skip_git_repo_check: true

machines:
  default: local
  defs:
    local:
      type: local
      default_workdir: /home/ubuntu
      allowed_roots: [/home/ubuntu, /tmp]
```

Notes:

- `telegram.allowed_user_ids` is your security boundary: if the user ID is not listed, the bot refuses requests.
- `allowed_roots` restricts `/cd` and path resolution to an allowlist.
- For SSH machines you can override the remote `codex` path with `machines.defs.<name>.codex_bin`.

## Running And Operations

### Background Mode (Detached)

```bash
tgcodex-bot start --config config.yaml
tgcodex-bot status --config config.yaml
tgcodex-bot stop --config config.yaml
```

By default the runtime artifacts are created next to your config:

- PID file: `./.tgcodex-bot/config.yaml.pid`
- Log file: `./.tgcodex-bot/config.yaml.log`

Log tail:

```bash
tail -f .tgcodex-bot/config.yaml.log
```

### Foreground Mode

```bash
tgcodex-bot run --config config.yaml
```

## Telegram Commands

Start here:

- `/start`: health check
- `/menu`: list commands
- `/status`: current machine/workdir/session + token telemetry

Session management:

- `/new`: clear the active session (next message starts fresh)
- `/rename <title>`: set the current session title
- `/resume`: pick a recent session to resume
- `/exit`: cancel an active run and clear session state

Environment:

- `/machine <name>`: switch machine (clears session)
- `/cd <path>`: change working directory (restricted by `allowed_roots`)

Run behavior:

- `/approval <untrusted|on-request|on-failure|never>`: update approval policy
- `/plan`: toggle “plan mode”
- `/reasoning`: toggle reasoning output (if enabled)
- `/compact`: compact the active session and continue in a new one
- `/model [slug] [effort]`: pick a model (and thinking level if supported)
- `/skills`: list available Codex skills (on the active machine)
- `/mcp`: list MCP servers configured for Codex

Tip:

- If you want to send a literal slash-prefixed prompt to Codex, you can type `//...` in Telegram and it will be rewritten to `/...`.

## Approvals And Safety Model

Execution approvals are shown as inline buttons:

- Accept once
- Accept similar (stores a trusted prefix for this session; not offered in `untrusted`)
- Reject

Approval policies:

- `untrusted`: always ask, and keep Codex sandbox read-only; stateful actions are proxy-approved in Telegram
- `on-request`: ask only when Codex explicitly requests approval
- `on-failure`: ask after a failure
- `never`: never ask

## Machines (Local And SSH)

You can define multiple machines and switch per chat with `/machine`.

Local machine:

- Runs on the host where `tgcodex-bot` is running.

SSH machine:

- Runs `codex` remotely via `asyncssh`.
- Requires a reachable SSH host and correct auth settings.
- If remote PATH isn’t initialized for non-interactive shells, set `machines.defs.<name>.codex_bin` to an absolute `codex` path.

## Troubleshooting

### Bot Starts But Doesn’t Reply

1. Confirm it’s running:

```bash
tgcodex-bot status --config config.yaml
```

2. Check logs:

```bash
tail -n 200 .tgcodex-bot/config.yaml.log
```

3. Confirm you’re allowlisted:

- Your Telegram user ID must be in `telegram.allowed_user_ids`.

4. Ensure only one poller:

- Running two instances with the same token will cause `getUpdates` conflicts.

5. If your chat is set to an unreachable SSH machine:

- Switch back to local:
  - `/machine local`

### `codex` Not Found

- Ensure `codex` is installed and in `PATH` on the active machine.
- For SSH machines, set `machines.defs.<name>.codex_bin` to the absolute path.

## Development

Install dev dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
python -m pytest -q
```

Lint (if you use ruff):

```bash
ruff check .
```

## Security Notes

- Keep your bot token secret (use env vars; never commit it).
- Do not commit `config.yaml` (this repo ignores it by default).
- Logs and runtime files live in `./.tgcodex-bot/` (also ignored by default).
- You are running a tool that can execute commands; use approval policies appropriately.

## License

No license file is currently included. If you intend this to be broadly reusable, add a LICENSE file before advertising it as open source.
