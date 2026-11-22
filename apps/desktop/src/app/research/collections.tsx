import { useEffect, useState } from 'react'
import { researchRequest, type Source } from './rpc'
type S = Source & { note: string; revision: number }
type Row = {
  project: string
  collection: string
  source: number
  edition: number
  title: string
  origin_project: string
  note: string
  revision: number
}
type Data = { items: Row[]; sources: S[] }
export function Collections({ project }: { project: string }) {
  const [data, setData] = useState<Data>({ items: [], sources: [] }),
    [id, setId] = useState(0),
    [note, setNote] = useState(''),
    [name, setName] = useState('Read later'),
    [title, setTitle] = useState(''),
    [all, setAll] = useState(false),
    [filter, setFilter] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const selected = data.sources.find(s => s.source === id)
  useEffect(() => {
    let a = true
    researchRequest<Data>('collection_list', project, { all_projects: all, filter })
      .then(v => {
        if (a) setData(v)
      })
      .catch(e => {
        if (a) setError(String(e))
      })
    return () => {
      a = false
    }
  }, [project, all, filter])
  async function act(action: string, fields: Record<string, unknown>) {
    if (!selected) return
    setBusy(true)
    setError('')
    try {
      setData(
        await researchRequest<Data>(action, project, {
          source: id,
          edition: selected.edition,
          all_projects: all,
          filter,
          ...fields
        })
      )
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="research-editor">
      <h3>File a source</h3>
      <fieldset disabled={busy}>
        <div className="research-actions">
          {data.sources.map(s => (
            <button
              key={s.source}
              aria-pressed={id === s.source}
              onClick={() => {
                setId(s.source)
                setNote(s.note)
                setTitle(s.title)
              }}
            >
              {s.title}
            </button>
          ))}
        </div>
        {selected && (
          <>
            <p>
              {selected.shared_from || project} · Edition {selected.edition}
            </p>
            <label>
              Collection name
              <input value={name} onChange={e => setName(e.target.value)} />
            </label>
            <label>
              My project annotation
              <textarea value={note} onChange={e => setNote(e.target.value)} />
            </label>
            <button
              disabled={!name.trim()}
              onClick={() =>
                void act('collection_file', {
                  collection: name,
                  note,
                  expected_revision: selected.revision
                })
              }
            >
              File in collection
            </button>
            {!selected.shared_from && (
              <details>
                <summary>Correct shared source title</summary>
                <label>
                  Shared display title
                  <input value={title} onChange={e => setTitle(e.target.value)} />
                </label>
                <button
                  disabled={!title.trim() || title === selected.title}
                  onClick={() =>
                    void act('collection_retitle', { title, expected_title: selected.title })
                  }
                >
                  Save shared title
                </button>
              </details>
            )}
          </>
        )}
        <h3>Collections</h3>
        <label>
          Filter by collection name
          <input value={filter} onChange={e => setFilter(e.target.value)} />
        </label>
        <button aria-pressed={all} onClick={() => setAll(!all)}>
          Include other projects
        </button>
        {data.items.map(i => (
          <article key={JSON.stringify([i.project, i.collection, i.source, i.edition])}>
            <h4>{i.title}</h4>
            <p>
              {i.collection} · {i.project} · Original {i.origin_project} · Edition {i.edition}
            </p>
            <p>{i.note}</p>
          </article>
        ))}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
