import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  AtomEvidenceView,
  getMemoryAtom,
  getMemoryAtoms,
  getMemoryCommunities,
  getMemoryCommunity,
  getMemoryEntities,
  getMemoryEntity,
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryOverview,
  getMemoryRelation,
  getMemoryRelations,
  MemoryAtom,
  MemoryAtomEvolution,
  MemoryCommunity,
  MemoryEntity,
  MemoryEntityAliasAmbiguity,
  MemoryEpisode,
  MemoryOverview,
  MemoryRelation,
  MemoryTimeBounds,
  resolveMemoryEntityAlias,
  resolveMemoryAtomConflict
} from '@/api/lightrag'
import DocumentManager from '@/features/DocumentManager'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Badge from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from '@/components/ui/AlertDialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover'
import { cn, errorMessage } from '@/lib/utils'
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  AtomIcon,
  BookOpenIcon,
  FingerprintIcon,
  FilterIcon,
  GitBranchIcon,
  HexagonIcon,
  HistoryIcon,
  InboxIcon,
  LinkIcon,
  OrbitIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldCheckIcon,
  TagsIcon,
  XIcon
} from 'lucide-react'

type MemoryView = 'episodes' | 'atoms' | 'entities' | 'relations' | 'communities' | 'ingest'

type PendingConflictResolution = {
  evolution: MemoryAtomEvolution
  winnerAtomId: string
}

type PendingAliasResolution = {
  ambiguity: MemoryEntityAliasAmbiguity
  winner: {
    entity_id: string
    canonical_name: string
  }
  candidates: Array<{
    entity_id: string
    canonical_name: string
  }>
}

type TimeRange = [number, number]

type AppliedTimeRanges = {
  episodeReference: TimeRange | null
  atomValidity: TimeRange | null
  atomSystem: TimeRange | null
}

const formatTime = (value?: string | null) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const memoryErrorMessage = (reason: unknown) => {
  const detail = typeof reason === 'object' && reason !== null
    ? (reason as { response?: { data?: { detail?: string } } }).response?.data?.detail
    : undefined
  return detail || errorMessage(reason)
}

const shorten = (value: string, length = 120) =>
  value.length > length ? `${value.slice(0, length)}…` : value

const comparableText = (value?: string | null) =>
  (value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase()

const hasDistinctQuote = (atom: MemoryAtom) =>
  Boolean(atom.quote && comparableText(atom.quote) !== comparableText(atom.content))

const displayAliases = (entity: MemoryEntity) =>
  (entity.aliases || []).filter(
    (alias) => comparableText(alias) !== comparableText(entity.canonical_name)
  )

const boundsRange = (bounds?: MemoryTimeBounds | null): TimeRange | null => {
  const minimum = bounds?.min ? new Date(bounds.min).getTime() : Number.NaN
  const maximum = bounds?.max ? new Date(bounds.max).getTime() : Number.NaN
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null
  return minimum <= maximum ? [minimum, maximum] : [maximum, minimum]
}

const formatRangeTime = (value: number) => new Date(value).toLocaleString([], {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false
})

const rangeParam = (value: number) => new Date(value).toISOString()

function TimeRangeSlider({
  label,
  description,
  bounds,
  value,
  onChange,
  emptyLabel
}: {
  label: string
  description: string
  bounds?: MemoryTimeBounds | null
  value: TimeRange | null
  onChange: (value: TimeRange) => void
  emptyLabel: string
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const activeHandleRef = useRef<'start' | 'end' | null>(null)
  const fullRange = boundsRange(bounds)
  if (!fullRange) {
    return (
      <div className="magi-memory-inset p-3">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-muted-foreground mt-1 text-xs">{emptyLabel}</div>
      </div>
    )
  }
  const [minimum, maximum] = fullRange
  const [rawStart, rawEnd] = value || fullRange
  const start = Math.max(minimum, Math.min(rawStart, maximum))
  const end = Math.max(start, Math.min(rawEnd, maximum))
  const span = Math.max(maximum - minimum, 1)
  const startPercent = ((start - minimum) / span) * 100
  const endPercent = ((end - minimum) / span) * 100
  const step = Math.max(Math.round(span / 600), 60_000)

  const valueAtPointer = (clientX: number) => {
    const track = trackRef.current
    if (!track) return minimum
    const rect = track.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(rect.width, 1)))
    return Math.round((minimum + ratio * span) / step) * step
  }

  const updateHandle = (handle: 'start' | 'end', nextValue: number) => {
    if (handle === 'start') {
      onChange([Math.max(minimum, Math.min(nextValue, end)), end])
    } else {
      onChange([start, Math.min(maximum, Math.max(nextValue, start))])
    }
  }

  const moveHandleByKey = (
    event: React.KeyboardEvent<HTMLDivElement>,
    handle: 'start' | 'end'
  ) => {
    const current = handle === 'start' ? start : end
    let next = current
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next -= step
    else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next += step
    else if (event.key === 'Home') next = minimum
    else if (event.key === 'End') next = maximum
    else return
    event.preventDefault()
    updateHandle(handle, next)
  }

  return (
    <div className="magi-memory-inset p-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-muted-foreground mt-0.5 text-[11px]">{description}</div>
      </div>
      <div
        ref={trackRef}
        className="magi-memory-dual-range mt-4 touch-none"
        onPointerDown={(event) => {
          const next = valueAtPointer(event.clientX)
          const handle = Math.abs(next - start) <= Math.abs(next - end) ? 'start' : 'end'
          activeHandleRef.current = handle
          event.currentTarget.setPointerCapture(event.pointerId)
          updateHandle(handle, next)
        }}
        onPointerMove={(event) => {
          if (activeHandleRef.current) updateHandle(activeHandleRef.current, valueAtPointer(event.clientX))
        }}
        onPointerUp={(event) => {
          activeHandleRef.current = null
          event.currentTarget.releasePointerCapture(event.pointerId)
        }}
        onPointerCancel={() => {
          activeHandleRef.current = null
        }}
      >
        <div className="magi-memory-range-track" />
        <div
          className="magi-memory-range-fill"
          style={{ left: `${startPercent}%`, right: `${100 - endPercent}%` }}
        />
        <div
          role="slider"
          tabIndex={0}
          aria-label={`${label} start`}
          aria-valuemin={minimum}
          aria-valuemax={end}
          aria-valuenow={start}
          className="magi-memory-range-thumb"
          style={{ left: `${startPercent}%` }}
          onKeyDown={(event) => moveHandleByKey(event, 'start')}
        />
        <div
          role="slider"
          tabIndex={0}
          aria-label={`${label} end`}
          aria-valuemin={start}
          aria-valuemax={maximum}
          aria-valuenow={end}
          className="magi-memory-range-thumb"
          style={{ left: `${endPercent}%` }}
          onKeyDown={(event) => moveHandleByKey(event, 'end')}
        />
      </div>
      <div className="mt-2 flex items-start justify-between gap-4 font-mono text-[10px] leading-4 tabular-nums">
        <div className="min-w-0">
          <div className="text-muted-foreground/70 text-[9px] tracking-[0.12em]">FROM</div>
          <div>{formatRangeTime(start)}</div>
        </div>
        <div className="min-w-0 text-right">
          <div className="text-muted-foreground/70 text-[9px] tracking-[0.12em]">TO</div>
          <div>{formatRangeTime(end)}</div>
        </div>
      </div>
    </div>
  )
}

const statusClass = (status?: string) => {
  if (status === 'indexed' || status === 'active') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  }
  if (status === 'failed' || status === 'invalid' || status === 'expired') {
    return 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300'
  }
  return 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300'
}

function StatCard({
  icon: Icon,
  iconClassName,
  label,
  value,
  detail
}: {
  icon: typeof BookOpenIcon
  iconClassName: string
  label: string
  value: number
  detail: string
}) {
  return (
    <Card className="magi-memory-stat shadow-none">
      <CardContent className="flex items-center gap-3 p-4">
        <div className={cn('magi-memory-stat-icon rounded-lg p-2.5', iconClassName)}>
          <Icon className="size-4" />
        </div>
        <div className="min-w-0">
          <div className="text-xl font-semibold tracking-tight">{value}</div>
          <div className="text-sm">{label}</div>
          <div className="text-muted-foreground truncate text-xs">{detail}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function MemoryFilterSelect({
  value,
  label,
  options,
  onChange
}: {
  value: string
  label: string
  options: Array<[string, string]>
  onChange: (value: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="magi-memory-input h-9 w-full min-w-0 gap-2 rounded-lg px-3">
        <SelectValue aria-label={label} />
      </SelectTrigger>
      <SelectContent className="rounded-xl border-border/70 bg-background/70 shadow-[0_16px_36px_-20px_rgba(0,0,0,0.8)] backdrop-blur-xl">
        {options.map(([optionValue, optionLabel]) => (
          <SelectItem key={optionValue} value={optionValue}>{optionLabel}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function EvidenceList({
  evidence,
  onOpenEpisode
}: {
  evidence: AtomEvidenceView[]
  onOpenEpisode: (episodeId: string) => void
}) {
  if (!evidence.length) {
    return <p className="text-muted-foreground text-sm">No evidence records.</p>
  }
  return (
    <div className="space-y-3">
      {evidence.map((item, index) => (
        <button
          key={`${item.episode_id}-${index}`}
          type="button"
          className="magi-memory-inset magi-memory-linked-item block w-full p-3 text-left text-sm"
          onClick={() => onOpenEpisode(item.episode_id)}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <code className="text-muted-foreground truncate text-xs">{item.episode_id}</code>
            <span className="text-muted-foreground flex shrink-0 items-center gap-1 text-xs">
              {formatTime(item.reference_at)} <ArrowRightIcon className="size-3" />
            </span>
          </div>
          <p className="whitespace-pre-wrap">{item.quote || shorten(item.episode_content || '', 260)}</p>
        </button>
      ))}
    </div>
  )
}

export default function MemoryWorkspace() {
  const { i18n } = useTranslation()
  const zh = i18n.language.startsWith('zh')
  const [view, setView] = useState<MemoryView>('episodes')
  const [overview, setOverview] = useState<MemoryOverview | null>(null)
  const [episodes, setEpisodes] = useState<MemoryEpisode[]>([])
  const [atoms, setAtoms] = useState<MemoryAtom[]>([])
  const [entities, setEntities] = useState<MemoryEntity[]>([])
  const [relations, setRelations] = useState<MemoryRelation[]>([])
  const [communities, setCommunities] = useState<MemoryCommunity[]>([])
  const [selectedEpisode, setSelectedEpisode] = useState<MemoryEpisode | null>(null)
  const [selectedAtom, setSelectedAtom] = useState<MemoryAtom | null>(null)
  const [selectedEntity, setSelectedEntity] = useState<MemoryEntity | null>(null)
  const [selectedRelation, setSelectedRelation] = useState<MemoryRelation | null>(null)
  const [selectedCommunity, setSelectedCommunity] = useState<MemoryCommunity | null>(null)
  const [episodeStatus, setEpisodeStatus] = useState('all')
  const [episodeKind, setEpisodeKind] = useState('all')
  const [atomOwnerType, setAtomOwnerType] = useState('all')
  const [atomTemporalStatus, setAtomTemporalStatus] = useState('all')
  const [atomEvolutionType, setAtomEvolutionType] = useState('all')
  const [episodeReferenceRange, setEpisodeReferenceRange] = useState<TimeRange | null>(null)
  const [atomValidityRange, setAtomValidityRange] = useState<TimeRange | null>(null)
  const [atomSystemRange, setAtomSystemRange] = useState<TimeRange | null>(null)
  const [appliedTimeRanges, setAppliedTimeRanges] = useState<AppliedTimeRanges>({
    episodeReference: null,
    atomValidity: null,
    atomSystem: null
  })
  const [filterOpen, setFilterOpen] = useState(false)
  const [pendingConflict, setPendingConflict] = useState<PendingConflictResolution | null>(null)
  const [pendingAlias, setPendingAlias] = useState<PendingAliasResolution | null>(null)
  const [resolvingConflict, setResolvingConflict] = useState(false)
  const [resolvingAlias, setResolvingAlias] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [backendNeedsRestart, setBackendNeedsRestart] = useState(false)
  const legacyApiToastShown = useRef(false)

  const copy = useMemo(() => ({
    title: zh ? '记忆中枢' : 'Memory Core',
    subtitle: zh
      ? 'Episode 是输入边界，Atom 是独立记录；图谱仅保存结构、描述与 Atom ID。'
      : 'Episodes define input boundaries. Atoms remain independent records while the graph stores structure, descriptions, and Atom IDs.',
    episodes: zh ? '事件片段' : 'Episodes',
    atoms: zh ? '原子记忆' : 'Atoms',
    entityRegistry: 'Entities',
    relationRegistry: 'Relations',
    communityRegistry: 'Communities',
    ingest: zh ? '写入与队列' : 'Ingest & Queue',
    evidence: zh ? '证据关联' : 'Evidence',
    entities: zh ? '实体' : 'Entities',
    relations: zh ? '关系' : 'Relations',
    search: zh ? '搜索内容、名称或 ID' : 'Search content, name, or ID',
    empty: zh ? '当前工作区为空，可以从“写入与队列”加入第一个 Episode。' : 'This workspace is empty. Add the first Episode from Ingest & Queue.',
    refresh: zh ? '刷新' : 'Refresh',
    allStatuses: zh ? '全部状态' : 'All statuses',
    allKinds: zh ? '全部类型' : 'All kinds',
    allOwners: zh ? '全部归属' : 'All owners',
    entityOwner: zh ? '实体 Atom' : 'Entity Atoms',
    relationOwner: zh ? '关系 Atom' : 'Relation Atoms',
    evolution: zh ? '演化记录' : 'Evolution',
    aliasAmbiguity: zh ? '同名候选' : 'Alias ambiguity',
    resolveConflict: zh ? '解除冲突' : 'Resolve conflict'
  }), [zh])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const summary = await getMemoryOverview()
      setOverview(summary)
      const needsRestart = !summary.time_bounds
      setBackendNeedsRestart(needsRestart)
      if (needsRestart && !legacyApiToastShown.current) {
        legacyApiToastShown.current = true
        toast.error(
          zh
            ? '当前 API 进程尚未加载 Memory Core 新接口，请重启后端。'
            : 'The API process has not loaded the new Memory Core endpoints. Restart the backend.',
          { id: 'memory-core-legacy-api' }
        )
      } else if (!needsRestart) {
        legacyApiToastShown.current = false
      }
      if (view === 'episodes') {
        const result = await getMemoryEpisodes({
          page,
          page_size: 20,
          status: episodeStatus === 'all' ? undefined : episodeStatus,
          kind: episodeKind === 'all' ? undefined : episodeKind,
          q: query || undefined,
          reference_from: appliedTimeRanges.episodeReference
            ? rangeParam(appliedTimeRanges.episodeReference[0])
            : undefined,
          reference_to: appliedTimeRanges.episodeReference
            ? rangeParam(appliedTimeRanges.episodeReference[1])
            : undefined
        })
        setEpisodes(result.items)
        setPages(result.pages)
        setTotal(result.total)
      } else if (view === 'atoms') {
        const result = await getMemoryAtoms({
          page,
          page_size: 20,
          owner_type: atomOwnerType === 'all' ? undefined : atomOwnerType as 'entity' | 'relation',
          temporal_status: atomTemporalStatus === 'all'
            ? undefined
            : atomTemporalStatus as 'pending' | 'active' | 'invalid' | 'expired',
          evolution_type: atomEvolutionType === 'all'
            ? undefined
            : atomEvolutionType as 'any' | 'REFINEMENT' | 'TEMPORAL_SUCCESSOR' | 'CONTRADICTION',
          q: query || undefined,
          valid_time_from: appliedTimeRanges.atomValidity
            ? rangeParam(appliedTimeRanges.atomValidity[0])
            : undefined,
          valid_time_to: appliedTimeRanges.atomValidity
            ? rangeParam(appliedTimeRanges.atomValidity[1])
            : undefined,
          system_time_from: appliedTimeRanges.atomSystem
            ? rangeParam(appliedTimeRanges.atomSystem[0])
            : undefined,
          system_time_to: appliedTimeRanges.atomSystem
            ? rangeParam(appliedTimeRanges.atomSystem[1])
            : undefined
        })
        setAtoms(result.items)
        setPages(result.pages)
        setTotal(result.total)
      } else if (view === 'entities') {
        const result = await getMemoryEntities({ page, page_size: 20, q: query || undefined })
        setEntities(result.items)
        setPages(result.pages)
        setTotal(result.total)
      } else if (view === 'relations') {
        const result = await getMemoryRelations({ page, page_size: 20, q: query || undefined })
        setRelations(result.items)
        setPages(result.pages)
        setTotal(result.total)
      } else if (view === 'communities') {
        const result = await getMemoryCommunities({ page, page_size: 20, q: query || undefined })
        setCommunities(result.items)
        setPages(result.pages)
        setTotal(result.total)
      }
    } catch (reason) {
      const status = typeof reason === 'object' && reason !== null
        ? (reason as { response?: { status?: number } }).response?.status
        : undefined
      if (status === 404 && view === 'entities') {
        setBackendNeedsRestart(true)
        toast.error(
          zh
            ? '当前后端仍在运行旧版本：缺少 /memory/entities。请重启 API 服务后再刷新。'
            : 'The API is still running the previous backend version. Restart it to enable /memory/entities.',
          { id: 'memory-core-legacy-api' }
        )
      } else {
        toast.error(memoryErrorMessage(reason), { id: 'memory-core-load-error' })
      }
    } finally {
      setLoading(false)
    }
  }, [
    appliedTimeRanges,
    atomEvolutionType,
    atomOwnerType,
    atomTemporalStatus,
    episodeKind,
    episodeStatus,
    page,
    query,
    view,
    zh
  ])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const switchView = (next: MemoryView) => {
    setView(next)
    setPage(1)
    setQuery('')
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
  }

  const openEpisode = async (episode: MemoryEpisode) => {
    setSelectedAtom(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedEpisode(await getMemoryEpisode(episode.episode_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openAtom = async (atom: MemoryAtom) => {
    setSelectedEpisode(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedAtom(await getMemoryAtom(atom.atom_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openEntity = async (entity: MemoryEntity) => {
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedEntity(await getMemoryEntity(entity.entity_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openEntityById = async (entityId: string) => {
    setView('entities')
    setPage(1)
    setQuery('')
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedEntity(await getMemoryEntity(entityId))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openRelation = async (relation: MemoryRelation) => {
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedEntity(null)
    setSelectedCommunity(null)
    try {
      setSelectedRelation(await getMemoryRelation(relation.relation_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openCommunity = async (community: MemoryCommunity) => {
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    try {
      setSelectedCommunity(await getMemoryCommunity(community.community_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openAtomById = async (atomId: string) => {
    setView('atoms')
    setPage(1)
    setQuery('')
    setSelectedEpisode(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedAtom(await getMemoryAtom(atomId))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openEpisodeById = async (episodeId: string) => {
    setView('episodes')
    setPage(1)
    setQuery('')
    setSelectedAtom(null)
    setSelectedEntity(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedEpisode(await getMemoryEpisode(episodeId))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const openOwnerEntity = async (atom: MemoryAtom) => {
    if (atom.owner_type !== 'entity') return
    setView('entities')
    setPage(1)
    setQuery('')
    setSelectedEpisode(null)
    setSelectedAtom(null)
    setSelectedRelation(null)
    setSelectedCommunity(null)
    try {
      setSelectedEntity(await getMemoryEntity(atom.owner_id))
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-detail-error' })
    }
  }

  const changeFilter = (setter: (value: string) => void, value: string) => {
    setter(value)
    setPage(1)
  }

  const fullEpisodeRange = boundsRange(overview?.time_bounds?.episode_reference)
  const fullAtomValidityRange = boundsRange(overview?.time_bounds?.atom_validity)
  const fullAtomSystemRange = boundsRange(overview?.time_bounds?.atom_system)

  const applyTimeFilters = () => {
    const normalizedRange = (
      selected: TimeRange | null,
      full: TimeRange | null
    ): TimeRange | null => {
      if (!selected || !full) return null
      return selected[0] === full[0] && selected[1] === full[1] ? null : selected
    }
    setAppliedTimeRanges({
      episodeReference: view === 'episodes'
        ? normalizedRange(episodeReferenceRange || fullEpisodeRange, fullEpisodeRange)
        : appliedTimeRanges.episodeReference,
      atomValidity: view === 'atoms'
        ? normalizedRange(atomValidityRange || fullAtomValidityRange, fullAtomValidityRange)
        : appliedTimeRanges.atomValidity,
      atomSystem: view === 'atoms'
        ? normalizedRange(atomSystemRange || fullAtomSystemRange, fullAtomSystemRange)
        : appliedTimeRanges.atomSystem
    })
    setPage(1)
    setFilterOpen(false)
  }

  const resetCurrentFilters = () => {
    if (view === 'episodes') {
      setEpisodeKind('all')
      setEpisodeStatus('all')
      setEpisodeReferenceRange(null)
      setAppliedTimeRanges((current) => ({ ...current, episodeReference: null }))
    } else if (view === 'atoms') {
      setAtomOwnerType('all')
      setAtomTemporalStatus('all')
      setAtomEvolutionType('all')
      setAtomValidityRange(null)
      setAtomSystemRange(null)
      setAppliedTimeRanges((current) => ({
        ...current,
        atomValidity: null,
        atomSystem: null
      }))
    }
    setPage(1)
  }

  const activeFilterCount = view === 'episodes'
    ? Number(episodeKind !== 'all') + Number(episodeStatus !== 'all') + Number(Boolean(appliedTimeRanges.episodeReference))
    : view === 'atoms'
      ? Number(atomOwnerType !== 'all') + Number(atomTemporalStatus !== 'all') + Number(atomEvolutionType !== 'all') + Number(Boolean(appliedTimeRanges.atomValidity)) + Number(Boolean(appliedTimeRanges.atomSystem))
      : 0

  const resolveConflict = async () => {
    if (!pendingConflict) return
    setResolvingConflict(true)
    try {
      await resolveMemoryAtomConflict({
        source_atom_id: pendingConflict.evolution.source_atom_id,
        target_atom_id: pendingConflict.evolution.target_atom_id,
        winner_atom_id: pendingConflict.winnerAtomId
      })
      const currentAtomId = selectedAtom?.atom_id
      if (currentAtomId) {
        setSelectedAtom(await getMemoryAtom(currentAtomId))
      }
      setPendingConflict(null)
      await load()
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-conflict-error' })
    } finally {
      setResolvingConflict(false)
    }
  }

  const resolveAlias = async (alias: string, winnerEntityId: string) => {
    setResolvingAlias(`${alias}:${winnerEntityId}`)
    try {
      const resolved = await resolveMemoryEntityAlias({
        alias,
        winner_entity_id: winnerEntityId
      })
      setSelectedEntity(resolved)
      setPendingAlias(null)
      const result = await getMemoryEntities({ page, page_size: 20, q: query || undefined })
      setEntities(result.items)
      setPages(result.pages)
      setTotal(result.total)
      toast.success(zh ? '别名归属已更新' : 'Alias assignment updated')
    } catch (reason) {
      toast.error(memoryErrorMessage(reason), { id: 'memory-core-alias-error' })
    } finally {
      setResolvingAlias(null)
    }
  }

  return (
    <div className="magi-memory-workspace min-h-full">
      <div className="mx-auto w-full max-w-[1600px] space-y-4 p-4 lg:p-6 xl:p-8">
        <>
          <section className="magi-memory-overview magi-memory-intro flex flex-col justify-between gap-6 p-6 md:flex-row md:items-end lg:p-8">
            <div>
              <h1 className="text-[28px] font-medium tracking-tight">{copy.title}</h1>
              <p className="text-muted-foreground mt-1 max-w-3xl text-sm leading-6">{copy.subtitle}</p>
            </div>
            <div className="magi-memory-meta min-w-64 px-4 py-3 text-sm">
              <div className="text-muted-foreground mb-1 text-xs font-medium">WORKSPACE</div>
              <div className="font-medium">{overview?.workspace_id || '—'}</div>
              <div className="text-muted-foreground mt-0.5 text-xs">SQLite · magi-memory.db</div>
            </div>
          </section>

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard icon={BookOpenIcon} iconClassName="text-sky-300" label={copy.episodes} value={overview?.episodes || 0} detail={`${overview?.episode_statuses.indexed || 0} indexed`} />
            <StatCard icon={AtomIcon} iconClassName="text-violet-300" label={copy.atoms} value={overview?.atoms || 0} detail={`${overview?.atom_statuses.active || 0} active`} />
            <StatCard icon={HexagonIcon} iconClassName="text-cyan-300" label={copy.entities} value={overview?.entities || 0} detail="Neo4j projection owners" />
            <StatCard icon={GitBranchIcon} iconClassName="text-teal-300" label={copy.relations} value={overview?.relations || 0} detail="semantic graph edges" />
            <StatCard icon={OrbitIcon} iconClassName="text-indigo-300" label={copy.communityRegistry} value={overview?.communities || 0} detail="published Balthasar groups" />
          </section>
        </>

        <div className="magi-memory-control magi-memory-toolbar flex min-h-[52px] flex-col gap-3 p-2 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap gap-2">
            {([
              ['episodes', copy.episodes, BookOpenIcon],
              ['atoms', copy.atoms, AtomIcon],
              ['entities', copy.entityRegistry, HexagonIcon],
              ['relations', copy.relationRegistry, GitBranchIcon],
              ['communities', copy.communityRegistry, OrbitIcon],
              ['ingest', copy.ingest, ActivityIcon]
            ] as const).map(([key, label, Icon]) => (
              <Button
                key={key}
                variant="ghost"
                size="sm"
                className={cn('magi-memory-tab', view === key && 'is-active')}
                onClick={() => switchView(key)}
              >
                <Icon className="size-4" /> {label}
              </Button>
            ))}
          </div>
          {view !== 'ingest' ? (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {(view === 'episodes' || view === 'atoms') && (
                <Popover open={filterOpen} onOpenChange={setFilterOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      size="icon"
                      className={cn('magi-memory-icon-button relative size-9', activeFilterCount && 'border-violet-400/30 text-violet-300')}
                      aria-label={zh ? '筛选' : 'Filters'}
                    >
                      <FilterIcon className="size-4" />
                      {activeFilterCount > 0 && (
                        <span className="absolute -top-1.5 -right-1.5 flex size-4 items-center justify-center rounded-full bg-violet-500 text-[9px] font-semibold text-white shadow-sm">
                          {activeFilterCount}
                        </span>
                      )}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    align="end"
                    sideOffset={8}
                    collisionPadding={12}
                    className="magi-memory-filter-popover w-[min(420px,calc(100vw-2rem))] rounded-xl border-border/70 bg-background/72 p-4 shadow-[0_22px_58px_-24px_rgba(0,0,0,0.82)] backdrop-blur-2xl"
                  >
                    <div className="mb-4 flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold">{zh ? '过滤记忆记录' : 'Filter memory records'}</div>
                        <div className="text-muted-foreground mt-0.5 text-xs">
                          {view === 'episodes'
                            ? (zh ? '按输入类型、状态与参考时间筛选。' : 'Filter by input kind, status, and reference time.')
                            : (zh ? '按归属、状态、演化与双时间筛选。' : 'Filter by owner, state, evolution, and bitemporal ranges.')}
                        </div>
                      </div>
                      {activeFilterCount > 0 && (
                        <Button variant="ghost" size="sm" className="h-7" onClick={resetCurrentFilters}>
                          {zh ? '重置' : 'Reset'}
                        </Button>
                      )}
                    </div>
                    {view === 'episodes' ? (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-2">
                          <MemoryFilterSelect
                            value={episodeKind}
                            label={copy.allKinds}
                            options={[
                              ['all', copy.allKinds],
                              ['document', zh ? '文档' : 'Document'],
                              ['conversation', zh ? '对话' : 'Conversation'],
                              ['multimodal_text', zh ? '多模态文本' : 'Multimodal text']
                            ]}
                            onChange={(value) => changeFilter(setEpisodeKind, value)}
                          />
                          <MemoryFilterSelect
                            value={episodeStatus}
                            label={copy.allStatuses}
                            options={[
                              ['all', copy.allStatuses],
                              ['indexed', zh ? '已索引' : 'Indexed'],
                              ['pending', zh ? '等待中' : 'Pending'],
                              ['failed', zh ? '失败' : 'Failed']
                            ]}
                            onChange={(value) => changeFilter(setEpisodeStatus, value)}
                          />
                        </div>
                        <TimeRangeSlider
                          label={zh ? '参考时间范围' : 'Reference-time range'}
                          description={zh ? '命中 Episode.reference_at' : 'Matches Episode.reference_at'}
                          bounds={overview?.time_bounds?.episode_reference}
                          value={episodeReferenceRange}
                          onChange={setEpisodeReferenceRange}
                          emptyLabel={zh ? '暂无参考时间数据' : 'No reference-time data'}
                        />
                      </div>
                    ) : (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-2">
                          <MemoryFilterSelect
                            value={atomOwnerType}
                            label={copy.allOwners}
                            options={[
                              ['all', copy.allOwners],
                              ['entity', copy.entityOwner],
                              ['relation', copy.relationOwner]
                            ]}
                            onChange={(value) => changeFilter(setAtomOwnerType, value)}
                          />
                          <MemoryFilterSelect
                            value={atomTemporalStatus}
                            label={copy.allStatuses}
                            options={[
                              ['all', copy.allStatuses],
                              ['active', zh ? '有效' : 'Active'],
                              ['pending', zh ? '待生效' : 'Pending'],
                              ['invalid', zh ? '已失效' : 'Invalid'],
                              ['expired', zh ? '已退出投影' : 'Expired']
                            ]}
                            onChange={(value) => changeFilter(setAtomTemporalStatus, value)}
                          />
                        </div>
                        <MemoryFilterSelect
                          value={atomEvolutionType}
                          label={zh ? '全部演化关系' : 'All evolution relations'}
                          options={[
                            ['all', zh ? '全部演化关系' : 'All evolution relations'],
                            ['any', zh ? '存在演化记录' : 'Has evolution'],
                            ['REFINEMENT', 'Refinement'],
                            ['TEMPORAL_SUCCESSOR', 'Temporal successor'],
                            ['CONTRADICTION', zh ? '冲突' : 'Contradiction']
                          ]}
                          onChange={(value) => changeFilter(setAtomEvolutionType, value)}
                        />
                        <TimeRangeSlider
                          label={zh ? '有效时间范围' : 'Valid-time range'}
                          description={zh ? '命中 valid_at 或 invalid_at' : 'Matches valid_at or invalid_at'}
                          bounds={overview?.time_bounds?.atom_validity}
                          value={atomValidityRange}
                          onChange={setAtomValidityRange}
                          emptyLabel={zh ? '暂无有效时间数据' : 'No valid-time data'}
                        />
                        <TimeRangeSlider
                          label={zh ? '系统时间范围' : 'System-time range'}
                          description={zh ? '命中 created_at 或 expired_at' : 'Matches created_at or expired_at'}
                          bounds={overview?.time_bounds?.atom_system}
                          value={atomSystemRange}
                          onChange={setAtomSystemRange}
                          emptyLabel={zh ? '暂无系统时间数据' : 'No system-time data'}
                        />
                      </div>
                    )}
                    <div className="mt-4 flex justify-end border-t border-white/[0.06] pt-3">
                      <Button size="sm" onClick={applyTimeFilters}>{zh ? '应用过滤' : 'Apply filters'}</Button>
                    </div>
                  </PopoverContent>
                </Popover>
              )}
              <div className="relative">
                <SearchIcon className="text-muted-foreground absolute left-3 top-2.5 size-4" />
                <Input
                  className="magi-memory-input w-60 pl-9"
                  value={query}
                  placeholder={copy.search}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && void load()}
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                className="magi-memory-icon-button size-9"
                tooltip={copy.refresh}
                onClick={() => void load()}
              >
                <RefreshCwIcon className={cn('size-4', loading && 'animate-spin')} />
              </Button>
            </div>
          ) : (
            <div className="hidden h-9 w-[316px] shrink-0 md:block" aria-hidden="true" />
          )}
        </div>

        {view === 'ingest' ? (
          <Card className="magi-memory-panel magi-memory-data-panel magi-memory-ingest flex h-[720px] min-w-0 flex-col overflow-hidden shadow-none">
            <DocumentManager />
          </Card>
        ) : (
          <div className={cn('grid gap-4', (selectedEpisode || selectedAtom || selectedEntity || selectedRelation || selectedCommunity) && 'xl:grid-cols-[minmax(0,1fr)_420px]')}>
            <Card className="magi-memory-panel magi-memory-data-panel flex h-[720px] min-w-0 flex-col overflow-hidden shadow-none">
              <div className="flex flex-none items-center justify-between border-b px-5 py-4">
                <div>
                  <h2 className="font-semibold">
                    {view === 'episodes'
                      ? copy.episodes
                      : view === 'atoms'
                        ? copy.atoms
                        : view === 'entities'
                          ? copy.entityRegistry
                          : view === 'relations'
                            ? copy.relationRegistry
                            : copy.communityRegistry}
                  </h2>
                  <p className="text-muted-foreground text-xs">{total} records · SQLite</p>
                </div>
                <div className="flex h-8 min-w-28 items-center justify-end gap-2 text-xs">
                  {pages > 1 && (
                    <>
                      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</Button>
                      <span>{page} / {pages}</span>
                      <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>→</Button>
                    </>
                  )}
                </div>
              </div>
              <div className="magi-memory-list-body min-h-0 flex-1 divide-y overflow-auto">
                {!loading && view === 'episodes' && episodes.map((episode) => (
                  <button key={episode.episode_id} onClick={() => void openEpisode(episode)} className="magi-memory-row grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_150px_120px]">
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <BookOpenIcon className="size-4 shrink-0 text-sky-300" />
                        <code className="text-muted-foreground truncate text-xs">{episode.episode_id}</code>
                        <Badge variant="outline" className={statusClass(episode.status)}>{episode.status}</Badge>
                      </div>
                      <p className="text-sm leading-6">{shorten(episode.content, 180)}</p>
                      <p className="text-muted-foreground mt-1 truncate text-xs">{episode.source_uri || episode.kind}</p>
                    </div>
                    <div className="text-muted-foreground text-xs md:text-right">
                      <div>{formatTime(episode.reference_at)}</div>
                      <div className="mt-1">valid reference</div>
                    </div>
                    <div className="text-xs md:text-right">
                      <div>{episode.atom_count} atoms</div>
                      <div className="text-muted-foreground mt-1">{episode.evidence_count} evidence</div>
                    </div>
                  </button>
                ))}
                {!loading && view === 'atoms' && atoms.map((atom) => (
                  <button key={atom.atom_id} onClick={() => void openAtom(atom)} className="magi-memory-row grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_190px_110px]">
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <AtomIcon className="size-4 shrink-0 text-violet-300" />
                        <Badge variant="outline">{atom.owner_type}</Badge>
                        <Badge variant="outline" className={statusClass(atom.temporal_status)}>{atom.temporal_status || 'active'}</Badge>
                        {Boolean(atom.evolution_count) && (
                          <Badge variant="outline" className="border-violet-400/25 text-violet-300">
                            {atom.evolution_count} evolution
                          </Badge>
                        )}
                        {Boolean(atom.unresolved_conflict_count) && (
                          <Badge variant="outline" className="border-amber-400/30 bg-amber-500/[0.06] text-amber-300">
                            {atom.unresolved_conflict_count} conflict
                          </Badge>
                        )}
                        <code className="text-muted-foreground truncate text-xs">{atom.atom_id}</code>
                      </div>
                      <p className="text-sm leading-6">{shorten(atom.content, 190)}</p>
                      <p className="text-muted-foreground mt-1 truncate text-xs">{atom.owner_name || atom.owner_id}</p>
                    </div>
                    <div className="text-muted-foreground text-xs md:text-right">
                      <div>valid · {formatTime(atom.valid_at)}</div>
                      <div className="mt-1">invalid · {formatTime(atom.invalid_at)}</div>
                    </div>
                    <div className="text-xs md:text-right">
                      <div>{atom.support_count} supports</div>
                      <div className="text-muted-foreground mt-1">
                        {atom.evidence_count || 0} {(atom.evidence_count || 0) === 1 ? 'Episode' : 'Episodes'}
                      </div>
                    </div>
                  </button>
                ))}
                {!loading && view === 'entities' && entities.map((entity) => (
                  <button
                    key={entity.entity_id}
                    onClick={() => void openEntity(entity)}
                    className="magi-memory-row grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_130px_120px]"
                  >
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <HexagonIcon className="size-4 shrink-0 text-cyan-300" />
                        <Badge variant="outline">{entity.entity_type || 'UNKNOWN'}</Badge>
                        {Boolean(entity.ambiguity_count) && (
                          <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-300">
                            {entity.ambiguity_count} {zh ? '处同名' : 'ambiguous'}
                          </Badge>
                        )}
                        <code className="text-muted-foreground truncate text-xs">{entity.entity_id}</code>
                      </div>
                      <p className="text-sm font-medium leading-6">{entity.canonical_name}</p>
                      <p className="text-muted-foreground mt-1 truncate text-xs">
                        {displayAliases(entity).join(' · ') || (zh ? '无额外别名' : 'No additional aliases')}
                      </p>
                    </div>
                    <div className="text-muted-foreground text-xs md:text-right">
                      <div>{displayAliases(entity).length} {zh ? '个别名' : 'aliases'}</div>
                      <div className="mt-1">rev {entity.revision}</div>
                    </div>
                    <div className="text-xs md:text-right">
                      <div>{entity.atom_count} atoms</div>
                      <div className="text-muted-foreground mt-1">{formatTime(entity.created_at)}</div>
                    </div>
                  </button>
                ))}
                {!loading && view === 'relations' && relations.map((relation) => (
                  <button
                    key={relation.relation_id}
                    onClick={() => void openRelation(relation)}
                    className="magi-memory-row grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_140px_120px]"
                  >
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <GitBranchIcon className="size-4 shrink-0 text-teal-300" />
                        <code className="text-muted-foreground truncate text-xs">{relation.relation_id}</code>
                      </div>
                      <p className="text-sm font-medium leading-6">
                        {relation.entity_a_name} <span className="text-muted-foreground">↔</span> {relation.entity_b_name}
                      </p>
                      <p className="text-muted-foreground mt-1 truncate text-xs">
                        {relation.keywords.join(' · ') || (zh ? '无关系关键词' : 'No relation keywords')}
                      </p>
                    </div>
                    <div className="text-muted-foreground text-xs md:text-right">
                      <div>rev {relation.revision}</div>
                      <div className="mt-1">{relation.keywords.length} keywords</div>
                    </div>
                    <div className="text-xs md:text-right">
                      <div>{relation.atom_count} atoms</div>
                      <div className="text-muted-foreground mt-1">{formatTime(relation.created_at)}</div>
                    </div>
                  </button>
                ))}
                {!loading && view === 'communities' && communities.map((community) => (
                  <button
                    key={community.community_id}
                    onClick={() => void openCommunity(community)}
                    className="magi-memory-row grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_140px]"
                  >
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <OrbitIcon className="size-4 shrink-0 text-indigo-300" />
                        <code className="text-muted-foreground truncate text-xs">{community.community_id}</code>
                      </div>
                      <p className="text-sm font-medium leading-6">{community.community_name}</p>
                      <p className="text-muted-foreground mt-1 line-clamp-2 text-xs leading-5">
                        {community.report || (zh ? '尚无社区报告' : 'No community report yet')}
                      </p>
                    </div>
                    <div className="text-xs md:text-right">
                      <div>{community.member_count} members</div>
                      <div className="text-muted-foreground mt-1">{formatTime(community.published_at)}</div>
                    </div>
                  </button>
                ))}
                {!loading && (
                  (view === 'episodes' && !episodes.length) ||
                  (view === 'atoms' && !atoms.length) ||
                  (view === 'entities' && !entities.length) ||
                  (view === 'relations' && !relations.length) ||
                  (view === 'communities' && !communities.length)
                ) && (
                  <div className="flex min-h-full flex-col items-center justify-center px-6 text-center">
                    <InboxIcon className="text-muted-foreground mb-3 size-8" />
                    <p className="max-w-lg text-sm">{copy.empty}</p>
                  </div>
                )}
                {loading && <div className="flex min-h-full items-center justify-center"><RefreshCwIcon className="text-muted-foreground size-6 animate-spin" /></div>}
              </div>
            </Card>

            {selectedEpisode && (
              <Card className="magi-memory-panel sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-none">
                <div className="border-b p-5">
                  <div className="flex items-start justify-between gap-3">
                    <Badge variant="outline" className={statusClass(selectedEpisode.status)}>{selectedEpisode.status}</Badge>
                    <Button
                      variant="ghost"
                      size="icon"
                      tooltip={zh ? '关闭详情' : 'Close details'}
                      onClick={() => setSelectedEpisode(null)}
                      aria-label={zh ? '关闭详情' : 'Close details'}
                    >
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <h3 className="mt-3 font-semibold">Episode detail</h3>
                  <code className="text-muted-foreground break-all text-xs">{selectedEpisode.episode_id}</code>
                </div>
                <CardContent className="max-h-[calc(100vh-15rem)] space-y-5 overflow-auto p-5">
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <BookOpenIcon className="size-4 text-sky-300" /> {zh ? '事件内容' : 'Episode content'}
                    </h4>
                    <div className="magi-memory-inset p-3">
                      <p className="whitespace-pre-wrap text-sm leading-6">{selectedEpisode.content}</p>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HistoryIcon className="size-4 text-cyan-300" /> {zh ? '时间信息' : 'Temporal metadata'}
                    </h4>
                    <div className="magi-memory-inset grid grid-cols-2 gap-3 p-3 text-xs">
                      <div><span className="text-muted-foreground">reference_at</span><br />{formatTime(selectedEpisode.reference_at)}</div>
                      <div><span className="text-muted-foreground">created_at</span><br />{formatTime(selectedEpisode.created_at)}</div>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <AtomIcon className="size-4 text-violet-300" /> Atoms from this Episode
                    </h4>
                    <div className="space-y-2">
                      {(selectedEpisode.atoms || []).map((atom) => (
                        <button
                          key={atom.atom_id}
                          type="button"
                          className="magi-memory-inset magi-memory-linked-item block w-full p-3 text-left text-sm"
                          onClick={() => void openAtomById(atom.atom_id)}
                        >
                          <div className="text-muted-foreground mb-1 flex items-center justify-between gap-2 text-xs">
                            <span className="truncate">{atom.owner_name || atom.owner_id}</span>
                            <ArrowRightIcon className="size-3 shrink-0" />
                          </div>
                          <p>{atom.content}</p>
                          {hasDistinctQuote(atom) && (
                            <p className="text-muted-foreground mt-2 border-l-2 pl-2 text-xs">{atom.quote}</p>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {selectedAtom && (
              <Card className="magi-memory-panel sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-none">
                <div className="border-b p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex gap-2">
                      <Badge variant="outline">{selectedAtom.owner_type}</Badge>
                      <Badge variant="outline" className={statusClass(selectedAtom.temporal_status)}>{selectedAtom.temporal_status || 'active'}</Badge>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      tooltip={zh ? '关闭详情' : 'Close details'}
                      onClick={() => setSelectedAtom(null)}
                      aria-label={zh ? '关闭详情' : 'Close details'}
                    >
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <h3 className="mt-3 font-semibold">Atom detail</h3>
                  <code className="text-muted-foreground break-all text-xs">{selectedAtom.atom_id}</code>
                </div>
                <CardContent className="max-h-[calc(100vh-15rem)] space-y-5 overflow-auto p-5">
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <AtomIcon className="size-4 text-violet-300" /> {zh ? '原子内容' : 'Atom content'}
                    </h4>
                    <div className="magi-memory-inset p-3">
                      <p className="whitespace-pre-wrap text-sm leading-6">{selectedAtom.content}</p>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      {selectedAtom.owner_type === 'entity'
                        ? <HexagonIcon className="size-4 text-cyan-300" />
                        : <GitBranchIcon className="size-4 text-teal-300" />}
                      {zh ? '归属对象' : 'Owner'}
                    </h4>
                    {selectedAtom.owner_type === 'entity' ? (
                      <button
                        type="button"
                        className="magi-memory-inset magi-memory-linked-item block w-full p-3 text-left text-xs"
                        onClick={() => void openOwnerEntity(selectedAtom)}
                      >
                        <div className="flex items-center justify-between gap-2 font-medium">
                          <span>{selectedAtom.owner_name || selectedAtom.owner_id}</span>
                          <ArrowRightIcon className="size-3 shrink-0" />
                        </div>
                        <code className="text-muted-foreground break-all">{selectedAtom.owner_id}</code>
                      </button>
                    ) : (
                      <div className="magi-memory-inset p-3 text-xs">
                        <div className="font-medium">{selectedAtom.owner_name || selectedAtom.owner_id}</div>
                        <code className="text-muted-foreground break-all">{selectedAtom.owner_id}</code>
                      </div>
                    )}
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HistoryIcon className="size-4 text-sky-300" /> {zh ? '双时间信息' : 'Bitemporal metadata'}
                    </h4>
                    <div className="magi-memory-inset grid grid-cols-2 gap-3 p-3 text-xs">
                      <div><span className="text-muted-foreground">valid_at</span><br />{formatTime(selectedAtom.valid_at)}</div>
                      <div><span className="text-muted-foreground">invalid_at</span><br />{formatTime(selectedAtom.invalid_at)}</div>
                      <div><span className="text-muted-foreground">created_at</span><br />{formatTime(selectedAtom.created_at)}</div>
                      <div><span className="text-muted-foreground">expired_at</span><br />{formatTime(selectedAtom.expired_at)}</div>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <FingerprintIcon className="size-4 text-amber-300" />
                      {zh ? '冲突裁决' : 'Conflict resolution'}
                    </h4>
                    {(selectedAtom.evolutions || []).some(
                      (evolution) => evolution.relation_type === 'CONTRADICTION' && !evolution.resolved
                    ) ? (
                        <div className="space-y-2">
                          {(selectedAtom.evolutions || [])
                            .filter((evolution) => evolution.relation_type === 'CONTRADICTION' && !evolution.resolved)
                            .map((evolution) => (
                              <div
                                key={`${evolution.source_atom_id}-${evolution.target_atom_id}-resolution`}
                                className="magi-memory-inset border-amber-400/20 p-3 text-xs"
                              >
                                <div className="mb-2 flex items-center gap-2 text-amber-200">
                                  <AlertTriangleIcon className="size-3.5" />
                                  <span className="font-medium">
                                    {zh ? '两条事实无法同时成立' : 'These facts cannot both remain active'}
                                  </span>
                                </div>
                                <p className="text-muted-foreground mb-2">
                                  {zh
                                    ? '请选择应继续生效的事实；另一条会被标记为 expired，但内容、证据和冲突历史仍会保留。'
                                    : 'Choose the fact that should remain active. The other will be marked expired while its content, evidence, and history remain available.'}
                                </p>
                                <div className="space-y-1.5">
                                  <div className="magi-memory-evolution-link flex items-center gap-2 rounded-md px-2 py-2">
                                    <div className="min-w-0 flex-1">
                                      <p className="leading-5">{shorten(selectedAtom.content, 150)}</p>
                                      <div className="text-muted-foreground mt-1 flex items-center gap-2">
                                        <Badge variant="outline" className={statusClass(selectedAtom.temporal_status)}>
                                          {selectedAtom.temporal_status || 'active'}
                                        </Badge>
                                        <code className="truncate">{selectedAtom.atom_id}</code>
                                      </div>
                                    </div>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      className="h-7 shrink-0 border-amber-400/25 text-[10px]"
                                      onClick={() => setPendingConflict({
                                        evolution,
                                        winnerAtomId: selectedAtom.atom_id
                                      })}
                                    >
                                      {zh ? '选择此项' : 'Select'}
                                    </Button>
                                  </div>
                                  <div className="magi-memory-evolution-link flex items-center gap-2 rounded-md px-2 py-2">
                                    <div className="min-w-0 flex-1">
                                      <p className="leading-5">{shorten(evolution.related_atom.content, 150)}</p>
                                      <div className="text-muted-foreground mt-1 flex items-center gap-2">
                                        <Badge variant="outline" className={statusClass(evolution.related_atom.temporal_status)}>
                                          {evolution.related_atom.temporal_status || 'active'}
                                        </Badge>
                                        <code className="truncate">{evolution.related_atom.atom_id}</code>
                                      </div>
                                    </div>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      className="h-7 shrink-0 border-amber-400/25 text-[10px]"
                                      onClick={() => setPendingConflict({
                                        evolution,
                                        winnerAtomId: evolution.related_atom.atom_id
                                      })}
                                    >
                                      {zh ? '选择此项' : 'Select'}
                                    </Button>
                                  </div>
                                </div>
                              </div>
                            ))}
                        </div>
                      ) : (
                        <div className="magi-memory-inset flex items-center gap-2 p-3 text-xs text-emerald-300">
                          <ShieldCheckIcon className="size-4" />
                          {zh ? '当前没有待裁决的冲突。' : 'No conflicts require a decision.'}
                        </div>
                      )}
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HistoryIcon className="size-4 text-violet-300" /> {copy.evolution}
                    </h4>
                    {(selectedAtom.evolutions || []).length ? (
                      <div className="magi-memory-timeline space-y-3">
                        {(selectedAtom.evolutions || []).map((evolution) => {
                          const isConflict = evolution.relation_type === 'CONTRADICTION'
                          const keptAtomId = evolution.metadata.winner_atom_id
                          return (
                            <div
                              key={`${evolution.source_atom_id}-${evolution.target_atom_id}-${evolution.relation_type}`}
                              className={cn(
                                'magi-memory-inset relative p-3 text-xs',
                                isConflict && !evolution.resolved && 'border-amber-400/25 bg-amber-500/[0.055]'
                              )}
                            >
                              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    'text-[10px]',
                                    isConflict
                                      ? 'border-amber-400/30 text-amber-300'
                                      : 'border-violet-400/25 text-violet-300'
                                  )}
                                >
                                  {evolution.relation_type}
                                </Badge>
                                <span className="text-muted-foreground">
                                  {evolution.resolved
                                    ? (zh ? '已人工解除' : 'Manually resolved')
                                    : formatTime(evolution.created_at)}
                                </span>
                              </div>
                              <button
                                type="button"
                                className="magi-memory-evolution-link block w-full text-left"
                                onClick={() => void openAtomById(evolution.related_atom.atom_id)}
                              >
                                <p className="leading-5">{shorten(evolution.related_atom.content, 180)}</p>
                                <div className="text-muted-foreground mt-1 flex items-center justify-between gap-2">
                                  <span>{evolution.related_atom.temporal_status || 'active'}</span>
                                  <span className="flex items-center gap-1">
                                    {evolution.direction === 'outgoing' ? '→' : '←'} {shorten(evolution.related_atom.atom_id, 18)}
                                  </span>
                                </div>
                              </button>
                              {evolution.resolved && keptAtomId && (
                                <div className="mt-2 flex items-center gap-1.5 border-t border-white/[0.06] pt-2 text-emerald-300">
                                  <ShieldCheckIcon className="size-3.5" />
                                  {zh
                                    ? `冲突已解除 · ${shorten(keptAtomId, 16)} 保持有效，另一项已标记 expired`
                                    : `Resolved · ${shorten(keptAtomId, 16)} remains active; the other Atom is expired`}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <p className="text-muted-foreground text-xs">
                        {backendNeedsRestart
                          ? (zh ? '当前后端详情接口尚未返回 evolution 字段，请重启 API 服务。' : 'The current backend detail endpoint does not return evolution yet. Restart the API service.')
                          : (zh ? '暂无演化或冲突记录。' : 'No evolution or conflict records.')}
                      </p>
                    )}
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <LinkIcon className="size-4 text-cyan-300" /> {copy.evidence}
                    </h4>
                    <EvidenceList
                      evidence={selectedAtom.evidence || []}
                      onOpenEpisode={(episodeId) => void openEpisodeById(episodeId)}
                    />
                  </div>
                </CardContent>
              </Card>
            )}

            {selectedEntity && (
              <Card className="magi-memory-panel sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-none">
                <div className="border-b p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex gap-2">
                      <Badge variant="outline">{selectedEntity.entity_type || 'UNKNOWN'}</Badge>
                      <Badge variant="outline">rev {selectedEntity.revision}</Badge>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      tooltip={zh ? '关闭详情' : 'Close details'}
                      onClick={() => setSelectedEntity(null)}
                      aria-label={zh ? '关闭详情' : 'Close details'}
                    >
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <h3 className="mt-3 font-semibold">{selectedEntity.canonical_name}</h3>
                  <code className="text-muted-foreground break-all text-xs">{selectedEntity.entity_id}</code>
                </div>
                <CardContent className="max-h-[calc(100vh-15rem)] space-y-5 overflow-auto p-5">
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <TagsIcon className="size-4 text-cyan-300" /> {zh ? '规范名称与别名' : 'Canonical name & aliases'}
                    </h4>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline" className="border-cyan-400/25 text-cyan-200">
                        {selectedEntity.canonical_name}
                      </Badge>
                      {displayAliases(selectedEntity).map((alias) => (
                        <Badge
                          key={alias}
                          variant="outline"
                          className="bg-background/20"
                        >
                          {alias}
                        </Badge>
                      ))}
                      {!displayAliases(selectedEntity).length && (
                        <span className="text-muted-foreground text-xs">{zh ? '暂无额外别名' : 'No additional aliases'}</span>
                      )}
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <FingerprintIcon className="size-4 text-amber-300" /> {copy.aliasAmbiguity}
                    </h4>
                    {(selectedEntity.alias_ambiguities || []).length ? (
                      <div className="space-y-2">
                        {(selectedEntity.alias_ambiguities || []).map((ambiguity) => (
                          <div key={ambiguity.normalized_alias} className="magi-memory-inset border-amber-400/20 p-3 text-xs">
                            <div className="mb-2 flex items-center gap-2 text-amber-200">
                              <AlertTriangleIcon className="size-3.5" />
                              <span className="font-medium">{ambiguity.alias}</span>
                            </div>
                            <p className="text-muted-foreground mb-2">
                              {zh
                                ? '规范化后的同一名称指向多个 Entity。选择唯一归属：'
                                : 'The same normalized alias points to multiple entities. Choose its sole target:'}
                            </p>
                            <div className="space-y-1.5">
                              <div className="magi-memory-evolution-link flex items-center justify-between gap-2 rounded-md px-2 py-1.5">
                                <span className="min-w-0 truncate font-medium">{selectedEntity.canonical_name}</span>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-7 shrink-0 text-[10px]"
                                  disabled={Boolean(resolvingAlias)}
                                  onClick={() => setPendingAlias({
                                    ambiguity,
                                    winner: {
                                      entity_id: selectedEntity.entity_id,
                                      canonical_name: selectedEntity.canonical_name
                                    },
                                    candidates: [
                                      {
                                        entity_id: selectedEntity.entity_id,
                                        canonical_name: selectedEntity.canonical_name
                                      },
                                      ...ambiguity.candidates
                                    ]
                                  })}
                                >
                                  {zh ? '选择此项' : 'Select'}
                                </Button>
                              </div>
                              {ambiguity.candidates.map((candidate) => (
                                <div
                                  key={candidate.entity_id}
                                  className="magi-memory-evolution-link flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left"
                                >
                                  <button
                                    type="button"
                                    className="min-w-0 flex-1 truncate text-left"
                                    onClick={() => void openEntityById(candidate.entity_id)}
                                  >
                                    {candidate.canonical_name}
                                  </button>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 shrink-0 text-[10px]"
                                    disabled={Boolean(resolvingAlias)}
                                    onClick={() => setPendingAlias({
                                      ambiguity,
                                      winner: candidate,
                                      candidates: [
                                        {
                                          entity_id: selectedEntity.entity_id,
                                          canonical_name: selectedEntity.canonical_name
                                        },
                                        ...ambiguity.candidates
                                      ]
                                    })}
                                  >
                                    {zh ? '选择此项' : 'Select'}
                                  </Button>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="magi-memory-inset flex items-center gap-2 p-3 text-xs text-emerald-300">
                        <ShieldCheckIcon className="size-4" />
                        {zh ? '当前别名均能唯一解析。' : 'All aliases currently resolve uniquely.'}
                      </div>
                    )}
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <LinkIcon className="size-4 text-violet-300" />
                      {zh ? `归属 Atom（${selectedEntity.atom_count}）` : `Owned Atoms (${selectedEntity.atom_count})`}
                    </h4>
                    <div className="space-y-2">
                      {(selectedEntity.atoms || []).map((atom) => (
                        <button
                          key={atom.atom_id}
                          type="button"
                          className="magi-memory-inset magi-memory-linked-item block w-full p-3 text-left text-xs"
                          onClick={() => void openAtomById(atom.atom_id)}
                        >
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <Badge variant="outline" className={statusClass(atom.temporal_status)}>
                              {atom.temporal_status || 'active'}
                            </Badge>
                            <ArrowRightIcon className="text-muted-foreground size-3" />
                          </div>
                          <p className="mt-2 leading-5">{shorten(atom.content, 180)}</p>
                        </button>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {selectedRelation && (
              <Card className="magi-memory-panel sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-none">
                <div className="border-b p-5">
                  <div className="flex items-start justify-between gap-3">
                    <Badge variant="outline"><GitBranchIcon className="mr-1 size-3" /> Relation</Badge>
                    <Button variant="ghost" size="icon" onClick={() => setSelectedRelation(null)} aria-label={zh ? '关闭详情' : 'Close details'}>
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <h3 className="mt-3 font-semibold">{selectedRelation.entity_a_name} ↔ {selectedRelation.entity_b_name}</h3>
                  <code className="text-muted-foreground break-all text-xs">{selectedRelation.relation_id}</code>
                </div>
                <CardContent className="max-h-[calc(100vh-15rem)] space-y-5 overflow-auto p-5">
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HexagonIcon className="size-4 text-cyan-300" /> {zh ? '端点实体' : 'Endpoint entities'}
                    </h4>
                    <div className="space-y-2">
                      {(selectedRelation.endpoints || []).map((endpoint) => (
                        <button
                          key={endpoint.entity_id}
                          type="button"
                          className="magi-memory-inset magi-memory-linked-item flex w-full items-center justify-between gap-3 p-3 text-left text-xs"
                          onClick={() => void openEntityById(endpoint.entity_id)}
                        >
                          <span>
                            <span className="block font-medium">{endpoint.canonical_name}</span>
                            <code className="text-muted-foreground">{endpoint.entity_id}</code>
                          </span>
                          <ArrowRightIcon className="size-3" />
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <TagsIcon className="size-4 text-violet-300" /> Keywords
                    </h4>
                    <div className="flex flex-wrap gap-2">
                      {selectedRelation.keywords.map((keyword) => <Badge key={keyword} variant="outline">{keyword}</Badge>)}
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <AtomIcon className="size-4 text-violet-300" /> {zh ? `归属 Atom（${selectedRelation.atom_count}）` : `Owned Atoms (${selectedRelation.atom_count})`}
                    </h4>
                    <div className="space-y-2">
                      {(selectedRelation.atoms || []).map((atom) => (
                        <button
                          key={atom.atom_id}
                          type="button"
                          className="magi-memory-inset magi-memory-linked-item block w-full p-3 text-left text-xs"
                          onClick={() => void openAtomById(atom.atom_id)}
                        >
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <Badge variant="outline" className={statusClass(atom.temporal_status)}>
                              {atom.temporal_status || 'active'}
                            </Badge>
                            <ArrowRightIcon className="text-muted-foreground size-3" />
                          </div>
                          <p className="mt-2 leading-5">{shorten(atom.content, 180)}</p>
                        </button>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {selectedCommunity && (
              <Card className="magi-memory-panel sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-none">
                <div className="border-b p-5">
                  <div className="flex items-start justify-between gap-3">
                    <Badge variant="outline"><OrbitIcon className="mr-1 size-3" /> Community</Badge>
                    <Button variant="ghost" size="icon" onClick={() => setSelectedCommunity(null)} aria-label={zh ? '关闭详情' : 'Close details'}>
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <h3 className="mt-3 font-semibold">{selectedCommunity.community_name}</h3>
                  <code className="text-muted-foreground break-all text-xs">{selectedCommunity.community_id}</code>
                </div>
                <CardContent className="max-h-[calc(100vh-15rem)] space-y-5 overflow-auto p-5">
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <OrbitIcon className="size-4 text-indigo-300" /> Community report
                    </h4>
                    <div className="magi-memory-inset p-3">
                      <p className="text-muted-foreground whitespace-pre-wrap text-xs leading-5">
                        {selectedCommunity.report || (zh ? '尚无社区报告。' : 'No community report yet.')}
                      </p>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HexagonIcon className="size-4 text-cyan-300" /> {zh ? `成员实体（${selectedCommunity.member_count}）` : `Member entities (${selectedCommunity.member_count})`}
                    </h4>
                    <div className="space-y-2">
                      {(selectedCommunity.members || []).map((member) => (
                        <button
                          key={member.membership_key}
                          type="button"
                          disabled={!member.entity_id}
                          className="magi-memory-inset magi-memory-linked-item flex w-full items-center justify-between gap-3 p-3 text-left text-xs disabled:cursor-default disabled:opacity-65"
                          onClick={() => member.entity_id && void openEntityById(member.entity_id)}
                        >
                          <span>
                            <span className="block font-medium">{member.canonical_name || member.membership_key}</span>
                            <code className="text-muted-foreground">{member.entity_id || member.membership_key}</code>
                          </span>
                          {member.entity_id && <ArrowRightIcon className="size-3" />}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                      <HistoryIcon className="size-4 text-sky-300" /> {zh ? '快照信息' : 'Snapshot metadata'}
                    </h4>
                    <div className="magi-memory-inset p-3 text-[11px]">
                      <span className="text-muted-foreground">{zh ? '发布于' : 'Published'} · </span>{formatTime(selectedCommunity.published_at)}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        )}
      </div>

      <AlertDialog
        open={Boolean(pendingAlias)}
        onOpenChange={(open) => !open && !resolvingAlias && setPendingAlias(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{zh ? '确认别名归属' : 'Confirm alias assignment'}</AlertDialogTitle>
            <AlertDialogDescription>
              {zh
                ? '确认后，该别名只会指向所选 Entity，并从其他候选 Entity 中移除。Entity 本身、其 Atom 和历史记录都不会被删除。'
                : 'The alias will point only to the selected Entity and be removed from the other candidates. No Entity, Atom, or history will be deleted.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="magi-memory-inset border-cyan-400/20 p-3 text-sm">
            <div className="mb-1 flex items-center gap-1.5 text-xs text-cyan-300">
              <FingerprintIcon className="size-3.5" />
              {zh ? `别名“${pendingAlias?.ambiguity.alias || '—'}”将归属于` : `Alias “${pendingAlias?.ambiguity.alias || '—'}” will belong to`}
            </div>
            <p className="font-medium leading-5">{pendingAlias?.winner.canonical_name || '—'}</p>
            <code className="text-muted-foreground break-all text-xs">{pendingAlias?.winner.entity_id || '—'}</code>
          </div>
          <div className="magi-memory-inset border-amber-400/20 p-3 text-sm">
            <div className="mb-2 flex items-center gap-1.5 text-xs text-amber-300">
              <AlertTriangleIcon className="size-3.5" />
              {zh ? '以下 Entity 将移除该别名映射' : 'Alias mapping will be removed from'}
            </div>
            <div className="space-y-1.5">
              {(pendingAlias?.candidates || [])
                .filter((candidate) => candidate.entity_id !== pendingAlias?.winner.entity_id)
                .map((candidate) => (
                  <div key={candidate.entity_id} className="rounded-md border border-white/[0.06] px-2 py-1.5">
                    <div className="font-medium">{candidate.canonical_name}</div>
                    <code className="text-muted-foreground break-all text-[10px]">{candidate.entity_id}</code>
                  </div>
                ))}
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(resolvingAlias)}>{zh ? '取消' : 'Cancel'}</AlertDialogCancel>
            <AlertDialogAction
              disabled={Boolean(resolvingAlias) || !pendingAlias}
              onClick={() => pendingAlias && void resolveAlias(pendingAlias.ambiguity.alias, pendingAlias.winner.entity_id)}
            >
              {resolvingAlias ? (zh ? '处理中…' : 'Resolving…') : (zh ? '确认归属' : 'Confirm assignment')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={Boolean(pendingConflict)}
        onOpenChange={(open) => !open && !resolvingConflict && setPendingConflict(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{zh ? '确认冲突裁决' : 'Confirm conflict resolution'}</AlertDialogTitle>
            <AlertDialogDescription>
              {zh
                ? '未被保留的 Atom 将退出当前图谱投影，但其内容、证据与冲突历史都会完整保留。此操作用于人工裁决，不会删除记录。'
                : 'The Atom not retained will leave the current graph projection, while its content, evidence, and conflict history remain intact.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <div className="magi-memory-inset border-emerald-400/20 p-3 text-sm">
              <div className="mb-1 flex items-center gap-1.5 text-xs text-emerald-300">
                <ShieldCheckIcon className="size-3.5" />
                {zh ? '继续作为有效事实' : 'Remain active'}
              </div>
              <p className="mb-1 leading-5">
                {pendingConflict && selectedAtom
                  ? shorten(
                    pendingConflict.winnerAtomId === selectedAtom.atom_id
                      ? selectedAtom.content
                      : pendingConflict.evolution.related_atom.content,
                    180
                  )
                  : '—'}
              </p>
              <code className="text-muted-foreground break-all text-xs">{pendingConflict?.winnerAtomId}</code>
            </div>
            <div className="magi-memory-inset border-amber-400/20 p-3 text-sm">
              <div className="mb-1 flex items-center gap-1.5 text-xs text-amber-300">
                <AlertTriangleIcon className="size-3.5" />
                {zh ? '将标记为 expired' : 'Will be marked expired'}
              </div>
              <p className="mb-1 leading-5">
                {pendingConflict && selectedAtom
                  ? shorten(
                    pendingConflict.winnerAtomId === selectedAtom.atom_id
                      ? pendingConflict.evolution.related_atom.content
                      : selectedAtom.content,
                    180
                  )
                  : '—'}
              </p>
              <code className="text-muted-foreground break-all text-xs">
                {pendingConflict && selectedAtom
                  ? (pendingConflict.winnerAtomId === selectedAtom.atom_id
                    ? pendingConflict.evolution.related_atom.atom_id
                    : selectedAtom.atom_id)
                  : '—'}
              </code>
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={resolvingConflict}>{zh ? '取消' : 'Cancel'}</AlertDialogCancel>
            <AlertDialogAction disabled={resolvingConflict} onClick={() => void resolveConflict()}>
              {resolvingConflict ? (zh ? '处理中…' : 'Resolving…') : (zh ? '确认裁决' : 'Confirm decision')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
