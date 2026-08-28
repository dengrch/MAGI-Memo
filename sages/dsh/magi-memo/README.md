# MAGI Memo plugin for DeepSeek Harness

This local Cordis plugin connects DeepSeek Harness to the MAGI Memo service at
`http://127.0.0.1:3491` by default. It registers the ordinary workspace and
memory tools plus an active-retrieval composite:

- `magi_workspace_create`: create or reuse a workspace and activate it, only on an explicit user request.
- `magi_workspace_activate`: activate an existing workspace by exact id or name, only on an explicit user request.
- `magi_memory_write_episode`: enqueue an Episode through MAGI's normal extraction pipeline.
- `magi_memory_write_extracted`: commit entities, relations, and Atoms already extracted in the current DSH step.
- `magi_memory_recall`: retrieve structured context through `/query/data` without final-answer generation.
- `magi_memory_explore`: start one s0 coordinator. Explicit explore mode cold-starts with one Mix recall. Auto mode can hand off the current turn's preceding ordinary recall with `hot_start=true`, skips the duplicate Mix, and asks for native DSH user approval before spawning s0. With `concurrent=false`, s0 explores directly; with `concurrent=true`, s0 dynamically dispatches concurrent subquery workers and merges their findings.

The plugin declares `ctx.subagents` as a required Cordis service and uses the
configured `spawn` provider by default. A direct s0 and every worker can see
only `magi_memory_expand`, `magi_memory_describe`, `magi_memory_evidence`, and
the internal `magi_memory_note` state-update tool.
`expand` returns compact topology only. Every non-empty expansion round must
select a small candidate subset and make one batched `describe` call before
continuing or completing. Every selected candidate must contribute at least
one relevant owner—relation, entity, or both when needed. The plugin owns
`visit` and `findings`: it injects the current visit into every expand and
automatically commits successfully described owners and descriptions. Models
never pass or return the complete state. Topology alone never becomes a
finding. `evidence` is the optional third layer, reserved for
query-critical factual, temporal, provenance, or evolution ambiguity that
remains after description. `magi_memory_note` stores a concise conclusion formed
from Evidence or from the explorer's wider accumulated context; it is not
restricted to immediately following an evidence call. The plugin rejects a
repeated frontier and refuses a later expand while the preceding candidates
still await description.
`expanded` is maintained separately from `visit`: any entity visible in the
exploration history may become a frontier, including an earlier topology-only
candidate, but each entity can be expanded only once per branch.
Concurrent s0 instead sees
only `magi_memory_delegate`, whose tasks run in one `Promise.all` batch with
independent subqueries, frontiers, plugin-owned visits, and a
minimal subquery-specific `known_owners` reference set selected by s0. The
plugin resolves those references from authoritative findings, so s0 never
copies notes into delegation arguments. Worker findings are automatically
merged back into s0 state. The parent prompt
tells the parent agent never to call any of these internal tools directly. Entity and
relation stable ids are validated inside MAGI and stripped by the client before
any recall, expansion, or evidence result reaches model context.

All workers in one run write expansion events under the coordinator's shared
exploration id while retaining their own agent id and subquery. Balthasar can
therefore render the batch live, color each worker independently, and replay
the exact worker/subquery/expand sequence. Workers intentionally do not share a
coverage lock: isolation preserves each assigned reasoning chain, while s0
controls overlap by choosing focused branches and frontiers. Workers receive
only s0-selected subquery-relevant description-backed findings and follow the
same expand → mandatory describe → optional evidence state machine as direct s0;
they return only notes newly discovered or materially strengthened by their
assigned branch.

Workspace creation and activation are user-authorized operations. The system
prompt explicitly forbids the agent from calling either workspace tool
proactively; ordinary memory operations stay scoped to the currently active
workspace.

For extracted writes, a relationship endpoint may be omitted from `entities`.
MAGI synthesizes an endpoint-only entity from `source` or `target`; the
relationship must still own at least one Atom. The plugin tells the model not to
invent an Entity Atom merely to satisfy the payload shape.

## Runtime memory mode

The Web profile registers a session-scoped `/memory` command:

```text
/memory auto
/memory manual
/memory explore
/memory off
/memory
```

- `auto` runs a Recall Gate before each answer and a Write Gate at the end;
  neither gate forces a tool call when memory is not useful. When ordinary
  recall activates relevant entities but leaves a concrete relational gap, the
  Host may call `magi_memory_explore(hot_start=true)`. The plugin reuses only
  the current turn's plugin-managed recall seeds and raises a native DSH
  approval request before starting s0. Rejection, cancellation, or a missing
  approval channel fails closed and cannot reprompt during that turn.
- `manual` uses recall/write tools only when the user explicitly asks.
- `explore` enables the Recall Gate to choose `magi_memory_explore` when hidden
  graph relations may matter. It selects concurrent mode for broad or
  multi-branch investigations and the direct s0 path for small focused chains.
  The parent receives only merged findings, a compact exploration `report`,
  `stop_reason`, and system completion metadata; private visit state remains
  inside the explorers.
- `off` prohibits recall and memory writes.
- In DSH Web, entering bare `/memory` opens the available mode selector; the
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
export MAGI_MEMO_SUBAGENT_PROVIDER=spawn
export MAGI_MEMO_EXPLORER_TOP_K=20
export MAGI_MEMO_EXPLORER_CHUNK_TOP_K=10
export MAGI_MEMO_EXPLORER_MAX_TOKENS=8192
export MAGI_MEMO_EXPLORER_MAX_CANDIDATES_PER_FRONTIER=50
export MAGI_MEMO_EXPLORER_WORKER_MAX_TOKENS=4096
export MAGI_MEMO_EXPLORER_COORDINATOR_PROVIDER=
export MAGI_MEMO_EXPLORER_COORDINATOR_MODEL=
export MAGI_MEMO_EXPLORER_WORKER_PROVIDER=
export MAGI_MEMO_EXPLORER_WORKER_MODEL=
```

The four optional provider/model variables let s0 and s1…sn use independent
LLM routes. Empty values preserve normal parent inheritance. To force
non-thinking workers, point the worker provider at a DSH adapter profile whose
thinking policy is disabled (or whose default reasoning effort is `off`), and
optionally select a worker-specific model here.

`MAGI_MEMO_API_KEY` is sent as `X-API-Key`. Leave it unset when MAGI authentication
is disabled.

## Verify

From the DeepSeek Harness checkout:

```sh
node node_modules/typescript/bin/tsc -p magi-memo/tsconfig.check.json
node --import tsx --test magi-memo/tests/*.test.ts
node --import tsx scripts/run-oxlint.ts magi-memo
```
