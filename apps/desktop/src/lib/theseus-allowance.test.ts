import { describe, expect, it } from 'vitest'

import { theseusAllowance } from './theseus-allowance'

const scope = 'account' as const
const base = { slug: 'openai-codex', name: 'Codex', models: [] }
describe('Theseus allowance footer', () => {
  it('shows the active model and the remaining shared allowance', () => {
    expect(theseusAllowance({ ...base, usage: { windows: [{ label: 'Weekly', used_percent: 26, scope }] } }, 'A'))
      .toBe('Codex · A · Account: Weekly 74% left')
  })
  it('keeps each account and model window distinct and drops expired windows', () => {
    const text = theseusAllowance({ ...base, usage: { accounts: [
      { id: '1', label: 'Work', state: 'ready', windows: [
        { label: 'A weekly', used_percent: 40, scope: 'model' },
        { label: 'Old', used_percent: 90, scope, resets_at: '2020-01-01T00:00:00Z' }] },
      { id: '2', label: 'Personal', state: 'unknown', windows: [] }
    ] } }, 'A')
    expect(text).toContain('Work: Model limit · A weekly 60% left')
    expect(text).toContain('Personal: unknown')
    expect(text).not.toContain('Old')
  })
})
