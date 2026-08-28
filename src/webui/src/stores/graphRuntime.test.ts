import { describe, expect, test } from 'bun:test'
import { DirectedGraph } from 'graphology'

import { createGraphStore, useGraphStore } from './graph'

describe('graph runtime stores', () => {
  test('keeps an exploration viewer isolated from the semantic graph viewer', () => {
    const explorationStore = createGraphStore()
    const semanticGraph = useGraphStore.getState().sigmaGraph
    const semanticSelection = useGraphStore.getState().selectedNode
    const graph = new DirectedGraph()
    graph.addNode('frontier')

    explorationStore.getState().setSigmaGraph(graph)
    explorationStore.getState().setSelectedNode('frontier')

    expect(explorationStore.getState().sigmaGraph).toBe(graph)
    expect(explorationStore.getState().graphNodeCount).toBe(1)
    expect(explorationStore.getState().selectedNode).toBe('frontier')
    expect(useGraphStore.getState().sigmaGraph).toBe(semanticGraph)
    expect(useGraphStore.getState().selectedNode).toBe(semanticSelection)
  })

  test('creates independent exploration runtimes', () => {
    const first = createGraphStore()
    const second = createGraphStore()

    first.getState().setGraphCounts(4, 3)

    expect(first.getState().graphNodeCount).toBe(4)
    expect(first.getState().graphEdgeCount).toBe(3)
    expect(second.getState().graphNodeCount).toBe(0)
    expect(second.getState().graphEdgeCount).toBe(0)
  })
})
