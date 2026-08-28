import assert from 'node:assert/strict'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import test from 'node:test'
import { MagiClient } from '../src/client.ts'

type Handler = (request: IncomingMessage, response: ServerResponse, body: string) => void

async function withServer(handler: Handler, run: (baseUrl: string) => Promise<void>): Promise<void> {
  const server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => handler(request, response, Buffer.concat(chunks).toString('utf8')))
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  try {
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('test server has no TCP address')
    await run(`http://127.0.0.1:${address.port}`)
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close(error => error === undefined ? resolve() : reject(error))
    })
  }
}

function json(response: ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(value))
}

test('creates and activates a workspace with API-key authentication', async () => {
  const seen: Array<{ method: string | undefined; url: string | undefined; body: string }> = []
  await withServer((request, response, body) => {
    assert.equal(request.headers['x-api-key'], 'secret')
    seen.push({ method: request.method, url: request.url, body })
    if (request.url === '/workspaces') {
      json(response, 200, { id: 'project-alpha', name: 'Project Alpha', active: false })
      return
    }
    json(response, 200, { active_workspace_id: 'project-alpha', status: 'ready' })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, apiKey: 'secret', timeoutMs: 1_000 })
    const result = await client.createAndActivateWorkspace(
      { name: 'Project Alpha', workspaceId: 'project-alpha' },
      new AbortController().signal,
    )
    assert.deepEqual(result, {
      workspace_id: 'project-alpha',
      name: 'Project Alpha',
      created: true,
      active: true,
    })
  })
  assert.deepEqual(seen, [
    {
      method: 'POST',
      url: '/workspaces',
      body: JSON.stringify({ name: 'Project Alpha', workspace_id: 'project-alpha' }),
    },
    { method: 'POST', url: '/workspaces/project-alpha/activate', body: '' },
  ])
})

test('reuses a matching workspace after a create conflict', async () => {
  const seen: string[] = []
  await withServer((request, response) => {
    seen.push(`${request.method} ${request.url}`)
    if (request.method === 'POST') {
      json(response, 409, { detail: 'workspace already exists' })
      return
    }
    json(response, 200, {
      active_workspace_id: 'existing',
      workspace_home: '/tmp/workspaces',
      items: [{ id: 'existing', name: 'Existing', active: true }],
    })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const result = await client.createAndActivateWorkspace(
      { name: 'Existing', workspaceId: 'existing' },
      new AbortController().signal,
    )
    assert.deepEqual(result, {
      workspace_id: 'existing',
      name: 'Existing',
      created: false,
      active: true,
    })
  })
  assert.deepEqual(seen, ['POST /workspaces', 'GET /workspaces'])
})

test('activates an existing workspace by exact user-selected name', async () => {
  const seen: string[] = []
  await withServer((request, response) => {
    seen.push(`${request.method} ${request.url}`)
    if (request.method === 'GET') {
      json(response, 200, {
        active_workspace_id: 'current',
        items: [
          { id: 'current', name: 'Current', active: true },
          { id: 'daily-chat', name: 'Daily Chat', active: false },
        ],
      })
      return
    }
    json(response, 200, { active_workspace_id: 'daily-chat', status: 'ready' })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const result = await client.activateWorkspace(
      { workspace: 'Daily Chat' },
      new AbortController().signal,
    )
    assert.deepEqual(result, {
      workspace_id: 'daily-chat',
      name: 'Daily Chat',
      active: true,
      changed: true,
    })
  })
  assert.deepEqual(seen, [
    'GET /workspaces',
    'POST /workspaces/daily-chat/activate',
  ])
})

test('does not reactivate an already-active workspace', async () => {
  const seen: string[] = []
  await withServer((request, response) => {
    seen.push(`${request.method} ${request.url}`)
    json(response, 200, {
      active_workspace_id: 'daily-chat',
      items: [{ id: 'daily-chat', name: 'Daily Chat', active: true }],
    })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const result = await client.activateWorkspace(
      { workspace: 'daily-chat' },
      new AbortController().signal,
    )
    assert.deepEqual(result, {
      workspace_id: 'daily-chat',
      name: 'Daily Chat',
      active: true,
      changed: false,
    })
  })
  assert.deepEqual(seen, ['GET /workspaces'])
})

test('writes a stable Episode source through the normal document pipeline', async () => {
  let firstBody = ''
  let secondBody = ''
  let calls = 0
  await withServer((_request, response, body) => {
    calls += 1
    if (calls === 1) firstBody = body
    else secondBody = body
    json(response, 200, { status: 'success', track_id: `insert-${calls}` })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const signal = new AbortController().signal
    const first = await client.writeEpisode({ content: 'A durable memory.', sourceId: 'turn-42' }, signal)
    const second = await client.writeEpisode({ content: 'Updated wording.', sourceId: 'turn-42' }, signal)
    assert.equal(
      (first as { episode_source: string }).episode_source,
      (second as { episode_source: string }).episode_source,
    )
  })
  assert.equal(JSON.parse(firstBody).text, 'A durable memory.')
  assert.equal(JSON.parse(secondBody).text, 'Updated wording.')
  assert.match(JSON.parse(firstBody).file_source, /^magi-memo-[a-f0-9]{32}\.txt$/)
})

test('forwards already-extracted memory in the same request', async () => {
  let seenBody = ''
  await withServer((request, response, body) => {
    assert.equal(request.url, '/memory/ingest/extracted')
    seenBody = body
    json(response, 202, {
      episode_ids: ['episode-1'],
      indexed_count: 0,
      skipped_count: 0,
      track_id: 'ingest-extracted-1',
      status: 'accepted',
      completion: 'background',
    })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const payload = {
      content: 'Alice prefers concise status reports.',
      kind: 'conversation',
      metadata: {},
      entities: [{
        name: 'Alice',
        aliases: [],
        atoms: [{ content: 'Alice prefers concise status reports.', importance: 0.8 }],
      }],
      relations: [],
    }
    const result = await client.writeExtracted(payload, new AbortController().signal)
    assert.deepEqual(result, {
      episode_ids: ['episode-1'],
      indexed_count: 0,
      skipped_count: 0,
      track_id: 'ingest-extracted-1',
      status: 'accepted',
      completion: 'background',
    })
    const submitted = JSON.parse(seenBody)
    assert.deepEqual(submitted, {
      ...payload,
      reference_at: submitted.reference_at,
      wait_for_completion: false,
    })
    assert.match(submitted.reference_at, /^\d{4}-\d{2}-\d{2}T.*Z$/)
  })
})

test('forwards a relationship without explicit endpoint entities', async () => {
  let seenBody = ''
  await withServer((request, response, body) => {
    assert.equal(request.url, '/memory/ingest/extracted')
    seenBody = body
    json(response, 202, {
      episode_ids: ['episode-relation-only'],
      indexed_count: 0,
      skipped_count: 0,
      track_id: 'ingest-extracted-relation-only',
      status: 'accepted',
      completion: 'background',
    })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    await client.writeExtracted({
      content: 'Alice joined MAGI.',
      relations: [{
        source: 'Alice',
        target: 'MAGI',
        atoms: [{ content: 'Alice joined MAGI.', predicate: 'member_of' }],
      }],
    }, new AbortController().signal)
  })

  const submitted = JSON.parse(seenBody)
  assert.deepEqual(submitted.entities, [])
  assert.equal(submitted.relations[0].atoms.length, 1)
  assert.equal(submitted.wait_for_completion, false)
})

test('maps recall options to structured query-data retrieval', async () => {
  let seenBody = ''
  await withServer((request, response, body) => {
    assert.equal(request.url, '/query/data')
    seenBody = body
    json(response, 200, { status: 'success', message: 'ok', data: { chunks: [] }, metadata: { mode: 'mix' } })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    await client.recall({
      query: 'What does Alice prefer?',
      topK: 8,
      chunkTopK: 4,
      highLevelKeywords: ['preference'],
      lowLevelKeywords: ['Alice', 'status report'],
      enableRerank: true,
    }, new AbortController().signal)
  })
  assert.deepEqual(JSON.parse(seenBody), {
    query: 'What does Alice prefer?',
    mode: 'mix',
    top_k: 8,
    chunk_top_k: 4,
    hl_keywords: ['preference'],
    ll_keywords: ['Alice', 'status report'],
    enable_rerank: true,
  })
})

test('maps compact expand, progressive describe, and evidence while hiding MAGI stable ids', async () => {
  const seen: Array<{ url: string | undefined; body: unknown }> = []
  await withServer((request, response, body) => {
    seen.push({ url: request.url, body: JSON.parse(body) })
    if (request.url === '/memory/explore/expand') {
      json(response, 200, {
        status: 'complete',
        exhausted: false,
        results: [{
          candidates: [{
            entity: {
              name: 'Bob',
              entity_type: 'PERSON',
              magi_entity_id: 'entity-secret',
            },
            relation: {
              endpoints: ['Alice', 'Bob'],
              keywords: 'mentors',
              magi_relation_id: 'relation-secret',
            },
          }],
        }],
      })
      return
    }
    if (request.url === '/memory/explore/describe') {
      json(response, 200, {
        status: 'complete',
        owners: [{
          owner: { type: 'entity', name: 'Bob' },
          description: '[atom-entity-secret] [status=pending; valid_at=2027-01-01T00:00:00Z] Bob studies MAGI.',
          magi_owner_id: 'entity-secret',
        }],
      })
      return
    }
    json(response, 200, {
      status: 'complete',
      owners: [{ magi_owner_id: 'entity-secret', atoms: [{ content: 'Evidence' }] }],
    })
  }, async (baseUrl) => {
    const client = new MagiClient({ baseUrl, timeoutMs: 1_000 })
    const signal = new AbortController().signal
    await client.registerExploration(
      'explore-1',
      'Who is Alice?',
      signal,
      { entities: ['Alice', 'Bob'], relations: [['Alice', 'Bob']] },
    )
    const expanded = await client.expand({
      frontier: ['Alice'],
      visit: { entities: ['Alice'], relations: [] },
      maxCandidatesPerFrontier: 25,
      trace: {
        explorationId: 'explore-1',
        agentId: 's0',
        callId: 'call-1',
        query: 'How does Alice connect to the project?',
      },
    }, signal)
    const described = await client.describe([{ entity: 'Bob' }], signal)
    const evidence = await client.evidence([
      { entity: 'Alice' },
      { relation: ['Bob', 'Alice'] },
    ], signal)
    assert.doesNotMatch(JSON.stringify(expanded), /entity-secret|relation-secret|magi_.*_id/)
    assert.doesNotMatch(JSON.stringify(expanded), /description|\[atom-|\[status=|valid_at/)
    assert.match(JSON.stringify(expanded), /PERSON|mentors/)
    assert.doesNotMatch(JSON.stringify(described), /entity-secret|\[atom-|\[status=|valid_at/)
    assert.match(JSON.stringify(described), /Bob studies MAGI\./)
    assert.doesNotMatch(JSON.stringify(evidence), /entity-secret|magi_owner_id/)
  })

  assert.deepEqual(seen, [
    {
      url: '/memory/explore/traces',
      body: {
        exploration_id: 'explore-1',
        query: 'Who is Alice?',
        initial_visit: {
          entities: ['Alice', 'Bob'],
          relations: [['Alice', 'Bob']],
        },
      },
    },
    {
      url: '/memory/explore/expand',
      body: {
        frontier: ['Alice'],
        visit: { entities: ['Alice'], relations: [] },
        max_candidates_per_frontier: 25,
        trace: {
          exploration_id: 'explore-1',
          agent_id: 's0',
          call_id: 'call-1',
          subquery: 'How does Alice connect to the project?',
        },
      },
    },
    {
      url: '/memory/explore/describe',
      body: { owners: [{ entity: 'Bob' }] },
    },
    {
      url: '/memory/explore/evidence',
      body: {
        owners: [
          { entity: 'Alice' },
          { relation: ['Bob', 'Alice'] },
        ],
      },
    },
  ])
})
