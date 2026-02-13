# tgcodex-bot

Telegram bot that wraps the `codex` CLI to provide a stateful, private, per-chat Codex session with:

- Telegram allowlist
- Per-chat persistent session id (resume across bot restarts)
- Local + SSH machines
- Buffered "block streaming" output with typing indicator
- Inline approval UI for command execution (accept once / accept similar / reject)

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

## Notes

- This project expects the `codex` CLI to be installed and available on each machine.
- Network and filesystem sandboxing are controlled by `codex` settings; this bot adds an additional workdir allowlist via `allowed_roots`.
