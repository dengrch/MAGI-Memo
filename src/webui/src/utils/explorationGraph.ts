import { UndirectedGraph } from 'graphology'

import type { ExplorationCandidate } from '@/api/lightrag'
import { latestExpandFramesByAgent, type ExpandFrame } from '@/stores/exploration'
import { DEFAULT_NODE_COLOR, resolveNodeColor } from '@/utils/graphColor'

export const EXPLORER_AGENT_COLORS = [
  '#a78bfa',
  '#38bdf8',
  '#34d399',
  '#fb7185',
  '#fbbf24',
  '#f472b6',
  '#22d3ee',
  '#fb923c',
  '#818cf8',
  '#a3e635',
  '#e879f9',
  '#2dd4bf',
  '#f87171',
  '#60a5fa',
  '#c084fc',
  '#facc15'
]

export const EXPLORATION_TRACE_COLOR = '#22D3EE'

const SEMANTIC_GRAPH_NODE_BORDER_COLOR = '#EEEEEE'
const CANDIDATE_NODE_COLOR = '#4B5563'
const INACTIVE_EXPLORATION_EDGE_COLOR = '#737373'

function stableHash(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function agentColor(agentId: string, agentOrder?: string[]): string {
  const orderedIndex = agentOrder?.indexOf(agentId) ?? -1
  if (orderedIndex >= 0) return EXPLORER_AGENT_COLORS[orderedIndex % EXPLORER_AGENT_COLORS.length]!
  return EXPLORER_AGENT_COLORS[stableHash(agentId) % EXPLORER_AGENT_COLORS.length]!
}

export function agentGlowShadow(color: string): string {
  return `0 0 8px 2px ${color}99, 0 0 18px 4px ${color}55`
}

function nodeKey(name: string): string {
  return name.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

function relationKey(source: string, target: string): string {
  return [nodeKey(source), nodeKey(target)].sort().join('\u0000')
}

type CandidateContext = {
  frontier: string
  candidate: ExplorationCandidate
}

export function buildExplorationGraph(frames: Record<string, ExpandFrame>): UndirectedGraph {
  const graph = new UndirectedGraph()
  const frontierOwners = new Map<string, Set<string>>()
  const displayNames = new Map<string, string>()
  const entityMetadata = new Map<string, ExplorationCandidate['entity']>()
  const relationMetadata = new Map<string, ExplorationCandidate['relation']>()
  const visitedEntities = new Set<string>()
  const visitedRelations = new Set<string>()
  const visitedRelationEndpoints = new Map<string, [string, string]>()
  const currentCandidates: CandidateContext[] = []
  const currentCandidateRelations = new Set<string>()
  const orderedFrames = Object.values(frames).sort((left, right) => left.startedSeq - right.startedSeq)
  const latestFrames = Object.values(latestExpandFramesByAgent(frames))
  const agentOrder = [...new Set(orderedFrames.map(frame => frame.agentId))]

  // Every visit snapshot is caller-confirmed exploration state. Unioning the
  // snapshots retains the initial findings and every path actually walked,
  // while never promoting merely returned candidates into history.
  for (const frame of orderedFrames) {
    for (const entity of frame.visit.entity_metadata ?? []) {
      const key = nodeKey(entity.name)
      if (!key) continue
      displayNames.set(key, entity.name)
      entityMetadata.set(key, entity)
    }
    for (const entity of frame.visit.entities) {
      const key = nodeKey(entity)
      if (!key) continue
      displayNames.set(key, entity)
      visitedEntities.add(key)
    }
    for (const [source, target] of frame.visit.relations) {
      const sourceKey = nodeKey(source)
      const targetKey = nodeKey(target)
      if (!sourceKey || !targetKey) continue
      displayNames.set(sourceKey, source)
      displayNames.set(targetKey, target)
      visitedEntities.add(sourceKey)
      visitedEntities.add(targetKey)
      const edge = relationKey(source, target)
      visitedRelations.add(edge)
      visitedRelationEndpoints.set(edge, [source, target])
    }

    // Completed calls remain a metadata cache for visited identities even
    // after their unselected candidate window is hidden by a later call.
    const results = frame.event.payload.result?.results ?? []
    for (const result of results) {
      const frontierKey = nodeKey(result.frontier)
      displayNames.set(frontierKey, result.frontier)
      for (const candidate of result.candidates ?? []) {
        const entityKey = nodeKey(candidate.entity.name)
        displayNames.set(entityKey, candidate.entity.name)
        entityMetadata.set(entityKey, candidate.entity)
        for (const endpoint of candidate.relation.endpoints ?? []) {
          displayNames.set(nodeKey(endpoint), endpoint)
        }
        relationMetadata.set(
          relationKey(candidate.relation.endpoints[0], candidate.relation.endpoints[1]),
          candidate.relation
        )
      }
    }
  }

  // Only the latest call per agent owns a live frontier/candidate window.
  // Starting the next call therefore hides leftovers from the previous one;
  // identities selected into its visit have already graduated above.
  for (const frame of latestFrames) {
    for (const frontier of frame.frontier) {
      const key = nodeKey(frontier)
      displayNames.set(key, frontier)
      const owners = frontierOwners.get(key) ?? new Set<string>()
      owners.add(frame.agentId)
      frontierOwners.set(key, owners)
    }
    for (const result of frame.event.payload.result?.results ?? []) {
      for (const candidate of result.candidates ?? []) {
        currentCandidates.push({ frontier: result.frontier, candidate })
        currentCandidateRelations.add(
          relationKey(candidate.relation.endpoints[0], candidate.relation.endpoints[1])
        )
      }
    }
  }

  const frontierKeys = [...frontierOwners.keys()].sort()
  const positions = new Map<string, { x: number; y: number }>()
  frontierKeys.forEach((key, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(frontierKeys.length, 1)
    const radius = frontierKeys.length === 1 ? 0 : 4.5
    positions.set(key, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius })
  })
  for (const { frontier, candidate } of currentCandidates) {
    const key = nodeKey(candidate.entity.name)
    if (positions.has(key)) continue
    const origin = positions.get(nodeKey(frontier)) ?? { x: 0, y: 0 }
    const angle = (stableHash(`${frontier}\u0000${candidate.entity.name}`) / 0xffffffff) * Math.PI * 2
    const radius = 1.8 + (stableHash(candidate.entity.name) % 160) / 100
    positions.set(key, {
      x: origin.x + Math.cos(angle) * radius,
      y: origin.y + Math.sin(angle) * radius
    })
  }

  let typeColorMap = new Map<string, string>()
  const addNode = (name: string) => {
    const key = nodeKey(name)
    if (!key) return
    const owners = [...(frontierOwners.get(key) ?? [])].sort(
      (left, right) => agentOrder.indexOf(left) - agentOrder.indexOf(right)
    )
    const metadata = entityMetadata.get(key)
    const explorationState = owners.length > 0
      ? 'frontier'
      : visitedEntities.has(key) ? 'visited' : 'candidate'
    const position = positions.get(key) ?? {
      x: ((stableHash(`${key}:x`) % 1000) - 500) / 100,
      y: ((stableHash(`${key}:y`) % 1000) - 500) / 100
    }
    const resolved = resolveNodeColor(metadata?.entity_type ?? undefined, typeColorMap)
    typeColorMap = resolved.map
    const color = resolved.color || DEFAULT_NODE_COLOR
    // Exploration state is intentionally binary at a glance: only identities
    // selected into visit (plus the selected frontier) carry semantic type
    // color. Unselected candidates stay neutral until the next visit promotes
    // them, while their entityType metadata remains available in properties.
    const displayColor = explorationState === 'candidate' ? CANDIDATE_NODE_COLOR : color
    const agentColors = owners.map(owner => agentColor(owner, agentOrder))
    const attributes = {
      x: position.x,
      y: position.y,
      size: explorationState === 'frontier' ? 14 : explorationState === 'visited' ? 9 : 7,
      label: displayNames.get(key) ?? name,
      color: displayColor,
      type: owners.length > 0 ? 'frontier' : 'border',
      borderColor: SEMANTIC_GRAPH_NODE_BORDER_COLOR,
      agentColors,
      owners,
      explorationState,
      entityType: metadata?.entity_type ?? 'unknown',
      description: metadata?.description ?? ''
    }
    if (graph.hasNode(key)) graph.mergeNodeAttributes(key, attributes)
    else graph.addNode(key, attributes)
  }

  for (const key of visitedEntities) addNode(displayNames.get(key) ?? key)
  for (const key of frontierKeys) addNode(displayNames.get(key) ?? key)
  for (const { candidate } of currentCandidates) {
    addNode(candidate.entity.name)
    const [source, target] = candidate.relation.endpoints
    if (!source || !target) continue
    if (!graph.hasNode(nodeKey(source))) addNode(source)
    if (!graph.hasNode(nodeKey(target))) addNode(target)
    const edge = relationKey(source, target)
    if (!graph.hasEdge(edge)) {
      graph.addUndirectedEdgeWithKey(edge, nodeKey(source), nodeKey(target), {
        size: visitedRelations.has(edge) ? 1.8 : 1,
        color: EXPLORATION_TRACE_COLOR,
        explorationState: visitedRelations.has(edge) ? 'visited' : 'candidate',
        label: Array.isArray(candidate.relation.keywords)
          ? candidate.relation.keywords.join(', ')
          : candidate.relation.keywords ?? '',
        description: candidate.relation.description ?? ''
      })
    }
  }


  for (const edge of visitedRelations) {
    if (graph.hasEdge(edge)) {
      graph.mergeEdgeAttributes(edge, {
        size: 1.8,
        color: currentCandidateRelations.has(edge)
          ? EXPLORATION_TRACE_COLOR
          : INACTIVE_EXPLORATION_EDGE_COLOR,
        explorationState: 'visited'
      })
      continue
    }
    const relation = relationMetadata.get(edge)
    const endpoints = relation?.endpoints ?? visitedRelationEndpoints.get(edge)
    if (endpoints === undefined) continue
    const [source, target] = endpoints
    if (!graph.hasNode(nodeKey(source))) addNode(source)
    if (!graph.hasNode(nodeKey(target))) addNode(target)
    graph.addUndirectedEdgeWithKey(edge, nodeKey(source), nodeKey(target), {
      size: 1.8,
      color: INACTIVE_EXPLORATION_EDGE_COLOR,
      explorationState: 'visited',
      label: Array.isArray(relation?.keywords)
        ? relation.keywords.join(', ')
        : relation?.keywords ?? '',
      description: relation?.description ?? ''
    })
  }

  return graph
}
