import { useCallback, useState } from 'react'
import { CpuIcon, SettingsIcon, SlidersHorizontalIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import RuntimeModelSettings from '@/components/RuntimeModelSettings'
import Button from '@/components/ui/Button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from '@/components/ui/Dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { cn } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settings'

type SettingsSection = 'general' | 'models'

interface AppSettingsProps {
  className?: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  showRuntimeModels?: boolean
}

const sections = [
  { id: 'general', label: 'General', icon: SlidersHorizontalIcon },
  { id: 'models', label: 'Models', icon: CpuIcon }
] as const

export default function AppSettings({ className }: AppSettingsProps) {
  const [open, setOpen] = useState(false)
  const [section, setSection] = useState<SettingsSection>('general')
  const { t } = useTranslation()
  const language = useSettingsStore.use.language()
  const setLanguage = useSettingsStore.use.setLanguage()
  const theme = useSettingsStore.use.theme()
  const setTheme = useSettingsStore.use.setTheme()

  const handleLanguageChange = useCallback((value: string) => {
    setLanguage(value as 'en' | 'zh' | 'fr' | 'ar' | 'zh_TW' | 'ru' | 'ja' | 'de' | 'uk' | 'ko' | 'vi')
  }, [setLanguage])

  const handleThemeChange = useCallback((value: string) => {
    setTheme(value as 'light' | 'dark' | 'system')
  }, [setTheme])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={cn('h-9 w-9', className)}
          aria-label="MAGI settings"
          title="MAGI settings"
        >
          <SettingsIcon className="text-muted-foreground size-5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="border-b px-6 py-5">
          <DialogTitle className="flex items-center gap-2"><SettingsIcon className="text-muted-foreground size-5" /> MAGI settings</DialogTitle>
          <DialogDescription>General preferences and runtime model routing.</DialogDescription>
        </DialogHeader>
        <div className="grid min-h-0 sm:grid-cols-[180px_1fr]">
          <nav className="border-b p-3 sm:border-r sm:border-b-0">
            <div className="flex gap-1 sm:flex-col">
              {sections.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSection(id)}
                  className={cn(
                    'flex h-9 flex-1 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors sm:flex-none',
                    section === id ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" /> {label}
                </button>
              ))}
            </div>
          </nav>
          <div className="min-h-0 overflow-y-auto p-6">
            {section === 'general' && (
              <div className="space-y-6">
                <div>
                  <h3 className="font-semibold">General</h3>
                  <p className="text-muted-foreground mt-1 text-xs">Language and appearance apply to the WebUI immediately.</p>
                </div>
                <div className="grid gap-5 sm:grid-cols-2">
                  <label className="space-y-2 text-sm">
                    <span className="font-medium">{t('settings.language')}</span>
                    <Select value={language} onValueChange={handleLanguageChange}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="en">English</SelectItem><SelectItem value="zh">中文</SelectItem>
                        <SelectItem value="fr">Français</SelectItem><SelectItem value="ar">العربية</SelectItem>
                        <SelectItem value="zh_TW">繁體中文</SelectItem><SelectItem value="ru">Русский</SelectItem>
                        <SelectItem value="ja">日本語</SelectItem><SelectItem value="de">Deutsch</SelectItem>
                        <SelectItem value="uk">Українська</SelectItem><SelectItem value="ko">한국어</SelectItem>
                        <SelectItem value="vi">Tiếng Việt</SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                  <label className="space-y-2 text-sm">
                    <span className="font-medium">{t('settings.theme')}</span>
                    <Select value={theme} onValueChange={handleThemeChange}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="light">{t('settings.light')}</SelectItem>
                        <SelectItem value="dark">{t('settings.dark')}</SelectItem>
                        <SelectItem value="system">{t('settings.system')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                </div>
              </div>
            )}
            {section === 'models' && <RuntimeModelSettings />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
