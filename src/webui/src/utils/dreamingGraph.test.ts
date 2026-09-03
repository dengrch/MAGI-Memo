import { describe, expect, test } from 'bun:test'

import {
  buildDreamingCommunityGraph,
  buildDreamingCommunityLegend,
  buildDreamingMemberGraph
} from './dreamingGraph'

const graph = {
  nodes: [
    { id: 'a', labels: ['Alice'], properties: { community_id: 'one', community_status: 'stable' } },
    { id: 'b', labels: ['Bob'], properties: { community_id: 'one', community_status: 'stable' } },
    { id: 'c', labels: ['Carol'], properties: { community_id: 'two', community_status: 'stable' } },
    { id: 'd', labels: ['Dave'], properties: { community_id: 'two', community_status: 'stable' } },
    { id: 'solo', labels: ['Solo'], properties: { community_id: 'three', community_status: 'stable' } },
    { id: 'new', labels: ['New'], properties: { community_id: 'one', community_status: 'provisional' } }
  ],
  edges: [
    { id: 'ab', source: 'a', target: 'b', type: 'DIRECTED', properties: { weight: 1 } },
    { id: 'ac', source: 'a', target: 'c', type: 'DIRECTED', properties: { weight: 3 } },
    { id: 'an', source: 'a', target: 'new', type: 'DIRECTED', properties: { weight: 1 } }
  ]
}

describe('Dreaming graph projections', () => {
  test('member view preserves entities and colors by community', () => {
    const projected = buildDreamingMemberGraph(graph, [{
      community_id: 'one',
      community_name: 'Primary circle',
      report: 'Alice and Bob form the primary circle.',
      member_count: 2,
      snapshot_id: 'snapshot-a',
      prompt_tokens: 100,
      completion_tokens: 20,
      total_tokens: 120,
      token_usage_source: 'provider'
    }])
    expect(projected.order).toBe(6)
    expect(projected.size).toBe(3)
    expect(projected.getNodeAttribute('a', 'color')).toBe(projected.getNodeAttribute('b', 'color'))
    expect(projected.getNodeAttribute('a', 'color')).not.toBe(projected.getNodeAttribute('c', 'color'))
    expect(projected.getNodeAttribute('new', 'membershipStatus')).toBe('provisional')
    expect(projected.getNodeAttribute('solo', 'membershipStatus')).toBe('unassigned')
    expect(projected.getNodeAttribute('a', 'communityName')).toBe('Primary circle')
  })

  test('community view collapses stable members and keeps provisional nodes outside', () => {
    const projected = buildDreamingCommunityGraph(graph, [{
      community_id: 'one',
      community_name: 'Primary circle',
      report: 'Alice and Bob form the primary circle.',
      member_count: 2,
      snapshot_id: 'snapshot-a',
      prompt_tokens: 100,
      completion_tokens: 20,
      total_tokens: 120,
      token_usage_source: 'provider'
    }])
    expect(projected.hasNode('community:one')).toBeTrue()
    expect(projected.hasNode('community:two')).toBeTrue()
    expect(projected.hasNode('community:three')).toBeFalse()
    expect(projected.hasNode('new')).toBeTrue()
    expect(projected.hasNode('solo')).toBeTrue()
    expect(projected.hasNode('a')).toBeFalse()
    expect(projected.hasEdge('membership:new')).toBeTrue()
    expect(projected.getNodeAttribute('community:one', 'stableMembers')).toBe(2)
    expect(projected.getNodeAttribute('community:one', 'provisionalMembers')).toBe(1)
    expect(projected.getNodeAttribute('community:one', 'type')).toBe('border')
    expect(projected.getNodeAttribute('community:one', 'nodeKind')).toBe('community')
    expect(projected.getNodeAttribute('community:one', 'label')).toBe('Primary circle')
    expect(projected.getNodeAttribute('community:one', 'report')).toContain('primary circle')
    expect(projected.getNodeAttribute('community:one', 'size')).toBeGreaterThan(
      projected.getNodeAttribute('community:two', 'size')
    )
    const boundaryEdge = projected.edges().find((edge) => edge.startsWith('boundary:'))
    expect(boundaryEdge).toBeDefined()
    expect(projected.getEdgeAttribute(boundaryEdge!, 'linkCount')).toBe(1)
  })

  test('legend lists every community with the graph color', () => {
    const legend = buildDreamingCommunityLegend(graph)
    expect(legend.map((item) => item.id)).toEqual(['one', 'two'])
    expect(legend.every((item) => item.color.startsWith('#'))).toBeTrue()
  })
})
