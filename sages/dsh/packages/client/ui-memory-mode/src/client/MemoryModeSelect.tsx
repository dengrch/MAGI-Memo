import { useEffect, useState } from 'react'
import { IconChevronDownOutline14, Menu } from '@deepseek-ai/dsh-client-ui-primitives'
import type { MenuEntry } from '@deepseek-ai/dsh-client-ui-primitives'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { MemoryMode, MemoryModeInjected } from './index.ts'
import css from './MemoryModeSelect.module.css'

export type MemoryModeSelectProps = PropsRuntime<'conversation.input.memory'>
  & InjectFace<MemoryModeInjected>
  & PropsLocale<'memoryMode'>

const MODES: readonly MemoryMode[] = ['auto', 'manual', 'off']

export function MemoryModeSelect({ useProjection, locked, setMode, t }: MemoryModeSelectProps) {
  const mode = useProjection('magiMemoryMode')
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<MemoryMode | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (mode === undefined || locked) setOpen(false)
  }, [locked, mode])

  if (mode === undefined) return null
  const displayed = pending ?? mode
  const items: MenuEntry[] = MODES.map(value => ({ id: value, label: t(`mode.${value}`) }))

  const choose = (value: string): void => {
    setOpen(false)
    if (value === mode) return
    const selected = value as MemoryMode
    setPending(selected)
    setError(null)
    void setMode(selected).then((failure) => {
      setPending(null)
      setError(failure)
    }, (reason: unknown) => {
      setPending(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    })
  }

  return (
    <span className={css.root}>
      <Menu
        open={open}
        items={items}
        selectedId={displayed}
        onSelect={choose}
        onClose={() => { setOpen(false) }}
        side="top"
        align="end"
        anchor={(
          <button
            type="button"
            className={css.trigger}
            aria-label={t('trigger.aria', { mode: t(`mode.${displayed}`) })}
            title={t('trigger.title')}
            disabled={locked || pending !== null}
            onClick={() => { setOpen(!open) }}
          >
            <span>{t('trigger.label')}</span>
            <span className={css.value}>{t(`mode.${displayed}`)}</span>
            <span className={open ? css.chevronOpen : css.chevron} aria-hidden>
              <IconChevronDownOutline14 />
            </span>
          </button>
        )}
      />
      {error !== null && <span className={css.error} role="status" title={error}>{t('error')}</span>}
    </span>
  )
}
