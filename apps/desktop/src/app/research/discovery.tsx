import { SourceImpact, type Impact } from './source-impact'
import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'
type Hit = {
  source: number
  edition: number
  origin_project: string
  title: string
  created: string
  excerpt?: string
  current_id?: number
}
type MarkedPassage = {
  start: number
  before: string
  changed: string
  after: string
  continues: boolean
  empty: boolean
}
type Comparison = {
  changes: number | null
  change: number | null
  marked: { old: MarkedPassage; new: MarkedPassage } | null
  source: number
  previous: number
  chosen: number
  revision: number
  old_text: string
  new_text: string
  old_length: number
  new_length: number
  start: number
  next: number | null
}
export function Discovery({ project }: { project: string }) {
  const [captured, setCaptured] = useState<{ source: number; edition: number; impact: Impact }>()
  const [query, setQuery] = useState(''),
    [hits, setHits] = useState<Hit[] | null>(null),
    [pins, setPins] = useState<Hit[]>([]),
    [comparison, setComparison] = useState<Comparison | null>(null),
    [notice, setNotice] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    researchRequest<{ pins: Hit[] }>('discovery_list', project)
      .then(r => {
        if (active) setPins(r.pins)
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
    setNotice('')
    try {
      await f()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function compare(pin: Hit, start?: number, change?: number) {
    void act(async () => {
      setComparison(
        await researchRequest<Comparison>('discovery_compare', project, {
          source: pin.source,
          previous: pin.edition,
          chosen: pin.current_id,
          ...(start === undefined ? {} : { start }),
          ...(change === undefined ? {} : { change })
        })
      )
    })
  }
  async function reload() {
    setComparison(null)
    setPins((await researchRequest<{ pins: Hit[] }>('discovery_list', project)).pins)
  }
  return (
    <section className="research-editor">
      <fieldset disabled={busy}>
        <h3>Find work in another project</h3>
        <form
          onSubmit={e => {
            e.preventDefault()
            void act(async () =>
              setHits((await researchRequest<{ matches: Hit[] }>('discovery_search', project, { query })).matches)
            )
          }}
        >
          <label>
            Search other projects
            <input value={query} onChange={e => setQuery(e.target.value)} />
          </label>
          <button disabled={!query.trim()}>Find sources</button>
        </form>
        {hits?.length === 0 && <p>No matching sources in other projects.</p>}
        {hits?.map(h => (
          <article key={h.source}>
            <h4>{h.title}</h4>
            <p>
              {h.origin_project} · Edition {h.edition} · {h.created} UTC
            </p>
            <p>{h.excerpt}</p>
            <button
              disabled={pins.some(p => p.source === h.source)}
              onClick={() =>
                void act(async () =>
                  setPins(
                    (
                      await researchRequest<{ pins: Hit[] }>('discovery_pin', project, {
                        source: h.source,
                        edition: h.edition,
                        origin_project: h.origin_project
                      })
                    ).pins
                  )
                )
              }
            >
              Reuse {h.title}
            </button>
          </article>
        ))}
        <h3>Reused in {project}</h3>
        {pins.map(p => (
          <article key={p.source} aria-label={p.title}>
            <h4>{p.title}</h4>
            <p>
              {p.origin_project} · Pinned edition {p.edition} · {p.created} UTC
            </p>
            {p.current_id !== p.edition && (
              <>
                <p>The original project currently uses edition {p.current_id}.</p>
                <button onClick={() => compare(p)}>Compare editions</button>
              </>
            )}
            {comparison?.source === p.source && (
              <section className="research-pin-comparison" aria-label="Edition comparison">
                {comparison.change !== null && (
                  <nav className="research-actions" aria-label="Changed passages">
                    <strong aria-live="polite">
                      Change {comparison.change + 1} of {comparison.changes}
                    </strong>
                    <button
                      disabled={comparison.change === 0}
                      onClick={() => compare(p, undefined, comparison.change! - 1)}
                    >
                      Previous change
                    </button>
                    <button
                      disabled={comparison.change + 1 === comparison.changes}
                      onClick={() => compare(p, undefined, comparison.change! + 1)}
                    >
                      Next change
                    </button>
                  </nav>
                )}
                <div className="research-pin-passages">
                  {(
                    [
                      [
                        'Currently pinned',
                        comparison.previous,
                        comparison.old_text,
                        comparison.old_length,
                        comparison.marked?.old
                      ],
                      [
                        'Original project',
                        comparison.chosen,
                        comparison.new_text,
                        comparison.new_length,
                        comparison.marked?.new
                      ]
                    ] as const
                  ).map(([label, edition, passage, length, marked]) => (
                    <div key={label}>
                      <h4>
                        {label} · Edition {edition}
                      </h4>
                      {marked ? (
                        <>
                          <pre>
                            {marked.before}
                            <mark aria-label={marked.empty ? 'No text here' : undefined}>
                              {marked.empty ? '∅' : marked.changed}
                            </mark>
                            {marked.after}
                          </pre>
                          {marked.continues && <small>Changed passage continues in the full edition.</small>}
                        </>
                      ) : (
                        <>
                          <small>
                            {passage.length
                              ? `Characters ${comparison.start + 1}–${comparison.start + Array.from(passage).length} of ${length}`
                              : 'End of source'}
                          </small>
                          <pre>{passage}</pre>
                        </>
                      )}
                    </div>
                  ))}
                </div>
                <div className="research-actions">
                  {comparison.marked ? (
                    <button onClick={() => compare(p, 0)}>Read full editions</button>
                  ) : (
                    <>
                      {comparison.changes === null && <small>Large edition comparison · showing full text</small>}
                      {!!comparison.changes && (
                        <button onClick={() => compare(p, undefined, 0)}>Show changed passages</button>
                      )}
                      {comparison.start > 0 && (
                        <button onClick={() => compare(p, Math.max(0, comparison.start - 2400))}>Earlier text</button>
                      )}
                      {comparison.next !== null && (
                        <button onClick={() => compare(p, comparison.next!)}>Later text</button>
                      )}
                    </>
                  )}
                </div>
                <p>
                  Future lookups will use the selected edition. Saved briefs keep their citations until you review them.
                </p>
                <div className="research-actions">
                  <button
                    onClick={() =>
                      void act(async () => {
                        const { source, previous, chosen, revision } = comparison
                        const result = await researchRequest<{ pins: Hit[]; affected_briefs?: Impact }>(
                          'discovery_adopt',
                          project,
                          { source, previous, chosen, revision }
                        )
                        setPins(result.pins)
                        setCaptured(
                          result.affected_briefs
                            ? { source, edition: chosen, impact: result.affected_briefs }
                            : undefined
                        )
                        setComparison(null)
                        setNotice(`Now using edition ${chosen} in ${project}.`)
                      })
                    }
                  >
                    Use edition {comparison.chosen} in this project
                  </button>
                  <button onClick={() => setComparison(null)}>Keep current pin</button>
                </div>
              </section>
            )}
          </article>
        ))}
        {notice && <p role="status">{notice}</p>}
        {captured && <SourceImpact project={project} captured={captured} />}
        {error && (
          <div role="alert">
            {error} <button onClick={() => void act(reload)}>Refresh references</button>
          </div>
        )}
      </fieldset>
    </section>
  )
}
