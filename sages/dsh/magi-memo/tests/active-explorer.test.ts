import assert from 'node:assert/strict'
import test from 'node:test'
import { assertObjectJsonSchema } from '@deepseek-ai/dsh-tools'
import {
  ACTIVE_EXPLORER_OUTPUT_SCHEMA,
  addEntityFinding,
  addRelationFinding,
  activeExplorerWorkerPrompt,
  activeExplorerLabel,
  activeExplorerPrompt,
  concurrentExplorerPrompt,
  normalizeExplorerWorkerResult,
  parseRecallSeeds,
  runActiveExplorer,
} from '../src/active-explorer.ts'
import type { JsonValue } from '../src/client.ts'

const recall: JsonValue = {
  status: 'success',
  data: {
    entities: [
      {
        entity_name: 'Alice',
        description: '[atom-secret] [status=active; valid_at=2026-01-01T00:00:00Z] Alice leads MAGI.',
        magi_entity_id: 'hidden',
      },
    ],
    relationships: [
      {
        src_id: 'Bob',
        tgt_id: 'Alice',
        description: 'Bob works with Alice.',
        keywords: 'collaboration',
        magi_relation_id: 'hidden',
      },
    ],
  },
}

test('seeds only visit and findings from mix recall with unordered relation pairs', () => {
  const seeds = parseRecallSeeds(recall)
  assert.deepEqual(seeds.frontier, ['Alice', 'Bob'])
  assert.deepEqual(seeds.visit, {
    entities: ['Alice', 'Bob'],
    relations: [['Alice', 'Bob']],
  })
  assert.deepEqual(seeds.findings.entities, [
    { name: 'Alice', notes: ['Alice leads MAGI.'] },
  ])
  assert.deepEqual(seeds.findings.relations, [{
    endpoints: ['Alice', 'Bob'],
    notes: ['Bob works with Alice.\nKeywords: collaboration'],
  }])
})

test('omits a seed finding whose description contains only evidence metadata', () => {
  const seeds = parseRecallSeeds({
    data: {
      entities: [{
        entity_name: 'Alice',
        description: '[atom-secret] [status=active; valid_at=2026-01-01T00:00:00Z]',
      }],
    },
  })

  assert.deepEqual(seeds.visit.entities, ['Alice'])
  assert.deepEqual(seeds.findings.entities, [])
})

test('plugin-managed state normalizes owners and merges description notes', () => {
  const visit = { entities: ['Alice'], relations: [] as Array<[string, string]> }
  const findings = { entities: [], relations: [] } as ReturnType<typeof parseRecallSeeds>['findings']

  assert.equal(addEntityFinding(visit, findings, ' alice ', ' Leads MAGI. '), true)
  assert.equal(addEntityFinding(visit, findings, 'Alice', 'Leads MAGI.'), false)
  assert.equal(addRelationFinding(visit, findings, ['Bob', 'Alice'], 'Works together.'), true)

  assert.deepEqual(visit, {
    entities: ['Alice', 'Bob'],
    relations: [['Alice', 'Bob']],
  })
  assert.deepEqual(findings, {
    entities: [{ name: 'Alice', notes: ['Leads MAGI.'] }],
    relations: [{ endpoints: ['Alice', 'Bob'], notes: ['Works together.'] }],
  })
})

test('prompt enforces the canonical three-layer retrieval state machine', () => {
  const seeds = parseRecallSeeds(recall)
  const prompt = activeExplorerPrompt('How are Alice and Bob connected?', seeds)
  assert.match(prompt, /plugin, not you, owns visit and findings/)
  assert.match(prompt, /Canonical three-layer retrieval state machine/)
  assert.match(prompt, /DESCRIBE \(MANDATORY\)/)
  assert.match(prompt, /exactly one batched magi_memory_describe/)
  assert.match(prompt, /relation owner for a connection claim/)
  assert.match(prompt, /or both when both semantics are needed/)
  assert.match(prompt, /EVIDENCE \(OPTIONAL\)/)
  assert.match(prompt, /Compact topology alone never creates a finding/)
  assert.match(prompt, /describe commits both automatically/)
  assert.match(prompt, /maintains expanded separately from visit/)
  assert.match(prompt, /Brief narration .* is allowed for debugging/)
  assert.match(prompt, /Do not return visit or findings/)
  assert.match(prompt, /Each frontier entity may be expanded at most once/)
  assert.match(prompt, /Never expose MAGI stable ids/)
  assert.doesNotMatch(prompt, /"frontier":/)
  assert.doesNotMatch(prompt, /"visit":/)
  assert.doesNotMatch(prompt, /INITIAL MIX RECALL/)
  assert.doesNotMatch(prompt, /atom-secret|valid_at/)
})

test('labels one-shot histories with a query preview and timestamp', () => {
  assert.equal(
    activeExplorerLabel(
      '  How   are Alice and Bob connected?  ',
      new Date('2026-08-21T12:34:56.789Z'),
    ),
    'MAGI explore · How are Alice and Bob connected? · 2026-08-21T12:34:56Z',
  )
  assert.match(
    activeExplorerLabel('这'.repeat(50), new Date(0)),
    /^MAGI explore · .{42}… · 1970-01-01T00:00:00Z$/u,
  )
})

test('output schema stays inside the dsh portable keyword subset', () => {
  const schema = JSON.stringify(ACTIVE_EXPLORER_OUTPUT_SCHEMA)
  assert.doesNotMatch(schema, /minItems|maxItems/)
  assert.doesNotThrow(() => { assertObjectJsonSchema(ACTIVE_EXPLORER_OUTPUT_SCHEMA) })
})

test('runs exactly one isolated Sub-Agent and always disposes it', async () => {
  let recallCalls = 0
  let startCalls = 0
  let disposeCalls = 0
  let startedWith: Record<string, unknown> | undefined
  const structured = {
    report: 'The focused branch was exhausted.',
    stop_reason: 'No current or historical candidate was selected.',
  }

  const result = await runActiveExplorer({
    query: 'How are Alice and Bob connected?',
    signal: new AbortController().signal,
    topK: 20,
    chunkTopK: 10,
    maxTokens: 4096,
    async recall(input) {
      recallCalls += 1
      assert.equal(input.mode, 'mix')
      return recall
    },
    async start(input) {
      startCalls += 1
      startedWith = input
      return {
        result: Promise.resolve({ structured, stopReason: 'completed' }),
        snapshotFindings: () => parseRecallSeeds(recall).findings,
        async dispose() {
          disposeCalls += 1
        },
      }
    },
  })

  assert.equal(recallCalls, 1)
  assert.equal(startCalls, 1)
  assert.equal(disposeCalls, 1)
  assert.deepEqual(startedWith?.toolFilter, {
    allow: ['magi_memory_expand', 'magi_memory_describe', 'magi_memory_evidence', 'magi_memory_note'],
  })
  assert.deepEqual(startedWith?.initialVisit, parseRecallSeeds(recall).visit)
  assert.equal(startedWith?.maxTokens, 4096)
  assert.deepEqual(result, {
    status: 'complete',
    subagent_stop_reason: 'completed',
    findings: parseRecallSeeds(recall).findings,
    report: structured.report,
    stop_reason: structured.stop_reason,
  })
})

test('hot start reuses Host recall seeds without issuing another Mix recall', async () => {
  const initialSeeds = parseRecallSeeds(recall)
  let recallCalls = 0
  let startedWith: Record<string, unknown> | undefined

  const result = await runActiveExplorer({
    query: 'Which hidden relation connects Alice and Bob?',
    signal: new AbortController().signal,
    initialSeeds,
    topK: 20,
    chunkTopK: 10,
    maxTokens: 4096,
    async recall() {
      recallCalls += 1
      return { data: {} }
    },
    async start(input) {
      startedWith = input
      input.initialVisit.entities.push('Explorer-only mutation')
      return {
        result: Promise.resolve({
          stopReason: 'completed',
          structured: { report: 'Used the handed-off seeds.', stop_reason: 'Complete.' },
        }),
        snapshotFindings: () => input.initialFindings,
        async dispose() {},
      }
    },
  })

  assert.equal(recallCalls, 0)
  assert.deepEqual(initialSeeds.visit.entities, ['Alice', 'Bob'])
  assert.deepEqual(
    (startedWith?.initialVisit as { entities: string[] }).entities,
    ['Alice', 'Bob', 'Explorer-only mutation'],
  )
  assert.equal((result as { status: string }).status, 'complete')
})

test('concurrent mode lets s0 delegate distinct branches without fixed worker budgets', async () => {
  let startedWith: Record<string, unknown> | undefined
  await runActiveExplorer({
    query: 'How are Alice, Bob, and MAGI connected?',
    signal: new AbortController().signal,
    concurrent: true,
    topK: 20,
    chunkTopK: 10,
    maxTokens: 4096,
    async recall() {
      return recall
    },
    async start(input) {
      startedWith = input
      return {
        result: Promise.resolve({
          stopReason: 'completed',
          structured: {
            report: 'Delegated two independent branches and merged them.',
            stop_reason: 'Evidence is sufficient.',
          },
        }),
        snapshotFindings: () => parseRecallSeeds(recall).findings,
        async dispose() {},
      }
    },
  })

  assert.deepEqual(startedWith?.toolFilter, { allow: ['magi_memory_delegate'] })
  assert.match(String(startedWith?.prompt), /Active Memory Coordinator s0/)
  assert.match(String(startedWith?.prompt), /Tasks in one delegate call execute concurrently/)
  assert.match(String(startedWith?.prompt), /As soon as accumulated findings are sufficient/)
  assert.doesNotMatch(String(startedWith?.prompt), /"frontier":/)
  assert.match(String(startedWith?.prompt), /Delegation is optional/)
  assert.match(String(startedWith?.prompt), /known_owners/)
  assert.match(String(startedWith?.prompt), /never copy notes into delegate arguments/)
  assert.doesNotMatch(String(startedWith?.prompt), /Call magi_memory_expand with that entity-name frontier/)

  const workerPrompt = activeExplorerWorkerPrompt({
    parentQuery: 'How are Alice, Bob, and MAGI connected?',
    subquery: 'How does Alice relate to MAGI?',
    frontier: ['Alice', 'Carol'],
    knownFindings: { entities: [], relations: [] },
  })
  assert.doesNotMatch(workerPrompt, /Hard task budget|max_hops/)
  assert.match(workerPrompt, /DESCRIBE \(MANDATORY\)/)
  assert.match(workerPrompt, /EVIDENCE \(OPTIONAL\)/)
  assert.match(workerPrompt, /S0-SELECTED KNOWN FINDINGS FOR THIS SUBQUERY/)
  assert.match(workerPrompt, /minimal subquery-relevant subset selected by s0/)
  assert.match(workerPrompt, /"Carol"/)
  assert.match(workerPrompt, /Do not attempt to coordinate with siblings/)
  assert.match(workerPrompt, /Never return visit, findings/)
  assert.match(concurrentExplorerPrompt('query', parseRecallSeeds(recall)), /execute concurrently/)
})

test('attaches plugin-managed findings to the worker terminal result', () => {
  assert.deepEqual(normalizeExplorerWorkerResult({
    stop_reason: 'Branch complete.',
  }, 'completed', {
    entities: [{ name: 'Alice', notes: ['Relevant'] }],
    relations: [],
  }), {
    status: 'complete',
    subagent_stop_reason: 'completed',
    findings: {
      entities: [{ name: 'Alice', notes: ['Relevant'] }],
      relations: [],
    },
    stop_reason: 'Branch complete.',
  })
})

test('preserves seed findings without exposing visit when the Sub-Agent stops abnormally', async () => {
  let disposed = false
  const result = await runActiveExplorer({
    query: 'How are Alice and Bob connected?',
    signal: new AbortController().signal,
    topK: 20,
    chunkTopK: 10,
    maxTokens: 4096,
    async recall() {
      return recall
    },
    async start() {
      return {
        result: Promise.resolve({ stopReason: 'max-tokens' }),
        snapshotFindings: () => parseRecallSeeds(recall).findings,
        async dispose() {
          disposed = true
        },
      }
    },
  })

  assert.equal(disposed, true)
  assert.equal((result as { status: string }).status, 'abnormal')
  assert.equal('visit' in (result as Record<string, unknown>), false)
  assert.deepEqual((result as { findings: unknown }).findings, parseRecallSeeds(recall).findings)
})

test('accepts the minimal terminal schema and ignores model-owned findings', async () => {
  const result = await runActiveExplorer({
    query: 'How are Alice and Bob connected?',
    signal: new AbortController().signal,
    topK: 20,
    chunkTopK: 10,
    maxTokens: 4096,
    async recall() {
      return recall
    },
    async start() {
      return {
        result: Promise.resolve({
          stopReason: 'completed',
          structured: {
            report: 'Plugin state is authoritative.',
            stop_reason: 'done',
          },
        }),
        snapshotFindings: () => parseRecallSeeds(recall).findings,
        async dispose() {},
      }
    },
  })

  assert.equal((result as { status: string }).status, 'complete')
  assert.deepEqual((result as { findings: unknown }).findings, parseRecallSeeds(recall).findings)
})
