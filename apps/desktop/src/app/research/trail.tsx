import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
type Trail = {
  question: string
  remaining: number
  domains: string[]
  next: { url: string; reason: string; parent?: string } | null
  checked: { url: string; edition: number; verified: boolean }[]
  checkpoints: { question: string; note: string }[]
  attempts: { url: string; outcome: string | null }[]
}
export function TrailPanel({
  project,
  id,
  progress
}: {
  project: string
  id: number
  progress: string
}) {
  const [open, setOpen] = useState(false),
    [t, setT] = useState<Trail | null>(null),
    [question, setQuestion] = useState(''),
    [note, setNote] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!open) return
    let a = true
    researchRequest<Trail>('crawl_trail', project, { crawl: id })
      .then(r => {
        if (a) setT(r)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project, id, progress, open])
  return (
    <details className="research-editor" onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Research trail</summary>
      {t && (
        <article>
          <h4>Open question</h4>
          <p>{t.question}</p>
          <p>
            {t.remaining} queued · {t.domains.join(', ')}
          </p>
          {t.next && (
            <>
              <h4>Next page to inspect</h4>
              <p>{t.next.url}</p>
              <p>{t.next.reason}</p>
              {t.next.parent && <p>Found on {t.next.parent}</p>}
            </>
          )}
          <h4>Saved evidence</h4>
          {t.checked.map(c => (
            <p key={c.url}>
              {c.verified ? 'Verified' : 'Receipt mismatch'} · Edition {c.edition} · {c.url}
            </p>
          ))}
          <details>
            <summary>Page attempt history ({t.attempts.length})</summary>
            {t.attempts.map((a, i) => (
              <p key={i}>
                {a.outcome || 'In progress'} · {a.url}
              </p>
            ))}
          </details>
          {t.checkpoints.map((c, i) => (
            <p key={i}>
              {c.question} · {c.note}
            </p>
          ))}
        </article>
      )}
      <label>
        Unanswered question
        <input value={question} onChange={e => setQuestion(e.target.value)} />
      </label>
      <label>
        Checkpoint note
        <input value={note} onChange={e => setNote(e.target.value)} />
      </label>
      <button
        disabled={!question.trim() || busy}
        onClick={async () => {
          setError('')
          setBusy(true)
          try {
            setT(
              await researchRequest<Trail>('crawl_checkpoint', project, { crawl: id, question, note })
            )
            setQuestion('')
            setNote('')
          } catch (e) {
            setError(String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        Save research checkpoint
      </button>
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
