import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
type Version = {
  revision: number
  wording: string
  note: string
  evidence?: { title: string; edition: number; quote: string; context: string; located: boolean }
  export: { status: string; path?: string }
  reviews: { layer: string; verdict: string; reason: string }[]
}
type DecisionPage = {
  decisions: {
    id: number
    choice: 'keep' | 'reopen'
    created: string
    old_edition: number
    new_edition: number
    reason: string
  }[]
  next_before: number | null
}
type History = { versions: Version[]; next_before: number | null; citation_decisions: DecisionPage }
export function Evidence({
  project,
  brief,
  row,
  column,
  revision,
  close
}: {
  project: string
  brief: number
  row: string
  column: string
  revision: number
  close: () => void
}) {
  const [decisions, setDecisions] = useState<DecisionPage>({ decisions: [], next_before: null })
  const [open, setOpen] = useState(false),
    [versions, setVersions] = useState<Version[]>([]),
    [next, setNext] = useState<number | null>(null),
    [reason, setReason] = useState(''),
    [layer, setLayer] = useState('Observation'),
    [verdict, setVerdict] = useState('Insufficient'),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!open) return
    let active = true
    researchRequest<History>('evidence_history', project, { brief, row, column })
      .then(h => {
        if (active) {
          setVersions(h.versions)
          setDecisions(h.citation_decisions)
          setNext(h.next_before)
        }
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [open, project, brief, row, column, revision])
  async function submit(older = false) {
    setBusy(true)
    setError('')
    try {
      const h = await researchRequest<History>(older ? 'evidence_history' : 'evidence_review', project, {
        brief,
        row,
        column,
        revision,
        layer,
        verdict,
        reason,
        before: older ? next : undefined
      })
      setVersions(older ? [...versions, ...h.versions] : h.versions)
      setNext(h.next_before)
      if (!older) {
        setReason('')
        setDecisions(h.citation_decisions)
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function earlierDecisions() {
    setBusy(true)
    setError('')
    try {
      const page = await researchRequest<DecisionPage>('refresh_history', project, {
        brief,
        row,
        column,
        before: decisions.next_before
      })
      setDecisions({ decisions: [...decisions.decisions, ...page.decisions], next_before: page.next_before })
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <details onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Claim history and evidence</summary>
      {!!decisions.decisions.length && (
        <section aria-label="Citation review history">
          <h4>Citation review history</h4>
          {decisions.decisions.map(d => (
            <p key={d.id}>
              {d.choice === 'keep' ? 'Kept earlier citation' : 'Reopened review'} · Edition {d.old_edition} →{' '}
              {d.new_edition} · {d.created} UTC{d.reason && ' · ' + d.reason}
            </p>
          ))}
          {decisions.next_before && (
            <button disabled={busy} onClick={() => void earlierDecisions()}>
              Earlier citation decisions
            </button>
          )}
        </section>
      )}
      {versions.map(v => (
        <article key={v.revision}>
          <h4>
            Revision {v.revision} · {v.wording}
          </h4>
          {v.note && <p>Annotation · {v.note}</p>}
          {v.evidence && (
            <>
              <p>
                {v.evidence.title} · Edition {v.evidence.edition} ·{' '}
                {v.evidence.located ? 'Passage located' : 'Passage missing'}
              </p>
              <blockquote>{v.evidence.context}</blockquote>
            </>
          )}
          {v.export.path ? (
            <button
              onClick={() => {
                const p = localPreviewTarget(v.export.path!)
                if (p) {
                  close()
                  openPreview(p)
                }
              }}
            >
              Open verified revision {v.revision} CSV
            </button>
          ) : (
            <p>{v.export.status}</p>
          )}
          {!v.reviews.length && <p>Support has not been reviewed.</p>}
          {v.reviews.map((r, i) => (
            <p key={i}>
              {r.layer} · {r.verdict} · {r.reason}
            </p>
          ))}
        </article>
      ))}
      {next && (
        <button disabled={busy} onClick={() => void submit(true)}>
          Earlier revisions
        </button>
      )}
      <fieldset disabled={busy}>
        <fieldset>
          <legend>Claim layer</legend>
          {['Observation', 'Comparison', 'Conclusion'].map(x => (
            <button key={x} aria-pressed={layer === x} onClick={() => setLayer(x)}>
              {x}
            </button>
          ))}
        </fieldset>
        <fieldset>
          <legend>Support judgment</legend>
          {['Insufficient', 'Supports', 'Contradicts'].map(x => (
            <button key={x} aria-pressed={verdict === x} onClick={() => setVerdict(x)}>
              {x}
            </button>
          ))}
        </fieldset>
        <label>
          Review reason
          <input value={reason} onChange={e => setReason(e.target.value)} />
        </label>
        <button disabled={!reason.trim()} onClick={() => void submit()}>
          Record claim review
        </button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
