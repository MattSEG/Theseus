import { useEffect, useState } from 'react'
import { researchRequest, type Source } from './rpc'
type Group = {
  group: string
  n: number
  mean: string
  minimum: string
  maximum: string
  min_record: number
  max_record: number
}
type Run = {
  id: number
  title: string
  source: number
  edition: number
  parameters: { measure: string; group: string; unit: string }
  report: {
    groups: Group[]
    records: number
    excluded_count: number
    excluded: { record: number; reason: string }[]
  }
}
export function Analysis({ project }: { project: string }) {
  const [sources, setSources] = useState<Source[]>([]),
    [runs, setRuns] = useState<Run[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [source, setSource] = useState(0),
    [cols, setCols] = useState<string[]>([]),
    [edition, setEdition] = useState(0)
  const [title, setTitle] = useState(''),
    [measure, setMeasure] = useState(''),
    [group, setGroup] = useState(''),
    [unit, setUnit] = useState(''),
    [record, setRecord] = useState('')
  const q = <T,>(action: string, body: Record<string, unknown> = {}) =>
    researchRequest<T>(action, project, body)
  useEffect(() => {
    let active = true
    Promise.all([
      researchRequest<{ sources: Source[] }>('search', project),
      researchRequest<{ runs: Run[] }>('analysis_list', project)
    ])
      .then(([s, r]) => {
        if (active) {
          setSources(s.sources)
          setRuns(r.runs)
        }
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function act(f: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await f()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function choose(id: number) {
    setSource(id)
    setCols([])
    setGroup('')
    setMeasure('')
    setEdition(0)
    if (!id) return
    const s = await q<{ columns: string[]; edition: number }>('analysis_columns', { source: id })
    setCols(s.columns)
    setEdition(s.edition)
    setMeasure(s.columns[0])
  }
  async function calculate(args: Record<string, unknown>) {
    const r = await q<{ runs: Run[] }>('analysis_run', args)
    setRuns(r.runs)
    setRecord('')
  }
  async function inspect(id: number, num: number) {
    const r = await q<{ header: string[]; values: string[]; edition: number }>('analysis_record', {
      run: id,
      record: num
    })
    setRecord(
      'Edition ' +
        r.edition +
        ' · Record ' +
        num +
        '\n' +
        r.header.map((h, i) => h + ' = ' + r.values[i]).join('\n')
    )
  }
  return (
    <section className="research-editor">
      <h3>Measurement analysis</h3>
      <fieldset disabled={busy}>
        <label>
          Measurement source
          <select value={source} onChange={e => void act(() => choose(Number(e.target.value)))}>
            <option value={0}>Select a saved CSV or TSV</option>
            {sources.map(s => (
              <option key={s.source} value={s.source}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        {cols.length > 0 && (
          <>
            <label>
              Analysis title
              <input value={title} onChange={e => setTitle(e.target.value)} />
            </label>
            <div className="research-limits">
              <label>
                Measurement
                <select value={measure} onChange={e => setMeasure(e.target.value)}>
                  {cols.map(k => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label>
                Group by
                <select value={group} onChange={e => setGroup(e.target.value)}>
                  <option value="">All records</option>
                  {cols
                    .filter(k => k !== measure)
                    .map(k => (
                      <option key={k}>{k}</option>
                    ))}
                </select>
              </label>
              <label>
                Measurement unit
                <input value={unit} onChange={e => setUnit(e.target.value)} />
              </label>
            </div>
            <button
              disabled={!title || !unit}
              onClick={() => void act(() => calculate({ title, source, edition, measure, group, unit }))}
            >
              Run analysis
            </button>
          </>
        )}
        {runs.map(r => (
          <article key={r.id}>
            <h3>
              {r.title} · edition {r.edition}
            </h3>
            <p>
              {r.parameters.measure} ({r.parameters.unit}) · {r.report.records} records ·{' '}
              {r.report.excluded_count} excluded
            </p>
            <div className="research-table">
              <table>
                <thead>
                  <tr>
                    {['Group', 'Count', 'Mean', 'Minimum', 'Maximum'].map(v => (
                      <th key={v}>{v}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {r.report.groups.map(g => (
                    <tr key={g.group}>
                      <th>{g.group}</th>
                      <td>{g.n}</td>
                      <td>{g.mean}</td>
                      {(['min', 'max'] as const).map(k => (
                        <td key={k}>
                          <button
                            aria-label={k + ' record ' + g.group + ' edition ' + r.edition}
                            onClick={() =>
                              void act(() => inspect(r.id, g[k === 'min' ? 'min_record' : 'max_record']))
                            }
                          >
                            {g[k === 'min' ? 'minimum' : 'maximum']}
                          </button>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {r.report.excluded_count > 0 && (
              <details>
                <summary>Inspect excluded records</summary>
                {r.report.excluded.map(x => (
                  <p key={x.record}>
                    Record {x.record} · {x.reason}
                  </p>
                ))}
              </details>
            )}
            <button
              aria-label={'Rerun edition ' + r.edition + ' on latest source'}
              onClick={() =>
                void act(() => calculate({ title: r.title, source: r.source, ...r.parameters }))
              }
            >
              Rerun on latest source
            </button>
          </article>
        ))}
        {record && <pre>{record}</pre>}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
