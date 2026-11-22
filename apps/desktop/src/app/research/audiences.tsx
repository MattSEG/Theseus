import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
type A = {
  row: string
  column: string
  item: string
  label: string
  value: string
  classification: string
}
type State = { approved: A[]; internal?: string; external?: string }
export function Audiences({
  project,
  brief,
  revision,
  row,
  column,
  value,
  close
}: {
  project: string
  brief: number
  revision: number
  row: string
  column: string
  value: string
  close: () => void
}) {
  const [data, setData] = useState<State>({ approved: [] }),
    [item, setItem] = useState(''),
    [label, setLabel] = useState(''),
    [kind, setKind] = useState('Estimate'),
    [title, setTitle] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    let a = true
    researchRequest<State>('audience_list', project, { brief, revision })
      .then(r => {
        if (a) setData(r)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project, brief, revision, open])
  async function act(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true)
    setError('')
    try {
      setData(
        await researchRequest<State>(action, project, {
          brief,
          revision,
          row,
          column,
          public_item: item,
          public_label: label,
          classification: kind,
          public_title: title,
          ...extra
        })
      )
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function preview(p: string) {
    const t = localPreviewTarget(p)
    if (t) {
      close()
      openPreview(t)
    }
  }
  return (
    <details onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Internal and external editions</summary>
      <fieldset disabled={busy}>
        <p>Public value from the saved cell · {value || 'Not recorded'}</p>
        <label>
          Public item name
          <input value={item} onChange={e => setItem(e.target.value)} />
        </label>
        <label>
          Public measure label
          <input value={label} onChange={e => setLabel(e.target.value)} />
        </label>
        <label>
          Basis for public value
          <select value={kind} onChange={e => setKind(e.target.value)}>
            {['Estimate', 'Measured', 'Quoted', 'Assumption', 'Decision'].map(k => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <button
          disabled={!value || !item.trim() || !label.trim()}
          onClick={() => void act('audience_approve')}
        >
          Approve this public field
        </button>
        <h4>External preview</h4>
        {data.approved.map(a => (
          <p key={JSON.stringify([a.row, a.column])}>
            {a.item} · {a.label} · {a.value} · {a.classification}{' '}
            <button onClick={() => void act('audience_remove', { row: a.row, column: a.column })}>
              Remove {a.item} {a.label}
            </button>
          </p>
        ))}
        <label>
          External brief title
          <input value={title} onChange={e => setTitle(e.target.value)} />
        </label>
        <button
          disabled={!title.trim() || !data.approved.length}
          onClick={() => void act('audience_export')}
        >
          Export both editions
        </button>
        {data.internal && data.external && (
          <div className="research-actions">
            <button onClick={() => preview(data.internal!)}>Open internal planning sheet</button>
            <button onClick={() => preview(data.external!)}>Open external brief</button>
          </div>
        )}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </details>
  )
}
