import type {} from '@deepseek-ai/dsh-api-remotes/client'
import type { ClientContext, SessionId } from '@deepseek-ai/dsh-client-runtime/client'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type { CommandUiContract, SelectOption } from '@deepseek-ai/dsh-client-ui-commands/client'
import { MemoryModeSelect } from './MemoryModeSelect.tsx'
import { en, zh, type MemoryModeKey } from './locales.ts'

declare module '@deepseek-ai/dsh-session-projection/types' {
  interface SessionProjectionMap {
    magiMemoryMode: 'auto' | 'manual' | 'off'
  }
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    memoryMode: MemoryModeKey
  }
}

export type MemoryMode = 'auto' | 'manual' | 'off'
export interface MemoryModeInjected {
  setMode: (mode: MemoryMode) => Promise<string | null>
}

export const inject = ['slots', 'remote', 'remote.commands', 'locale', 'commandUi']

export function apply(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register('memoryMode', { zh, en }), 'ui-memory-mode: dictionaries')

  const t = ctx.locale.bind('memoryMode')
  ctx.inject(['commandUi'], (scope: ClientContext) => {
    const command = scope.get('commandUi') as CommandUiContract
    const options: SelectOption[] = (['auto', 'manual', 'off'] as const).map(mode => ({
      id: mode,
      label: t(`mode.${mode}`),
    }))
    scope.effect(() => command.decorate({
      name: 'memory',
      available: () => true,
      ui: {
        kind: 'popupSelect',
        options: async () => options,
        onSelect: async (option, session) => {
          const result = await scope.remote.commands.execute(session.sessionId, `/memory ${option.id}`)
          if (!result.ok) throw new Error(`${result.error.message} (${result.error.code})`)
          if (result.value === undefined) throw new Error(`unknown command: /memory ${option.id}`)
          if (result.value.result.kind !== 'success') throw new Error(result.value.result.text)
        },
      },
    }), 'ui-memory-mode: decorate /memory with popupSelect')
  })

  ctx.slots.inject('conversation.input.memory', () => ctx.slots.register({
    name: 'conversation.input.memory',
    locale: 'memoryMode',
    inject: (sessionId: SessionId): MemoryModeInjected => ({
      setMode: async (mode) => {
        const result = await ctx.remote.commands.execute(sessionId, `/memory ${mode}`)
        if (!result.ok) return `${result.error.message} (${result.error.code})`
        if (result.value === undefined) return `unknown command: /memory ${mode}`
        if (result.value.result.kind !== 'success') return result.value.result.text
        return null
      },
    }),
  }, MemoryModeSelect))
}

export { MemoryModeSelect } from './MemoryModeSelect.tsx'
