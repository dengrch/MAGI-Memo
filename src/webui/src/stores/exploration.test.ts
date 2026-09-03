import { describe, expect, test } from 'bun:test'

import type { ExplorationEvent } from '@/api/lightrag'
import { agentColor, buildExplorationGraph } from '@/utils/explorationGraph'
import {
  latestExpandFramesByAgent,
  reduceExplorationEvents
} from '@/stores/exploration'

function event(
  seq: number,
  type: ExplorationEvent['type'],
  agentId: string,
  callId: string,
  payload: ExplorationEvent['payload']
): ExplorationEvent {
  return {
    exploration_id: 'explore-1',
    seq,
    type,
    agent_id: agentId,
    call_id: callId,
    payload,
    created_at: '2026-08-25T00:00:00Z'
  }
}

describe('exploration projection', () => {
  test('assigns distinct colors beyond the original six-agent palette', () => {
    const agents = Array.from({ length: 12 }, (_, index) => `worker-${index}`)
    const colors = agents.map(agent => agentColor(agent, agents))

    expect(new Set(colors).size).toBe(agents.length)
  })

  test('projects the initial visit before the first expand call', () => {
    const frames = reduceExplorationEvents({}, [
      event(1, 'exploration_initialized', 's0', 'initial', {
        visit: {
          entities: ['Alice', 'Bob'],
          relations: [['Alice', 'Bob']],
          entity_metadata: [
            { name: 'Alice', entity_type: 'person' },
            { name: 'Bob', entity_type: 'person' }
          ]
        }
      })
    ])
    const graph = buildExplorationGraph(frames)

    expect(graph.nodes().sort()).toEqual(['alice', 'bob'])
    expect(graph.size).toBe(1)
    expect(graph.getNodeAttribute('alice', 'explorationState')).toBe('visited')
    expect(graph.getNodeAttribute('alice', 'color')).toBe('#4169E1')
    expect(graph.getNodeAttribute('alice', 'agentColors')).toEqual([])
  })

  test('retains call history while resolving the latest call per agent', () => {
    const frames = reduceExplorationEvents({}, [
      event(1, 'expand_started', 's0', 'old', { frontier: ['Alice'] }),
      event(2, 'expand_started', 's0', 'new', {
        frontier: ['Bob'],
        subquery: 'How is Bob connected?'
      }),
      event(3, 'expand_completed', 's0', 'old', {
        frontier: ['Alice'],
        result: { results: [] }
      })
    ])

    expect(Object.values(frames)).toHaveLength(2)
    const latest = latestExpandFramesByAgent(frames)
    expect(latest.s0?.callId).toBe('new')
    expect(latest.s0?.frontier).toEqual(['Bob'])
    expect(latest.s0?.query).toBe('How is Bob connected?')
    expect(latest.s0?.status).toBe('expanding')
  })

  test('keeps discrete native glow colors when concurrent workers share a frontier', () => {
    const frames = reduceExplorationEvents({}, [
      event(1, 'expand_started', 'worker-a', 'call-a', {
        frontier: ['Alice'],
        subquery: 'Branch A',
        visit: { entities: ['Alice'], relations: [] }
      }),
      event(2, 'expand_started', 'worker-b', 'call-b', {
        frontier: ['Alice'],
        subquery: 'Branch B',
        visit: { entities: ['Alice'], relations: [] }
      })
    ])
    const graph = buildExplorationGraph(frames)

    expect(graph.getNodeAttribute('alice', 'owners')).toEqual(['worker-a', 'worker-b'])
    expect(graph.getNodeAttribute('alice', 'agentColors')).toEqual([
      agentColor('worker-a', ['worker-a', 'worker-b']),
      agentColor('worker-b', ['worker-a', 'worker-b'])
    ])
  })

  test('retains visited history and hides candidates left behind by the next call', () => {
    const frames = reduceExplorationEvents({}, [
      event(1, 'expand_started', 's0', 'call-1', {
        frontier: ['Alice'],
        visit: {
          entities: ['Seed', 'Alice'],
          relations: [['Seed', 'Alice']],
          entity_metadata: [
            { name: 'Seed', entity_type: 'person' },
            { name: 'Alice', entity_type: 'person' }
          ]
        }
      }),
      event(2, 'expand_completed', 's0', 'call-1', {
        frontier: ['Alice'],
        result: {
          results: [{
            frontier: 'Alice',
            candidates: [
              {
                entity: { name: 'Bob', entity_type: 'person' },
                relation: { endpoints: ['Alice', 'Bob'], keywords: ['knows'] }
              },
              {
                entity: { name: 'Carol', entity_type: 'person' },
                relation: { endpoints: ['Alice', 'Carol'], keywords: ['knows'] }
              }
            ]
          }]
        }
      }),
      event(3, 'expand_started', 's0', 'call-2', {
        frontier: ['Bob'],
        visit: {
          entities: ['Seed', 'Alice', 'Bob'],
          relations: [['Seed', 'Alice'], ['Alice', 'Bob']]
        }
      })
    ])
    const graph = buildExplorationGraph(frames)

    expect(graph.nodes().sort()).toEqual(['alice', 'bob', 'seed'])
    expect(graph.hasNode('carol')).toBeFalse()
    expect(graph.getNodeAttribute('alice', 'explorationState')).toBe('visited')
    expect(graph.getNodeAttribute('seed', 'color')).toBe('#4169E1')
    expect(graph.getNodeAttribute('bob', 'type')).toBe('frontier')
    expect(graph.getNodeAttribute('bob', 'owners')).toEqual(['s0'])
    expect(graph.getEdgeAttribute(graph.edge('seed', 'alice')!, 'explorationState')).toBe('visited')
    expect(graph.getEdgeAttribute(graph.edge('alice', 'bob')!, 'explorationState')).toBe('visited')
    expect(graph.getEdgeAttribute(graph.edge('seed', 'alice')!, 'color')).toBe('#737373')
    expect(graph.getEdgeAttribute(graph.edge('alice', 'bob')!, 'color')).toBe('#737373')
    expect(graph.size).toBe(2)
  })

  test('uses semantic graph styling with agent metadata only on frontiers', () => {
    const frames = reduceExplorationEvents({}, [
      event(1, 'expand_started', 's0', 'call-1', {
        frontier: ['Alice'],
        visit: { entities: ['Alice'], relations: [] }
      }),
      event(2, 'expand_completed', 's0', 'call-1', {
        frontier: ['Alice'],
        result: {
          results: [{
            frontier: 'Alice',
            candidates: [{
              entity: { name: 'Bob', entity_type: 'person' },
              relation: { endpoints: ['Alice', 'Bob'], keywords: ['knows'] }
            }]
          }]
        }
      })
    ])
    const graph = buildExplorationGraph(frames)

    expect(graph.getNodeAttribute('alice', 'type')).toBe('frontier')
    expect(graph.getNodeAttribute('alice', 'borderColor')).toBe('#EEEEEE')
    expect(graph.getNodeAttribute('alice', 'agentColors')).toEqual(['#a78bfa'])
    expect(graph.getNodeAttribute('bob', 'type')).toBe('border')
    expect(graph.getNodeAttribute('bob', 'borderColor')).toBe('#EEEEEE')
    expect(graph.getNodeAttribute('bob', 'color')).toBe('#4B5563')
    expect(graph.getNodeAttribute('bob', 'agentColors')).toEqual([])
    expect(graph.getNodeAttribute('bob', 'explorationState')).toBe('candidate')
    expect(graph.getEdgeAttribute(graph.edges()[0]!, 'type')).toBeUndefined()
    expect(graph.getEdgeAttribute(graph.edges()[0]!, 'color')).toBe('#22D3EE')
  })
})
