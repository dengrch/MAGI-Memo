import { create } from 'zustand'

import type { ExplorationEvent, ExplorationTraceSummary } from '@/api/lightrag'
import { createSelectors } from '@/lib/utils'

export type ExpandFrame = {
  agentId: string
  callId: string
  startedSeq: number
  status: 'initialized' | 'expanding' | 'complete' | 'failed'
  frontier: string[]
  query?: string
  visit: {
    entities: string[]
    relations: [string, string][]
    entity_metadata?: Array<{
      name: string
      entity_type?: string | null
      description?: string | null
    }>
  }
  event: ExplorationEvent
}

export function reduceExplorationEvents(
  frames: Record<string, ExpandFrame>,
  events: ExplorationEvent[]
): Record<string, ExpandFrame> {
  const next = { ...frames }
  for (const event of events) {
    const key = `${event.agent_id}\u0000${event.call_id}`
    const current = next[key]
    if (event.type === 'exploration_initialized') {
      next[key] = {
        agentId: event.agent_id,
        callId: event.call_id,
        startedSeq: event.seq,
        status: 'initialized',
        frontier: [],
        query: undefined,
        visit: event.payload.visit ?? { entities: [], relations: [] },
        event
      }
      continue
    }
    if (event.type === 'expand_started') {
      next[key] = {
        agentId: event.agent_id,
        callId: event.call_id,
        startedSeq: event.seq,
        status: 'expanding',
        frontier: event.payload.frontier ?? [],
        query: event.payload.subquery,
        visit: event.payload.visit ?? { entities: [], relations: [] },
        event
      }
      continue
    }
    if (current === undefined || current.callId !== event.call_id) continue
    next[key] = {
      ...current,
      status: event.type === 'expand_completed' ? 'complete' : 'failed',
      frontier: event.payload.frontier ?? current.frontier,
      event
    }
  }
  return next
}

export function latestExpandFramesByAgent(
  frames: Record<string, ExpandFrame>
): Record<string, ExpandFrame> {
  const latest: Record<string, ExpandFrame> = {}
  for (const frame of Object.values(frames)) {
    const current = latest[frame.agentId]
    if (current === undefined || frame.startedSeq > current.startedSeq) {
      latest[frame.agentId] = frame
    }
  }
  return latest
}

interface ExplorationState {
  traces: ExplorationTraceSummary[]
  currentExplorationId: string | null
  lastSeq: number
  events: ExplorationEvent[]
  expandCalls: Record<string, ExpandFrame>
  error: string | null
  setTraces: (traces: ExplorationTraceSummary[]) => void
  applyEvents: (events: ExplorationEvent[]) => void
  setError: (error: string | null) => void
}

const useExplorationStoreBase = create<ExplorationState>()((set) => ({
  traces: [],
  currentExplorationId: null,
  lastSeq: 0,
  events: [],
  expandCalls: {},
  error: null,
  setTraces: (traces) =>
    set((state) => {
      const newestId = traces[0]?.exploration_id ?? null
      if (newestId === state.currentExplorationId) return { traces }
      return {
        traces,
        currentExplorationId: newestId,
        lastSeq: 0,
        events: [],
        expandCalls: {},
        error: null
      }
    }),
  applyEvents: (events) =>
    set((state) => ({
      events: [...state.events, ...events],
      expandCalls: reduceExplorationEvents(state.expandCalls, events),
      lastSeq: events.length === 0 ? state.lastSeq : events[events.length - 1]!.seq
    })),
  setError: (error) => set({ error })
}))

export const useExplorationStore = createSelectors(useExplorationStoreBase)
