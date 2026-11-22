import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
type V = {
  revision: number
  choice: string
  reason: string
  assumption: string
  event: string
  due: string
  action: string
  review_note?: string
  brief?: number
  brief_revision?: number
}
type D = { id: number; revision: number; project: string; title: string; status: string; history: V[] }
export function Decisions({ project }: { project: string }) {
  const [ds, setDs] = useState<D[]>([]),
    [fields, setFields] = useState<Record<string, string>>({}),
    [others, setOthers] = useState(false),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const set = (k: string, v: string) => setFields(o => ({ ...o, [k]: v }))
  useEffect(() => {
    let a = true
    researchRequest<{ decisions: D[] }>('decision_list', project, { all_projects: true })
      .then(r => {
        if (a) setDs(r.decisions)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project])
  function f(k: string, name: string) {
    return (
      <label>
        {name}
        <input value={fields[k] || ''} onChange={e => set(k, e.target.value)} />
      </label>
    )
  }
  async function act(action: string, p: string, args: Record<string, unknown>) {
    setBusy(true)
    setError('')
    try {
      const r = await researchRequest<{ decisions: D[] }>(action, p, { ...args, all_projects: true })
      setDs(r.decisions)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="research-editor">
      <h3>Decision review</h3>
      <fieldset disabled={busy}>
        <details>
          <summary>Watch a decision</summary>
          {f('title', 'Decision title')}
          {f('choice', 'Current decision')}
          {f('reason', 'Original rationale')}
          {f('assumption', 'Key assumption')}
          {f('due', 'Review date (YYYY-MM-DD)')}
          {f('event', 'Event that warrants review')}
          {f('brief', 'Related brief ID (optional)')}
          <button
            onClick={() =>
              void act('decision_save', project, {
                ...fields,
                brief: fields.brief ? Number(fields.brief) : undefined
              })
            }
          >
            Save decision watch
          </button>
        </details>
        <button aria-pressed={others} onClick={() => setOthers(!others)}>
          Show upcoming and reviewed decisions
        </button>
        {ds
          .filter(d => others || ['Due', 'Triggered'].includes(d.status))
          .map(d => {
            const v = d.history[0],
              id = d.id
            return (
              <article key={id}>
                <h4>
                  {d.title} · {d.status}
                </h4>
                <p>
                  {d.project} · {v.choice}
                </p>
                <p>Rationale · {v.reason}</p>
                <p>Assumption · {v.assumption}</p>
                <p>
                  {v.due && 'Review ' + v.due + ' · '}
                  {v.event}
                </p>
                <details>
                  <summary>Decision history for {d.title}</summary>
                  {d.history.map(h => (
                    <p key={h.revision}>
                      Revision {h.revision} · {h.action} · {h.choice} · {h.review_note || h.reason}
                      {h.brief ? ' · Brief ' + h.brief + ' revision ' + h.brief_revision : ''}
                    </p>
                  ))}
                </details>
                {d.status !== 'Reviewed' && (
                  <>
                    {f('note' + id, 'Review note for ' + d.title)}
                    {f('new' + id, 'Revised choice for ' + d.title)}
                    {f('date' + id, 'Next review date for ' + d.title)}
                    {d.status !== 'Triggered' && (
                      <button
                        onClick={() =>
                          void act('decision_trigger', d.project, {
                            decision: id,
                            revision: d.revision,
                            note: fields['note' + id]
                          })
                        }
                      >
                        Report event for {d.title}
                      </button>
                    )}
                    {['Kept', 'Revised', 'Deferred'].map(outcome => (
                      <button
                        key={outcome}
                        onClick={() =>
                          void act('decision_review', d.project, {
                            decision: id,
                            revision: d.revision,
                            note: fields['note' + id],
                            choice: fields['new' + id],
                            due: fields['date' + id],
                            outcome
                          })
                        }
                      >
                        {outcome} · {d.title}
                      </button>
                    ))}
                  </>
                )}
              </article>
            )
          })}
        {!others && !ds.some(d => ['Due', 'Triggered'].includes(d.status)) && (
          <p>No decisions due for review.</p>
        )}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
