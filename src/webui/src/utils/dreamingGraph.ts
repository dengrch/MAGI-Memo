import { DirectedGraph } from 'graphology'

import type { DreamingCommunityReport, LightragGraphType, LightragNodeType } from '@/api/lightrag'

const COMMUNITY_COLORS = [
  '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981', '#ec4899',
  '#3b82f6', '#f97316', '#14b8a6', '#a855f7', '#84cc16'
]
const UNASSIGNED_COLOR = '#64748b'

type Membership = {
  communityId: string | null
  provisional: boolean
}

function hash(value: string): number {
  let result = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index)
    result = Math.imul(result, 16777619)
  }
  return result >>> 0
}

function position(id: string): { x: number; y: number } {
  const angle = (hash(id) / 0xffffffff) * Math.PI * 2
  const radius = 0.35 + ((hash(`${id}:radius`) % 10_000) / 10_000) * 0.65
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }
}

function membership(node: LightragNodeType): Membership {
  const properties = node.properties ?? {}
  const rawId = properties.dream_community_id ?? properties.community_id ?? properties.communityId
  const rawStatus = properties.dream_membership_status ?? properties.community_status ?? properties.membership_status
  return {
    communityId: rawId === undefined || rawId === null || rawId === '' ? null : String(rawId),
    provisional: String(rawStatus ?? '').toLowerCase() === 'provisional'
  }
}

function effectiveMemberships(data: LightragGraphType): Map<string, Membership> {
  const rawMemberships = new Map(data.nodes.map((node) => [node.id, membership(node)]))
  const stableCounts = new Map<string, number>()
  for (const item of rawMemberships.values()) {
    if (item.communityId !== null && !item.provisional) {
      stableCounts.set(item.communityId, (stableCounts.get(item.communityId) ?? 0) + 1)
    }
  }
  const validCommunityIds = new Set(
    [...stableCounts]
      .filter(([, count]) => count >= 2)
      .map(([communityId]) => communityId)
  )
  return new Map(
    [...rawMemberships].map(([nodeId, item]) => [
      nodeId,
      item.communityId !== null && validCommunityIds.has(item.communityId)
        ? item
        : { communityId: null, provisional: false }
    ])
  )
}

function communityColor(communityId: string | null): string {
  if (communityId === null) return UNASSIGNED_COLOR
  return COMMUNITY_COLORS[hash(communityId) % COMMUNITY_COLORS.length]
}

function communityLabel(communityId: string): string {
  return `Community ${communityId.replace(/^community-/, '').slice(0, 8)}`
}

export type DreamingCommunityLegendItem = {
  id: string
  label: string
  color: string
}

export function buildDreamingCommunityLegend(data: LightragGraphType): DreamingCommunityLegendItem[] {
  const membershipByNode = effectiveMemberships(data)
  const communityIds = new Set<string>()
  const communityNames = new Map<string, string>()
  for (const node of data.nodes) {
    const communityId = membershipByNode.get(node.id)?.communityId ?? null
    if (communityId !== null) {
      communityIds.add(communityId)
      const communityName = node.properties?.dream_community_name ?? node.properties?.community_name
      if (communityName) communityNames.set(communityId, String(communityName))
    }
  }
  return [...communityIds]
    .sort()
    .map((id) => ({
      id,
      label: communityNames.get(id) || communityLabel(id),
      color: communityColor(id)
    }))
}

function nodeLabel(node: LightragNodeType): string {
  const entityId = node.properties?.entity_id
  if (entityId) return String(entityId)
  if (Array.isArray(node.labels) && node.labels.length > 0) return node.labels.join(', ')
  return node.id
}

function edgeLabel(properties: Record<string, any> | undefined): string {
  const keywords = properties?.keywords
  if (Array.isArray(keywords)) return keywords.join(', ')
  return String(keywords ?? properties?.description ?? '')
}

function safeEdgeKey(graph: DirectedGraph, preferred: string, index: number): string {
  let key = preferred || `edge:${index}`
  let suffix = 1
  while (graph.hasEdge(key)) key = `${preferred || `edge:${index}`}:${suffix++}`
  return key
}

export function buildDreamingMemberGraph(
  data: LightragGraphType,
  reports: DreamingCommunityReport[] = []
): DirectedGraph {
  const graph = new DirectedGraph()
  const membershipByNode = effectiveMemberships(data)
  const reportsByCommunity = new Map(reports.map((report) => [report.community_id, report]))
  const degrees = new Map<string, number>()
  for (const edge of data.edges) {
    degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1)
  }

  for (const node of data.nodes) {
    const member = membershipByNode.get(node.id)!
    graph.addNode(node.id, {
      ...position(node.id),
      ...node.properties,
      label: nodeLabel(node),
      size: 7 + Math.min(10, Math.sqrt(degrees.get(node.id) ?? 0) * 2),
      color: communityColor(member.communityId),
      borderColor: member.provisional ? '#f5d0fe' : '#ffffff',
      communityId: member.communityId,
      communityName: member.communityId
        ? reportsByCommunity.get(member.communityId)?.community_name
          ?? node.properties?.dream_community_name
          ?? node.properties?.community_name
          ?? null
        : null,
      membershipStatus: member.provisional ? 'provisional' : member.communityId ? 'stable' : 'unassigned'
    })
  }

  data.edges.forEach((edge, index) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target) || edge.source === edge.target) return
    if (graph.hasEdge(edge.source, edge.target)) return
    const sourceCommunity = membershipByNode.get(edge.source)?.communityId ?? null
    const targetCommunity = membershipByNode.get(edge.target)?.communityId ?? null
    graph.addDirectedEdgeWithKey(safeEdgeKey(graph, edge.id, index), edge.source, edge.target, {
      ...edge.properties,
      label: edgeLabel(edge.properties),
      size: Math.max(1, Number(edge.properties?.weight) || 1),
      color: sourceCommunity && targetCommunity && sourceCommunity !== targetCommunity
        ? '#f59e0b99'
        : '#94a3b866'
    })
  })
  return graph
}

export function buildDreamingCommunityGraph(
  data: LightragGraphType,
  reports: DreamingCommunityReport[] = []
): DirectedGraph {
  const membershipByNode = effectiveMemberships(data)
  const reportsByCommunity = new Map(reports.map((report) => [report.community_id, report]))
  const namesByCommunity = new Map<string, string>()
  for (const node of data.nodes) {
    const communityId = membershipByNode.get(node.id)?.communityId
    const communityName = node.properties?.dream_community_name ?? node.properties?.community_name
    if (communityId && communityName) namesByCommunity.set(communityId, String(communityName))
  }
  const hasCommunity = [...membershipByNode.values()].some((item) => item.communityId !== null)
  if (!hasCommunity) return buildDreamingMemberGraph(data, reports)

  const graph = new DirectedGraph()
  const stableCounts = new Map<string, number>()
  const provisionalCounts = new Map<string, number>()
  for (const item of membershipByNode.values()) {
    if (item.communityId === null) continue
    const target = item.provisional ? provisionalCounts : stableCounts
    target.set(item.communityId, (target.get(item.communityId) ?? 0) + 1)
  }

  const communityIds = new Set([...stableCounts.keys(), ...provisionalCounts.keys()])
  for (const communityId of communityIds) {
    const nodeId = `community:${communityId}`
    const stableCount = stableCounts.get(communityId) ?? 0
    const provisionalCount = provisionalCounts.get(communityId) ?? 0
    const memberCount = stableCount + provisionalCount
    const report = reportsByCommunity.get(communityId)
    graph.addNode(nodeId, {
      ...position(nodeId),
      label: report?.community_name || namesByCommunity.get(communityId) || communityLabel(communityId),
      type: 'border',
      nodeKind: 'community',
      size: 16 + Math.min(44, Math.sqrt(Math.max(memberCount, 1)) * 7),
      color: communityColor(communityId),
      borderColor: '#ffffff',
      communityId,
      communityName: report?.community_name ?? namesByCommunity.get(communityId) ?? null,
      stableMembers: stableCount,
      provisionalMembers: provisionalCount,
      memberCount,
      report: report?.report ?? null,
      promptTokens: report?.prompt_tokens ?? 0,
      completionTokens: report?.completion_tokens ?? 0,
      totalTokens: report?.total_tokens ?? 0,
      llmCallCount: report?.llm_call_count ?? (report ? 1 : 0),
      tokenUsageSource: report?.token_usage_source ?? null
    })
  }

  for (const node of data.nodes) {
    const item = membershipByNode.get(node.id)!
    if (!item.provisional && item.communityId !== null) continue
    graph.addNode(node.id, {
      ...position(node.id),
      ...node.properties,
      label: nodeLabel(node),
      size: item.provisional ? 6 : 7,
      color: item.provisional ? communityColor(item.communityId) : UNASSIGNED_COLOR,
      borderColor: item.provisional ? '#f5d0fe' : '#cbd5e1',
      communityId: item.communityId,
      membershipStatus: item.provisional ? 'provisional' : 'unassigned'
    })
    if (item.communityId !== null) {
      const communityNode = `community:${item.communityId}`
      graph.addDirectedEdgeWithKey(`membership:${node.id}`, communityNode, node.id, {
        label: 'provisional member',
        size: 1,
        color: `${communityColor(item.communityId)}88`,
        membership: true
      })
    }
  }

  const aggregateEdges = new Map<string, { source: string; target: string; linkCount: number }>()
  data.edges.forEach((edge, index) => {
    const sourceMembership = membershipByNode.get(edge.source)
    const targetMembership = membershipByNode.get(edge.target)
    if (!sourceMembership || !targetMembership) return

    const source = sourceMembership.provisional || sourceMembership.communityId === null
      ? edge.source
      : `community:${sourceMembership.communityId}`
    const target = targetMembership.provisional || targetMembership.communityId === null
      ? edge.target
      : `community:${targetMembership.communityId}`
    if (source === target || !graph.hasNode(source) || !graph.hasNode(target)) return

    if (source.startsWith('community:') && target.startsWith('community:')) {
      const pair = [source, target].sort()
      const key = pair.join('→')
      const current = aggregateEdges.get(key)
      aggregateEdges.set(key, {
        source: pair[0],
        target: pair[1],
        linkCount: (current?.linkCount ?? 0) + 1
      })
      return
    }

    if (graph.hasEdge(source, target)) return
    graph.addDirectedEdgeWithKey(safeEdgeKey(graph, `semantic:${edge.id}`, index), source, target, {
      label: edgeLabel(edge.properties),
      size: 1,
      color: '#94a3b877'
    })
  })

  for (const [key, edge] of aggregateEdges) {
    graph.addDirectedEdgeWithKey(`boundary:${key}`, edge.source, edge.target, {
      label: `${edge.linkCount} boundary ${edge.linkCount === 1 ? 'link' : 'links'}`,
      size: Math.min(10, 1 + Math.sqrt(edge.linkCount)),
      linkCount: edge.linkCount,
      color: '#f59e0baa'
    })
  }
  return graph
}
