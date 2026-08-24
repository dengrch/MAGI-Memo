import {
  stripExplorerEvidenceMetadata,
  type JsonValue,
} from './client.ts'

export interface Visit {
  entities: string[]
  relations: Array<[string, string]>
}

export interface FindingOwner {
  name: string
  notes: string[]
}

export interface RelationFindingOwner {
  endpoints: [string, string]
  notes: string[]
}

export interface Findings {
  entities: FindingOwner[]
  relations: RelationFindingOwner[]
}

export interface ExplorerSeeds {
  frontier: string[]
  visit: Visit
  findings: Findings
}

export interface ActiveExplorerRun {
  result: Promise<{
    structured?: unknown
    stopReason: string
  }>
  dispose(): Promise<void>
}

export interface ActiveExplorerInvocation {
  query: string
  signal: AbortSignal
  topK: number
  chunkTopK: number
  maxTokens: number
  recall(input: {
    query: string
    mode: 'mix'
    topK: number
    chunkTopK: number
  }, signal: AbortSignal): Promise<JsonValue>
  start(input: {
    prompt: string
    outputSchema: typeof ACTIVE_EXPLORER_OUTPUT_SCHEMA
    toolFilter: { allow: string[] }
    maxTokens: number
  }): Promise<ActiveExplorerRun>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function nameKey(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

export function normalizeRelationPair(source: string, target: string): [string, string] {
  const endpoints: [string, string] = [source.trim().replace(/\s+/g, ' '), target.trim().replace(/\s+/g, ' ')]
  endpoints.sort((left, right) => nameKey(left).localeCompare(nameKey(right)))
  return endpoints
}

function relationKey(pair: [string, string]): string {
  return `${nameKey(pair[0])}\u0000${nameKey(pair[1])}`
}

/** Give durable one-shot histories a human-readable, restart-safe identity. */
export function activeExplorerLabel(query: string, startedAt = new Date()): string {
  const normalized = query.trim().replace(/\s+/g, ' ')
  const characters = Array.from(normalized)
  const preview = characters.length <= 42
    ? normalized
    : `${characters.slice(0, 42).join('')}…`
  const timestamp = startedAt.toISOString().replace(/\.\d{3}Z$/, 'Z')
  return `MAGI explore · ${preview} · ${timestamp}`
}

/** Convert one structured mix-recall response into the Explorer's only two states. */
export function parseRecallSeeds(recall: JsonValue): ExplorerSeeds {
  const visit: Visit = { entities: [], relations: [] }
  const findings: Findings = { entities: [], relations: [] }
  const frontier: string[] = []
  const entityIndexes = new Map<string, number>()
  const relationIndexes = new Map<string, number>()
  const entityFindingIndexes = new Map<string, number>()
  const relationFindingIndexes = new Map<string, number>()

  const addEntity = (name: string, note?: string) => {
    const normalized = name.trim().replace(/\s+/g, ' ')
    if (!normalized) return
    const key = nameKey(normalized)
    let index = entityIndexes.get(key)
    if (index === undefined) {
      index = visit.entities.length
      entityIndexes.set(key, index)
      visit.entities.push(normalized)
      frontier.push(normalized)
    }
    if (note?.trim()) {
      const cleaned = stripExplorerEvidenceMetadata(note)
      if (!cleaned) return
      let findingIndex = entityFindingIndexes.get(key)
      if (findingIndex === undefined) {
        findingIndex = findings.entities.length
        entityFindingIndexes.set(key, findingIndex)
        findings.entities.push({ name: normalized, notes: [] })
      }
      const notes = findings.entities[findingIndex]!.notes
      if (!notes.includes(cleaned)) notes.push(cleaned)
    }
  }

  const addRelation = (source: string, target: string, note?: string) => {
    const pair = normalizeRelationPair(source, target)
    if (!pair[0] || !pair[1]) return
    addEntity(pair[0])
    addEntity(pair[1])
    const key = relationKey(pair)
    let index = relationIndexes.get(key)
    if (index === undefined) {
      index = visit.relations.length
      relationIndexes.set(key, index)
      visit.relations.push(pair)
    }
    if (note?.trim()) {
      const cleaned = stripExplorerEvidenceMetadata(note)
      if (!cleaned) return
      let findingIndex = relationFindingIndexes.get(key)
      if (findingIndex === undefined) {
        findingIndex = findings.relations.length
        relationFindingIndexes.set(key, findingIndex)
        findings.relations.push({ endpoints: pair, notes: [] })
      }
      const notes = findings.relations[findingIndex]!.notes
      if (!notes.includes(cleaned)) notes.push(cleaned)
    }
  }

  const data = isRecord(recall) && isRecord(recall.data) ? recall.data : undefined
  const entities = data !== undefined && Array.isArray(data.entities) ? data.entities : []
  for (const item of entities) {
    if (!isRecord(item)) continue
    const name = nonEmptyString(item.entity_name) ?? nonEmptyString(item.name)
    if (name !== undefined) addEntity(name, nonEmptyString(item.description))
  }

  const relations = data !== undefined && Array.isArray(data.relationships) ? data.relationships : []
  for (const item of relations) {
    if (!isRecord(item)) continue
    const source = nonEmptyString(item.src_id) ?? nonEmptyString(item.source)
    const target = nonEmptyString(item.tgt_id) ?? nonEmptyString(item.target)
    if (source === undefined || target === undefined) continue
    const description = nonEmptyString(item.description)
    const keywords = nonEmptyString(item.keywords)
    const note = [description, keywords === undefined ? undefined : `Keywords: ${keywords}`]
      .filter((value): value is string => value !== undefined)
      .join('\n')
    addRelation(source, target, note || undefined)
  }

  return { frontier, visit, findings }
}

export const ACTIVE_EXPLORER_OUTPUT_SCHEMA = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    findings: {
      type: 'object' as const,
      additionalProperties: false,
      properties: {
        entities: {
          type: 'array' as const,
          items: {
            type: 'object' as const,
            additionalProperties: false,
            properties: {
              name: { type: 'string' as const },
              notes: { type: 'array' as const, items: { type: 'string' as const } },
            },
            required: ['name', 'notes'],
          },
        },
        relations: {
          type: 'array' as const,
          items: {
            type: 'object' as const,
            additionalProperties: false,
            properties: {
              endpoints: {
                type: 'array' as const,
                items: { type: 'string' as const },
              },
              notes: { type: 'array' as const, items: { type: 'string' as const } },
            },
            required: ['endpoints', 'notes'],
          },
        },
      },
      required: ['entities', 'relations'],
    },
    stop_reason: { type: 'string' as const },
  },
  required: ['findings', 'stop_reason'],
}

function invalidRelationPair(value: unknown): boolean {
  return !Array.isArray(value)
    || value.length !== 2
    || value.some(endpoint => typeof endpoint !== 'string' || !endpoint.trim())
}

/** Enforce tuple cardinality that dsh's portable JSON-Schema subset cannot express. */
function structuredPairError(value: unknown): string | undefined {
  if (!isRecord(value) || !isRecord(value.findings)) {
    return 'Explorer structured result is missing findings.'
  }
  if (!Array.isArray(value.findings.relations)
    || value.findings.relations.some(item => (
      !isRecord(item) || invalidRelationPair(item.endpoints)
    ))) {
    return 'Explorer findings.relations must contain only two-name endpoint pairs.'
  }
  return undefined
}

/** Build the complete isolated instruction for one active-retrieval Sub-Agent. */
export function activeExplorerPrompt(query: string, seeds: ExplorerSeeds): string {
  return [
    'You are MAGI Active Memory Explorer v1. Investigate hidden relational memory for the parent query.',
    '',
    `PARENT QUERY:\n${query}`,
    '',
    `COMPACT INITIAL MIX STATE:\n${JSON.stringify(seeds, null, 2)}`,
    '',
    'State contract:',
    '- Maintain exactly two semantic accumulators: visit and findings. Do not create seen, deferred, expanded, path, or other graph-selection state.',
    '- visit.entities contains selected entity names. visit.relations contains selected unordered endpoint-name pairs.',
    '- findings stores descriptions from every selected candidate and concise conclusions from evidence calls under the matching entity or relation owner.',
    '',
    'Exploration loop:',
    '1. Before each expand, choose the frontier entities you judge most promising for the parent query. There is no fixed count: prefer focused expansion over expanding every available entity merely because it is in visit. Any unchosen entity remains available for later reconsideration.',
    '2. Call magi_memory_expand with that entity-name frontier and the complete current visit.',
    '3. Inspect every returned (relation, neighbor entity) candidate. Select candidates that may help the parent query. A selected candidate adds both its relation pair and neighbor entity to visit, records their useful descriptions in findings, and puts the selected entity into the next frontier.',
    '4. In the same round, reconsider useful candidates from earlier tool results still present in context. Earlier rejection is not permanent. Add any newly selected historical candidates exactly as above.',
    '5. When evidence is needed, choose only the owners you judge most promising for resolving the question or an actual ambiguity. There is no fixed count. After each magi_memory_evidence call, write a concise semantic conclusion under the matching findings owner; do not copy raw Atom ids, status/time annotations, evidence rows, or evolution payloads into findings.',
    '6. Relation pairs are unordered. De-duplicate visit by normalized entity name and unordered endpoint pair when updating it.',
    'Brief narration of your selection and tool-use reasoning is allowed for debugging, but keep it concise.',
    '',
    'The only normal stopping rule is:',
    '(the current expand is truly exhausted OR you select no candidate from its current results) AND you add no candidate from historical context in the same round.',
    'Here, truly exhausted means the tool explicitly returns exhausted=true. Missing frontier, partial status, truncation, tool failure, cancellation, or a safety/budget limit is not exhaustion. If such an abnormal condition prevents continuation, preserve partial visit/findings and explain it in stop_reason.',
    '',
    'visit is internal exploration state: keep using it for expand de-duplication, but do not return it to the parent Agent.',
    'Finish by calling structured_output with exactly this object shape; every shown key is required even when its array is empty:',
    '{"findings":{"entities":[],"relations":[]},"stop_reason":"short completion or abnormal-stop explanation"}',
    'Populate findings.entities only with {"name":"...","notes":["..."]} objects and findings.relations only with {"endpoints":["...","..."],"notes":["..."]} objects. findings.notes is invalid.',
    'Never omit findings.entities, findings.relations, or stop_reason. Never expose or copy MAGI stable ids.',
  ].join('\n')
}

/** Execute exactly one mix recall and one disposable Explorer Sub-Agent run. */
export async function runActiveExplorer(invocation: ActiveExplorerInvocation): Promise<JsonValue> {
  const recall = await invocation.recall({
    query: invocation.query,
    mode: 'mix',
    topK: invocation.topK,
    chunkTopK: invocation.chunkTopK,
  }, invocation.signal)
  const seeds = parseRecallSeeds(recall)
  const run = await invocation.start({
    prompt: activeExplorerPrompt(invocation.query, seeds),
    outputSchema: ACTIVE_EXPLORER_OUTPUT_SCHEMA,
    toolFilter: { allow: ['magi_memory_expand', 'magi_memory_evidence'] },
    maxTokens: invocation.maxTokens,
  })
  try {
    const result = await run.result
    const pairError = result.structured === undefined
      ? undefined
      : structuredPairError(result.structured)
    if (result.structured === undefined || pairError !== undefined) {
      return {
        status: 'abnormal',
        subagent_stop_reason: result.stopReason,
        findings: seeds.findings as unknown as JsonValue,
        stop_reason: pairError
          ?? `Explorer stopped with ${result.stopReason} before producing a valid structured result.`,
      }
    }
    return {
      status: result.stopReason === 'completed' ? 'complete' : 'abnormal',
      subagent_stop_reason: result.stopReason,
      ...(result.structured as Record<string, JsonValue>),
    }
  } finally {
    await run.dispose()
  }
}
