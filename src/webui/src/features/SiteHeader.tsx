import Button from '@/components/ui/Button'
import { SiteInfo, webuiPrefix } from '@/lib/constants'
import AppSettings from '@/components/AppSettings'
import { TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/state'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { navigationService } from '@/services/navigation'
import {
  BookOpenIcon,
  LogOutIcon,
  MessagesSquareIcon,
  Share2Icon
} from 'lucide-react'
import GithubIcon from '@/components/icons/GithubIcon'
import MagiCoreMark from '@/components/icons/MagiCoreMark'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip'

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
        'h-9 cursor-pointer gap-2 rounded-md px-3 text-sm font-medium transition-colors',
        active
          ? 'bg-background text-foreground shadow-sm ring-1 ring-border'
          : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'
      )}
    >
      <Icon className="size-4" />
      <span className="whitespace-nowrap">{children}</span>
    </TabsTrigger>
  )
}

function TabsNavigation() {
  const storedTab = useSettingsStore.use.currentTab()
  const currentTab = storedTab === 'api' ? 'documents' : storedTab
  const { t } = useTranslation()

  return (
    <div className="flex self-center">
      <TabsList className="h-10 gap-1 rounded-lg border bg-secondary/80 p-0.5">
        <NavigationTab value="documents" currentTab={currentTab} icon={BookOpenIcon}>
          {t('header.documents')}
        </NavigationTab>
        <NavigationTab value="knowledge-graph" currentTab={currentTab} icon={Share2Icon}>
          {t('header.knowledgeGraph')}
        </NavigationTab>
        <NavigationTab value="retrieval" currentTab={currentTab} icon={MessagesSquareIcon}>
          {t('header.retrieval')}
        </NavigationTab>
      </TabsList>
    </div>
  )
}

export default function SiteHeader() {
  const { t } = useTranslation()
  const { isGuestMode, coreVersion, apiVersion, username, webuiTitle, webuiDescription } = useAuthStore()

  const versionDisplay = (coreVersion && apiVersion)
    ? `${coreVersion}/${apiVersion}`
    : null;

  // Check if frontend needs rebuild (apiVersion ends with warning symbol)
  const hasWarning = apiVersion?.endsWith('⚠️');
  const versionTooltip = hasWarning
    ? t('header.frontendNeedsRebuild')
    : versionDisplay ? `v${versionDisplay}` : '';

  const handleLogout = () => {
    navigationService.navigateToLogin();
  }

  return (
    <header className="magi-system-header sticky top-0 z-50 flex h-14 w-full border-b px-4 backdrop-blur">
      <div className="w-auto min-w-[200px] flex items-center">
        <a href={webuiPrefix} className="flex items-center gap-2">
          <MagiCoreMark className="size-7 text-foreground" />
          <span className="text-base font-semibold md:inline-block">{SiteInfo.name}</span>
        </a>
        {webuiTitle && webuiTitle !== SiteInfo.name && (
          <div className="flex items-center">
            <span className="mx-1 text-xs text-gray-500 dark:text-gray-400">|</span>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="font-medium text-sm cursor-default">
                    {webuiTitle}
                  </span>
                </TooltipTrigger>
                {webuiDescription && (
                  <TooltipContent side="bottom">
                    {webuiDescription}
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          </div>
        )}
      </div>

      <div className="flex h-14 flex-1 items-center justify-center">
        <TabsNavigation />
        {isGuestMode && (
          <div className="ml-2 self-center px-2 py-1 text-xs bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200 rounded-md">
            {t('login.guestMode', 'Guest Mode')}
          </div>
        )}
      </div>

      <nav className="w-[200px] flex items-center justify-end">
        <div className="flex items-center gap-2">
          {versionDisplay && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="text-xs text-gray-500 dark:text-gray-400 mr-1 cursor-default">
                    v{versionDisplay}
                  </span>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  {versionTooltip}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          <Button variant="ghost" size="icon" side="bottom" tooltip={t('header.projectRepository')}>
            <a href={SiteInfo.github} target="_blank" rel="noopener noreferrer">
              <GithubIcon className="size-4" />
            </a>
          </Button>
          <AppSettings />
          {!isGuestMode && (
            <Button
              variant="ghost"
              size="icon"
              side="bottom"
              tooltip={`${t('header.logout')} (${username})`}
              onClick={handleLogout}
            >
              <LogOutIcon className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </nav>
    </header>
  )
}
