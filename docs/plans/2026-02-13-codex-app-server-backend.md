# Codex App Server Backend (tgcodex)

Date: 2026-02-13

## Goal

Replace the existing `codex exec --json` integration with `codex app-server` (JSON-RPC over stdio) so:

- Approvals are *protocol enforced* via server-initiated approval requests (no more "exec then ask").
- `/approval` supports the 3 user-facing modes:
  - `always`: strict ask-before-any-command behavior.
  - `on-request`: model decides when to ask.
  - `yolo`: no approval prompts.
- Remove the `proxy_exec` fallback path entirely (tgcodex never executes shell commands itself).

## Non-goals (for the first cut)

- End-to-end integration tests that require network access to Codex Cloud.
  - We will use unit tests with synthetic JSON-RPC messages instead.

## Architecture

### CodexBackend abstraction

Introduce an internal backend interface:

- `start_session(chat_id, machine, workdir, policy, sandbox, model, thinking_level, ...) -> session_handle`
- `send_user_message(session_handle, text) -> async stream of normalized events`
- `respond_to_approval(session_handle, approval_request_id, decision, ...)`
- `cancel_turn(session_handle)`
- `close(session_handle)`

### Backend implementation

Implement **AppServerBackend** only:

- Spawns `codex app-server` on the active Machine.
- Speaks newline-delimited JSON messages.
- `initialize` handshake.
- `thread/start` when `chat_state.active_session_id` is empty, else `thread/resume`.
- `turn/start` with text input for each user message.
- Consumes:
  - notifications (e.g. `item/agentMessage/delta`, `turn/completed`, `thread/tokenUsage/updated`)
  - server requests (e.g. `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`)

## Approval State Machine (bot-level)

States:

- `IDLE` (no active run)
- `RUNNING_TURN` (streaming)
- `AWAITING_APPROVAL` (server has paused and is waiting for allow/deny)

Rules:

- Only server approval requests can create an approval UI.
- While `AWAITING_APPROVAL`, new user messages are rejected with "Approval pending".
- Pending approval is persisted in `active_run.pending_action_json` as the canonical record.
- Callback data is minimal (`approval:approve:<run_id>`, etc); command text is fetched from DB.

## Data model changes

- Keep existing `chat_state.approval_mode` (already present).
- Continue using `active_run.pending_action_json`:

```json
{
  "v": 1,
  "type": "approval_request",
  "request_kind": "commandExecution" | "fileChange",
  "rpc_id": 123,
  "thread_id": "...",
  "turn_id": "...",
  "item_id": "...",
  "command": "rm -rf foo",
  "cwd": "/home/ubuntu",
  "reason": "..."
}
```

## Mode mapping

- `always`:
  - `approvalPolicy="on-request"`
  - `developerInstructions` include "request approval before *any* shell command".
- `on-request`:
  - `approvalPolicy="on-request"`
- `yolo`:
  - `approvalPolicy="never"`

Sandbox:

- Use config `codex.sandbox` as the thread's initial sandbox mode (`read-only|workspace-write|danger-full-access`).

## Implementation Tasks (batches)

Batch 1 (plumbing + wiring):

1. Add JSON-RPC stdio connection + message parser (`codex app-server`).
2. Add `CodexBackend` + `AppServerBackend` that exposes a `Run`/`Session` handle compatible with the bot loop.
3. Wire bot runtime to use AppServerBackend and remove the `proxy_exec` path.

Batch 2 (approvals + state machine):

4. Implement approval request handling + persistence (`pending_action_json`) and callback responses.
5. Update `/approval` mode mapping to the new backend (strict `always` behavior).

Batch 3 (tests + cleanup):

6. Replace/port unit tests that depended on `codex exec --json` parsing and CLI adapter.
7. Remove legacy exec-only modules and update docs/config.

## Verification

- `PYTHONPATH=src python3 -m unittest -q`
- Manual smoke test (requires network + valid Codex auth):
  - Start bot
  - `/new`, `/approval always`
  - Prompt: "create a folder foo"
  - Expect: bot shows an approval prompt (inline buttons) before the command executes.

