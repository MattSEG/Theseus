import type { ModelOptionProvider, ProviderUsageWindow } from '@hermes/shared'

export function theseusAllowance(row: ModelOptionProvider | undefined, model: string, now = Date.now()): string {
  const prefix = [row?.slug === 'openai-codex' ? 'Codex' : row?.name, model].filter(Boolean).join(' · ')
  const windows = (values: ProviderUsageWindow[]) => values
    .filter(w => Number.isFinite(w.used_percent) && (!w.resets_at || Date.parse(w.resets_at) > now))
    .map(w => `${w.scope === 'model' ? 'Model limit · ' : ''}${w.label} ${Math.max(0, Math.min(100, Math.round(100 - w.used_percent)))}% left`)
    .join(' · ')
  const accounts = row?.usage?.accounts
  const detail = accounts?.length
    ? accounts.map((account, index) => {
        const quota = ['ready', 'limited'].includes(account.state) ? windows(account.windows ?? []) : ''
        return `${account.label || `Account ${index + 1}`}: ${quota || account.state}`
      }).join(' | ')
    : `Account: ${windows(row?.usage?.windows ?? []) || 'usage unavailable'}`
  return [prefix, detail].filter(Boolean).join(' · ')
}
