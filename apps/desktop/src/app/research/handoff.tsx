import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
type Brief = { id: number; title: string; revision: number }
type Run = { id: number; title: string; edition: number }
export function Handoff({ project, close }: { project: string; close: () => void }) {
  const [options, setOptions] = useState<{ briefs: Brief[]; runs: Run[] }>({ briefs: [], runs: [] }),
    [brief, setBrief] = useState<Brief | null>(null),
    [run, setRun] = useState(0),
    [planning, setPlanning] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState<{
      folder: string
      index: string
      files: number
      sources: number
    } | null>(null)
  useEffect(() => {
    let a = true
    researchRequest<typeof options>('handoff_list', project)
      .then(r => {
        if (a) setOptions(r)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project])
  return (
    <section className="research-editor">
      <h3>Internal project handoff</h3>
      <p>Select a brief or measurement run. The folder includes its cited editions and opens offline.</p>
      <fieldset disabled={busy}>
        {options.briefs.map(b => (
          <button
            key={'b' + b.id}
            aria-pressed={brief?.id === b.id}
            onClick={() => {
              setBrief(brief?.id === b.id ? null : b)
              setSaved(null)
            }}
          >
            {b.title} · revision {b.revision}
          </button>
        ))}
        {options.runs.map(r => (
          <button
            key={'r' + r.id}
            aria-pressed={run === r.id}
            onClick={() => {
              setRun(run === r.id ? 0 : r.id)
              setSaved(null)
            }}
          >
            {r.title} · edition {r.edition}
          </button>
        ))}
        <div className="research-actions">
          <button
            aria-pressed={planning}
            onClick={() => {
              setPlanning(!planning)
              setSaved(null)
            }}
          >
            Include outstanding project work
          </button>
          <button
            disabled={!brief && !run}
            onClick={async () => {
              setBusy(true)
              setError('')
              try {
                setSaved(
                  await researchRequest('handoff_export', project, {
                    brief: brief?.id,
                    revision: brief?.revision,
                    run: run || undefined,
                    include_planning: planning
                  })
                )
              } catch (e) {
                setError(String(e))
              } finally {
                setBusy(false)
              }
            }}
          >
            Build offline handoff
          </button>
        </div>
        {saved && (
          <article>
            <p>
              {saved.files} files · {saved.sources} source editions
            </p>
            <p>{saved.folder}</p>
            <button
              onClick={() => {
                const p = localPreviewTarget(saved.index)
                if (p) {
                  close()
                  openPreview(p)
                }
              }}
            >
              Preview reading order
            </button>
          </article>
        )}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
