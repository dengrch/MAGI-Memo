import { createHash } from 'node:crypto'

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue }

export interface MagiClientConfig {
  baseUrl: string
  apiKey?: string
  timeoutMs: number
}

export interface WorkspaceCreateInput {
  name: string
  workspaceId?: string
}

export interface WorkspaceActivateInput {
  workspace: string
}

export interface EpisodeWriteInput {
  content: string
  sourceId?: string
}

export interface RecallInput {
  query: string
  mode?: 'local' | 'global' | 'hybrid' | 'naive' | 'mix'
  topK?: number
  chunkTopK?: number
  highLevelKeywords?: string[]
  lowLevelKeywords?: string[]
  enableRerank?: boolean
}

export interface VisitInput {
  entities: string[]
  relations: Array<[string, string]>
}

export interface ExpandInput {
  frontier: string[]
  visit: VisitInput
  maxCandidatesPerFrontier?: number
}

export type EvidenceOwnerInput =
  | { entity: string }
  | { relation: [string, string] }

interface WorkspaceItem {
  id: string
  name: string
  active: boolean
}

interface WorkspaceListResponse {
  active_workspace_id: string | null
  items: WorkspaceItem[]
}

interface WorkspaceCreateResponse {
  id: string
  name: string
  active: boolean
}

export class MagiHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: JsonValue,
  ) {
    super(message)
    this.name = 'MagiHttpError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isWorkspaceItem(value: unknown): value is WorkspaceItem {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.active === 'boolean'
}

function parseWorkspaceList(value: JsonValue): WorkspaceListResponse {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error('MAGI returned an invalid workspace list')
  }
  const items: WorkspaceItem[] = []
  for (const item of value.items) {
    if (!isWorkspaceItem(item)) throw new Error('MAGI returned an invalid workspace list')
    items.push(item)
  }
  const active = value.active_workspace_id
  if (active !== null && typeof active !== 'string') {
    throw new Error('MAGI returned an invalid active workspace id')
  }
  return { active_workspace_id: active, items }
}

function parseWorkspaceCreate(value: JsonValue): WorkspaceCreateResponse {
  if (!isWorkspaceItem(value)) {
    throw new Error('MAGI returned an invalid workspace creation response')
  }
  return value
}

function errorMessage(status: number, body: JsonValue): string {
  if (isRecord(body) && typeof body.detail === 'string') return body.detail
  if (isRecord(body) && typeof body.message === 'string') return body.message
  return `MAGI request failed with HTTP ${status}`
}

function nonEmpty(value: string, field: string): string {
  const normalized = value.trim()
  if (!normalized) throw new Error(`${field} must not be empty`)
  return normalized
}

function positiveInteger(value: number | undefined, field: string): number | undefined {
  if (value === undefined) return undefined
  if (!Number.isInteger(value) || value < 1) throw new Error(`${field} must be a positive integer`)
  return value
}

function compactObject(entries: Record<string, JsonValue | undefined>): Record<string, JsonValue> {
  return Object.fromEntries(
    Object.entries(entries).filter((entry): entry is [string, JsonValue] => entry[1] !== undefined),
  )
}

/** Stable Entity/Relation ids validate Core addressing but never enter model context. */
export function hideMagiStableIds(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(hideMagiStableIds)
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !['magi_entity_id', 'magi_relation_id', 'magi_owner_id'].includes(key))
      .map(([key, child]) => [key, hideMagiStableIds(child as JsonValue)]),
  )
}

/** Keep graph previews semantic; Atom lineage and temporal metadata belong to evidence. */
export function stripExplorerEvidenceMetadata(value: string): string {
  return value
    .replace(/\[atom-[^\]\r\n]+\]\s*/gi, '')
    .replace(/\[status=[^\]\r\n]+\]\s*/gi, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .trim()
}

/** Filter only expandable graph descriptions; evidence responses stay lossless. */
export function compactExplorerExpandResult(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(compactExplorerExpandResult)
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [
      key,
      key === 'description' && typeof child === 'string'
        ? stripExplorerEvidenceMetadata(child)
        : compactExplorerExpandResult(child as JsonValue),
    ]),
  )
}

export class MagiClient {
  readonly baseUrl: string
  readonly apiKey: string | undefined
  readonly timeoutMs: number

  constructor(config: MagiClientConfig) {
    this.baseUrl = config.baseUrl.trim().replace(/\/+$/, '')
    if (!this.baseUrl) throw new Error('MAGI baseUrl must not be empty')
    if (!Number.isInteger(config.timeoutMs) || config.timeoutMs < 1) {
      throw new Error('MAGI timeoutMs must be a positive integer')
    }
    this.apiKey = config.apiKey?.trim() || undefined
    this.timeoutMs = config.timeoutMs
  }

  private async request(
    method: 'GET' | 'POST',
    path: string,
    signal: AbortSignal,
    body?: JsonValue,
  ): Promise<JsonValue> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (this.apiKey) headers['X-API-Key'] = this.apiKey

    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)]),
    })
    const text = await response.text()
    let value: JsonValue = null
    if (text) {
      try {
        value = JSON.parse(text) as JsonValue
      } catch {
        throw new Error(`MAGI returned non-JSON data from ${path}`)
      }
    }
    if (!response.ok) {
      throw new MagiHttpError(errorMessage(response.status, value), response.status, value)
    }
    return value
  }

  async createAndActivateWorkspace(
    input: WorkspaceCreateInput,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    const name = nonEmpty(input.name, 'name')
    const workspaceId = input.workspaceId === undefined
      ? undefined
      : nonEmpty(input.workspaceId, 'workspace_id')
    let workspace: WorkspaceItem
    let created = true

    try {
      workspace = parseWorkspaceCreate(await this.request(
        'POST',
        '/workspaces',
        signal,
        compactObject({ name, workspace_id: workspaceId }),
      ))
    } catch (error) {
      if (!(error instanceof MagiHttpError) || error.status !== 409) throw error
      const list = parseWorkspaceList(await this.request('GET', '/workspaces', signal))
      const existing = list.items.find(item => (
        workspaceId === undefined ? item.name === name : item.id === workspaceId
      ))
      if (!existing) throw error
      workspace = existing
      created = false
    }

    if (!workspace.active) {
      await this.request(
        'POST',
        `/workspaces/${encodeURIComponent(workspace.id)}/activate`,
        signal,
      )
    }
    return {
      workspace_id: workspace.id,
      name: workspace.name,
      created,
      active: true,
    }
  }

  async activateWorkspace(
    input: WorkspaceActivateInput,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    const requested = nonEmpty(input.workspace, 'workspace')
    const list = parseWorkspaceList(await this.request('GET', '/workspaces', signal))
    const workspace = list.items.find(item => item.id === requested)
      ?? list.items.find(item => item.name === requested)
    if (workspace === undefined) {
      throw new Error(`MAGI workspace not found: ${requested}`)
    }
    if (!workspace.active) {
      await this.request(
        'POST',
        `/workspaces/${encodeURIComponent(workspace.id)}/activate`,
        signal,
      )
    }
    return {
      workspace_id: workspace.id,
      name: workspace.name,
      active: true,
      changed: !workspace.active,
    }
  }

  async writeEpisode(input: EpisodeWriteInput, signal: AbortSignal): Promise<JsonValue> {
    const content = nonEmpty(input.content, 'content')
    const sourceIdentity = input.sourceId === undefined
      ? content
      : nonEmpty(input.sourceId, 'source_id')
    const digest = createHash('sha256').update(sourceIdentity).digest('hex').slice(0, 32)
    const fileSource = `magi-memo-${digest}.txt`
    const response = await this.request('POST', '/documents/text', signal, {
      text: content,
      file_source: fileSource,
    })
    return {
      episode_source: fileSource,
      accepted: true,
      pipeline: response,
    }
  }

  async writeExtracted(payload: JsonValue, signal: AbortSignal): Promise<JsonValue> {
    if (!isRecord(payload)) throw new Error('extracted memory payload must be an object')
    if (typeof payload.content !== 'string' || !payload.content.trim()) {
      throw new Error('content must not be empty')
    }
    const entities = payload.entities ?? []
    const relations = payload.relations ?? []
    if (!Array.isArray(entities)) throw new Error('entities must be an array')
    if (!Array.isArray(relations)) throw new Error('relations must be an array')
    if (entities.length === 0 && relations.length === 0) {
      throw new Error('extracted memory must contain an entity or a relationship')
    }
    return this.request('POST', '/memory/ingest/extracted', signal, {
      ...payload,
      entities,
      relations,
      reference_at: typeof payload.reference_at === 'string' && payload.reference_at.trim()
        ? payload.reference_at
        : new Date().toISOString(),
      wait_for_completion: false,
    })
  }

  async recall(input: RecallInput, signal: AbortSignal): Promise<JsonValue> {
    const query = nonEmpty(input.query, 'query')
    if (query.length < 3) throw new Error('query must contain at least 3 characters')
    return hideMagiStableIds(await this.request('POST', '/query/data', signal, compactObject({
      query,
      mode: input.mode ?? 'mix',
      top_k: positiveInteger(input.topK, 'top_k'),
      chunk_top_k: positiveInteger(input.chunkTopK, 'chunk_top_k'),
      hl_keywords: input.highLevelKeywords,
      ll_keywords: input.lowLevelKeywords,
      enable_rerank: input.enableRerank,
    })))
  }

  async expand(input: ExpandInput, signal: AbortSignal): Promise<JsonValue> {
    if (input.frontier.length === 0) throw new Error('frontier must not be empty')
    const frontier = input.frontier.map((name, index) => nonEmpty(name, `frontier[${index}]`))
    const entities = input.visit.entities.map((name, index) => nonEmpty(name, `visit.entities[${index}]`))
    const relations = input.visit.relations.map(([source, target], index) => [
      nonEmpty(source, `visit.relations[${index}][0]`),
      nonEmpty(target, `visit.relations[${index}][1]`),
    ] as [string, string])
    const result = hideMagiStableIds(await this.request('POST', '/memory/explore/expand', signal, compactObject({
      frontier,
      visit: { entities, relations },
      max_candidates_per_frontier: positiveInteger(
        input.maxCandidatesPerFrontier,
        'max_candidates_per_frontier',
      ),
    })))
    return compactExplorerExpandResult(result)
  }

  async evidence(owners: EvidenceOwnerInput[], signal: AbortSignal): Promise<JsonValue> {
    if (owners.length === 0) throw new Error('owners must not be empty')
    const normalized = owners.map((owner, index) => {
      if ('entity' in owner) return { entity: nonEmpty(owner.entity, `owners[${index}].entity`) }
      return {
        relation: [
          nonEmpty(owner.relation[0], `owners[${index}].relation[0]`),
          nonEmpty(owner.relation[1], `owners[${index}].relation[1]`),
        ],
      }
    })
    return hideMagiStableIds(await this.request(
      'POST',
      '/memory/explore/evidence',
      signal,
      { owners: normalized },
    ))
  }
}
