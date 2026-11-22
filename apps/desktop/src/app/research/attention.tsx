import { ProjectNext } from './project-next'
import { Decisions } from './decisions'
import { useEffect, useState } from 'react'
import { researchRequest, type Source } from './rpc'
const states = ['Needs decision', 'Ready', 'Waiting', 'Done']
type Item = {
  id: number
  project: string
  title: string
  state: string
  minutes?: number
  context: string
  source?: number
  edition?: number
  revision: number
}
export function Attention({ project }: { project: string }) {
  const [items, setItems] = useState<Item[]>([]),
    [sources, setSources] = useState<Source[]>([]),
    [title, setTitle] = useState(''),
    [context, setContext] = useState(''),
    [minutes, setMinutes] = useState(''),
    [source, setSource] = useState(''),
    [state, setState] = useState('Ready')
  const [filter, setFilter] = useState(''),
    [budget, setBudget] = useState(''),
    [revision, refresh] = useState(0),
    [error, setError] = useState(''),
    [trace, setTrace] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    Promise.all([
      researchRequest<{ items: Item[] }>('attention_list', project, {
        all_projects: true,
        filter,
        max_minutes: budget ? Number(budget) : undefined
      }),
      researchRequest<{ sources: Source[] }>('search', project)
    ])
      .then(([a, s]) => {
        if (active) {
          setItems(a.items)
          setSources(s.sources)
          setError('')
        }
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project, filter, budget, revision])
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
  return (
    <section className="research-editor">
      <fieldset disabled={busy}>
        <ProjectNext key={project} project={project} />
        <details>
          <summary>Capture a commitment</summary>
          <label>
            Next action
            <input value={title} onChange={e => setTitle(e.target.value)} />
          </label>
          <label>
            Action context
            <input value={context} onChange={e => setContext(e.target.value)} />
          </label>
          <div className="research-limits">
            <label>
              Action state
              <select value={state} onChange={e => setState(e.target.value)}>
                {states.map(s => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              Estimated minutes
              <input inputMode="numeric" value={minutes} onChange={e => setMinutes(e.target.value)} />
            </label>
            <label>
              Related source
              <select value={source} onChange={e => setSource(e.target.value)}>
                <option value="">Optional</option>
                {sources.map(s => (
                  <option key={s.source} value={s.source}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button
            disabled={!title.trim()}
            onClick={() =>
              void act(async () => {
                await researchRequest('attention_capture', project, {
                  title,
                  context,
                  state,
                  minutes: minutes ? Number(minutes) : undefined,
                  source: source ? Number(source) : undefined
                })
                setTitle('')
                setContext('')
                setSource('')
                setMinutes('')
                refresh(v => v + 1)
              })
            }
          >
            Capture action
          </button>
        </details>
        <h3>Across my projects</h3>
        <div className="research-actions">
          <label>
            Show work
            <select value={filter} onChange={e => setFilter(e.target.value)}>
              <option value="">All open</option>
              {states.map(s => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Minutes available
            <input inputMode="numeric" value={budget} onChange={e => setBudget(e.target.value)} />
          </label>
        </div>
        {budget && <p>Includes items with a recorded estimate within this limit.</p>}
        {!items.length && <p>No matching actions.</p>}
        {items.map(i => (
          <article key={i.id}>
            <h4>{i.title}</h4>
            <p>
              {i.project} · {i.state} · {i.minutes ? i.minutes + ' min' : 'Time unestimated'}
            </p>
            <p>{i.context}</p>
            {i.source && (
              <button
                onClick={() =>
                  void act(async () => {
                    const r = await researchRequest<{ title: string; text: string }>('read', i.project, {
                      source: i.source,
                      edition: i.edition
                    })
                    setTrace(r.title + ' · Edition ' + i.edition + '\n' + r.text)
                  })
                }
              >
                Open source for {i.title}
              </button>
            )}
            <label>
              Change state for {i.title}
              <select
                value={i.state}
                onChange={e => {
                  const state = e.target.value
                  void act(async () => {
                    await researchRequest('attention_change', i.project, {
                      item: i.id,
                      revision: i.revision,
                      state
                    })
                    refresh(v => v + 1)
                  })
                }}
              >
                {states.map(s => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
          </article>
        ))}
        {trace && <pre>{trace}</pre>}
        {error && <p role="alert">{error}</p>}
      </fieldset>
      <Decisions project={project} />
    </section>
  )
}
