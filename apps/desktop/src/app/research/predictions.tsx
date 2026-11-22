import { useEffect, useState } from 'react'
import { openPreview } from '@/store/preview'
import { localPreviewTarget } from '@/lib/local-preview'
import { researchRequest } from './rpc'

type Prediction = {
  id: number
  run: number
  title: string
  row_name: string
  col: string
  expected: string
  rehearsal: number
  outcome: string | null
  benchmark: { values_correct: number }
  receipt: { path: string; file_current: boolean; actual: string | null; revision: number; reason: string } | null
}
export type Example = {
  review: number
  digest: string
  brief: number
  revision: number
  title: string
  row: string
  column: string
  verdict: string
  reason: string
  edition: number
  quote: string
  value: string
}
type Dashboard = { predictions: Prediction[]; examples: Example[] }
export function Predictions({
  project,
  choose,
  close
}: {
  project: string
  choose: (e: Example) => void
  close: () => void
}) {
  const [rows, setRows] = useState<Prediction[]>([])
  const [examples, setExamples] = useState<Example[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [kind, setKind] = useState('revision')
  const [rehearsal, setRehearsal] = useState(false)
  const list = () => researchRequest<Dashboard>('prediction_list', project)
  useEffect(() => {
    let active = true
    researchRequest<Dashboard>('prediction_list', project)
      .then(r => {
        if (active) {
          setRows(r.predictions)
          setExamples(r.examples)
        }
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function refresh() {
    try {
      const r = await list()
      setRows(r.predictions)
      setExamples(r.examples)
      setError('')
    } catch (e) {
      setError(String(e))
    }
  }
  async function freeze() {
    setBusy(true)
    try {
      const r = await researchRequest<Dashboard>('prediction_freeze', project, {
        ...fields,
        kind,
        rehearsal,
        edition: Number(fields.edition)
      })
      setRows(r.predictions)
      setExamples(r.examples)
      setFields({})
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="Later job predictions">
      <h3>Check a prediction on a later job</h3>
      <details>
        <summary>Register a future CSV cell</summary>
        <p>
          Choose the governing source and exact expected value before creating the brief. The latest finished comparison
          must have passed every value check for this condition. Its prediction is scored on the first export.
        </p>
        <label>
          Condition
          <select aria-label="Condition" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="revision">Revision or correction</option>
            <option value="condition">Specific condition</option>
            <option value="conflict">Conflicting evidence</option>
          </select>
        </label>
        {Object.entries({
          title: 'Future brief title',
          row: 'Target row',
          column: 'Target column',
          edition: 'Governing source edition',
          quote: 'Governing passage',
          expected: 'Expected literal value'
        }).map(([k, label]) => (
          <label key={k}>
            {label}
            <input value={fields[k] || ''} onChange={e => setFields({ ...fields, [k]: e.target.value })} />
          </label>
        ))}
        <label>
          <input type="checkbox" checked={rehearsal} onChange={e => setRehearsal(e.target.checked)} />
          Rehearsal · exclude from future experiment selection
        </label>
        <button disabled={busy} onClick={() => void freeze()}>
          Freeze prediction
        </button>
      </details>
      {rows.map(r => (
        <p key={r.id}>
          <strong>{r.outcome || 'Awaiting first export'}</strong> · {r.title} · {r.row_name} / {r.col}. Run {r.run}{' '}
          passed {r.benchmark.values_correct} value checks. Expected saved value: “{r.expected}”.
          {r.receipt && (
            <>
              {' '}
              Saved in revision {r.receipt.revision}: {r.receipt.actual ?? 'no scored value'}. {r.receipt.reason}
              {r.receipt.file_current ? (
                <button
                  onClick={() => {
                    const target = localPreviewTarget(r.receipt!.path)
                    if (target) {
                      close()
                      openPreview(target)
                    }
                  }}
                >
                  Open scored CSV
                </button>
              ) : (
                ' The saved file no longer verifies.'
              )}
            </>
          )}
          {r.rehearsal ? ' Rehearsal.' : ''}
        </p>
      ))}
      {examples.length > 0 && (
        <details>
          <summary>Reviewed examples from saved work ({examples.length})</summary>
          {examples.map((e, i) => (
            <article key={i}>
              <strong>
                {e.title} · revision {e.revision}
              </strong>
              <p>
                {e.row} / {e.column}: {e.value}. Review: {e.verdict} · {e.reason}
              </p>
              <p>
                Source edition {e.edition}: {e.quote}
              </p>
              <button onClick={() => choose(e)}>Select review for next experiment</button>
            </article>
          ))}
        </details>
      )}
      <button onClick={() => void refresh()}>Refresh predictions</button>
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
