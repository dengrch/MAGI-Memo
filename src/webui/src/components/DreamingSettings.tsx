import { useCallback, useEffect, useRef, useState } from 'react'
import {
  LoaderCircleIcon,
  MoonStarIcon,
  RefreshCwIcon,
  SparklesIcon
} from 'lucide-react'
import { toast } from 'sonner'

import { getDreamingStatus, startDreamingRun, type DreamingStatus } from '@/api/lightrag'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { graphGlassPanelClass } from '@/components/graph/glassStyles'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/Dialog'
import { cn, errorMessage } from '@/lib/utils'

import './DreamingSettings.css'

function formatCount(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: value >= 10_000 ? 'compact' : 'standard' }).format(value)
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-muted/40 rounded-lg border p-3">
      <div className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold">{value}</div>
    </div>
  )
}

export default function DreamingSettings({ onRunFinished }: { onRunFinished?: () => void }) {
  const [status, setStatus] = useState<DreamingStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const previouslyActive = useRef(false)

  const load = useCallback(async () => {
    try {
      const nextStatus = await getDreamingStatus()
      if (
        previouslyActive.current
        && nextStatus.active_run === null
        && nextStatus.latest_run?.status === 'succeeded'
      ) onRunFinished?.()
      previouslyActive.current = nextStatus.active_run !== null
      setStatus(nextStatus)
    } catch {
      // The global connection indicator already owns background connectivity.
      // Keep the last known Dreaming state instead of producing polling toasts.
    }
  }, [onRunFinished])

  useEffect(() => {
    const initialLoad = window.setTimeout(() => { void load() }, 0)
    const interval = window.setInterval(() => { void load() }, 2000)
    return () => {
      window.clearTimeout(initialLoad)
      window.clearInterval(interval)
    }
  }, [load])

  const dreamNow = async () => {
    setStarting(true)
    try {
      await startDreamingRun()
      await load()
      toast.success('BALTHASAR started a new Dreaming run')
    } catch (cause) {
      toast.error(`Unable to start Dreaming: ${errorMessage(cause)}`)
    } finally {
      setStarting(false)
    }
  }

  const activeRun = status?.active_run
  const latestRun = status?.latest_run
  const snapshot = status?.latest_snapshot
  const isFailed = latestRun?.status === 'failed' && !activeRun
  const stateLabel = activeRun ? activeRun.phase : isFailed ? 'Failed' : snapshot ? 'Ready' : 'Not run'
  const communityCount = snapshot?.community_count ?? 0
  const totalTokens = snapshot?.total_tokens ?? 0
  const compactStatus = activeRun
    ? `Dreaming · ${activeRun.phase}`
    : isFailed
      ? 'Latest run failed'
      : snapshot
        ? 'Latest run succeeded'
        : 'No Dreaming run yet'

  return (
    <>
      <section className={cn(graphGlassPanelClass, 'w-60 p-2')}>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDetailsOpen(true)}
            className="hover:bg-accent/50 flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1 text-left transition-colors"
            aria-label="Open Dreaming details"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-500 dark:bg-violet-400/10 dark:text-violet-300">
              <MoonStarIcon className="size-3.5" />
            </span>
            <span className="truncate text-xs font-medium">Dreaming</span>
          </button>
          <span className="dream-action-halo">
            <Button
              variant="ghost"
              size="sm"
              tooltip="Start Dreaming"
              tooltipAlign="center"
              aria-label="Start Dreaming"
              disabled={Boolean(activeRun) || starting}
              onClick={() => void dreamNow()}
              className="dream-glass-button dream-glass-button--compact group h-8 gap-1.5 px-2.5 text-[11px] font-semibold disabled:opacity-60"
            >
              <span className="dream-glass-button__content flex items-center gap-1.5">
                {activeRun || starting
                  ? <LoaderCircleIcon className="size-3.5 animate-spin" />
                  : <SparklesIcon className="size-3.5" />}
                <span>{activeRun || starting ? 'Running' : 'Start'}</span>
              </span>
            </Button>
          </span>
        </div>
        <div className="text-muted-foreground mt-1.5 flex items-center gap-1.5 border-t border-border/60 px-1.5 pt-1.5 text-[10px]">
          <span
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              activeRun
                ? 'bg-amber-500/80'
                : isFailed
                  ? 'bg-destructive/80'
                  : snapshot
                    ? 'bg-emerald-500/70'
                    : 'bg-muted-foreground/35'
            )}
          />
          <span className={cn('truncate', isFailed && 'text-destructive')} title={compactStatus}>
            {compactStatus}
          </span>
        </div>
      </section>

      <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MoonStarIcon className="size-5 text-violet-500 dark:text-violet-300" /> Dreaming
            </DialogTitle>
            <DialogDescription>
              Rebuilds memory communities, synthesizes their reports with the Dream model, and publishes a new snapshot.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Badge variant="outline">{activeRun ? `Running · ${activeRun.phase}` : stateLabel}</Badge>
                {snapshot?.token_usage_source && <Badge variant="outline">{snapshot.token_usage_source} usage</Badge>}
              </div>
              <Button variant="ghost" size="icon" onClick={() => void load()} aria-label="Refresh Dreaming status">
                <RefreshCwIcon className="size-4" />
              </Button>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Communities" value={communityCount} />
              <Metric label="Reports" value={snapshot?.report_count ?? 0} />
              <Metric label="LLM calls" value={snapshot?.llm_call_count ?? 0} />
              <Metric label="Total tokens" value={formatCount(totalTokens)} />
            </div>

            <div className="rounded-lg border p-4">
              <h4 className="mb-3 text-sm font-medium">Published snapshot cost</h4>
              <dl className="grid gap-3 text-xs sm:grid-cols-3">
                <div><dt className="text-muted-foreground">Prompt tokens</dt><dd className="mt-1 font-mono text-base">{snapshot?.prompt_tokens ?? 0}</dd></div>
                <div><dt className="text-muted-foreground">Completion tokens</dt><dd className="mt-1 font-mono text-base">{snapshot?.completion_tokens ?? 0}</dd></div>
                <div><dt className="text-muted-foreground">Published</dt><dd className="mt-1 text-sm">{snapshot?.published_at ? new Date(snapshot.published_at).toLocaleString() : 'Never'}</dd></div>
              </dl>
            </div>

            {isFailed && (
              <div className="border-destructive/30 bg-destructive/5 rounded-lg border p-3">
                <p className="text-destructive text-xs font-medium">Latest run failed</p>
                <p className="text-muted-foreground mt-1 break-words text-xs">{latestRun?.error || 'Dreaming failed'}</p>
              </div>
            )}

            <div className="dream-action-halo dream-action-halo--wide">
              <Button
                variant="ghost"
                disabled={Boolean(activeRun) || starting}
                onClick={() => void dreamNow()}
                className="dream-glass-button dream-glass-button--wide group w-full disabled:opacity-60"
              >
                <span className="dream-glass-button__content flex items-center gap-2">
                  {activeRun || starting
                    ? <LoaderCircleIcon className="size-4 animate-spin" />
                    : <SparklesIcon className="size-4" />}
                  {activeRun ? `Dreaming · ${activeRun.phase}…` : starting ? 'Starting…' : 'Dream now'}
                </span>
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
