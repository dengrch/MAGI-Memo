import type { Context } from '@deepseek-ai/cordis'
import { randomUUID } from 'node:crypto'
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-commands'
import type {} from '@deepseek-ai/dsh-session-projection'
import type {} from '@deepseek-ai/dsh-subagent'
import { MagiClient, type JsonValue } from './client.ts'
import {
  ACTIVE_EXPLORER_WORKER_OUTPUT_SCHEMA,
  activeExplorerWorkerPrompt,
  activeExplorerLabel,
  addEntityFinding,
  addRelationFinding,
  cloneFindings,
  cloneVisit,
  mergeFindings,
  normalizeExplorerWorkerResult,
  parseRecallSeeds,
  runActiveExplorer,
  type ExplorerSeeds,
  type Findings,
  type Visit,
} from './active-explorer.ts'

export const name = 'magi-memo'
export const inject = ['tools', 'systemPrompt', 'subagents']

export type MemoryMode = 'auto' | 'manual' | 'explore' | 'off'
type ExploreApprovalOutcome = 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'

declare module '@deepseek-ai/dsh-session-projection/types' {
  interface SessionProjectionMap {
    /** MAGI memory policy selected for this session. */
    magiMemoryMode: 'auto' | 'manual' | 'explore' | 'off'
  }
}

export interface Config {
  baseUrl: string
  apiKey: string
  timeoutMs: number
  maxModelOutputChars: number
  defaultMemoryMode: MemoryMode
  subagentProvider: string
  explorerTopK: number
  explorerChunkTopK: number
  explorerMaxTokens: number
  explorerMaxCandidatesPerFrontier: number
  explorerWorkerMaxTokens: number
  explorerCoordinatorProvider: string
  explorerCoordinatorModel: string
  explorerWorkerProvider: string
  explorerWorkerModel: string
}

export const Config: Schema<Config> = Schema.object({
  baseUrl: Schema.string().default('http://127.0.0.1:3491'),
  apiKey: Schema.string().default(''),
  timeoutMs: Schema.number().default(30_000),
  maxModelOutputChars: Schema.number().default(30_000),
  defaultMemoryMode: Schema.union([
    Schema.const('auto'),
    Schema.const('manual'),
    Schema.const('explore'),
    Schema.const('off'),
  ]).default('auto'),
  subagentProvider: Schema.string().default('spawn'),
  explorerTopK: Schema.number().default(20),
  explorerChunkTopK: Schema.number().default(10),
  explorerMaxTokens: Schema.number().default(8_192),
  explorerMaxCandidatesPerFrontier: Schema.number().default(50),
  explorerWorkerMaxTokens: Schema.number().default(4_096),
  explorerCoordinatorProvider: Schema.string().default(''),
  explorerCoordinatorModel: Schema.string().default(''),
  explorerWorkerProvider: Schema.string().default(''),
  explorerWorkerModel: Schema.string().default(''),
})

const MEMORY_MODES = new Set<MemoryMode>(['auto', 'manual', 'explore', 'off'])

const memoryModeProjectionSchema = {
  parse(value: unknown): MemoryMode {
    if (typeof value === 'string' && MEMORY_MODES.has(value as MemoryMode)) return value as MemoryMode
    throw new Error('invalid MAGI memory mode projection')
  },
}

export function parseMemoryMode(value: string): MemoryMode | undefined {
  const normalized = value.trim().toLowerCase() as MemoryMode
  return MEMORY_MODES.has(normalized) ? normalized : undefined
}

/** Read the former `active` spelling from existing session logs. */
function parseLoggedMemoryMode(value: string): MemoryMode | undefined {
  return value.trim().toLowerCase() === 'active' ? 'auto' : parseMemoryMode(value)
}

/** Fold only successfully completed /memory commands from the durable log. */
export function foldMemoryMode(
  events: readonly SessionEvent[],
  defaultMode: MemoryMode = 'auto',
): MemoryMode {
  let mode = defaultMode
  const pending = new Map<string, MemoryMode>()
  for (const event of events) {
    if (event.type === 'command/run' && event.data.name === 'memory') {
      const selected = parseLoggedMemoryMode(event.data.args ?? '')
      if (selected !== undefined) pending.set(String(event.data.commandId), selected)
      continue
    }
    if (event.type !== 'command/done') continue
    const commandId = String(event.data.commandId)
    const selected = pending.get(commandId)
    pending.delete(commandId)
    if (selected !== undefined && event.data.kind === 'success') mode = selected
  }
  return mode
}

/** Return the currently open turn number, or undefined between durable turn boundaries. */
export function currentOpenTurn(events: readonly SessionEvent[]): number | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index] as SessionEvent
    if (event.type === 'turn/end') return undefined
    if (event.type === 'turn/start') return event.data.turn
  }
  return undefined
}

export function memoryModePrompt(mode: MemoryMode, referenceAt = new Date().toISOString()): string {
  const common = [
    `MAGI Memo memory mode for this session: ${mode}.`,
    `Current system time and default Episode reference_at: ${referenceAt}.`,
    'Unless the user explicitly provides a historical Episode time, use this reference_at before generating Atom temporal fields. Do not invent a different current year.',
    'MAGI memory tools always operate on the currently active workspace.',
    'Never create, switch, or activate a workspace proactively. Call magi_workspace_create or magi_workspace_activate only when the user explicitly asks to create or switch the memory workspace.',
    'If no suitable workspace is active, tell the user and ask which workspace to use; do not create or activate one on your own.',
    'The user controls memory mode with /memory auto, /memory manual, /memory explore, or /memory off. Never attempt to change memory mode yourself.',
    'MAGI extracted-memory temporal contract: use the Episode reference_at as the anchor for relative expressions. Atom content must preserve temporal wording when semantically important. valid_at is when the fact first becomes true: infer it from the evidence and reference_at, use reference_at for a fact asserted as current when no more precise time exists, and leave it empty only for a truly timeless definitional fact. invalid_at is when that same fact stops being true: set it only for an explicit end, replacement, correction, or expiry, never automatically. temporal_text is the exact short source expression. temporal_precision is exact, day, month, year, relative, inferred_current, or empty. All supplied timestamps must be timezone-aware ISO-8601 values.',
  ]
  if (mode === 'off') {
    return [
      ...common,
      'Do not call magi_memory_recall, magi_memory_write_episode, or magi_memory_write_extracted in this mode, even proactively.',
    ].join('\n')
  }
  if (mode === 'manual') {
    return [
      ...common,
      'Call MAGI recall or write tools only when the user explicitly asks you to use, search, save, or update memory.',
      'Do not perform proactive recall or proactive memory writing in this mode.',
    ].join('\n')
  }
  if (mode === 'explore') {
    return [
      ...common,
      'For every user turn, run a Recall Gate before answering. When prior memory may materially help and hidden graph relations may matter, call magi_memory_explore once with a focused question. Set concurrent=true for a broad or multi-branch graph investigation; leave it false for a small focused chain.',
      'Use magi_memory_recall instead only when ordinary retrieval is sufficient. Never call magi_memory_delegate, magi_memory_expand, magi_memory_describe, or magi_memory_evidence directly; those are isolated Explorer tools.',
      'Treat the Explorer result as retrieved context, not as the final answer. Its findings contain the selected graph descriptions and semantic evidence conclusions.',
      'After forming the answer, run the same Write Gate as auto mode. Write only new durable information, and make any write the final tool step before the user-visible answer.',
    ].join('\n')
  }
  return [
    ...common,
    'For every user turn, run a Recall Gate before answering: decide whether prior preferences, decisions, people, project facts, or episodes may materially improve the answer. Recall only when useful; do not call it mechanically.',
    'When recalling, choose the retrieval mode deliberately: local for entity-focused context, global for broad themes, hybrid for graph-wide plus entity context, naive for direct chunk similarity, and mix for the recommended graph-plus-vector retrieval.',
    'If an ordinary recall activates relevant entities and descriptions but still cannot answer because hidden graph relations must be followed, upgrade that same current-turn result with magi_memory_explore(hot_start=true). The tool reuses the plugin-managed recall seeds without repeating Mix and presents the user with a native approval prompt before exploration begins.',
    'Request active exploration only for a concrete unresolved relational gap. Choose concurrent=true only for broad or genuinely multi-branch investigation. Do not ask for permission in conversational prose before the tool call; the tool owns the approval prompt. If approval is rejected, cancelled, or unavailable, do not retry during that turn—answer from the ordinary recall and state the limitation.',
    'After forming the answer, run a Write Gate at the end of the turn: decide whether the turn contains new, durable information worth retaining. Do not write on every turn.',
    'Write only durable, useful memory; never store secrets, credentials, raw tool noise, or facts useful only for the current turn.',
    'When the turn already provides enough structure, use magi_memory_write_extracted and submit the Episode evidence plus entities, relations, and atomic facts. Use magi_memory_write_episode only when MAGI should perform the normal extraction stage.',
    'Before every extracted write, run a Temporal Pass over every entity and relation Atom according to the MAGI temporal contract above; do not omit temporal fields merely because the evidence uses relative phrases such as today, last Friday, next week, or before graduation.',
    'If writing, make the memory write the final tool step before the user-visible answer. A successful extracted write means MAGI accepted background processing; do not poll for resolution or projection unless the user asks.',
  ].join('\n')
}

export function scopedMemoryPrompt(
  mode: MemoryMode,
  delegated: boolean,
  referenceAt = new Date().toISOString(),
): string {
  if (delegated) {
    return 'You are a delegated subagent. The parent-agent MAGI Recall Gate, Write Gate, workspace-management, and memory-mode policies do not apply inside this isolated task. Follow only the delegated task instructions and your restricted tool set.'
  }
  return memoryModePrompt(mode, referenceAt)
}

const atomSchema = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    content: {
      type: 'string' as const,
      required: true as const,
      description: 'One atomic, standalone fact stated clearly enough to retrieve later.',
    },
    valid_at: { type: 'string' as const, description: 'Timezone-aware ISO-8601 time when the fact became valid. Resolve relative/current claims against Episode reference_at; omit only for genuinely timeless facts.' },
    invalid_at: { type: 'string' as const, description: 'Timezone-aware ISO-8601 time when the fact stopped being valid. Use only for an explicit end, expiry, replacement, or correction.' },
    temporal_text: { type: 'string' as const, description: 'Exact short temporal phrase from the evidence, such as "上周五" or "before graduation".' },
    temporal_precision: { type: 'string' as const, enum: ['exact', 'day', 'month', 'year', 'relative', 'inferred_current'], description: 'MAGI temporal precision: exact, day, month, year, relative, or inferred_current.' },
    predicate: { type: 'string' as const, description: 'Compact predicate describing the fact.' },
    confidence: { type: 'number' as const, description: 'Extraction confidence from 0 to 1.' },
    importance: { type: 'number' as const, description: 'Long-term importance from 0 to 1.' },
  },
}

const entitySchema = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    name: { type: 'string' as const, required: true as const, description: 'Canonical entity name.' },
    aliases: { type: 'array' as const, items: { type: 'string' as const }, description: 'Known aliases.' },
    entity_type: { type: 'string' as const, description: 'Optional entity category.' },
    atoms: {
      type: 'array' as const,
      required: true as const,
      items: atomSchema,
      description: 'Facts owned by this entity. May be empty only when the entity is an endpoint of a submitted relation that owns at least one Atom.',
    },
  },
}

const relationSchema = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    source: { type: 'string' as const, required: true as const, description: 'Source entity name. MAGI synthesizes an endpoint-only entity when it is absent from entities.' },
    target: { type: 'string' as const, required: true as const, description: 'Target entity name. MAGI synthesizes an endpoint-only entity when it is absent from entities.' },
    keywords: { type: 'array' as const, items: { type: 'string' as const }, description: 'Short relation keywords.' },
    atoms: {
      type: 'array' as const,
      required: true as const,
      items: atomSchema,
      description: 'One or more facts owned by this relation.',
    },
  },
}

function renderJson(value: JsonValue, maxChars: number) {
  const json = JSON.stringify(value, null, 2)
  const text = json.length <= maxChars
    ? json
    : `${json.slice(0, maxChars)}\n… [MAGI result truncated for model context]`
  return [{ type: 'text' as const, text }]
}

function recordValue(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function ownerNameKey(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

function relationOwnerKey(pair: readonly string[]): string {
  return pair.map(ownerNameKey).sort().join('\u0000')
}

function assertConfig(config: Config): void {
  if (!config.baseUrl.trim()) throw new Error('magi-memo baseUrl must not be empty')
  if (!MEMORY_MODES.has(config.defaultMemoryMode)) {
    throw new Error('magi-memo defaultMemoryMode must be auto, manual, explore, or off')
  }
  if (!config.subagentProvider.trim()) throw new Error('magi-memo subagentProvider must not be empty')
  for (const [field, value] of [
    ['timeoutMs', config.timeoutMs],
    ['maxModelOutputChars', config.maxModelOutputChars],
    ['explorerTopK', config.explorerTopK],
    ['explorerChunkTopK', config.explorerChunkTopK],
    ['explorerMaxTokens', config.explorerMaxTokens],
    ['explorerMaxCandidatesPerFrontier', config.explorerMaxCandidatesPerFrontier],
    ['explorerWorkerMaxTokens', config.explorerWorkerMaxTokens],
  ] as const) {
    if (!Number.isInteger(value) || value < 1) {
      throw new Error(`magi-memo ${field} must be a positive integer`)
    }
  }
}

export function apply(ctx: Context, config: Config) {
  assertConfig(config)
  const client = new MagiClient({
    baseUrl: config.baseUrl,
    apiKey: config.apiKey,
    timeoutMs: config.timeoutMs,
  })
  const output = {
    schema: { type: 'json' as const },
    render: (_args: unknown, value: JsonValue) => renderJson(value, config.maxModelOutputChars),
  }
  interface ExplorationAgentContext {
    explorationId: string
    query: string
    subquery: string
    visit: Visit
    findings: Findings
    concurrent: boolean
    delegatedTaskKeys?: Set<string>
    expanded?: Set<string>
    pendingDescribeRequired?: boolean
  }
  interface RecallHandoff {
    turn: number
    query: string
    seeds: ExplorerSeeds
  }
  interface ExploreApprovalDecision {
    turn: number
    outcome: ExploreApprovalOutcome
  }
  const explorationAgents = new Map<string, ExplorationAgentContext>()
  const recallHandoffs = new WeakMap<object, RecallHandoff>()
  const exploreApprovalDecisions = new WeakMap<object, ExploreApprovalDecision>()
  const explorationForAgent = (agent: {
    id: unknown
    session: { header: { parentSession?: unknown } }
  }) => {
    const direct = explorationAgents.get(String(agent.id))
    if (direct !== undefined) return direct
    const parentSession = agent.session.header.parentSession
    return parentSession === undefined
      ? undefined
      : explorationAgents.get(String(parentSession))
  }

  ctx.systemPrompt.section({
    name: 'magi-memo',
    order: 112,
    text: context => scopedMemoryPrompt(
      context.agent === undefined
        ? config.defaultMemoryMode
        : foldMemoryMode(context.agent.session.events, config.defaultMemoryMode),
      context.agent?.session.header.parentSession !== undefined,
    ),
  })

  ctx.inject(['sessionProjections'], (projectionCtx) => {
    interface MemoryModeProjectionState {
      mode: MemoryMode
      pending: Record<string, MemoryMode>
    }

    projectionCtx.sessionProjections.register<'magiMemoryMode', MemoryModeProjectionState>({
      key: 'magiMemoryMode',
      // The projection registry only consumes schema.parse(); keep this local
      // plugin independent of DSH's private zod installation.
      schema: memoryModeProjectionSchema as never,
      init: () => ({ mode: config.defaultMemoryMode, pending: {} }),
      apply: (state, event) => {
        if (event.type === 'command/run' && event.data.name === 'memory') {
          const selected = parseLoggedMemoryMode(event.data.args ?? '')
          if (selected === undefined) return state
          return {
            ...state,
            pending: { ...state.pending, [String(event.data.commandId)]: selected },
          }
        }
        if (event.type !== 'command/done') return state
        const commandId = String(event.data.commandId)
        const selected = state.pending[commandId]
        if (selected === undefined) return state
        const pending = { ...state.pending }
        delete pending[commandId]
        return {
          mode: event.data.kind === 'success' ? selected : state.mode,
          pending,
        }
      },
      view: state => state.mode,
      stateVersion: 1,
    })
  })

  ctx.inject(['commands'], (commandCtx) => {
    commandCtx.commands.register({
      name: 'memory',
      description: 'Show or switch this session\'s MAGI memory mode',
      input: { hint: '[auto|manual|explore|off]' },
      handler: ({ agent, rawInput }) => {
        const requested = rawInput.trim()
        if (requested === '') {
          const current = foldMemoryMode(agent.session.events, config.defaultMemoryMode)
          return { kind: 'success', text: `Memory mode is ${current}.` }
        }
        const selected = parseMemoryMode(requested)
        if (selected === undefined) {
          return { kind: 'error', text: 'Usage: /memory auto|manual|explore|off' }
        }
        return { kind: 'success', text: `Memory mode switched to ${selected}.` }
      },
    })
  })

  ctx.tools.register(defineTool({
    name: 'magi_workspace_create',
    description: 'USER-AUTHORIZED ONLY: call this only when the user explicitly asks to create a workspace. Creates a MAGI Memo workspace and makes it active; an existing exact match is reused and activated.',
    parameters: {
      name: { type: 'string', required: true, description: 'Human-readable workspace name.' },
      workspace_id: { type: 'string', description: 'Optional stable workspace id. Omit to let MAGI derive one.' },
    },
    output,
    async execute(args, exec) {
      return client.createAndActivateWorkspace({
        name: args.name,
        ...(args.workspace_id === undefined ? {} : { workspaceId: args.workspace_id }),
      }, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_workspace_activate',
    description: 'USER-AUTHORIZED ONLY: call this only when the user explicitly asks to switch or activate a workspace. Activates one existing MAGI Memo workspace by exact id or exact name; it never creates a workspace.',
    parameters: {
      workspace: {
        type: 'string',
        required: true,
        description: 'Exact id or exact human-readable name of the existing workspace explicitly selected by the user.',
      },
    },
    output,
    async execute(args, exec) {
      return client.activateWorkspace({ workspace: args.workspace }, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_write_episode',
    description: 'Submit one durable episode to the active MAGI workspace. MAGI stores the Episode and runs its normal entity, relation, and Atom extraction pipeline in the background.',
    parameters: {
      content: {
        type: 'string',
        required: true,
        description: 'Self-contained episode content with enough context to remain meaningful later.',
      },
      source_id: {
        type: 'string',
        description: 'Optional stable id for idempotency. Reusing it intentionally targets the same episode source.',
      },
    },
    output,
    async execute(args, exec) {
      return client.writeEpisode({
        content: args.content,
        ...(args.source_id === undefined ? {} : { sourceId: args.source_id }),
      }, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_write_extracted',
    description: 'Submit durable memory already extracted in this DSH step. Before calling, perform a Temporal Pass: anchor the Episode and populate each time-bearing Atom\'s valid_at, invalid_at, temporal_text, and temporal_precision. MAGI accepts it for background resolution, temporal evolution, projection, and bookkeeping.',
    parameters: {
      content: { type: 'string', required: true, description: 'Original Episode evidence supporting every extracted fact.' },
      kind: {
        type: 'string',
        enum: ['document', 'conversation', 'multimodal_text'],
        description: 'Episode kind. Defaults to conversation.',
      },
      reference_at: { type: 'string', description: 'Timezone-aware ISO-8601 temporal anchor for resolving Episode-relative expressions. If omitted, the plugin supplies the tool invocation time.' },
      source_uri: {
        type: 'string',
        description: 'Optional provenance URI or source label. This is not an idempotency key; identical extracted content is resolved to the canonical existing Episode by MAGI.',
      },
      metadata: {
        type: 'object',
        additionalProperties: true,
        description: 'Optional JSON metadata attached to the Episode.',
      },
      entities: {
        type: 'array',
        items: entitySchema,
        description: 'Optional extracted entities that own facts or metadata. Pure relation endpoints may be omitted and MAGI will synthesize them.',
      },
      relations: {
        type: 'array',
        items: relationSchema,
        description: 'Optional extracted relations. Missing source/target entity containers are synthesized by MAGI.',
      },
    },
    output,
    async execute(args, exec) {
      const payload: JsonValue = {
        content: args.content,
        kind: args.kind ?? 'conversation',
        metadata: args.metadata ?? {},
        entities: args.entities ?? [],
        relations: args.relations ?? [],
        ...(args.reference_at === undefined ? {} : { reference_at: args.reference_at }),
        ...(args.source_uri === undefined ? {} : { source_uri: args.source_uri }),
      }
      return client.writeExtracted(payload, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_recall',
    description: 'Retrieve structured memory context from the active MAGI workspace without asking MAGI to generate the final answer. Returns entities, relationships, chunks, references, and retrieval metadata.',
    parameters: {
      query: {
        type: 'string',
        required: true,
        description: 'A natural-language memory query containing at least 3 characters. Expand short entity names into a complete question: use "赵凯是谁" instead of "赵凯".',
      },
      mode: {
        type: 'string',
        enum: ['local', 'global', 'hybrid', 'naive', 'mix'],
        description: 'Retrieval mode. Defaults to mix.',
      },
      top_k: { type: 'integer', description: 'Maximum KG entities or relationships to retrieve.' },
      chunk_top_k: { type: 'integer', description: 'Maximum text chunks to retrieve.' },
      high_level_keywords: { type: 'array', items: { type: 'string' }, description: 'Optional broad keywords to avoid a separate keyword-extraction step.' },
      low_level_keywords: { type: 'array', items: { type: 'string' }, description: 'Optional specific keywords to avoid a separate keyword-extraction step.' },
      enable_rerank: { type: 'boolean', description: 'Whether MAGI should rerank retrieved chunks.' },
    },
    output,
    async execute(args, exec) {
      const result = await client.recall({
        query: args.query,
        ...(args.mode === undefined ? {} : { mode: args.mode }),
        ...(args.top_k === undefined ? {} : { topK: args.top_k }),
        ...(args.chunk_top_k === undefined ? {} : { chunkTopK: args.chunk_top_k }),
        ...(args.high_level_keywords === undefined ? {} : { highLevelKeywords: args.high_level_keywords }),
        ...(args.low_level_keywords === undefined ? {} : { lowLevelKeywords: args.low_level_keywords }),
        ...(args.enable_rerank === undefined ? {} : { enableRerank: args.enable_rerank }),
      }, exec.signal)
      if (exec.agent !== undefined) {
        const turn = currentOpenTurn(exec.agent.session.events)
        if (turn !== undefined) {
          recallHandoffs.set(exec.agent, {
            turn,
            query: args.query,
            seeds: parseRecallSeeds(result),
          })
        }
      }
      return result
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_expand',
    description: 'EXPLORER-ONLY: layer 1 topology scan returning only neighbor names/types and relation endpoints/keywords. If candidates are returned, select a non-empty subset and make one mandatory batched magi_memory_describe call before expanding again or completing. Do not call from the parent agent.',
    parameters: {
      frontier: {
        type: 'array',
        required: true,
        items: { type: 'string' },
        description: 'Current frontier as exact entity names.',
      },
    },
    output,
    async execute(args, exec) {
      const agentId = exec.agent === undefined ? undefined : String(exec.agent.id)
      const exploration = exec.agent === undefined
        ? undefined
        : explorationForAgent(exec.agent)
      if (exploration === undefined) {
        throw new Error('magi_memory_expand is available only inside MAGI exploration')
      }
      {
        if (exploration.pendingDescribeRequired === true) {
          return {
            status: 'refused',
            exhausted: false,
            reason: 'The preceding expand returned candidates. Select a non-empty subset and call magi_memory_describe once, using at least one relevant entity or relation owner per selected candidate, before another expand.',
            results: [],
          }
        }
        const expanded = exploration.expanded ?? new Set<string>()
        const requestedFrontiers = [...new Set(args.frontier.map(name => (
          ownerNameKey(name)
        )))]
        const repeated = requestedFrontiers.filter(name => expanded.has(name))
        if (repeated.length > 0) {
          return {
            status: 'refused',
            exhausted: false,
            reason: `Frontier already expanded in this branch: ${repeated.join(', ')}. Do not retry it; describe/evidence an identified relevant owner or stop.`,
            results: [],
          }
        }
        exploration.expanded = expanded
        for (const frontier of requestedFrontiers) expanded.add(frontier)
      }
      const result = await client.expand({
        frontier: args.frontier,
        visit: cloneVisit(exploration.visit),
        maxCandidatesPerFrontier: config.explorerMaxCandidatesPerFrontier,
        ...(agentId === undefined
          ? {}
          : {
              trace: {
                explorationId: exploration?.explorationId ?? agentId,
                agentId,
                callId: randomUUID(),
                ...(exploration === undefined ? {} : { query: exploration.subquery }),
              },
            }),
      }, exec.signal)
      if (exploration !== undefined && recordValue(result) && Array.isArray(result.results)) {
        let hasCandidates = false
        for (const frontierResult of result.results) {
          if (!recordValue(frontierResult) || !Array.isArray(frontierResult.candidates)) continue
          if (frontierResult.candidates.length > 0) hasCandidates = true
        }
        exploration.pendingDescribeRequired = hasCandidates
      }
      return result
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_describe',
    description: 'EXPLORER-ONLY: mandatory layer 2 loading after expand. Describe only selected candidates. Successful owner descriptions are automatically committed to plugin-managed visit/findings; evidence remains optional layer 3.',
    parameters: {
      entities: {
        type: 'array',
        items: { type: 'string' },
        description: 'Entity owners to describe by exact name.',
      },
      relations: {
        type: 'array',
        items: { type: 'array', items: { type: 'string' } },
        description: 'Relation owners to describe as unordered endpoint-name pairs.',
      },
    },
    output,
    async execute(args, exec) {
      const exploration = exec.agent === undefined
        ? undefined
        : explorationForAgent(exec.agent)
      if (exploration === undefined) {
        throw new Error('magi_memory_describe is available only inside MAGI exploration')
      }
      const owners = [
        ...(args.entities ?? []).map(entity => ({ entity } as const)),
        ...(args.relations ?? []).map(relation => ({ relation: relation as [string, string] } as const)),
      ]
      if (owners.length === 0) throw new Error('magi_memory_describe requires at least one owner')
      const result = await client.describe(owners, exec.signal)
      {
        exploration.pendingDescribeRequired = false
        if (recordValue(result) && Array.isArray(result.owners)) {
          for (const item of result.owners) {
            if (!recordValue(item) || !recordValue(item.owner)) continue
            const description = typeof item.description === 'string' ? item.description : undefined
            if (item.owner.type === 'entity' && typeof item.owner.name === 'string') {
              addEntityFinding(exploration.visit, exploration.findings, item.owner.name, description)
            } else if (
              item.owner.type === 'relation'
              && Array.isArray(item.owner.name)
              && item.owner.name.length === 2
              && item.owner.name.every(value => typeof value === 'string')
            ) {
              addRelationFinding(
                exploration.visit,
                exploration.findings,
                item.owner.name as [string, string],
                description,
              )
            }
          }
        }
      }
      return result
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_evidence',
    description: 'EXPLORER-ONLY: optional layer 3 after describe. Inspect Atom evidence/evolution only when a useful description leaves a query-critical factual, temporal, provenance, or evolution ambiguity. Add a concise conclusion to matching findings notes.',
    parameters: {
      entities: {
        type: 'array',
        items: { type: 'string' },
        description: 'Entity owners to inspect by exact name.',
      },
      relations: {
        type: 'array',
        items: { type: 'array', items: { type: 'string' } },
        description: 'Relation owners to inspect as unordered endpoint-name pairs.',
      },
    },
    output,
    async execute(args, exec) {
      const owners = [
        ...(args.entities ?? []).map(entity => ({ entity } as const)),
        ...(args.relations ?? []).map(relation => ({ relation: relation as [string, string] } as const)),
      ]
      return client.evidence(owners, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_note',
    description: 'EXPLORER-ONLY: record concise semantic conclusions derived from optional evidence into plugin-managed findings. Do not copy raw evidence rows, ids, or metadata.',
    parameters: {
      entities: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            name: { type: 'string', required: true },
            note: { type: 'string', required: true },
          },
        },
      },
      relations: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            endpoints: { type: 'array', required: true, items: { type: 'string' } },
            note: { type: 'string', required: true },
          },
        },
      },
    },
    output,
    async execute(args, exec) {
      if (exec.agent === undefined) throw new Error('magi_memory_note requires an agent-owned tool execution')
      const exploration = explorationForAgent(exec.agent)
      if (exploration === undefined) throw new Error('magi_memory_note is available only inside MAGI exploration')
      let entities = 0
      let relations = 0
      for (const owner of args.entities ?? []) {
        if (addEntityFinding(exploration.visit, exploration.findings, owner.name, owner.note)) entities += 1
      }
      for (const owner of args.relations ?? []) {
        if (owner.endpoints.length !== 2) continue
        if (addRelationFinding(
          exploration.visit,
          exploration.findings,
          owner.endpoints as [string, string],
          owner.note,
        )) relations += 1
      }
      return { status: 'complete', recorded: { entities, relations } }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_delegate',
    description: 'COORDINATOR-ONLY: start one concurrent batch of isolated active-retrieval workers. s0 references a minimal subquery-specific known findings subset by owner; the plugin resolves notes and automatically merges each worker delta.',
    parameters: {
      tasks: {
        type: 'array',
        required: true,
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            subquery: {
              type: 'string',
              required: true,
              description: 'Focused branch question derived from the parent query and current coordinator context.',
            },
            frontier: {
              type: 'array',
              required: true,
              items: { type: 'string' },
              description: 'Non-empty exact entity-name frontier assigned to this worker.',
            },
            known_owners: {
              type: 'object',
              required: true,
              additionalProperties: false,
              description: 'Owner references for the smallest existing findings subset relevant to this subquery. The plugin resolves notes from authoritative state.',
              properties: {
                entities: {
                  type: 'array',
                  required: true,
                  items: { type: 'string' },
                },
                relations: {
                  type: 'array',
                  required: true,
                  items: { type: 'array', items: { type: 'string' } },
                },
              },
            },
          },
        },
      },
    },
    output,
    async execute(args, exec) {
      if (exec.agent === undefined) {
        throw new Error('magi_memory_delegate requires an agent-owned tool execution')
      }
      const exploration = explorationForAgent(exec.agent)
      if (exploration === undefined || !exploration.concurrent) {
        throw new Error('magi_memory_delegate is available only to an active concurrent MAGI coordinator')
      }
      if (args.tasks.length === 0) throw new Error('tasks must not be empty')

      const normalizedTasks: Array<{
        subquery: string
        frontier: string[]
        knownFindings: Findings
      }> = []
      const taskKeys = new Set<string>()
      const subqueryKeys = new Set<string>()
      const delegatedTaskKeys = exploration.delegatedTaskKeys ?? new Set<string>()
      for (const [index, task] of args.tasks.entries()) {
        const subquery = task.subquery.trim().replace(/\s+/g, ' ')
        if (subquery.length < 3) throw new Error(`tasks[${index}].subquery must contain at least 3 characters`)
        const frontier = [...new Set(task.frontier.map(name => name.trim().replace(/\s+/g, ' ')).filter(Boolean))]
        if (frontier.length === 0) throw new Error(`tasks[${index}].frontier must not be empty`)
        const entityKeys = new Set(task.known_owners.entities.map(ownerNameKey))
        const relationKeys = new Set(task.known_owners.relations
          .filter(pair => pair.length === 2)
          .map(pair => relationOwnerKey(pair as [string, string])))
        const knownFindings: Findings = {
          entities: exploration.findings.entities
            .filter(owner => entityKeys.has(ownerNameKey(owner.name)))
            .map(owner => ({ name: owner.name, notes: [...owner.notes] })),
          relations: exploration.findings.relations
            .filter(owner => relationKeys.has(relationOwnerKey(owner.endpoints)))
            .map(owner => ({ endpoints: [...owner.endpoints] as [string, string], notes: [...owner.notes] })),
        }
        const frontierKeys = frontier.map(name => name.toLocaleLowerCase()).sort()
        const subqueryKey = subquery.toLocaleLowerCase()
        const taskKey = `${subqueryKey}\u0000${frontierKeys.join('\u0000')}`
        if (taskKeys.has(taskKey)) {
          return {
            status: 'refused',
            reason: `Duplicate task in the same batch: ${subquery}. Submit only genuinely distinct branches.`,
            workers: [],
          }
        }
        if (delegatedTaskKeys.has(taskKey)) {
          return {
            status: 'refused',
            reason: `This exact subquery and frontier task was already delegated: ${subquery}. Reuse a frontier only for a genuinely different unresolved branch.`,
            workers: [],
          }
        }
        if (subqueryKeys.has(subqueryKey)) {
          return {
            status: 'refused',
            reason: `The same subquery was assigned more than once in one batch: ${subquery}. Use genuinely different unresolved targets.`,
            workers: [],
          }
        }
        taskKeys.add(taskKey)
        subqueryKeys.add(subqueryKey)
        normalizedTasks.push({ subquery, frontier, knownFindings })
      }

      exploration.delegatedTaskKeys = delegatedTaskKeys
      for (const taskKey of taskKeys) delegatedTaskKeys.add(taskKey)

      const workers = await Promise.all(normalizedTasks.map(async (task, index) => {
        const { subquery, frontier, knownFindings } = task

        let run: Awaited<ReturnType<typeof ctx.subagents.start>> | undefined
        try {
          run = await ctx.subagents.start(config.subagentProvider, {
            label: activeExplorerLabel(subquery),
            parent: exec.agent!,
            signal: exec.signal,
            prompt: [{
              type: 'text',
              text: activeExplorerWorkerPrompt({
                parentQuery: exploration.query,
                subquery,
                frontier,
                knownFindings,
              }),
            }],
            outputSchema: ACTIVE_EXPLORER_WORKER_OUTPUT_SCHEMA,
            toolFilter: {
              allow: ['magi_memory_expand', 'magi_memory_describe', 'magi_memory_evidence', 'magi_memory_note'],
            },
            agentOptions: {
              maxTokens: config.explorerWorkerMaxTokens,
              ...(config.explorerWorkerProvider.trim()
                ? { provider: config.explorerWorkerProvider.trim() }
                : {}),
              ...(config.explorerWorkerModel.trim()
                ? { model: config.explorerWorkerModel.trim() }
                : {}),
            },
          })
          const agentId = String(run.id)
          const workerContext: ExplorationAgentContext = {
            ...exploration,
            subquery,
            visit: cloneVisit(exploration.visit),
            findings: { entities: [], relations: [] },
            expanded: new Set<string>(),
            pendingDescribeRequired: false,
          }
          explorationAgents.set(agentId, workerContext)
          const result = await run.result
          const workerFindings = cloneFindings(workerContext.findings)
          mergeFindings(exploration.visit, exploration.findings, workerFindings)
          return {
            agent_id: agentId,
            subquery,
            frontier,
            result: normalizeExplorerWorkerResult(result.structured, result.stopReason, workerFindings),
          }
        } catch (error) {
          if (exec.signal.aborted) throw error
          return {
            agent_id: run === undefined ? `worker-${index + 1}-startup-failed` : String(run.id),
            subquery,
            frontier,
            result: {
              status: 'abnormal',
              subagent_stop_reason: 'error',
              findings: { entities: [], relations: [] },
              stop_reason: `Worker failed: ${error instanceof Error ? error.message : String(error)}`,
            },
          }
        } finally {
          if (run !== undefined) {
            explorationAgents.delete(String(run.id))
            await run.dispose()
          }
        }
      }))
      return { status: 'complete', workers }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_explore',
    description: 'Run user-approved active memory retrieval for one question. In auto mode, first call ordinary recall, then set hot_start=true to reuse that current-turn result without another Mix. It always creates coordinator s0; concurrent mode lets s0 dynamically dispatch isolated workers.',
    parameters: {
      query: {
        type: 'string',
        required: true,
        description: 'A focused natural-language question containing at least 3 characters.',
      },
      concurrent: {
        type: 'boolean',
        description: 'Use dynamic multi-worker retrieval for broad or multi-branch investigations. Defaults to false for the original single-explorer path.',
      },
      hot_start: {
        type: 'boolean',
        description: 'Reuse the calling Agent\'s most recent ordinary recall from this same turn as initial visit/findings. Required in auto mode; omit for a cold start in explicit explore mode.',
      },
    },
    output,
    async execute(args, exec) {
      if (exec.agent === undefined) {
        throw new Error('magi_memory_explore requires an agent-owned tool execution')
      }
      const mode = foldMemoryMode(exec.agent.session.events, config.defaultMemoryMode)
      const turn = currentOpenTurn(exec.agent.session.events)
      if (turn === undefined) {
        throw new Error('magi_memory_explore requires an open agent turn')
      }
      const handoff = args.hot_start === true ? recallHandoffs.get(exec.agent) : undefined
      if (args.hot_start === true && (handoff === undefined || handoff.turn !== turn)) {
        return {
          status: 'refused',
          reason: 'No ordinary recall from the current turn is available for hot start. Call magi_memory_recall first, inspect its result, and retry only if a concrete relational gap remains.',
        }
      }
      if (mode === 'auto' && args.hot_start !== true) {
        return {
          status: 'refused',
          reason: 'Auto mode requires an ordinary current-turn recall before active exploration. Call magi_memory_recall, then use magi_memory_explore with hot_start=true only if hidden graph relations remain necessary.',
        }
      }
      if (mode === 'auto') {
        const priorDecision = exploreApprovalDecisions.get(exec.agent)
        if (priorDecision?.turn === turn) {
          return {
            status: 'refused',
            reason: `Active exploration was already decided for this turn (${priorDecision.outcome}); do not request it again.`,
          }
        }
        const approver = (ctx as unknown as {
          get(name: 'approval'): {
            request(input: {
              agent: NonNullable<typeof exec.agent>
              toolName: string
              callId: typeof exec.callId
              reason: string
              signal: AbortSignal
            }): Promise<ExploreApprovalOutcome>
          } | undefined
        }).get('approval')
        const outcome = approver === undefined
          ? 'unavailable' as const
          : await approver.request({
              agent: exec.agent,
              toolName: 'magi_memory_explore',
              callId: exec.callId,
              reason: `Ordinary recall for “${handoff?.query ?? args.query}” found relevant memory but left a relational gap. Allow MAGI to actively explore the graph for “${args.query}”?`,
              signal: exec.signal,
            })
        exploreApprovalDecisions.set(exec.agent, { turn, outcome })
        if (outcome !== 'allowed-once') {
          return {
            status: 'refused',
            reason: outcome === 'rejected'
              ? 'The user declined active graph exploration. Use the ordinary recall result and do not ask again this turn.'
              : outcome === 'cancelled'
                ? 'The active graph exploration approval was cancelled. Use the ordinary recall result and do not ask again this turn.'
                : 'Active graph exploration requires an interactive approval channel, but none is available. Use the ordinary recall result.',
          }
        }
      }
      return runActiveExplorer({
        query: args.query,
        signal: exec.signal,
        ...(handoff === undefined ? {} : { initialSeeds: handoff.seeds }),
        topK: config.explorerTopK,
        chunkTopK: config.explorerChunkTopK,
        maxTokens: config.explorerMaxTokens,
        concurrent: args.concurrent ?? false,
        recall: (input, signal) => client.recall(input, signal),
        start: async request => {
          const run = await ctx.subagents.start(config.subagentProvider, {
            label: activeExplorerLabel(args.query),
            parent: exec.agent!,
            signal: exec.signal,
            prompt: [{ type: 'text', text: request.prompt }],
            outputSchema: request.outputSchema,
            toolFilter: request.toolFilter,
            agentOptions: {
              maxTokens: request.maxTokens,
              ...(config.explorerCoordinatorProvider.trim()
                ? { provider: config.explorerCoordinatorProvider.trim() }
                : {}),
              ...(config.explorerCoordinatorModel.trim()
                ? { model: config.explorerCoordinatorModel.trim() }
                : {}),
            },
          })
          const coordinatorId = String(run.id)
          const coordinatorContext: ExplorationAgentContext = {
            explorationId: coordinatorId,
            query: args.query,
            subquery: args.query,
            visit: cloneVisit(request.initialVisit),
            findings: cloneFindings(request.initialFindings),
            concurrent: args.concurrent ?? false,
            delegatedTaskKeys: new Set<string>(),
            expanded: new Set<string>(),
            pendingDescribeRequired: false,
          }
          explorationAgents.set(coordinatorId, coordinatorContext)
          try {
            await client.registerExploration(
              coordinatorId,
              args.query,
              exec.signal,
              request.initialVisit,
            )
          } catch (error) {
            ctx.logger.warn(`Failed to register MAGI exploration trace: ${String(error)}`)
          }
          return {
            result: run.result,
            snapshotFindings() {
              return cloneFindings(coordinatorContext.findings)
            },
            async dispose() {
              explorationAgents.delete(coordinatorId)
              await run.dispose()
            },
          }
        },
      })
    },
  }))
}
