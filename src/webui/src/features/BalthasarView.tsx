import { useCallback, useEffect, useMemo, useState } from 'react'
import { LoaderCircleIcon, NetworkIcon, OrbitIcon, RefreshCwIcon } from 'lucide-react'

import {
  getDreamingCommunityReports,
  queryGraphs,
  type DreamingCommunityReport,
  type LightragGraphType
} from '@/api/lightrag'
import DreamingSettings from '@/components/DreamingSettings'
import {
  graphGlassInsetClass,
  graphGlassPanelClass,
  graphGlassControlClass
} from '@/components/graph/glassStyles'
import Badge from '@/components/ui/Badge'
import { GraphRuntimeProvider } from '@/contexts/GraphRuntimeContext'
import GraphViewer from '@/features/GraphViewer'
import { cn, isNetworkError } from '@/lib/utils'
import { createGraphStore } from '@/stores/graph'
import { useSettingsStore } from '@/stores/settings'
import {
  buildDreamingCommunityGraph,
  buildDreamingCommunityLegend,
  buildDreamingMemberGraph,
  type DreamingCommunityLegendItem
} from '@/utils/dreamingGraph'

type DreamingView = 'members' | 'communities'

const dreamingGraphStore = createGraphStore()
let cachedDreamingData: LightragGraphType | null = null
let cachedDreamingReports: DreamingCommunityReport[] = []
let cachedDreamingView: DreamingView = 'members'
let cachedProjection: {
  data: LightragGraphType
  reports: DreamingCommunityReport[]
  view: DreamingView
  graph: ReturnType<typeof buildDreamingMemberGraph>
} | null = null

function getDreamingProjection(
  data: LightragGraphType,
  reports: DreamingCommunityReport[],
  view: DreamingView
): ReturnType<typeof buildDreamingMemberGraph> {
  if (
    cachedProjection?.data === data
    && cachedProjection.reports === reports
    && cachedProjection.view === view
  ) return cachedProjection.graph
  const graph = view === 'members'
    ? buildDreamingMemberGraph(data, reports)
    : buildDreamingCommunityGraph(data, reports)
  cachedProjection = { data, reports, view, graph }
  return graph
}

function DreamingGraphProjector({
  data,
  reports,
  view
}: {
  data: LightragGraphType | null
  reports: DreamingCommunityReport[]
  view: DreamingView
}) {
  const graph = useMemo(() => {
    if (data === null) return null
    return getDreamingProjection(data, reports, view)
  }, [data, reports, view])

  useEffect(() => {
    if (graph === null) return
    if (dreamingGraphStore.getState().sigmaGraph === graph) return
    dreamingGraphStore.getState().clearSelection()
    dreamingGraphStore.getState().setSigmaGraph(graph)
    dreamingGraphStore.getState().setGraphCounts(graph.order, graph.size)
    dreamingGraphStore.getState().setGraphIsEmpty(graph.order === 0)
  }, [graph])
  return null
}

function ViewSwitch({
  value,
  onChange,
  onRefresh,
  refreshing
}: {
  value: DreamingView
  onChange: (value: DreamingView) => void
  onRefresh: () => void
  refreshing: boolean
}) {
  return (
    <div className={cn(graphGlassControlClass, 'flex items-center p-1')}>
      <button
        type="button"
        onClick={() => onChange('members')}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors',
          value === 'members' ? 'bg-accent text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
        )}
      >
        <NetworkIcon className="size-3.5" /> Members
      </button>
      <button
        type="button"
        onClick={() => onChange('communities')}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors',
          value === 'communities' ? 'bg-accent text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
        )}
      >
        <OrbitIcon className="size-3.5" /> Communities
      </button>
      <span className="bg-border mx-1 h-5 w-px" />
      <button
        type="button"
        onClick={onRefresh}
        disabled={refreshing}
        className="text-muted-foreground hover:text-foreground flex size-8 items-center justify-center rounded-lg transition-colors disabled:cursor-wait"
        aria-label="Refresh Balthasar graph"
        title="Refresh graph"
      >
        <RefreshCwIcon className={cn('size-3.5', refreshing && 'animate-spin')} />
      </button>
    </div>
  )
}

function CommunityLegend({ items }: { items: DreamingCommunityLegendItem[] }) {
  if (items.length === 0) return null
  return (
    <section className={cn(graphGlassPanelClass, 'absolute right-2 bottom-10 z-0 max-h-72 w-48 overflow-auto p-2')}>
      <h3 className="text-muted-foreground px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider">
        Communities
      </h3>
      <div className="space-y-0.5">
        {items.map((item) => (
          <div key={item.id} className="flex items-center gap-2 rounded-lg px-1 py-1 text-xs">
            <span
              className="size-3.5 shrink-0 rounded-full border border-white/60 shadow-sm"
              style={{ backgroundColor: item.color }}
            />
            <span className="truncate" title={`${item.label}\n${item.id}`}>{item.label}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function DreamingProperties() {
  const selectedNode = dreamingGraphStore.use.selectedNode()
  const focusedNode = dreamingGraphStore.use.focusedNode()
  const graph = dreamingGraphStore.use.sigmaGraph()
  const nodeId = focusedNode ?? selectedNode

  if (graph === null || nodeId === null || !graph.hasNode(nodeId)) return null
  const attributes = graph.getNodeAttributes(nodeId)
  const isCommunity = attributes.nodeKind === 'community'

  if (isCommunity) {
    return (
      <section className={cn(graphGlassPanelClass, 'flex max-h-[calc(100vh-7rem)] w-80 flex-col overflow-hidden text-xs')}>
        <div className="border-b p-3">
          <div className="flex items-center justify-between gap-2">
            <h3
              className="truncate text-sm font-semibold"
              title={String(attributes.communityName || attributes.label)}
            >
              {attributes.communityName || attributes.label}
            </h3>
            <Badge variant="outline" className="shrink-0 text-[9px]">community</Badge>
          </div>
          <p className="text-muted-foreground mt-1 font-mono text-[10px]">{attributes.communityId}</p>
        </div>
        <div className="min-h-0 space-y-4 overflow-y-auto p-3">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className={cn(graphGlassInsetClass, 'border-0 p-2')}><strong className="block text-sm">{attributes.memberCount}</strong><span className="text-muted-foreground">members</span></div>
            <div className={cn(graphGlassInsetClass, 'border-0 p-2')}><strong className="block text-sm">{attributes.stableMembers}</strong><span className="text-muted-foreground">stable</span></div>
            <div className={cn(graphGlassInsetClass, 'border-0 p-2')}><strong className="block text-sm">{attributes.provisionalMembers}</strong><span className="text-muted-foreground">new</span></div>
          </div>
          <div>
            <h4 className="text-muted-foreground mb-1.5 text-[10px] font-semibold uppercase tracking-wider">Community report</h4>
            <p className="whitespace-pre-wrap text-xs leading-relaxed">
              {attributes.report || 'No report has been generated for this community yet.'}
            </p>
          </div>
          {Number(attributes.totalTokens) > 0 && (
            <div className={cn(graphGlassInsetClass, 'p-2.5')}>
              <h4 className="text-muted-foreground mb-2 text-[10px] font-semibold uppercase tracking-wider">Report token usage</h4>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                <dt className="text-muted-foreground">Prompt</dt><dd className="text-right font-mono">{attributes.promptTokens}</dd>
                <dt className="text-muted-foreground">Completion</dt><dd className="text-right font-mono">{attributes.completionTokens}</dd>
                <dt className="font-medium">Total</dt><dd className="text-right font-mono font-medium">{attributes.totalTokens}</dd>
                <dt className="text-muted-foreground">Calls</dt><dd className="text-right font-mono">{attributes.llmCallCount}</dd>
                <dt className="text-muted-foreground">Source</dt><dd className="text-right">{attributes.tokenUsageSource || 'unknown'}</dd>
              </dl>
            </div>
          )}
        </div>
      </section>
    )
  }

  const entityType = attributes.entity_type ?? attributes.entityType ?? 'entity'
  const description = attributes.description ?? 'No description is available for this entity.'
  const communityId = attributes.communityId ?? attributes.dream_community_id ?? null
  const communityName = attributes.communityName ?? attributes.dream_community_name ?? null
  const membershipStatus = attributes.membershipStatus ?? attributes.dream_membership_status ?? 'unassigned'

  return (
    <section className={cn(graphGlassPanelClass, 'flex max-h-[calc(100vh-7rem)] w-80 flex-col overflow-hidden text-xs')}>
      <div className="border-b p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate text-sm font-semibold" title={String(attributes.label || nodeId)}>
            {attributes.label || nodeId}
          </h3>
          <Badge variant="outline" className="shrink-0 text-[9px]">{String(entityType)}</Badge>
        </div>
        <p className="text-muted-foreground mt-1">{String(membershipStatus)}</p>
      </div>
      <div className="min-h-0 space-y-4 overflow-y-auto p-3">
        <div>
          <h4 className="text-muted-foreground mb-1.5 text-[10px] font-semibold uppercase tracking-wider">Description</h4>
          <p className="whitespace-pre-wrap text-xs leading-relaxed">{String(description)}</p>
        </div>
        <div>
          <h4 className="text-muted-foreground mb-1.5 text-[10px] font-semibold uppercase tracking-wider">Community</h4>
          {communityId ? (
            <div className={cn(graphGlassInsetClass, 'p-2.5')}>
              <p className="font-medium">{communityName ? String(communityName) : 'Unnamed community'}</p>
              <p className="text-muted-foreground mt-1 break-all font-mono text-[10px]">{String(communityId)}</p>
            </div>
          ) : (
            <p className="text-muted-foreground">Not assigned to a community.</p>
          )}
        </div>
      </div>
    </section>
  )
}

function DreamingOverlay({
  view,
  onViewChange,
  legend,
  onDreamingFinished,
  onRefresh,
  refreshing
}: {
  view: DreamingView
  onViewChange: (value: DreamingView) => void
  legend: DreamingCommunityLegendItem[]
  onDreamingFinished: () => void
  onRefresh: () => void
  refreshing: boolean
}) {
  return (
    <>
      <div className="absolute top-2 left-1/2 z-30 -translate-x-1/2">
        <ViewSwitch
          value={view}
          onChange={onViewChange}
          onRefresh={onRefresh}
          refreshing={refreshing}
        />
      </div>
      <div className="absolute top-14 left-2 z-30">
        <DreamingSettings onRunFinished={onDreamingFinished} />
      </div>
      <CommunityLegend items={legend} />
    </>
  )
}

export default function BalthasarView() {
  const [view, setView] = useState<DreamingView>(cachedDreamingView)
  const [data, setData] = useState<LightragGraphType | null>(cachedDreamingData)
  const [reports, setReports] = useState<DreamingCommunityReport[]>(cachedDreamingReports)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const currentTab = useSettingsStore.use.currentTab()
  const queryLabel = useSettingsStore.use.queryLabel()
  const maxDepth = useSettingsStore.use.graphQueryMaxDepth()
  const maxNodes = useSettingsStore.use.graphMaxNodes()
  const legend = useMemo(() => data === null ? [] : buildDreamingCommunityLegend(data), [data])
  const refreshAfterDreaming = useCallback(() => setRefreshKey((value) => value + 1), [])
  const refreshGraph = useCallback(() => setRefreshKey((value) => value + 1), [])
  const changeView = useCallback((nextView: DreamingView) => {
    cachedDreamingView = nextView
    setView(nextView)
  }, [])

  useEffect(() => {
    if (currentTab === 'balthasar') return
    // Balthasar is force-mounted while hidden. Stop its relaxing layout when
    // leaving the tab so returning shows the exact cached coordinates instead
    // of revealing a worker that kept animating behind the inactive tab.
    dreamingGraphStore.getState().setActiveLayoutSupervisor(null)
  }, [currentTab])

  useEffect(() => {
    if (currentTab !== 'balthasar') return
    if (cachedDreamingData !== null && refreshKey === 0) return
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [graph, communityReports] = await Promise.all([
          queryGraphs(queryLabel || '*', maxDepth, maxNodes, true),
          getDreamingCommunityReports().catch(() => [])
        ])
        if (!cancelled) {
          cachedDreamingData = graph
          cachedDreamingReports = communityReports
          cachedProjection = null
          setData(graph)
          setReports(communityReports)
          setError(null)
        }
      } catch (cause) {
        if (!cancelled) {
          setError(isNetworkError(cause)
            ? null
            : cause instanceof Error ? cause.message : 'Unable to load graph')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [currentTab, maxDepth, maxNodes, queryLabel, refreshKey])

  return (
    <GraphRuntimeProvider store={dreamingGraphStore}>
      <DreamingGraphProjector data={data} reports={reports} view={view} />
      <div className="relative h-full min-h-0">
        <GraphViewer
          readOnly
          propertyPanel={<DreamingProperties />}
          overlay={(
            <DreamingOverlay
              view={view}
              onViewChange={changeView}
              legend={legend}
              onDreamingFinished={refreshAfterDreaming}
              onRefresh={refreshGraph}
              refreshing={loading}
            />
          )}
        />
        {loading && data === null && (
          <div className="bg-background/70 pointer-events-none absolute inset-0 z-20 flex items-center justify-center gap-2 text-sm backdrop-blur-sm">
            <LoaderCircleIcon className="size-4 animate-spin text-violet-400" /> Loading BALTHASAR graph…
          </div>
        )}
        {error && data === null && (
          <div className="bg-destructive/10 text-destructive absolute bottom-4 left-1/2 z-30 -translate-x-1/2 rounded-md border px-3 py-2 text-xs">
            {error}
          </div>
        )}
      </div>
    </GraphRuntimeProvider>
  )
}
