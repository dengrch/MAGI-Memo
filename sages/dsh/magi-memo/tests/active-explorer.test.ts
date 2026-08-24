import assert from 'node:assert/strict'
import test from 'node:test'
import { assertObjectJsonSchema } from '@deepseek-ai/dsh-tools'
import {
  ACTIVE_EXPLORER_OUTPUT_SCHEMA,
  activeExplorerLabel,
  activeExplorerPrompt,
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

test('prompt preserves the exact normal stopping rule and rejects extra state', () => {
  const seeds = parseRecallSeeds(recall)
  const prompt = activeExplorerPrompt('How are Alice and Bob connected?', seeds)
  assert.match(prompt, /Maintain exactly two semantic accumulators: visit and findings/)
  assert.match(prompt, /There is no fixed count: prefer focused expansion/)
  assert.match(prompt, /choose only the owners you judge most promising/)
  assert.match(prompt, /Brief narration .* is allowed for debugging/)
  assert.match(prompt, /visit is internal exploration state/)
  assert.match(prompt, /every shown key is required even when its array is empty/)
  assert.match(prompt, /findings\.notes is invalid/)
  assert.match(prompt, /Never omit findings\.entities, findings\.relations, or stop_reason/)
  assert.match(prompt, /Earlier rejection is not permanent/)
  assert.match(prompt, /the current expand is truly exhausted OR you select no candidate/)
  assert.match(prompt, /AND you add no candidate from historical context/)
  assert.match(prompt, /explicitly returns exhausted=true/)
  assert.match(prompt, /Never expose or copy MAGI stable ids/)
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
    findings: { entities: [], relations: [] },
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
    allow: ['magi_memory_expand', 'magi_memory_evidence'],
  })
  assert.equal(startedWith?.maxTokens, 4096)
  assert.deepEqual(result, {
    status: 'complete',
    subagent_stop_reason: 'completed',
    ...structured,
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

test('rejects non-binary relation arrays after portable schema validation', async () => {
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
            findings: {
              entities: [],
              relations: [{ endpoints: ['Alice'], notes: [] }],
            },
            stop_reason: 'done',
          },
        }),
        async dispose() {},
      }
    },
  })

  assert.equal((result as { status: string }).status, 'abnormal')
  assert.match(
    (result as { stop_reason: string }).stop_reason,
    /findings\.relations must contain only two-name endpoint pairs/,
  )
})
