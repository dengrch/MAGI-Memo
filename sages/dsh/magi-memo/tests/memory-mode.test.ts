import assert from 'node:assert/strict'
import test from 'node:test'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import { currentOpenTurn, foldMemoryMode, memoryModePrompt, parseMemoryMode, scopedMemoryPrompt } from '../src/index.ts'

function events(...rows: Array<Record<string, unknown>>): SessionEvent[] {
  return rows as unknown as SessionEvent[]
}

test('parses supported memory modes only', () => {
  assert.equal(parseMemoryMode(' AUTO '), 'auto')
  assert.equal(parseMemoryMode('manual'), 'manual')
  assert.equal(parseMemoryMode('explore'), 'explore')
  assert.equal(parseMemoryMode('off'), 'off')
  assert.equal(parseMemoryMode('active'), undefined)
  assert.equal(parseMemoryMode('sometimes'), undefined)
})

test('folds only successfully completed memory commands', () => {
  const log = events(
    { type: 'command/run', data: { commandId: 'one', name: 'memory', args: ' off' } },
    { type: 'command/done', data: { commandId: 'one', kind: 'success' } },
    { type: 'command/run', data: { commandId: 'two', name: 'memory', args: ' auto' } },
    { type: 'command/done', data: { commandId: 'two', kind: 'error' } },
    { type: 'command/run', data: { commandId: 'three', name: 'other', args: 'active' } },
    { type: 'command/done', data: { commandId: 'three', kind: 'success' } },
  )
  assert.equal(foldMemoryMode(log, 'manual'), 'off')
})

test('ignores an incomplete command and honors the configured default', () => {
  const log = events(
    { type: 'command/run', data: { commandId: 'pending', name: 'memory', args: 'off' } },
  )
  assert.equal(foldMemoryMode(log, 'auto'), 'auto')
  assert.equal(foldMemoryMode([], 'manual'), 'manual')
})

test('auto prompt defines per-turn recall and write gates', () => {
  const prompt = memoryModePrompt('auto', '2026-08-19T12:34:56.000Z')
  assert.match(prompt, /Current system time and default Episode reference_at: 2026-08-19T12:34:56\.000Z/)
  assert.match(prompt, /Do not invent a different current year/)
  assert.match(prompt, /Recall Gate/)
  assert.match(prompt, /Write Gate/)
  assert.match(prompt, /final tool step/)
  assert.match(prompt, /Temporal Pass/)
  assert.match(prompt, /use the Episode reference_at as the anchor/)
  assert.match(prompt, /use reference_at for a fact asserted as current/)
  assert.match(prompt, /truly timeless definitional fact/)
  assert.match(prompt, /never automatically/)
  assert.match(prompt, /exact, day, month, year, relative, inferred_current/)
  assert.match(prompt, /Never create, switch, or activate a workspace proactively/)
  assert.match(prompt, /magi_memory_explore\(hot_start=true\)/)
  assert.match(prompt, /native approval prompt/)
  assert.match(prompt, /without repeating Mix/)
  assert.match(prompt, /do not retry during that turn/)
})

test('identifies only a currently open durable turn for recall handoff', () => {
  assert.equal(currentOpenTurn(events(
    { type: 'turn/start', data: { turn: 4 } },
    { type: 'tool/call', data: { turn: 4 } },
  )), 4)
  assert.equal(currentOpenTurn(events(
    { type: 'turn/start', data: { turn: 4 } },
    { type: 'turn/end', data: { turn: 4 } },
  )), undefined)
  assert.equal(currentOpenTurn([]), undefined)
})

test('restores the former active spelling as auto from existing logs', () => {
  const log = events(
    { type: 'command/run', data: { commandId: 'legacy', name: 'memory', args: ' active' } },
    { type: 'command/done', data: { commandId: 'legacy', kind: 'success' } },
  )
  assert.equal(foldMemoryMode(log, 'manual'), 'auto')
})

test('manual and off prompts prohibit proactive memory use', () => {
  assert.match(memoryModePrompt('manual'), /only when the user explicitly asks/)
  assert.match(memoryModePrompt('off'), /Do not call magi_memory_recall/)
})

test('explore mode selects single or concurrent retrieval through one composite tool', () => {
  const prompt = memoryModePrompt('explore')
  assert.match(prompt, /magi_memory_explore once/)
  assert.match(prompt, /concurrent=true for a broad or multi-branch graph investigation/)
  assert.match(prompt, /Never call magi_memory_delegate, magi_memory_expand, magi_memory_describe, or magi_memory_evidence directly/)
})

test('delegated subagents do not inherit host recall and write policy', () => {
  const prompt = scopedMemoryPrompt('explore', true, '2026-08-19T12:34:56.000Z')
  assert.match(prompt, /delegated subagent/)
  assert.match(prompt, /do not apply inside this isolated task/)
  assert.doesNotMatch(prompt, /For every user turn|Temporal Pass|reference_at/)
})
