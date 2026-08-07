import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AtomEvidenceView,
  getMemoryAtom,
  getMemoryAtoms,
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryOverview,
  MemoryAtom,
  MemoryEpisode,
  MemoryOverview
} from '@/api/lightrag'
import DocumentManager from '@/features/DocumentManager'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Badge from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import { cn, errorMessage } from '@/lib/utils'
import {
  ActivityIcon,
  AtomIcon,
  BookOpenIcon,
  GitBranchIcon,
  HexagonIcon,
  InboxIcon,
  RefreshCwIcon,
  SearchIcon,
  XIcon
} from 'lucide-react'

type MemoryView = 'episodes' | 'atoms' | 'ingest'

const formatTime = (value?: string | null) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const shorten = (value: string, length = 120) =>
  value.length > length ? `${value.slice(0, length)}…` : value

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
  label,
  value,
  detail
}: {
  icon: typeof BookOpenIcon
  label: string
  value: number
  detail: string
}) {
  return (
    <Card className="shadow-none">
      <CardContent className="flex items-center gap-3 p-4">
        <div className="bg-muted text-muted-foreground rounded-md p-2.5">
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

function EvidenceList({ evidence }: { evidence: AtomEvidenceView[] }) {
  if (!evidence.length) {
    return <p className="text-muted-foreground text-sm">No evidence records.</p>
  }
  return (
    <div className="space-y-3">
      {evidence.map((item, index) => (
        <div key={`${item.episode_id}-${index}`} className="rounded-lg border p-3 text-sm">
          <div className="mb-2 flex items-center justify-between gap-2">
            <code className="text-muted-foreground truncate text-xs">{item.episode_id}</code>
            <span className="text-muted-foreground shrink-0 text-xs">{formatTime(item.reference_at)}</span>
          </div>
          <p className="whitespace-pre-wrap">{item.quote || shorten(item.episode_content || '', 260)}</p>
        </div>
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
  const [selectedEpisode, setSelectedEpisode] = useState<MemoryEpisode | null>(null)
  const [selectedAtom, setSelectedAtom] = useState<MemoryAtom | null>(null)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const copy = useMemo(() => ({
    title: zh ? '记忆中枢' : 'Memory Core',
    subtitle: zh
      ? 'Episode 是输入边界，Atom 是独立记录；图谱仅保存结构、描述与 Atom ID。'
      : 'Episodes define input boundaries. Atoms remain independent records while the graph stores structure, descriptions, and Atom IDs.',
    episodes: zh ? '事件片段' : 'Episodes',
    atoms: zh ? '原子记忆' : 'Atoms',
    ingest: zh ? '写入与队列' : 'Ingest & Queue',
    evidence: zh ? '证据关联' : 'Evidence',
    entities: zh ? '实体' : 'Entities',
    relations: zh ? '关系' : 'Relations',
    search: zh ? '搜索内容或 ID' : 'Search content or ID',
    empty: zh ? '当前工作区为空，可以从“写入与队列”加入第一个 Episode。' : 'This workspace is empty. Add the first Episode from Ingest & Queue.',
    refresh: zh ? '刷新' : 'Refresh'
  }), [zh])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const summary = await getMemoryOverview()
      setOverview(summary)
      if (view === 'episodes') {
        const result = await getMemoryEpisodes({ page, page_size: 20, q: query || undefined })
        setEpisodes(result.items)
        setPages(result.pages)
        setTotal(result.total)
      } else if (view === 'atoms') {
        const result = await getMemoryAtoms({ page, page_size: 20, q: query || undefined })
        setAtoms(result.items)
        setPages(result.pages)
        setTotal(result.total)
      }
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [page, query, view])

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
  }

  const openEpisode = async (episode: MemoryEpisode) => {
    setSelectedAtom(null)
    try {
      setSelectedEpisode(await getMemoryEpisode(episode.episode_id))
    } catch (reason) {
      setError(errorMessage(reason))
    }
  }

  const openAtom = async (atom: MemoryAtom) => {
    setSelectedEpisode(null)
    try {
      setSelectedAtom(await getMemoryAtom(atom.atom_id))
    } catch (reason) {
      setError(errorMessage(reason))
    }
  }

  return (
    <div className="min-h-full bg-transparent">
      <div className="mx-auto w-full max-w-[1600px] space-y-4 p-4 lg:p-6">
        <>
          <section className="magi-memory-intro flex flex-col justify-between gap-4 rounded-lg border p-5 md:flex-row md:items-end">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{copy.title}</h1>
              <p className="text-muted-foreground mt-1 max-w-3xl text-sm leading-6">{copy.subtitle}</p>
            </div>
            <div className="bg-muted/50 min-w-64 rounded-lg border px-4 py-3 text-sm">
              <div className="text-muted-foreground mb-1 text-xs font-medium">WORKSPACE</div>
              <div className="font-medium">{overview?.workspace_id || '—'}</div>
              <div className="text-muted-foreground mt-0.5 text-xs">SQLite · magi-memory.db</div>
            </div>
          </section>

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard icon={BookOpenIcon} label={copy.episodes} value={overview?.episodes || 0} detail={`${overview?.episode_statuses.indexed || 0} indexed`} />
            <StatCard icon={AtomIcon} label={copy.atoms} value={overview?.atoms || 0} detail={`${overview?.atom_statuses.active || 0} active`} />
            <StatCard icon={InboxIcon} label={copy.evidence} value={overview?.evidence || 0} detail="Episode ↔ Atom" />
            <StatCard icon={HexagonIcon} label={copy.entities} value={overview?.entities || 0} detail="Neo4j projection owners" />
            <StatCard icon={GitBranchIcon} label={copy.relations} value={overview?.relations || 0} detail="semantic graph edges" />
          </section>
        </>

        <div className="flex flex-col gap-3 rounded-lg border bg-card p-2 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap gap-2">
            {([
              ['episodes', copy.episodes, BookOpenIcon],
              ['atoms', copy.atoms, AtomIcon],
              ['ingest', copy.ingest, ActivityIcon]
            ] as const).map(([key, label, Icon]) => (
              <Button key={key} variant={view === key ? 'default' : 'ghost'} size="sm" onClick={() => switchView(key)}>
                <Icon className="size-4" /> {label}
              </Button>
            ))}
          </div>
          {view !== 'ingest' && (
            <div className="flex gap-2">
              <div className="relative">
                <SearchIcon className="text-muted-foreground absolute left-3 top-2.5 size-4" />
                <Input
                  className="w-64 pl-9"
                  value={query}
                  placeholder={copy.search}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && void load()}
                />
              </div>
              <Button variant="outline" size="icon" tooltip={copy.refresh} onClick={() => void load()}>
                <RefreshCwIcon className={cn('size-4', loading && 'animate-spin')} />
              </Button>
            </div>
          )}
        </div>

        {error && <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div>}

        {view === 'ingest' ? (
          <div className="h-[720px] min-h-[680px] overflow-hidden rounded-lg border bg-card">
            <DocumentManager />
          </div>
        ) : (
          <div className={cn('grid gap-4', (selectedEpisode || selectedAtom) && 'xl:grid-cols-[minmax(0,1fr)_420px]')}>
            <Card className="overflow-hidden shadow-sm">
              <div className="flex items-center justify-between border-b px-5 py-4">
                <div>
                  <h2 className="font-semibold">{view === 'episodes' ? copy.episodes : copy.atoms}</h2>
                  <p className="text-muted-foreground text-xs">{total} records · SQLite</p>
                </div>
                {pages > 1 && (
                  <div className="flex items-center gap-2 text-xs">
                    <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</Button>
                    <span>{page} / {pages}</span>
                    <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>→</Button>
                  </div>
                )}
              </div>
              <div className="divide-y">
                {!loading && view === 'episodes' && episodes.map((episode) => (
                  <button key={episode.episode_id} onClick={() => void openEpisode(episode)} className="hover:bg-muted/50 grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_150px_120px]">
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
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
                  <button key={atom.atom_id} onClick={() => void openAtom(atom)} className="hover:bg-muted/50 grid w-full gap-3 px-5 py-4 text-left transition-colors md:grid-cols-[minmax(0,1fr)_190px_110px]">
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <Badge variant="outline">{atom.owner_type}</Badge>
                        <Badge variant="outline" className={statusClass(atom.temporal_status)}>{atom.temporal_status || 'active'}</Badge>
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
                      <div className="text-muted-foreground mt-1">{atom.evidence_count || 0} evidence</div>
                    </div>
                  </button>
                ))}
                {!loading && ((view === 'episodes' && !episodes.length) || (view === 'atoms' && !atoms.length)) && (
                  <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
                    <InboxIcon className="text-muted-foreground mb-3 size-8" />
                    <p className="max-w-lg text-sm">{copy.empty}</p>
                  </div>
                )}
                {loading && <div className="flex min-h-64 items-center justify-center"><RefreshCwIcon className="text-muted-foreground size-6 animate-spin" /></div>}
              </div>
            </Card>

            {selectedEpisode && (
              <Card className="sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-sm">
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
                  <p className="whitespace-pre-wrap text-sm leading-6">{selectedEpisode.content}</p>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div><span className="text-muted-foreground">reference_at</span><br />{formatTime(selectedEpisode.reference_at)}</div>
                    <div><span className="text-muted-foreground">created_at</span><br />{formatTime(selectedEpisode.created_at)}</div>
                  </div>
                  <div>
                    <h4 className="mb-3 text-sm font-semibold">Atoms from this Episode</h4>
                    <div className="space-y-2">
                      {(selectedEpisode.atoms || []).map((atom) => (
                        <div key={atom.atom_id} className="rounded-lg border p-3 text-sm">
                          <div className="text-muted-foreground mb-1 text-xs">{atom.owner_name || atom.owner_id}</div>
                          <p>{atom.content}</p>
                          {atom.quote && <p className="text-muted-foreground mt-2 border-l-2 pl-2 text-xs">{atom.quote}</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {selectedAtom && (
              <Card className="sticky top-4 h-fit max-h-[calc(100vh-6rem)] self-start overflow-hidden shadow-sm">
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
                  <p className="whitespace-pre-wrap text-sm leading-6">{selectedAtom.content}</p>
                  <div className="rounded-lg bg-muted/60 p-3 text-xs">
                    <div className="font-medium">{selectedAtom.owner_name || selectedAtom.owner_id}</div>
                    <code className="text-muted-foreground break-all">{selectedAtom.owner_id}</code>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div><span className="text-muted-foreground">valid_at</span><br />{formatTime(selectedAtom.valid_at)}</div>
                    <div><span className="text-muted-foreground">invalid_at</span><br />{formatTime(selectedAtom.invalid_at)}</div>
                    <div><span className="text-muted-foreground">created_at</span><br />{formatTime(selectedAtom.created_at)}</div>
                    <div><span className="text-muted-foreground">expired_at</span><br />{formatTime(selectedAtom.expired_at)}</div>
                  </div>
                  <div>
                    <h4 className="mb-3 text-sm font-semibold">{copy.evidence}</h4>
                    <EvidenceList evidence={selectedAtom.evidence || []} />
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
