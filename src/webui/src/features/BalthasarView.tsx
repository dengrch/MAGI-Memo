import { useEffect, useMemo, useState } from 'react'
import { ActivityIcon, ChevronDownIcon, HistoryIcon, OrbitIcon } from 'lucide-react'

import { getExplorationEvents, getExplorationTraces } from '@/api/lightrag'
import Badge from '@/components/ui/Badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover'
import { GraphRuntimeProvider } from '@/contexts/GraphRuntimeContext'
import GraphViewer from '@/features/GraphViewer'
import {
  latestExpandFramesByAgent,
  reduceExplorationEvents,
  useExplorationStore,
  type ExpandFrame
} from '@/stores/exploration'
import { createGraphStore } from '@/stores/graph'
import { useSettingsStore } from '@/stores/settings'
import { agentColor, buildExplorationGraph } from '@/utils/explorationGraph'
import { ExplorationFrontierNodeProgram } from '@/utils/explorationNodeProgram'

const explorationGraphStore = createGraphStore()
const explorationNodePrograms = { frontier: ExplorationFrontierNodeProgram }

function shortId(value: string): string {
  return value.length <= 14 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`
}

function ExplorationGraphProjector({ frames }: { frames: Record<string, ExpandFrame> }) {
  const graph = useMemo(() => buildExplorationGraph(frames), [frames])

  useEffect(() => {
    const typeColors = new Map<string, string>()
    graph.forEachNode((_node, attributes) => {
      if (
        attributes.explorationState !== 'candidate' &&
        typeof attributes.entityType === 'string' &&
        typeof attributes.color === 'string'
      ) {
        typeColors.set(attributes.entityType, attributes.color)
      }
    })
    explorationGraphStore.getState().clearSelection()
    explorationGraphStore.getState().setTypeColorMap(typeColors)
    explorationGraphStore.getState().setSigmaGraph(graph as never)
    explorationGraphStore.getState().setGraphCounts(graph.order, graph.size)
  }, [graph])
  return null
}

function ExplorationProperties() {
  const selectedNode = explorationGraphStore.use.selectedNode()
  const focusedNode = explorationGraphStore.use.focusedNode()
  const selectedEdge = explorationGraphStore.use.selectedEdge()
  const focusedEdge = explorationGraphStore.use.focusedEdge()
  const graph = explorationGraphStore.use.sigmaGraph()
  const node = focusedNode ?? selectedNode
  const edge = node === null ? focusedEdge ?? selectedEdge : null

  if (graph === null) return null
  const isNode = node !== null && graph.hasNode(node)
  const isEdge = !isNode && edge !== null && graph.hasEdge(edge)
  if (!isNode && !isEdge) return null

  const attributes = isNode
    ? graph.getNodeAttributes(node!)
    : graph.getEdgeAttributes(edge!)
  const title = isNode
    ? String(attributes.label ?? node)
    : `${String(graph.getNodeAttribute(graph.source(edge!), 'label'))} → ${String(graph.getNodeAttribute(graph.target(edge!), 'label'))}`

  return (
    <div className="bg-background/90 flex max-h-[calc(100vh-7rem)] w-80 flex-col rounded-lg border text-xs shadow-lg backdrop-blur-lg">
      <div className="border-border border-b px-3 py-2">
        <div className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
          {isNode ? 'node' : 'edge'} · read only
        </div>
        <div className="mt-0.5 truncate text-sm font-medium">{title}</div>
      </div>
      <dl className="min-h-0 space-y-3 overflow-auto p-3">
        {Object.entries(attributes)
          .filter(([key, value]) => ![
            'x', 'y', 'color', 'borderColor', 'agentColors'
          ].includes(key) && value !== undefined && value !== '')
          .map(([key, value]) => (
            <div key={key}>
              <dt className="text-muted-foreground mb-1 font-medium">{key}</dt>
              <dd className="whitespace-pre-wrap break-words">
                {Array.isArray(value) ? value.join(', ') : String(value)}
              </dd>
            </div>
          ))}
      </dl>
    </div>
  )
}

function ExplorationOverlay({
  query,
  explorationId,
  lastSeq,
  frames,
  historyFrames,
  replaySeq,
  onReplaySeq
}: {
  query: string
  explorationId: string
  lastSeq: number
  frames: ExpandFrame[]
  historyFrames: ExpandFrame[]
  replaySeq: number | null
  onReplaySeq: (seq: number | null) => void
}) {
  const expanding = frames.some((frame) => frame.status === 'expanding')
  const agentOrder = useMemo(() => [
    ...new Set(
      [...historyFrames]
        .sort((left, right) => left.startedSeq - right.startedSeq)
        .map((frame) => frame.agentId)
    )
  ], [historyFrames])
  const timeline = useMemo(() => {
    let round = 0
    return [...historyFrames]
      .sort((left, right) => left.startedSeq - right.startedSeq)
      .map((frame) => ({
        frame,
        round: frame.status === 'initialized' ? null : ++round,
        sceneSeq: frame.event.seq
      }))
      .reverse()
  }, [historyFrames])
  return (
    <>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="bg-background/60 absolute top-2 left-1/2 z-20 flex max-w-[42rem] -translate-x-1/2 items-center gap-2 rounded-xl border px-3 py-2 opacity-70 shadow-sm backdrop-blur-lg transition-opacity hover:opacity-100 data-[state=open]:opacity-100"
          >
            <OrbitIcon className="size-3.5 shrink-0 text-cyan-400" />
            <span className="truncate text-xs">{query}</span>
            <Badge variant="outline" className="shrink-0 text-[10px]">
              {replaySeq !== null ? `history · seq ${replaySeq}` : expanding ? 'expanding' : `seq ${lastSeq}`}
            </Badge>
            <ChevronDownIcon className="text-muted-foreground size-3.5 shrink-0" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="center"
          sideOffset={6}
          className="bg-background/75 max-h-[min(32rem,70vh)] w-[min(34rem,calc(100vw-2rem))] overflow-hidden rounded-xl p-0 shadow-xl backdrop-blur-xl"
        >
          <div className="border-border flex items-center justify-between border-b px-3 py-2.5">
            <div className="min-w-0">
              <div className="text-muted-foreground flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider">
                <HistoryIcon className="size-3" /> Exploration history
              </div>
              <div className="mt-1 truncate text-xs">{query}</div>
            </div>
            {replaySeq !== null && (
              <button
                type="button"
                className="hover:bg-accent ml-3 shrink-0 rounded-md border px-2 py-1 text-[10px]"
                onClick={() => onReplaySeq(null)}
              >
                Return to live
              </button>
            )}
          </div>
          <div className="max-h-[min(26rem,60vh)] space-y-1 overflow-y-auto p-2">
            {timeline.map(({ frame, round, sceneSeq }) => {
              const selected = replaySeq === sceneSeq
              return (
                <button
                  key={`${frame.agentId}:${frame.callId}`}
                  type="button"
                  className={`hover:bg-accent/70 flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${selected ? 'border-cyan-400/50 bg-cyan-400/10' : 'border-transparent'}`}
                  onClick={() => onReplaySeq(sceneSeq)}
                >
                  <span
                    className="size-2.5 shrink-0 rounded-full border-2"
                    style={{
                      borderColor: agentColor(frame.agentId, agentOrder),
                      boxShadow: `0 0 8px 2px ${agentColor(frame.agentId, agentOrder)}77`
                    }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-xs font-medium">
                      {round === null ? 'Initial visit' : `Expand ${round}`}
                      <span className="text-muted-foreground font-normal">
                        {frame.startedSeq === sceneSeq ? `seq ${sceneSeq}` : `seq ${frame.startedSeq}–${sceneSeq}`}
                      </span>
                    </span>
                    <span className="text-muted-foreground mt-0.5 block truncate text-[10px]">
                      {shortId(frame.agentId)} · {frame.query ?? query}
                    </span>
                  </span>
                  <Badge variant="outline" className="shrink-0 text-[9px]">
                    {frame.status}
                  </Badge>
                </button>
              )
            })}
          </div>
        </PopoverContent>
      </Popover>
      <div className="pointer-events-none absolute top-14 left-2 z-10 rounded-lg border bg-background/60 px-3 py-2 opacity-75 shadow-sm backdrop-blur-lg">
        <div className="text-muted-foreground mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider">
          <ActivityIcon className="size-3" /> Agents
        </div>
        <div className="space-y-1.5">
          {frames.map((frame, index) => {
            const color = agentColor(frame.agentId, agentOrder)
            return (
              <div key={frame.agentId} className="flex items-center gap-2 text-xs">
                <span
                  className="size-2.5 rounded-full border-2"
                  style={{ borderColor: color, boxShadow: `0 0 8px 2px ${color}99` }}
                />
                <span>s{index}</span>
                <span className="text-muted-foreground">{shortId(frame.agentId)}</span>
              </div>
            )
          })}
        </div>
        <div className="text-muted-foreground mt-2 text-[10px]">
          {shortId(explorationId)}
        </div>
      </div>
    </>
  )
}

export default function BalthasarView() {
  const [replaySelection, setReplaySelection] = useState<{
    explorationId: string
    seq: number
  } | null>(null)
  const currentTab = useSettingsStore.use.currentTab()
  const traces = useExplorationStore.use.traces()
  const currentExplorationId = useExplorationStore.use.currentExplorationId()
  const lastSeq = useExplorationStore.use.lastSeq()
  const events = useExplorationStore.use.events()
  const expandCalls = useExplorationStore.use.expandCalls()
  const error = useExplorationStore.use.error()
  const replaySeq = replaySelection?.explorationId === currentExplorationId
    ? replaySelection.seq
    : null
  const activeTrace = traces.find((trace) => trace.exploration_id === currentExplorationId)
  const visibleExpandCalls = useMemo(
    () => replaySeq === null
      ? expandCalls
      : reduceExplorationEvents({}, events.filter((event) => event.seq <= replaySeq)),
    [events, expandCalls, replaySeq]
  )
  const latestExpandByAgent = useMemo(
    () => latestExpandFramesByAgent(visibleExpandCalls),
    [visibleExpandCalls]
  )
  const frames = Object.values(latestExpandByAgent)
  const historyFrames = Object.values(expandCalls)

  useEffect(() => {
    if (currentTab !== 'balthasar') return
    let cancelled = false
    let pending = false
    const refresh = async () => {
      if (pending) return
      pending = true
      try {
        const result = await getExplorationTraces()
        if (!cancelled) {
          useExplorationStore.getState().setTraces(result)
          useExplorationStore.getState().setError(null)
        }
      } catch (cause) {
        if (!cancelled) {
          useExplorationStore.getState().setError(
            cause instanceof Error ? cause.message : 'Unable to load exploration traces'
          )
        }
      } finally {
        pending = false
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, 250)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [currentTab])

  useEffect(() => {
    if (currentTab !== 'balthasar' || currentExplorationId === null) return
    let cancelled = false
    let pending = false
    const refresh = async () => {
      if (pending) return
      pending = true
      try {
        const state = useExplorationStore.getState()
        const result = await getExplorationEvents(currentExplorationId, state.lastSeq)
        if (!cancelled && result.events.length > 0) {
          useExplorationStore.getState().applyEvents(result.events)
          useExplorationStore.getState().setError(null)
        }
      } catch (cause) {
        if (!cancelled) {
          useExplorationStore.getState().setError(
            cause instanceof Error ? cause.message : 'Unable to load exploration events'
          )
        }
      } finally {
        pending = false
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, 250)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [currentExplorationId, currentTab])

  return (
    <GraphRuntimeProvider store={explorationGraphStore}>
      <ExplorationGraphProjector frames={visibleExpandCalls} />
      <div className="relative h-full min-h-0">
        <GraphViewer
          readOnly
          nodeProgramClasses={explorationNodePrograms}
          propertyPanel={<ExplorationProperties />}
          overlay={activeTrace ? (
            <ExplorationOverlay
              query={activeTrace.query ?? ''}
              explorationId={activeTrace.exploration_id}
              lastSeq={Math.max(activeTrace.last_seq, lastSeq)}
              frames={frames}
              historyFrames={historyFrames}
              replaySeq={replaySeq}
              onReplaySeq={(seq) => {
                if (seq === null || currentExplorationId === null) {
                  setReplaySelection(null)
                  return
                }
                setReplaySelection({ explorationId: currentExplorationId, seq })
              }}
            />
          ) : undefined}
        />
        {error && (
          <div className="absolute right-4 bottom-4 z-30 max-w-md rounded-md border border-rose-400/30 bg-rose-950/80 px-3 py-2 text-xs text-rose-200">
            {error}
          </div>
        )}
      </div>
    </GraphRuntimeProvider>
  )
}
