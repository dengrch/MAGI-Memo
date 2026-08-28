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
  snapshotFindings(): Findings
  dispose(): Promise<void>
}

export interface ActiveExplorerInvocation {
  query: string
  signal: AbortSignal
  concurrent?: boolean
  /** Description-backed seeds handed off by a recall already completed by the Host. */
  initialSeeds?: ExplorerSeeds
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
    initialVisit: Visit
    initialFindings: Findings
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

export function cloneVisit(visit: Visit): Visit {
  return {
    entities: [...visit.entities],
    relations: visit.relations.map(pair => [...pair] as [string, string]),
  }
}

export function cloneFindings(findings: Findings): Findings {
  return {
    entities: findings.entities.map(owner => ({ name: owner.name, notes: [...owner.notes] })),
    relations: findings.relations.map(owner => ({
      endpoints: [...owner.endpoints] as [string, string],
      notes: [...owner.notes],
    })),
  }
}

export function cloneExplorerSeeds(seeds: ExplorerSeeds): ExplorerSeeds {
  return {
    frontier: [...seeds.frontier],
    visit: cloneVisit(seeds.visit),
    findings: cloneFindings(seeds.findings),
  }
}

export function addEntityFinding(visit: Visit, findings: Findings, name: string, note?: string): boolean {
  const normalized = name.trim().replace(/\s+/g, ' ')
  if (!normalized) return false
  const cleaned = note === undefined ? undefined : stripExplorerEvidenceMetadata(note)
  if (note !== undefined && !cleaned) return false
  const existingName = visit.entities.find(value => nameKey(value) === nameKey(normalized))
  const canonicalName = existingName ?? normalized
  if (existingName === undefined) visit.entities.push(canonicalName)
  if (!cleaned) return false
  let owner = findings.entities.find(value => nameKey(value.name) === nameKey(normalized))
  if (owner === undefined) {
    owner = { name: canonicalName, notes: [] }
    findings.entities.push(owner)
  }
  if (owner.notes.includes(cleaned)) return false
  owner.notes.push(cleaned)
  return true
}

export function addRelationFinding(
  visit: Visit,
  findings: Findings,
  endpoints: [string, string],
  note?: string,
): boolean {
  const pair = normalizeRelationPair(endpoints[0], endpoints[1])
  if (!pair[0] || !pair[1]) return false
  const cleaned = note === undefined ? undefined : stripExplorerEvidenceMetadata(note)
  if (note !== undefined && !cleaned) return false
  addEntityFinding(visit, findings, pair[0])
  addEntityFinding(visit, findings, pair[1])
  if (!visit.relations.some(value => relationKey(normalizeRelationPair(value[0], value[1])) === relationKey(pair))) {
    visit.relations.push(pair)
  }
  if (!cleaned) return false
  let owner = findings.relations.find(value => relationKey(normalizeRelationPair(...value.endpoints)) === relationKey(pair))
  if (owner === undefined) {
    owner = { endpoints: pair, notes: [] }
    findings.relations.push(owner)
  }
  if (owner.notes.includes(cleaned)) return false
  owner.notes.push(cleaned)
  return true
}

export function mergeFindings(visit: Visit, target: Findings, source: Findings): void {
  for (const owner of source.entities) {
    for (const note of owner.notes) addEntityFinding(visit, target, owner.name, note)
  }
  for (const owner of source.relations) {
    for (const note of owner.notes) addRelationFinding(visit, target, owner.endpoints, note)
  }
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
    report: { type: 'string' as const },
    stop_reason: { type: 'string' as const },
  },
  required: ['report', 'stop_reason'],
}

export const ACTIVE_EXPLORER_WORKER_OUTPUT_SCHEMA = {
  type: 'object' as const,
  additionalProperties: false,
  properties: {
    stop_reason: { type: 'string' as const },
  },
  required: ['stop_reason'],
}

/** Normalize one worker's structured terminal result for the coordinator tool. */
export function normalizeExplorerWorkerResult(
  structured: unknown,
  stopReason: string,
  findings: Findings,
): JsonValue {
  if (structured === undefined || !isRecord(structured) || typeof structured.stop_reason !== 'string') {
    return {
      status: 'abnormal',
      subagent_stop_reason: stopReason,
      findings: findings as unknown as JsonValue,
      stop_reason: `Worker stopped with ${stopReason} before producing a valid structured result.`,
    }
  }
  return {
    status: stopReason === 'completed' ? 'complete' : 'abnormal',
    subagent_stop_reason: stopReason,
    findings: findings as unknown as JsonValue,
    stop_reason: structured.stop_reason,
  }
}

/** One canonical three-layer retrieval state machine shared by s0-alone and workers. */
function progressiveExplorationContract(): string[] {
  return [
    'Canonical three-layer retrieval state machine:',
    '0. Inspect the supplied findings first. They are already description-backed; stop without expanding when they answer the assigned query.',
    '1. EXPAND — Choose the smallest promising frontier. Call magi_memory_expand with entity names only. The plugin owns and injects the complete visit state. Each frontier entity may be expanded at most once in this branch.',
    '2. SELECT — Inspect every compact candidate using only its entity name/type and relation endpoints/keywords, rank them against the assigned query, and provisionally select the smallest useful-looking subset. When expand returns at least one candidate, this subset must be non-empty; compact signals prioritize description loading but never justify skipping the description layer.',
    '3. DESCRIBE (MANDATORY) — Before another expand, make exactly one batched magi_memory_describe call. Represent every selected candidate with at least one relevant owner: its relation owner for a connection claim, its entity owner for node identity/context, or both when both semantics are needed. Calling describe is the final selection decision: successful returned owners and descriptions automatically enter plugin-managed visit/findings. Compact topology alone never creates a finding. Do not describe rejected candidates.',
    '4. EVIDENCE (OPTIONAL) — Call magi_memory_evidence only when a description leaves a query-critical factual, temporal, provenance, or evolution ambiguity. If it yields a useful conclusion, immediately call magi_memory_note with only that concise conclusion; never copy ids, status/time annotations, evidence rows, or evolution payloads.',
    '5. CONTINUE OR STOP — If no candidate was returned, stop. Otherwise continue from any entity visible in exploration history that has never been expanded and resolves a specific remaining gap. Relation endpoint pairs are unordered; de-duplicate visit and findings by normalized owner.',
    '',
    'Hard transition rules:',
    '- Every expand returning one or more candidates must be followed by SELECT and exactly one batched DESCRIBE before either another expand or normal completion. describe is mandatory; evidence is optional.',
    '- visit and findings are authoritative plugin state. Never pass, reproduce, or return them. describe commits both automatically; magi_memory_note records optional evidence conclusions.',
    '- The plugin maintains expanded separately from visit. A frontier may be any entity visible in exploration history, whether described or topology-only, but a repeated frontier is refused by the tool.',
    '- If expand returns status=refused, do not retry. Finish any already-required describe/evidence work, otherwise stop and report the refusal.',
    '- Missing frontier, partial/truncated output, tool failure, cancellation, or a safety/budget limit is abnormal rather than exhaustion. Preserve description-backed partial findings and explain the condition in stop_reason.',
  ]
}

/** Build the complete isolated instruction for one active-retrieval Sub-Agent. */
export function activeExplorerPrompt(query: string, seeds: ExplorerSeeds): string {
  const visibleSeeds = { findings: seeds.findings }
  return [
    'You are MAGI Active Memory Explorer v1. Investigate hidden relational memory for the parent query.',
    '',
    `PARENT QUERY:\n${query}`,
    '',
    `COMPACT INITIAL MIX STATE:\n${JSON.stringify(visibleSeeds, null, 2)}`,
    '',
    'State contract:',
    '- The plugin, not you, owns visit and findings. You see only the initial findings and each tool result needed for the next decision.',
    '- describe automatically stores selected owners and their descriptions. magi_memory_note stores concise conclusions derived from optional evidence.',
    '',
    ...progressiveExplorationContract(),
    'Brief narration of your selection and tool-use reasoning is allowed for debugging, but keep it concise.',
    '',
    'Finish by calling structured_output with exactly:',
    '{"report":"concise exploration process and conclusion report","stop_reason":"short completion or abnormal-stop explanation"}',
    'Do not return visit or findings; the plugin attaches its authoritative snapshot. Never expose MAGI stable ids.',
  ].join('\n')
}

/** Build the s0 coordinator instruction used by concurrent active retrieval. */
export function concurrentExplorerPrompt(
  query: string,
  seeds: ExplorerSeeds,
): string {
  const visibleSeeds = { findings: seeds.findings }
  return [
    'You are MAGI Active Memory Coordinator s0. Investigate the parent query by dynamically delegating focused graph searches to isolated worker Sub-Agents.',
    '',
    `PARENT QUERY:\n${query}`,
    '',
    `COMPACT INITIAL MIX STATE:\n${JSON.stringify(visibleSeeds, null, 2)}`,
    '',
    'Coordinator contract:',
    '- Do not call magi_memory_expand, magi_memory_describe, or magi_memory_evidence yourself. Call only magi_memory_delegate for graph exploration.',
    '- Tasks in one delegate call execute concurrently. Create only genuinely necessary tasks; do not delegate merely to increase worker count.',
    '- Derive each task subquery from the parent query plus all context accumulated so far. Each subquery must name one specific unresolved fact, not paraphrase the parent query. Assign only frontier entities that plausibly lead to that fact.',
    '- For every task, provide known_owners containing only owner references for the smallest plugin-managed findings subset useful to that subquery. The plugin resolves their notes; never copy notes into delegate arguments.',
    '- Use distinct subqueries that cover genuinely different reasoning branches. Two tasks are not distinct merely because their wording differs; their unresolved target or intended reasoning path must differ. Do not split merely to fill every worker slot.',
    '- Delegation is optional. First judge whether the initial findings already answer the parent query. Delegate only for a concrete missing reasoning branch that requires graph expansion; zero delegation is the preferred outcome when evidence is already sufficient.',
    '- Treat every worker and delegate batch as expensive. As soon as accumulated findings are sufficient to answer the parent query, stop. Continue only when you can name a specific unresolved fact and a genuinely useful frontier. Do not paraphrase an earlier subquery into a supposedly new task.',
    '- Workers are isolated and preserve their own complete reasoning chain. They return findings, not shared visit state. There is intentionally no cross-worker coverage lock or candidate suppression.',
    '- Every worker follows the same mandatory three-layer branch protocol: compact expand, one batched describe for selected candidates, then optional evidence. Treat only description-backed worker notes as findings; topology-only names/keywords are never evidence.',
    '- Worker findings are automatically merged into s0 plugin state before each delegate result is returned. Reason about the returned branch deltas, then stop or issue a genuinely necessary follow-up.',
    '- The first batch normally chooses frontier owners named in the initial findings. Later batches should prefer useful description-backed entity findings returned by workers. Reusing an earlier frontier is allowed only when accumulated findings expose a genuinely different unresolved branch that requires an independent interpretation of that node.',
    '- Never repeat the same subquery + frontier task, and never disguise a repeated branch by merely rewording its subquery.',
    '- Keep the initial findings even when no delegation is needed. Never expose MAGI stable ids.',
    '',
    'Finish by calling structured_output with exactly this object shape; every key is required:',
    '{"report":"which subqueries/frontiers were delegated, what they found, and why the merged evidence is sufficient or blocked","stop_reason":"short completion or abnormal-stop explanation"}',
    'report must concisely record the worker/subquery allocation, exploration result, and merge decision. Never return visit or findings.',
  ].join('\n')
}

/** Build one isolated worker instruction for an s0-selected subquery/frontier. */
export function activeExplorerWorkerPrompt(input: {
  parentQuery: string
  subquery: string
  frontier: string[]
  knownFindings: Findings
}): string {
  return [
    'You are an isolated MAGI active-retrieval worker. Follow one assigned reasoning branch and return evidence to coordinator s0.',
    '',
    `PARENT QUERY:\n${input.parentQuery}`,
    '',
    `ASSIGNED SUBQUERY:\n${input.subquery}`,
    '',
    `ASSIGNED INITIAL FRONTIER:\n${JSON.stringify(input.frontier)}`,
    '',
    `S0-SELECTED KNOWN FINDINGS FOR THIS SUBQUERY:\n${JSON.stringify(input.knownFindings, null, 2)}`,
    '',
    'The plugin owns private visit and findings state. Never pass or reproduce either one. describe automatically commits selected descriptions; use magi_memory_note only for concise conclusions from optional evidence.',
    ...progressiveExplorationContract(),
    'Stay inside the assigned subquery. Do not attempt to coordinate with siblings and do not broaden back into the whole parent query.',
    'The known findings above are the minimal subquery-relevant subset selected by s0, not the complete exploration state. Use them as context. The plugin will expose only this branch\'s newly discovered or materially strengthened notes to s0.',
    'Stop as soon as existing findings answer the assigned subquery, the branch is exhausted, or described findings reveal no useful continuation. Do not expand merely because another frontier is available.',
    '',
    'Finish with structured_output using exactly:',
    '{"stop_reason":"short branch completion or abnormal-stop explanation"}',
    'Never return visit, findings, or MAGI stable ids.',
  ].join('\n')
}

/** Execute one disposable Explorer, reusing Host recall seeds or cold-starting with one Mix recall. */
export async function runActiveExplorer(invocation: ActiveExplorerInvocation): Promise<JsonValue> {
  const seeds = invocation.initialSeeds === undefined
    ? parseRecallSeeds(await invocation.recall({
        query: invocation.query,
        mode: 'mix',
        topK: invocation.topK,
        chunkTopK: invocation.chunkTopK,
      }, invocation.signal))
    : cloneExplorerSeeds(invocation.initialSeeds)
  const concurrent = invocation.concurrent ?? false
  const run = await invocation.start({
    prompt: concurrent
      ? concurrentExplorerPrompt(invocation.query, seeds)
      : activeExplorerPrompt(invocation.query, seeds),
    initialVisit: seeds.visit,
    initialFindings: seeds.findings,
    outputSchema: ACTIVE_EXPLORER_OUTPUT_SCHEMA,
    toolFilter: {
      allow: concurrent
        ? ['magi_memory_delegate']
        : ['magi_memory_expand', 'magi_memory_describe', 'magi_memory_evidence', 'magi_memory_note'],
    },
    maxTokens: invocation.maxTokens,
  })
  try {
    const result = await run.result
    const findings = run.snapshotFindings()
    if (result.structured === undefined || !isRecord(result.structured)) {
      return {
        status: 'abnormal',
        subagent_stop_reason: result.stopReason,
        findings: findings as unknown as JsonValue,
        report: concurrent
          ? 'The coordinator did not produce a valid structured merge.'
          : 'The explorer did not produce a valid structured result.',
        stop_reason: `Explorer stopped with ${result.stopReason} before producing a valid structured result.`,
      }
    }
    return {
      status: result.stopReason === 'completed' ? 'complete' : 'abnormal',
      subagent_stop_reason: result.stopReason,
      findings: findings as unknown as JsonValue,
      report: typeof result.structured.report === 'string' ? result.structured.report : '',
      stop_reason: typeof result.structured.stop_reason === 'string'
        ? result.structured.stop_reason
        : `Explorer stopped with ${result.stopReason} without a stop reason.`,
    }
  } finally {
    await run.dispose()
  }
}
