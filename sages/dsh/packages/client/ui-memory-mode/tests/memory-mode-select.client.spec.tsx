// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createSnapshotStore } from '@deepseek-ai/dsh-client-runtime/client'
import { bindSnapshotSelector } from '@deepseek-ai/dsh-client-web-react'
import { makeTranslate } from '@deepseek-ai/dsh-client-test-runtime'
import { zh as commonZh } from '@deepseek-ai/dsh-client-locale/src/locales/zh.ts'
import { MemoryModeSelect, type MemoryModeSelectProps } from '../src/client/MemoryModeSelect.tsx'
import { zh } from '../src/client/locales.ts'

afterEach(cleanup)
const t: MemoryModeSelectProps['t'] = makeTranslate(zh, commonZh)

function setup(value: 'auto' | 'manual' | 'off' | undefined) {
  const store = createSnapshotStore({ value })
  const useProjection = (_key: string, selector?: (current: unknown) => unknown) =>
    bindSnapshotSelector(store)(state => (selector ?? (current => current))(state.value))
  const setMode = vi.fn(() => Promise.resolve<string | null>(null))
  const view = render(<MemoryModeSelect {...({ useProjection, locked: false, setMode, t } as unknown as MemoryModeSelectProps)} />)
  return { setMode, view }
}

describe('MemoryModeSelect', () => {
  it('is absent without the MAGI projection capability', () => {
    expect(setup(undefined).view.container.innerHTML).toBe('')
  })

  it('shows auto and switches through the memory command', async () => {
    const { setMode } = setup('auto')
    fireEvent.click(screen.getByRole('button', { name: '记忆模式：自动' }))
    fireEvent.click(screen.getByText('手动'))
    await waitFor(() => { expect(setMode).toHaveBeenCalledWith('manual') })
  })
})
