import { useCallback, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import Button from '@/components/ui/Button'
import { useGraphStore } from '@/stores/graph'
import { useSettingsStore } from '@/stores/settings'

/** Refreshes the current graph without adding another graph-filter control. */
const GraphLabels = () => {
  const { t } = useTranslation()
  const label = useSettingsStore.use.queryLabel()
  const [isRefreshing, setIsRefreshing] = useState(false)

  const getRefreshTooltip = useCallback(() => {
    if (isRefreshing) return t('graphPanel.graphLabels.refreshingTooltip')
    if (!label || label === '*') return t('graphPanel.graphLabels.refreshGlobalTooltip')
    return t('graphPanel.graphLabels.refreshCurrentLabelTooltip', { label })
  }, [isRefreshing, label, t])

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true)

    try {
      if (!label || label.trim() === '') {
        useSettingsStore.getState().setQueryLabel('*')
      }

      const graphStore = useGraphStore.getState()
      graphStore.setTypeColorMap(new Map<string, string>())
      graphStore.setGraphDataFetchAttempted(false)
      graphStore.setLastSuccessfulQueryLabel('')
      graphStore.incrementGraphDataVersion()

      await new Promise(resolve => setTimeout(resolve, 0))
    } catch (error) {
      console.error('Error during graph refresh:', error)
    } finally {
      setIsRefreshing(false)
    }
  }, [label])

  return (
    <Button
      size="icon"
      variant="outline"
      onClick={handleRefresh}
      tooltip={getRefreshTooltip()}
      tooltipAlign="center"
      className="bg-background/55 rounded-xl border-white/15 shadow-[inset_0_1px_rgba(255,255,255,0.09),0_4px_14px_-8px_rgba(0,0,0,0.55)] backdrop-blur-xl hover:border-white/20 hover:bg-white/[0.07] dark:border-white/[0.08] dark:hover:border-white/[0.13]"
      disabled={isRefreshing}
    >
      <RefreshCw className={`size-4 ${isRefreshing ? 'animate-spin' : ''}`} />
    </Button>
  )
}

export default GraphLabels
