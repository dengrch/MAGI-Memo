import { useEffect, useState } from 'react'
import { CpuIcon, LoaderCircleIcon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  discoverRuntimeLLMModels,
  getRuntimeLLMSettings,
  updateRuntimeLLMRole,
  type LightragRoleLLMConfig,
  type RuntimeLLMSettings
} from '@/api/lightrag'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'
import { errorMessage } from '@/lib/utils'

type Draft = {
  binding: string
  model: string
  host: string
  apiKey: string
  maxAsync: string
  timeout: string
}

const emptyDraft: Draft = { binding: '', model: '', host: '', apiKey: '', maxAsync: '', timeout: '' }

const ROLE_DETAILS: Record<string, { label: string; description: string }> = {
  extract: { label: 'Extract', description: 'Entity, relation, and Atom extraction during document ingestion.' },
  resolve: { label: 'Resolve', description: 'Entity identity matching and canonicalization during memory writes.' },
  deduplicate: { label: 'Deduplicate', description: 'Atom comparison, conflict detection, and merge decisions during memory writes.' },
  keyword: { label: 'Keyword', description: 'Query keyword extraction before knowledge-graph retrieval.' },
  query: { label: 'Query', description: 'Final response generation after MAGI assembles retrieval context.' },
  vlm: { label: 'VLM', description: 'Image, table, and other multimodal analysis during ingestion.' },
  dream: { label: 'Dream · BALTHASAR', description: 'Offline community naming and report generation after Leiden clustering.' }
}

function draftFromConfig(config: LightragRoleLLMConfig | undefined): Draft {
  if (!config) return emptyDraft
  return {
    binding: config.binding ?? '',
    model: config.model ?? '',
    host: config.host ?? '',
    apiKey: '',
    maxAsync: config.max_async ? String(config.max_async) : '',
    timeout: config.timeout ? String(config.timeout) : ''
  }
}

function roleLabel(role: string): string {
  return ROLE_DETAILS[role]?.label ?? role.charAt(0).toUpperCase() + role.slice(1)
}

export default function RuntimeModelSettings() {
  const [settings, setSettings] = useState<RuntimeLLMSettings | null>(null)
  const [role, setRole] = useState('')
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [models, setModels] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [discovering, setDiscovering] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const load = async () => {
      try {
        const result = await getRuntimeLLMSettings()
        const nextRole = Object.keys(result.roles)[0] ?? ''
        setSettings(result)
        setRole(nextRole)
        setDraft(draftFromConfig(result.roles[nextRole]))
      } catch (cause) {
        toast.error(`Unable to load runtime models: ${errorMessage(cause)}`)
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const handleRoleChange = (nextRole: string) => {
    setRole(nextRole)
    setDraft(draftFromConfig(settings?.roles[nextRole]))
    setModels([])
  }

  const discover = async () => {
    if (!role) return
    setDiscovering(true)
    try {
      const result = await discoverRuntimeLLMModels({
        role,
        binding: draft.binding || undefined,
        host: draft.host || undefined,
        api_key: draft.apiKey || undefined
      })
      setModels(result.models)
      if (!result.supported) toast.info(result.message ?? 'Model discovery is not supported by this provider')
      else if (result.models.length === 0) toast.info('The provider returned no models')
    } catch (cause) {
      toast.error(`Unable to discover models: ${errorMessage(cause)}`)
    } finally {
      setDiscovering(false)
    }
  }

  const save = async () => {
    if (!role || !draft.binding || !draft.model || !draft.host) {
      toast.error('Binding, model, and host are required')
      return
    }
    setSaving(true)
    try {
      const result = await updateRuntimeLLMRole(role, {
        binding: draft.binding,
        model: draft.model,
        host: draft.host,
        api_key: draft.apiKey || undefined,
        max_async: draft.maxAsync ? Number(draft.maxAsync) : undefined,
        timeout: draft.timeout ? Number(draft.timeout) : undefined
      })
      setSettings((current) => current ? {
        ...current,
        roles: { ...current.roles, [role]: result.config }
      } : current)
      setDraft(draftFromConfig(result.config))
      toast.success(`${roleLabel(role)} switched without restarting MAGI Core`)
    } catch (cause) {
      toast.error(`Unable to switch model: ${errorMessage(cause)}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading || settings === null) {
    return (
      <div className="text-muted-foreground flex h-48 items-center justify-center gap-2 text-sm">
        <LoaderCircleIcon className="size-4 animate-spin" /> Loading role configuration…
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-violet-400/30 bg-violet-500/10 text-violet-400">
          <CpuIcon className="size-5" />
        </span>
        <div>
          <h3 className="font-semibold">Runtime model routing</h3>
          <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
            Switch MAGI Core role models immediately. Runtime changes keep stored credentials private and require no restart.
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Role</span>
          <Select value={role} onValueChange={handleRoleChange}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {Object.keys(settings.roles).map((name) => (
                <SelectItem key={name} value={name}>{roleLabel(name)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-muted-foreground block min-h-8 text-[11px] leading-4">
            {ROLE_DETAILS[role]?.description}
          </span>
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Binding</span>
          <Select
            value={draft.binding}
            onValueChange={(binding) => {
              setDraft((value) => ({ ...value, binding }))
              setModels([])
            }}
          >
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {settings.bindings.map((binding) => (
                <SelectItem key={binding} value={binding}>{binding}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>

      <label className="block space-y-1.5 text-sm">
        <span className="font-medium">Provider host</span>
        <Input value={draft.host} onChange={(event) => setDraft((value) => ({ ...value, host: event.target.value }))} />
      </label>

      <div className="space-y-1.5 text-sm">
        <div className="flex items-center justify-between gap-3">
          <span className="font-medium">Model</span>
          <Button variant="outline" size="sm" onClick={() => void discover()} disabled={discovering}>
            {discovering ? <LoaderCircleIcon className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCwIcon className="mr-1.5 size-3.5" />}
            Discover
          </Button>
        </div>
        {models.length > 0 ? (
          <Select value={draft.model} onValueChange={(model) => setDraft((value) => ({ ...value, model }))}>
            <SelectTrigger><SelectValue placeholder="Select a provider model" /></SelectTrigger>
            <SelectContent>{models.map((model) => <SelectItem key={model} value={model}>{model}</SelectItem>)}</SelectContent>
          </Select>
        ) : (
          <Input value={draft.model} onChange={(event) => setDraft((value) => ({ ...value, model: event.target.value }))} />
        )}
      </div>

      <label className="block space-y-1.5 text-sm">
        <span className="font-medium">API key <span className="text-muted-foreground font-normal">· blank keeps the current key</span></span>
        <Input type="password" value={draft.apiKey} onChange={(event) => setDraft((value) => ({ ...value, apiKey: event.target.value }))} autoComplete="new-password" placeholder="Unchanged" />
      </label>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Max concurrency</span>
          <Input type="number" min={1} value={draft.maxAsync} onChange={(event) => setDraft((value) => ({ ...value, maxAsync: event.target.value }))} />
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Timeout · seconds</span>
          <Input type="number" min={1} value={draft.timeout} onChange={(event) => setDraft((value) => ({ ...value, timeout: event.target.value }))} />
        </label>
      </div>

      <div className="border-border flex items-center justify-between gap-4 border-t pt-4">
        <p className="text-muted-foreground text-xs">Active requests finish on the old queue; new requests use this route immediately.</p>
        <Button onClick={() => void save()} disabled={saving} className="min-w-28">
          {saving && <LoaderCircleIcon className="mr-1.5 size-4 animate-spin" />}
          Apply live
        </Button>
      </div>
    </div>
  )
}
