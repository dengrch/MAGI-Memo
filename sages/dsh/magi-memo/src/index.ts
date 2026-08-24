import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-commands'
import type {} from '@deepseek-ai/dsh-session-projection'
import type {} from '@deepseek-ai/dsh-subagent'
import { MagiClient, type JsonValue } from './client.ts'
import {
  activeExplorerLabel,
  runActiveExplorer,
} from './active-explorer.ts'

export const name = 'magi-memo'
export const inject = ['tools', 'systemPrompt', 'subagents']

export type MemoryMode = 'auto' | 'manual' | 'explore' | 'off'

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
      'For every user turn, run a Recall Gate before answering. When prior memory may materially help and hidden graph relations may matter, call magi_memory_explore once with a focused question; it performs mix recall followed by one isolated active-retrieval Sub-Agent.',
      'Use magi_memory_recall instead only when ordinary retrieval is sufficient. Never call magi_memory_expand or magi_memory_evidence directly; those are isolated Explorer tools.',
      'Treat the Explorer result as retrieved context, not as the final answer. Its findings contain the selected graph descriptions and semantic evidence conclusions.',
      'After forming the answer, run the same Write Gate as auto mode. Write only new durable information, and make any write the final tool step before the user-visible answer.',
    ].join('\n')
  }
  return [
    ...common,
    'For every user turn, run a Recall Gate before answering: decide whether prior preferences, decisions, people, project facts, or episodes may materially improve the answer. Recall only when useful; do not call it mechanically.',
    'When recalling, choose the retrieval mode deliberately: local for entity-focused context, global for broad themes, hybrid for graph-wide plus entity context, naive for direct chunk similarity, and mix for the recommended graph-plus-vector retrieval.',
    'After forming the answer, run a Write Gate at the end of the turn: decide whether the turn contains new, durable information worth retaining. Do not write on every turn.',
    'Write only durable, useful memory; never store secrets, credentials, raw tool noise, or facts useful only for the current turn.',
    'When the turn already provides enough structure, use magi_memory_write_extracted and submit the Episode evidence plus entities, relations, and atomic facts. Use magi_memory_write_episode only when MAGI should perform the normal extraction stage.',
    'Before every extracted write, run a Temporal Pass over every entity and relation Atom according to the MAGI temporal contract above; do not omit temporal fields merely because the evidence uses relative phrases such as today, last Friday, next week, or before graduation.',
    'If writing, make the memory write the final tool step before the user-visible answer. A successful extracted write means MAGI accepted background processing; do not poll for resolution or projection unless the user asks.',
  ].join('\n')
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

  ctx.systemPrompt.section({
    name: 'magi-memo',
    order: 112,
    text: context => memoryModePrompt(
      context.agent === undefined
        ? config.defaultMemoryMode
        : foldMemoryMode(context.agent.session.events, config.defaultMemoryMode),
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
      return client.recall({
        query: args.query,
        ...(args.mode === undefined ? {} : { mode: args.mode }),
        ...(args.top_k === undefined ? {} : { topK: args.top_k }),
        ...(args.chunk_top_k === undefined ? {} : { chunkTopK: args.chunk_top_k }),
        ...(args.high_level_keywords === undefined ? {} : { highLevelKeywords: args.high_level_keywords }),
        ...(args.low_level_keywords === undefined ? {} : { lowLevelKeywords: args.low_level_keywords }),
        ...(args.enable_rerank === undefined ? {} : { enableRerank: args.enable_rerank }),
      }, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_expand',
    description: 'EXPLORER-ONLY: perform one complete entity-frontier hop and remove only candidates whose neighbor entity and unordered relation pair are both already in visit. Do not call from the parent agent.',
    parameters: {
      frontier: {
        type: 'array',
        required: true,
        items: { type: 'string' },
        description: 'Current frontier as exact entity names.',
      },
      visit: {
        type: 'object',
        required: true,
        additionalProperties: false,
        properties: {
          entities: { type: 'array', required: true, items: { type: 'string' } },
          relations: {
            type: 'array',
            required: true,
            items: { type: 'array', items: { type: 'string' } },
          },
        },
      },
    },
    output,
    async execute(args, exec) {
      return client.expand({
        frontier: args.frontier,
        visit: {
          entities: args.visit.entities,
          relations: args.visit.relations as Array<[string, string]>,
        },
        maxCandidatesPerFrontier: config.explorerMaxCandidatesPerFrontier,
      }, exec.signal)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'magi_memory_evidence',
    description: 'EXPLORER-ONLY: inspect Atom evidence and evolution history for entity or unordered relation owners addressed by names. After every call, add a concise conclusion to the matching findings notes.',
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
    name: 'magi_memory_explore',
    description: 'Run active memory retrieval for one focused question: seed from MAGI mix recall, then delegate graph expansion and evidence inspection to one isolated Explorer Sub-Agent. Returns only findings and explicit completion metadata.',
    parameters: {
      query: {
        type: 'string',
        required: true,
        description: 'A focused natural-language question containing at least 3 characters.',
      },
    },
    output,
    async execute(args, exec) {
      if (exec.agent === undefined) {
        throw new Error('magi_memory_explore requires an agent-owned tool execution')
      }
      return runActiveExplorer({
        query: args.query,
        signal: exec.signal,
        topK: config.explorerTopK,
        chunkTopK: config.explorerChunkTopK,
        maxTokens: config.explorerMaxTokens,
        recall: (input, signal) => client.recall(input, signal),
        start: async request => ctx.subagents.start(config.subagentProvider, {
          label: activeExplorerLabel(args.query),
          parent: exec.agent!,
          signal: exec.signal,
          prompt: [{ type: 'text', text: request.prompt }],
          outputSchema: request.outputSchema,
          toolFilter: request.toolFilter,
          agentOptions: { maxTokens: request.maxTokens },
        }),
      })
    },
  }))
}
