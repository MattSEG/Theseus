import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'

interface Workflow {
  id: number
  origin: string
  title: string
  steps: string[]
  output: string
  review: string
}
interface Job {
  id: number
  brief: number
  title: string
  steps: string[]
  progress: { note: string; brief_revision: number }[]
  review: string
}
interface State {
  workflows: Workflow[]
  jobs: Job[]
  briefs: { id: number; title: string }[]
}
export function Workflows({ project }: { project: string }) {
  const [s, setS] = useState<State>({ workflows: [], jobs: [], briefs: [] }),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [source, setSource] = useState(0),
    [flow, setFlow] = useState<Workflow | null>(null),
    [fields, setFields] = useState<Record<string, string>>({})
  const set = (k: string, v: string) => setFields(o => ({ ...o, [k]: v }))
  useEffect(() => {
    let active = true
    researchRequest<State>('workflow_list', project)
      .then(r => {
        if (active) setS(r)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function act(action: string, args: Record<string, unknown>) {
    setBusy(true)
    setError('')
    try {
      setS(await researchRequest<State>(action, project, args))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function input(k: string, label: string, multi = false) {
    return (
      <label>
        {label}
        {multi ? (
          <textarea value={fields[k] || ''} onChange={e => set(k, e.target.value)} />
        ) : (
          <input value={fields[k] || ''} onChange={e => set(k, e.target.value)} />
        )}
      </label>
    )
  }
  return (
    <section className="research-editor">
      <fieldset disabled={busy}>
        <details>
          <summary>Save a procedure from a brief</summary>
          <label>
            Completed brief
            <select value={source} onChange={e => setSource(Number(e.target.value))}>
              <option value={0}>Select a brief</option>
              {s.briefs.map(b => (
                <option value={b.id} key={b.id}>
                  {b.title}
                </option>
              ))}
            </select>
          </label>
          {input('name', 'Workflow name')}
          {input('steps', 'Procedure steps (one per line)', true)}
          {input('output', 'Expected output')}
          {input('review', 'Decision requiring review')}
          <button
            disabled={!source}
            onClick={() =>
              void act('workflow_save', {
                brief: source,
                title: fields.name,
                steps: fields.steps,
                output: fields.output,
                review: fields.review
              })
            }
          >
            Save workflow
          </button>
        </details>
        <h3>Saved workflows</h3>
        {s.workflows.map(w => (
          <button
            key={w.id}
            onClick={() => {
              setFlow(w)
              set('runSteps', w.steps.join('\n'))
            }}
          >
            {w.title} · {w.origin}
          </button>
        ))}
        {flow && (
          <article>
            <h4>{flow.title}</h4>
            <p>Expected output · {flow.output}</p>
            <p>Review · {flow.review}</p>
            {input('job', 'New brief title')}
            {input('question', 'New decision or question')}
            {input('runSteps', 'Steps for this run', true)}
            <button
              onClick={() =>
                void act('workflow_start', {
                  workflow: flow.id,
                  title: fields.job,
                  question: fields.question,
                  steps: fields.runSteps
                })
              }
            >
              Start workflow
            </button>
          </article>
        )}
        {s.jobs.map(j => (
          <article key={j.id}>
            <h3>{j.title}</h3>
            <p>Output brief {j.brief}</p>
            <ol>
              {[...j.steps, 'Review: ' + j.review].map((step, i) => (
                <li key={i}>
                  {j.progress[i] ? '✓ ' : ''}
                  {step}
                  {j.progress[i] && (
                    <small>
                      {' '}
                      · {j.progress[i].note} (revision {j.progress[i].brief_revision})
                    </small>
                  )}
                </li>
              ))}
            </ol>
            {j.progress.length <= j.steps.length ? (
              <>
                {input('receipt' + j.id, 'Step result for ' + j.title)}
                <button
                  onClick={() =>
                    void act('workflow_advance', {
                      job: j.id,
                      expected_step: j.progress.length,
                      note: fields['receipt' + j.id]
                    })
                  }
                >
                  Record step for {j.title}
                </button>
              </>
            ) : (
              <p>Reviewed and complete</p>
            )}
          </article>
        ))}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
