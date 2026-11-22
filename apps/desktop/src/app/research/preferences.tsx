import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
type Layout = {
  template: string
  fingerprint: string
  columns: string[]
  active: boolean
  receipts: { brief: number; revision: number; title: string }[]
}
export function LayoutPreferences({ project, refresh }: { project: string; refresh: number }) {
  const [rows, setRows] = useState<Layout[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    researchRequest<{ suggestions: Layout[] }>('preference_list', project)
      .then(r => {
        if (active) setRows(r.suggestions)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project, refresh])
  async function change(r: Layout) {
    setBusy(true)
    setError('')
    try {
      const v = await researchRequest<{ suggestions: Layout[] }>(
        r.active ? 'preference_undo' : 'preference_accept',
        project,
        { template: r.template, fingerprint: r.fingerprint }
      )
      setRows(v.suggestions)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <details>
      <summary>Project layout preferences{rows.length ? ' (' + rows.length + ')' : ''}</summary>
      {!rows.length && (
        <p>
          A suggestion appears after two different populated briefs with the same columns have been
          exported.
        </p>
      )}
      {rows.map(r => (
        <article key={r.template + r.fingerprint}>
          <h4>
            {r.template} · {r.active ? 'Current default' : 'Suggested layout'}
          </h4>
          <p>{r.columns.join(' · ')}</p>
          <p>
            Exported briefs ·{' '}
            {r.receipts.map(x => x.title + ' (revision ' + x.revision + ')').join(', ')}
          </p>
          <button disabled={busy} onClick={() => void change(r)}>
            {r.active ? 'Undo default' : 'Use as project default'}
          </button>
        </article>
      ))}
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
