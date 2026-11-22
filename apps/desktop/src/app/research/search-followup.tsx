import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'

type Cell = { row: string; column: string; title: string }
type Followup = {
  choice: number
  arm: string
  origin: string
  state: string
  eligible: number
  limit: number
  attempt_limit: number
  observations: {
    job: number
    title: string
    kind: string
    reason?: string
    reads?: number
    gained?: Cell[]
    lost?: Cell[]
    eligible: boolean
  }[]
}
const labels: Record<string, string> = {
  supported: 'Prediction supported',
  counterexample: 'Counterexample',
  'no difference': 'No difference on new evidence',
  'repeated evidence': 'Previously seen evidence',
  unverified: 'Evidence insufficient'
}
export function SearchFollowup({ project, revision }: { project: string; revision: number }) {
  const [items, setItems] = useState<Followup[]>([]),
    [error, setError] = useState('')
  useEffect(() => {
    let active = true
    researchRequest<{ followups: Followup[] }>('assist_followups', project)
      .then(r => {
        if (active) {
          setItems(r.followups)
          setError('')
        }
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project, revision])
  if (!items.length && !error) return null
  return (
    <section aria-label="Search setting follow-up">
      <h4>How the setting performed</h4>
      {error && <p role="alert">{error}</p>}
      {items.map(f => (
        <article key={f.choice}>
          <p>
            <strong>{f.arm === 'focused' ? 'Contextual' : 'Standard'} search</strong> · chosen from {f.origin}
          </p>
          <p>
            We predicted an ordinary export would cite a passage the chosen search returned and the alternative missed,
            without losing another cited passage.
          </p>
          <p>
            {f.eligible} of {f.limit} new-source exports checked · {f.state}. Up to {f.attempt_limit} attempts.
          </p>
          {f.observations.map(o => (
            <details key={o.job}>
              <summary>
                {o.title} · {labels[o.kind] ?? o.kind}
              </summary>
              {o.reason && <p>{o.reason}</p>}
              {o.reads !== undefined && (
                <p>
                  {o.reads} separate source {o.reads === 1 ? 'read' : 'reads'} before the brief was saved.
                </p>
              )}
              {o.gained?.map(c => (
                <p key={JSON.stringify(c)}>
                  Recovered {c.row} · {c.column} from {c.title}
                </p>
              ))}
              {o.lost?.map(c => (
                <p key={JSON.stringify(c)}>
                  Missed {c.row} · {c.column} from {c.title}
                </p>
              ))}
            </details>
          ))}
        </article>
      ))}
    </section>
  )
}
