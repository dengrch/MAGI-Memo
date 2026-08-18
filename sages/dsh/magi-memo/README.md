# MAGI Memo plugin for DeepSeek Harness

This local Cordis plugin connects DeepSeek Harness to the MAGI Memo service at
`http://127.0.0.1:3491` by default. It registers five model-facing tools:

- `magi_workspace_create`: create or reuse a workspace and activate it, only on an explicit user request.
- `magi_workspace_activate`: activate an existing workspace by exact id or name, only on an explicit user request.
- `magi_memory_write_episode`: enqueue an Episode through MAGI's normal extraction pipeline.
- `magi_memory_write_extracted`: commit entities, relations, and Atoms already extracted in the current DSH step.
- `magi_memory_recall`: retrieve structured context through `/query/data` without final-answer generation.

Workspace creation and activation are user-authorized operations. The system
prompt explicitly forbids the agent from calling either workspace tool
proactively; ordinary memory operations stay scoped to the currently active
workspace.

## Runtime memory mode

The Web profile registers a session-scoped `/memory` command:

```text
/memory auto
/memory manual
/memory off
/memory
```

- `auto` runs a Recall Gate before each answer and a Write Gate at the end;
  neither gate forces a tool call when memory is not useful.
- `manual` uses recall/write tools only when the user explicitly asks.
- `off` prohibits recall and memory writes.
- In DSH Web, entering bare `/memory` opens an Auto / Manual / Off popup; the
  right-side composer selector uses the same command and session state.
- Direct host/API execution of `/memory` with no argument reports the current mode.

Successful mode commands are already recorded in the DSH session log. The
plugin folds those records before every model request, so switching takes effect
without restarting DSH and survives session resume or fork. The agent cannot
change the mode itself. `MAGI_MEMO_DEFAULT_MODE` only selects the initial mode
for a session that has no successful `/memory` command yet.

## Run the Web profile

Start MAGI Memo first, then run from the DeepSeek Harness checkout:

```sh
cd /Users/dengruochen/Desktop/MAGI-Memo/sages/dsh
pnpm dsh web --patch ./magi-memo/cordis.yml
```

Open `http://127.0.0.1:3080`. The existing DSH process must be restarted with
the patch before the tools appear. Use a DSH-supported Node.js runtime
(`^22.19.0` or `>=24.0.0`).

Optional environment variables:

```sh
export MAGI_MEMO_BASE_URL=http://127.0.0.1:3491
export MAGI_MEMO_API_KEY=your-x-api-key
export MAGI_MEMO_TIMEOUT_MS=30000
export MAGI_MEMO_MAX_OUTPUT_CHARS=30000
export MAGI_MEMO_DEFAULT_MODE=auto
```

`MAGI_MEMO_API_KEY` is sent as `X-API-Key`. Leave it unset when MAGI authentication
is disabled.

## Verify

From the DeepSeek Harness checkout:

```sh
node node_modules/typescript/bin/tsc -p magi-memo/tsconfig.check.json
node --import tsx --test magi-memo/tests/client.test.ts
node --import tsx scripts/run-oxlint.ts magi-memo
```
