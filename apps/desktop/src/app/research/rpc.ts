import { requestGatewayForProfile } from '@/store/gateway'
import { $activeProfile } from '@/store/profile'

export async function researchRequest<T>(action: string, project: string, fields: Record<string, unknown> = {}) {
  try {
    return await requestGatewayForProfile<T>(
      $activeProfile.get(),
      'plugin.theseus-project-memory.research',
      { ...fields, project, action },
      30_000,
      undefined,
      { spawnPriority: 'foreground' }
    )
  } catch (error) {
    const data = (error as { data?: { reason?: unknown; detail?: unknown } } | null)?.data
    if (
      data?.reason === 'plugin_rpc_failed' &&
      typeof data.detail === 'string' &&
      data.detail.startsWith('ValueError: ')
    ) {
      throw new Error(data.detail.slice('ValueError: '.length, 900), { cause: error })
    }
    throw error
  }
}
export interface Source {
  source: number
  edition: number
  title: string
  kind: 'note' | 'web' | 'file'
  origin: string
  digest: string
  created: string
  file_proof?: { status: string; scope: string; sha256?: string; bytes?: number }
  origin_current_edition?: number
  pin_status?: string
  shared_from?: string
  excerpt_start?: number
  matched_passage?: { text: string; start: number; end: number; sha256: string }
  matched_passage_deferred?: boolean
  excerpt?: string
}
export interface SourcePage extends Source {
  text: string
  next_start: number | null
}
