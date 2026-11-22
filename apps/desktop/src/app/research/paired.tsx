import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
import { SearchTrials } from './search-trials'
import { Predictions, type Example } from './predictions'
import { LearningPilot } from './learning-pilot'
type Arm = { scores: { kind: string; actual: string | null; passed: boolean }[] }
type Suite = {
  id: number
  title: string
  question: string
  col: string
  results: { id: number; decision: string; baseline: Arm; candidate: Arm }[]
}
export function Paired({ project, close }: { project: string; close: () => void }) {
  const [selection,setSelection] = useState<{project:string;example:Example}>()
  const [suites, setSuites] = useState<Suite[]>([]),
    [v, setV] = useState<Record<string, string>>({}),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [path, setPath] = useState('')
  const set = (k: string, x: string) => setV(o => ({ ...o, [k]: x }))
  useEffect(() => {
    let a = true
    researchRequest<{ suites: Suite[] }>('paired_list', project)
      .then(r => {
        if (a) setSuites(r.suites)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project])
  async function act(action: string, fields: Record<string, unknown>) {
    setBusy(true)
    setError('')
    try {
      const r = await researchRequest<{ suites?: Suite[]; path?: string }>(action, project, fields)
      if (r.suites) setSuites(r.suites)
      if (r.path) setPath(r.path)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function field(k: string, label: string) {
    return (
      <label>
        {label}
        <input value={v[k] || ''} onChange={e => set(k, e.target.value)} />
      </label>
    )
  }
  return (
    <section className="research-editor">
      <LearningPilot project={project} close={close} example={selection?.project===project?selection.example:undefined} clear={()=>setSelection(undefined)} />
      <SearchTrials key={"search-"+project} project={project} />
      <Predictions close={close} key={project} project={project} choose={example=>setSelection({project,example})} />
      <fieldset disabled={busy}>
        <details>
          <summary>Register a paired trial</summary>
          {field('title', 'Trial title')}
          {field('question', 'Common question')}
          {field('column', 'Answer column')}
          {['positive', 'ambiguous', 'missing'].map(k => (
            <details key={k} open>
              <summary>{k} condition</summary>
              <div className="research-limits">
                {field(k + 'row', k + ' row name')}
                {field(k + 'expected', k + ' expected answer')}
                {field(k + 'edition', k + ' source edition')}
              </div>
              {field(k + 'quote', k + ' exact passage')}
            </details>
          ))}
          <button
            onClick={() =>
              void act('paired_freeze', {
                title: v.title,
                question: v.question,
                column: v.column,
                cases: ['positive', 'ambiguous', 'missing'].map(k => ({
                  kind: k,
                  row: v[k + 'row'],
                  expected: v[k + 'expected'],
                  edition: Number(v[k + 'edition']),
                  quote: v[k + 'quote']
                }))
              })
            }
          >
            Freeze expected outcomes
          </button>
        </details>
        {suites.map(s => (
          <article key={s.id}>
            <h3>{s.title}</h3>
            <p>
              {s.question} · Column {s.col}
            </p>
            <p>Use the brief ID and exported revision, separated by a colon, for each arm.</p>
            <div className="research-actions">
              {field('b' + s.id, 'Baseline output for ' + s.title)}
              {field('c' + s.id, 'Candidate output for ' + s.title)}
            </div>
            <button
              onClick={() =>
                void act('paired_score', {
                  suite: s.id,
                  baseline: v['b' + s.id],
                  candidate: v['c' + s.id]
                })
              }
            >
              Score {s.title}
            </button>
            {s.results.map(r => (
              <div key={r.id}>
                <h4>{r.decision}</h4>
                <div className="research-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Condition</th>
                        <th>Baseline</th>
                        <th>Candidate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.baseline.scores.map((b, i) => (
                        <tr key={b.kind}>
                          <th>{b.kind}</th>
                          {[b, r.candidate.scores[i]].map((x, j) => (
                            <td key={j}>
                              {x.actual ?? 'Absent'} · {x.passed ? 'Pass' : 'Fail'}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button onClick={() => void act('paired_export', { suite: s.id, result: r.id })}>
                  Export trial {r.id} receipts
                </button>
              </div>
            ))}
          </article>
        ))}
        {path && <p>Saved {path}</p>}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
