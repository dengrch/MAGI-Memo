import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeftIcon,
  BookOpenIcon,
  CheckIcon,
  ChevronRightIcon,
  LoaderCircleIcon,
  LogOutIcon,
  PlusIcon,
  Share2Icon,
  Trash2Icon
} from 'lucide-react'
import { toast } from 'sonner'

import {
  activateRuntimeWorkspace,
  createRuntimeWorkspace,
  deleteRuntimeWorkspace,
  getRuntimeLogs,
  getRuntimeWorkspaces,
  RuntimeLogEntry,
  RuntimeWorkspace
} from '@/api/lightrag'
import AppSettings from '@/components/AppSettings'
import GithubIcon from '@/components/icons/GithubIcon'
import MagiCoreMark from '@/components/icons/MagiCoreMark'
import Button from '@/components/ui/Button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/Dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover'
import { TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip'
import { SiteInfo, webuiPrefix } from '@/lib/constants'
import { errorMessage } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { navigationService } from '@/services/navigation'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/state'

interface NavigationTabProps {
  value: string
  currentTab: string
  icon: React.ComponentType<{ className?: string }>
  children: React.ReactNode
}

function NavigationTab({ value, currentTab, icon: Icon, children }: NavigationTabProps) {
  const active = currentTab === value
  return (
    <TabsTrigger
      value={value}
      className={cn(
        'magi-nav-item h-9 w-full cursor-pointer justify-start gap-2.5 rounded-md px-2.5 text-sm font-medium transition-colors max-md:w-auto max-md:justify-center',
        active
          ? 'is-active bg-accent text-foreground'
          : 'text-muted-foreground hover:bg-accent hover:text-foreground'
      )}
    >
      <Icon className="size-4" />
      <span className="whitespace-nowrap">{children}</span>
    </TabsTrigger>
  )
}

function SolidDisclosureArrow({ expanded }: { expanded: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'ml-auto size-0 border-y-[4px] border-y-transparent border-l-[6px] border-l-current transition-transform duration-150',
        expanded && 'rotate-90'
      )}
    />
  )
}

function TabsNavigation() {
  const storedTab = useSettingsStore.use.currentTab()
  const currentTab = storedTab === 'api' ? 'documents' : storedTab
  const { t } = useTranslation()

  return (
    <div className="magi-tabs-navigation mt-2 flex w-full flex-col max-md:mt-0 max-md:w-auto max-md:min-w-0 max-md:flex-1 max-md:overflow-x-auto">
      <TabsList className="flex h-auto w-full flex-col items-stretch justify-start gap-0.5 rounded-none bg-transparent p-0 max-md:w-max max-md:flex-row max-md:items-center">
        <NavigationTab value="documents" currentTab={currentTab} icon={BookOpenIcon}>
          {t('header.documents')}
        </NavigationTab>
        <NavigationTab value="knowledge-graph" currentTab={currentTab} icon={Share2Icon}>
          {t('header.knowledgeGraph')}
        </NavigationTab>
      </TabsList>
    </div>
  )
}

const sages = [
  { name: 'Balthasar', initial: 'B', tab: 'balthasar' },
  { name: 'Melchior', initial: 'M', tab: 'melchior' },
  { name: 'Casper', initial: 'C', tab: 'casper' }
] as const

function TheSagesNavigation() {
  const [expanded, setExpanded] = useState(true)
  const currentTab = useSettingsStore.use.currentTab()

  return (
    <section className="mt-4 max-md:hidden">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="text-muted-foreground hover:text-foreground mx-0.5 flex h-7 w-[calc(100%-0.25rem)] items-center rounded px-2 text-[11px] font-medium transition-colors"
      >
        <span>The Sages</span>
        <SolidDisclosureArrow expanded={expanded} />
      </button>
      <div
        className={cn(
          'grid transition-[grid-template-rows,opacity,transform] duration-200 ease-out',
          expanded
            ? 'grid-rows-[1fr] translate-y-0 opacity-100'
            : 'grid-rows-[0fr] -translate-y-1 opacity-0'
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="space-y-0.5 pt-0.5">
            {sages.map((sage) => (
              'tab' in sage ? (
                <TabsList key={sage.name} className="block h-auto w-full bg-transparent p-0">
                  <TabsTrigger
                    value={sage.tab}
                    className={cn(
                      'flex h-8 w-full cursor-pointer items-center justify-start gap-2.5 rounded-md px-2.5 text-sm transition-colors',
                      currentTab === sage.tab
                        ? 'bg-accent text-foreground'
                        : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                    )}
                  >
                    <span className="border-input bg-background/50 flex size-5 items-center justify-center rounded border text-[9px] font-semibold">
                      {sage.initial}
                    </span>
                    <span>{sage.name}</span>
                  </TabsTrigger>
                </TabsList>
              ) : null
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

function logLevelColor(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'bg-destructive'
  if (level === 'WARNING') return 'bg-amber-500'
  return 'bg-emerald-500'
}

function LogsNavigation() {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(true)
  const [entries, setEntries] = useState<RuntimeLogEntry[]>([])
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)
  const [selectedEntry, setSelectedEntry] = useState<RuntimeLogEntry | null>(null)

  useEffect(() => {
    let mounted = true
    const refresh = () => {
      getRuntimeLogs(5)
        .then((result) => {
          if (mounted) {
            setEntries(result.items)
            setWorkspaceId(result.workspace_id)
          }
        })
        .catch(() => undefined)
    }
    refresh()
    const interval = window.setInterval(refresh, 15_000)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [])

  return (
    <section className="mt-2 max-md:hidden">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="text-muted-foreground hover:text-foreground mx-0.5 flex h-7 w-[calc(100%-0.25rem)] items-center rounded px-2 text-[11px] font-medium transition-colors"
      >
        <span>Logs</span>
        <SolidDisclosureArrow expanded={expanded} />
      </button>
      <div
        className={cn(
          'grid transition-[grid-template-rows,opacity,transform] duration-200 ease-out',
          expanded
            ? 'grid-rows-[1fr] translate-y-0 opacity-100'
            : 'grid-rows-[0fr] -translate-y-1 opacity-0'
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="space-y-0.5 pt-0.5">
            {entries.length === 0 ? (
              <div className="text-muted-foreground px-2.5 py-2 text-xs">No recent logs</div>
            ) : (
              entries.map((entry, index) => (
                <button
                  type="button"
                  key={`${entry.timestamp}-${entry.logger}-${index}`}
                  onClick={() => setSelectedEntry(entry)}
                  className="hover:bg-accent block w-full rounded-md px-2.5 py-1.5 text-left transition-colors"
                  aria-label={`${t('header.openLogDetails')}: ${entry.message.split('\n', 1)[0]}`}
                >
                  <div className="text-muted-foreground flex items-center gap-1.5 font-mono text-[9px] leading-3">
                    <span className={cn('size-1.5 rounded-full', logLevelColor(entry.level))} />
                    <span>{entry.timestamp.slice(11, 19)}</span>
                    <span className="truncate">{entry.level.toLocaleLowerCase()}</span>
                  </div>
                  <p className="text-muted-foreground mt-0.5 truncate text-[11px] leading-4">
                    {entry.message.split('\n', 1)[0]}
                  </p>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
      <Dialog
        open={selectedEntry !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedEntry(null)
        }}
      >
        <DialogContent className="max-h-[82vh] overflow-hidden sm:max-w-2xl">
          <DialogHeader className="pr-7">
            <DialogTitle>{t('header.logDetails')}</DialogTitle>
            <DialogDescription>{t('header.logDetailsDescription')}</DialogDescription>
          </DialogHeader>
          {selectedEntry && (
            <div className="min-h-0 space-y-4 overflow-y-auto">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-md border p-3 text-xs">
                <dt className="text-muted-foreground">{t('header.logTimestamp')}</dt>
                <dd className="font-mono">{selectedEntry.timestamp}</dd>
                <dt className="text-muted-foreground">{t('header.logLevel')}</dt>
                <dd className="flex items-center gap-2 font-mono">
                  <span
                    className={cn('size-2 rounded-full', logLevelColor(selectedEntry.level))}
                  />
                  {selectedEntry.level}
                </dd>
                <dt className="text-muted-foreground">{t('header.logLogger')}</dt>
                <dd className="break-all font-mono">{selectedEntry.logger}</dd>
                <dt className="text-muted-foreground">{t('header.logWorkspace')}</dt>
                <dd className="break-all font-mono">{workspaceId || '—'}</dd>
              </dl>
              <div>
                <div className="text-muted-foreground mb-2 text-xs font-medium">
                  {t('header.logMessage')}
                </div>
                <pre className="bg-muted/40 max-h-[48vh] overflow-auto whitespace-pre-wrap break-words rounded-md border p-4 font-mono text-xs leading-5">
                  {selectedEntry.message}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}

function workspaceInitial(workspace: RuntimeWorkspace | undefined): string {
  const title = workspace?.name?.trim() || workspace?.id || 'M'
  return Array.from(title)[0]?.toLocaleUpperCase() || 'M'
}

interface WorkspaceSwitcherProps {
  workspaces: RuntimeWorkspace[]
  activeWorkspaceId: string
  switching: boolean
  onSwitch: (workspaceId: string) => Promise<void>
  onCreated: (workspace: RuntimeWorkspace) => Promise<void>
  onDeleteRequest: (workspace: RuntimeWorkspace) => void
}

function WorkspaceSwitcher({
  workspaces,
  activeWorkspaceId,
  switching,
  onSwitch,
  onCreated,
  onDeleteRequest
}: WorkspaceSwitcherProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [workspaceName, setWorkspaceName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const activeWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.id === activeWorkspaceId),
    [activeWorkspaceId, workspaces]
  )

  const resetCreate = () => {
    setCreating(false)
    setWorkspaceName('')
  }

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen)
    if (!nextOpen) resetCreate()
  }

  const submitWorkspace = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedName = workspaceName.trim()
    if (!normalizedName) return
    setSubmitting(true)
    try {
      const workspace = await createRuntimeWorkspace({
        name: normalizedName
      })
      await onCreated(workspace)
      setOpen(false)
      resetCreate()
    } catch (reason) {
      toast.error(t('header.workspaceCreateFailed', { error: errorMessage(reason) }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={switching}
          className="hover:bg-accent group flex h-10 w-full min-w-0 items-center gap-2 rounded-md px-1.5 text-left transition-colors disabled:opacity-60"
          aria-label={t('header.workspace', 'Workspace')}
        >
          <span className="bg-primary/12 text-primary flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold ring-1 ring-inset ring-primary/15">
            {workspaceInitial(activeWorkspace)}
          </span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {activeWorkspace?.name || activeWorkspace?.id || t('header.workspace', 'Workspace')}
          </span>
          {switching ? (
            <LoaderCircleIcon className="text-muted-foreground size-3.5 animate-spin" />
          ) : (
            <ChevronRightIcon className="text-muted-foreground size-3.5 transition-transform group-data-[state=open]:rotate-90" />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        collisionPadding={12}
        avoidCollisions
        className="w-[288px] overflow-hidden rounded-lg p-0 shadow-xl"
      >
        {creating ? (
          <form onSubmit={(event) => void submitWorkspace(event)}>
            <div className="flex h-11 items-center gap-2 border-b px-3">
              <button
                type="button"
                onClick={resetCreate}
                className="text-muted-foreground hover:bg-accent hover:text-foreground -ml-1 flex size-7 items-center justify-center rounded"
                aria-label={t('common.cancel')}
              >
                <ArrowLeftIcon className="size-4" />
              </button>
              <span className="text-sm font-medium">{t('header.createWorkspace')}</span>
            </div>
            <div className="space-y-3 p-3">
              <label className="block space-y-1.5">
                <span className="text-muted-foreground text-xs">{t('header.workspaceName')}</span>
                <input
                  autoFocus
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  placeholder={t('header.workspaceNamePlaceholder')}
                  maxLength={200}
                  className="border-input bg-background focus:border-primary/60 h-8 w-full rounded-md border px-2.5 text-sm outline-none"
                />
              </label>
            </div>
            <div className="flex justify-end border-t px-3 py-2.5">
              <Button
                type="submit"
                size="sm"
                disabled={submitting || !workspaceName.trim()}
              >
                {submitting ? t('header.creatingWorkspace') : t('header.createWorkspace')}
              </Button>
            </div>
          </form>
        ) : (
          <>
            <div className="text-muted-foreground px-3 pb-1 pt-2.5 text-[11px] font-medium">
              {t('header.workspace', 'Workspace')}
            </div>
            <div className="max-h-64 overflow-y-auto px-1.5 pb-1.5">
              {workspaces.map((workspace) => {
                const active = workspace.id === activeWorkspaceId
                return (
                  <div
                    key={workspace.id}
                    className={cn(
                      'hover:bg-accent flex h-10 w-full items-center gap-2 rounded-md px-2 text-left transition-colors',
                      active && 'bg-accent/70'
                    )}
                  >
                    <button
                      type="button"
                      disabled={switching}
                      onClick={() => {
                        if (!active) void onSwitch(workspace.id)
                      }}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:opacity-60"
                    >
                      <span className="bg-primary/12 text-primary flex size-6 shrink-0 items-center justify-center rounded text-[11px] font-semibold">
                        {workspaceInitial(workspace)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">
                          {workspace.name || workspace.id}
                        </span>
                        {workspace.name && workspace.name !== workspace.id && (
                          <span className="text-muted-foreground block truncate font-mono text-[10px]">
                            {workspace.id}
                          </span>
                        )}
                      </span>
                      {active && <CheckIcon className="text-primary size-3.5" />}
                    </button>
                    {workspace.deletable && (
                      <button
                        type="button"
                        disabled={switching}
                        onClick={() => {
                          setOpen(false)
                          onDeleteRequest(workspace)
                        }}
                        className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive flex size-7 shrink-0 items-center justify-center rounded"
                        aria-label={t('header.deleteWorkspace', { name: workspace.name })}
                      >
                        <Trash2Icon className="size-3.5" />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
            <div className="border-t p-1.5">
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="text-muted-foreground hover:bg-accent hover:text-foreground flex h-9 w-full items-center gap-2 rounded-md px-2 text-sm transition-colors"
              >
                <span className="border-input flex size-6 items-center justify-center rounded border">
                  <PlusIcon className="size-3.5" />
                </span>
                {t('header.addWorkspace')}
              </button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}

export default function SiteHeader() {
  const { t } = useTranslation()
  const { isGuestMode, coreVersion, apiVersion, username, webuiTitle, webuiDescription } = useAuthStore()
  const [workspaces, setWorkspaces] = useState<RuntimeWorkspace[]>([])
  const [activeWorkspace, setActiveWorkspace] = useState('')
  const [switchingWorkspace, setSwitchingWorkspace] = useState(false)
  const [workspaceToDelete, setWorkspaceToDelete] = useState<RuntimeWorkspace | null>(null)
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [deletingWorkspace, setDeletingWorkspace] = useState(false)

  useEffect(() => {
    getRuntimeWorkspaces()
      .then((result) => {
        setWorkspaces(result.items)
        setActiveWorkspace(result.active_workspace_id)
      })
      .catch(() => undefined)
  }, [])

  const handleWorkspaceChange = async (workspaceId: string) => {
    if (!workspaceId || workspaceId === activeWorkspace) return
    setSwitchingWorkspace(true)
    try {
      await activateRuntimeWorkspace(workspaceId)
      setActiveWorkspace(workspaceId)
      window.location.reload()
    } catch (reason) {
      toast.error(t('header.workspaceSwitchFailed', { error: errorMessage(reason) }))
    } finally {
      setSwitchingWorkspace(false)
    }
  }

  const handleWorkspaceCreated = async (workspace: RuntimeWorkspace) => {
    setWorkspaces((current) => [...current, workspace])
    await handleWorkspaceChange(workspace.id)
  }

  const handleWorkspaceDelete = async () => {
    if (!workspaceToDelete || deleteConfirmation !== workspaceToDelete.name) return
    setDeletingWorkspace(true)
    try {
      await deleteRuntimeWorkspace(workspaceToDelete.id)
      window.location.reload()
    } catch (reason) {
      toast.error(t('header.workspaceDeleteFailed', { error: errorMessage(reason) }))
      setDeletingWorkspace(false)
    }
  }

  const versionDisplay = coreVersion && apiVersion ? `${coreVersion}/${apiVersion}` : null
  const hasWarning = apiVersion?.endsWith('⚠️')
  const versionTooltip = hasWarning
    ? t('header.frontendNeedsRebuild')
    : versionDisplay
      ? `v${versionDisplay}`
      : ''

  return (
    <aside className="magi-system-header relative z-50 flex h-full w-[240px] shrink-0 flex-col border-r p-3 max-md:h-14 max-md:w-full max-md:flex-row max-md:items-center max-md:border-r-0 max-md:border-b max-md:px-3 max-md:py-1.5">
      <div className="flex min-h-11 items-center px-2 max-md:min-h-0 max-md:px-0">
        <a href={webuiPrefix} className="flex items-center gap-2.5">
          <span className="text-foreground flex size-8 items-center justify-center">
            <MagiCoreMark variant="solid" className="size-7" />
          </span>
          <span className="text-sm font-medium tracking-[-0.01em] max-sm:hidden">{SiteInfo.name}</span>
        </a>
        {webuiTitle && webuiTitle !== SiteInfo.name && (
          <div className="flex min-w-0 items-center max-lg:hidden">
            <span className="text-border mx-2 text-xs">/</span>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="text-muted-foreground max-w-24 cursor-default truncate text-xs font-medium">
                    {webuiTitle}
                  </span>
                </TooltipTrigger>
                {webuiDescription && <TooltipContent side="bottom">{webuiDescription}</TooltipContent>}
              </Tooltip>
            </TooltipProvider>
          </div>
        )}
      </div>

      <div className="magi-sidebar-nav flex min-w-0 flex-1 flex-col overflow-y-auto max-md:flex-row max-md:items-center max-md:overflow-visible">
        <div className="mt-2 max-md:hidden">
          <WorkspaceSwitcher
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspace}
            switching={switchingWorkspace}
            onSwitch={handleWorkspaceChange}
            onCreated={handleWorkspaceCreated}
            onDeleteRequest={(workspace) => {
              setWorkspaceToDelete(workspace)
              setDeleteConfirmation('')
            }}
          />
        </div>
        <TabsNavigation />
        <TheSagesNavigation />
        <LogsNavigation />
      </div>

      <nav className="magi-sidebar-footer flex flex-col border-t pt-3 max-md:flex-row max-md:border-0 max-md:p-0">
        <div className="flex items-center gap-1 max-md:justify-end">
          {versionDisplay && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="text-muted-foreground mr-auto cursor-default px-2 font-mono text-[10px] max-md:hidden">
                    v{versionDisplay}
                  </span>
                </TooltipTrigger>
                <TooltipContent side="bottom">{versionTooltip}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          <Button asChild variant="ghost" size="icon" side="bottom" tooltip={t('header.projectRepository')} className="max-md:hidden">
            <a href={SiteInfo.github} target="_blank" rel="noopener noreferrer" aria-label={t('header.projectRepository')}>
              <GithubIcon className="size-4" />
            </a>
          </Button>
          <AppSettings side="top" showRuntimeModels />
          {!isGuestMode && (
            <Button
              variant="ghost"
              size="icon"
              side="bottom"
              tooltip={`${t('header.logout')} (${username})`}
              onClick={() => navigationService.navigateToLogin()}
            >
              <LogOutIcon className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </nav>
      <Dialog
        open={workspaceToDelete !== null}
        onOpenChange={(open) => {
          if (!open && !deletingWorkspace) {
            setWorkspaceToDelete(null)
            setDeleteConfirmation('')
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('header.deleteWorkspaceTitle')}</DialogTitle>
            <DialogDescription>
              {t('header.deleteWorkspaceDescription', { name: workspaceToDelete?.name })}
            </DialogDescription>
          </DialogHeader>
          <label className="space-y-1.5">
            <span className="text-muted-foreground text-xs">
              {t('header.deleteWorkspaceConfirm', { name: workspaceToDelete?.name })}
            </span>
            <input
              autoFocus
              value={deleteConfirmation}
              disabled={deletingWorkspace}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
              className="border-input bg-background focus:border-destructive/60 h-9 w-full rounded-md border px-2.5 text-sm outline-none"
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              disabled={deletingWorkspace}
              onClick={() => setWorkspaceToDelete(null)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={
                deletingWorkspace ||
                !workspaceToDelete ||
                deleteConfirmation !== workspaceToDelete.name
              }
              onClick={() => void handleWorkspaceDelete()}
            >
              {deletingWorkspace ? t('header.deletingWorkspace') : t('header.deleteWorkspaceAction')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
