import { useState } from 'react'
import { researchRequest } from './rpc'
import { SearchExportCheck, type ExportCheck, type Scan } from './search-export-check'

type Role = 'required' | 'context' | 'unrelated'
type Judgment = { edition: number; role: Role; quote: string; passages?: string[] }
type Source = { edition: number; title: string }
type Arm = 'current' | 'focused'
type Exposure = {
  sources: {
    edition: number
    title?: string
    role: Role | 'unjudged'
    passage: boolean
    digest: string
    scope: string
  }[]
  required: number
  passages: number
  missing: number[]
  unrelated: number
  unjudged: number
  excerpt_bytes: number
}
type Pair = { id: number; query: string; version: string; arm: Arm | null; results: Record<Arm, Exposure> }
type Trial = { id: number; title: string; question: string; current: boolean; pairs: Pair[] }
type Data = {
  sources: Source[]
  briefs: { id: number; title: string; revision: number }[]
  checks: ExportCheck[]
  policy: { id: number; version: string | null; focused: boolean }
  policy_history: { id: number; version: string | null; created: number }[]
  oversize: boolean
  cases: Trial[]
  window: { id: number; case: number; arm: Arm; used: number; expires: number } | null
  limit: number
  minutes: number
}
const arms: Arm[] = ['current', 'focused']

function PairResult({ pair, sources }: { pair: Pair; sources: Source[] }) {
  const names = new Map(sources.map(s => [s.edition, s.title]))
  return (
    <details>
      <summary>
        {pair.query || 'List library'} · {pair.arm ? 'Chat received ' + pair.arm : 'Offline replay'}
      </summary>
      <p>
        Search version <code>{pair.version}</code>
      </p>
      <div className="research-table">
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>Required passages</th>
              <th>Unrelated</th>
              <th>Unjudged</th>
              <th>Passage bytes</th>
            </tr>
          </thead>
          <tbody>
            {arms.map(arm => {
              const r = pair.results[arm]
              return (
                <tr key={arm}>
                  <th>{arm}</th>
                  <td>
                    {r.passages}/{r.required}
                  </td>
                  <td>{r.unrelated}</td>
                  <td>{r.unjudged}</td>
                  <td>{r.excerpt_bytes}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Inspect source receipts</summary>
        {arms.map(arm => (
          <ul key={arm} aria-label={arm + ' source receipts'}>
            {pair.results[arm].sources.map(s => (
              <li key={s.edition}>
                {arm} · {s.title ?? names.get(s.edition) ?? 'Source'} · edition {s.edition} · {s.scope} ·{' '}
                <code>{s.digest}</code>
              </li>
            ))}
          </ul>
        ))}
      </details>
      {arms.map(arm => {
        const r = pair.results[arm]
        return (
          <p key={arm}>
            <strong>{arm}</strong> ·{' '}
            {r.sources
              .map(s => (s.title ?? names.get(s.edition) ?? 'Edition ' + s.edition) + ' (' + s.role + ')')
              .join(', ') || 'No results'}
            .
            {r.missing.length > 0 && (
              <> Missing passages from {r.missing.map(e => names.get(e) ?? 'Edition ' + e).join(', ')}.</>
            )}
          </p>
        )
      })}
    </details>
  )
}

export function SearchTrials({ project }: { project: string }) {
  const [data, setData] = useState<Data>(),
    [title, setTitle] = useState(''),
    [question, setQuestion] = useState('')
  const [labels, setLabels] = useState<Judgment[]>([]),
    [edition, setEdition] = useState(''),
    [role, setRole] = useState<Role>('required'),
    [quote, setQuote] = useState('')
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [activeCase, setCase] = useState(0),
    [queries, setQueries] = useState<Record<number, string>>({})
  async function refresh(selected = activeCase, check = 0) {
    const next = await researchRequest<Data>('search_trial_list', project, { case: selected, check })
    setData(next)
    return next
  }
  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  const addLabel = () => {
    const id = Number(edition)
    if (!id || (role === 'required' && !quote.trim())) {
      setError('Choose an edition and enter its required passage.')
      return
    }
    const old = labels.find(l => l.edition === id)
    const passages =
      role === 'required'
        ? Array.from(new Set([...(old?.role === 'required' ? (old.passages ?? [old.quote]) : []), quote.trim()]))
        : undefined
    if (passages && passages.length > 8) {
      setError('Choose up to eight required passages per edition.')
      return
    }
    setLabels([...labels.filter(l => l.edition !== id), { edition: id, role, quote: passages?.[0] ?? '', passages }])
    setError('')
    setEdition('')
    setQuote('')
  }
  const trial = data?.cases.find(c => c.id === activeCase) ?? data?.cases[0]
  return (
    <details
      onToggle={e => {
        if (e.currentTarget.open && !data)
          void act(async () => {
            await refresh()
          })
      }}
    >
      <summary>Compare library search tools</summary>
      <section aria-label="Library search trials">
        {error && <p role="alert">{error}</p>}
        {data && (
          <fieldset disabled={busy}>
            <SearchExportCheck
              briefs={data.briefs}
              checks={data.checks}
              scan={async before => {
                let scan: Scan | undefined
                await act(async () => {
                  scan = await researchRequest<Scan>('search_trial_scan_exports', project, { before })
                  await refresh(activeCase, scan.recommended ?? 0)
                })
                return scan
              }}
              inspect={async (brief, revision) => {
                let id: number | undefined
                await act(async () => {
                  const saved = await researchRequest<{ id: number }>('search_trial_export_check', project, {
                    brief,
                    revision
                  })
                  await refresh(activeCase, saved.id)
                  id = saved.id
                })
                return id
              }}
            />
            <details>
              <summary>Search setting for this project</summary>
              <p>
                {data.policy.focused
                  ? 'Ordinary library searches use contextual passages.'
                  : 'Ordinary library searches use standard excerpts.'}
                {data.policy.version && !data.policy.focused && ' The previously selected search version has changed.'}
              </p>
              <p>
                Contextual passages preserve shared reports’ openings, add matching text, and finish clipped quantities
                in local search results.
              </p>
              <button
                onClick={() =>
                  void act(async () => {
                    await researchRequest('search_trial_configure', project, {
                      arm: data.policy.focused ? 'current' : 'focused',
                      expected: data.policy.id
                    })
                    await refresh()
                  })
                }
              >
                {data.policy.focused ? 'Restore standard search' : 'Use contextual passages for this project'}
              </button>
              {data.policy_history.length > 0 && (
                <details>
                  <summary>Search setting history</summary>
                  <ol>
                    {data.policy_history.map(e => (
                      <li key={e.id}>
                        {new Date(e.created * 1000).toLocaleString()} · {e.version ?? 'Standard search'}
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </details>
            <details>
              <summary>Register a search case</summary>
              <label>
                Case title
                <input value={title} onChange={e => setTitle(e.target.value)} />
              </label>
              <label>
                Question to test
                <input value={question} onChange={e => setQuestion(e.target.value)} />
              </label>
              <label>
                Source edition
                <select aria-label="Source to judge" value={edition} onChange={e => setEdition(e.target.value)}>
                  <option value="">Choose a source</option>
                  {data.sources.map(s => (
                    <option key={s.edition} value={s.edition}>
                      {s.title} · edition {s.edition}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Source role
                <select aria-label="Search source role" value={role} onChange={e => setRole(e.target.value as Role)}>
                  <option value="required">Required evidence</option>
                  <option value="context">Useful context</option>
                  <option value="unrelated">Unrelated</option>
                </select>
              </label>
              {role === 'required' && (
                <label>
                  Required passage
                  <input value={quote} onChange={e => setQuote(e.target.value)} />
                </label>
              )}
              <button onClick={addLabel}>Add judgment</button>
              <ul>
                {labels.map(l => (
                  <li key={l.edition}>
                    {data.sources.find(s => s.edition === l.edition)?.title} · {l.role}
                    {l.passages && (
                      <ul>
                        {l.passages.map(q => (
                          <li key={q}>{q}</li>
                        ))}
                      </ul>
                    )}{' '}
                    <button onClick={() => setLabels(x => x.filter(a => a.edition !== l.edition))}>
                      Remove judgment {l.edition}
                    </button>
                  </li>
                ))}
              </ul>
              <p>Unlabeled sources remain unjudged. Freeze the case before testing.</p>
              {data.oversize && <p>This library exceeds the bounded trial size.</p>}
              <button
                disabled={data.oversize || !labels.length || !title.trim() || !question.trim()}
                onClick={() =>
                  void act(async () => {
                    const result = await researchRequest<{ case: number }>('search_trial_freeze', project, {
                      title,
                      question,
                      judgments: labels
                    })
                    setTitle('')
                    setQuestion('')
                    setLabels([])
                    await refresh(result.case)
                    setCase(result.case)
                  })
                }
              >
                Freeze search case
              </button>
            </details>
            {data.cases.length > 0 && (
              <label>
                Frozen case
                <select
                  aria-label="Frozen search case"
                  value={trial?.id ?? ''}
                  onChange={e => {
                    const id = Number(e.target.value)
                    void act(async () => {
                      await refresh(id)
                      setCase(id)
                    })
                  }}
                >
                  {data.cases.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {trial && (
              <>
                <p>{trial.question}</p>
                {!trial.current && (
                  <p>Library membership or editions changed. Register a fresh case to run another comparison.</p>
                )}
                <label>
                  Replay query
                  <input
                    value={queries[trial.id] ?? trial.question}
                    onChange={e => setQueries({ ...queries, [trial.id]: e.target.value })}
                  />
                </label>
                <button
                  disabled={!trial.current}
                  onClick={() =>
                    void act(async () => {
                      await researchRequest('search_trial_replay', project, {
                        case: trial.id,
                        query: queries[trial.id] ?? trial.question
                      })
                      await refresh()
                    })
                  }
                >
                  Replay both searches
                </button>
                {!data.window && trial.current && (
                  <details>
                    <summary>Test in a chat</summary>
                    <p>
                      The next agent session to search this project receives the selected tool's results for up to{' '}
                      {data.limit} lookups or {data.minutes} minutes. Both tools are scored on each query.
                    </p>
                    {arms.map(arm => (
                      <button
                        key={arm}
                        onClick={() =>
                          void act(async () => {
                            await researchRequest('search_trial_arm', project, { case: trial.id, arm })
                            await refresh()
                          })
                        }
                      >
                        Use {arm} search for one session
                      </button>
                    ))}
                  </details>
                )}
                {trial.pairs.map(pair => (
                  <PairResult key={pair.id} pair={pair} sources={data.sources} />
                ))}
              </>
            )}
            {data.window && (
              <p>
                {data.window.arm} search is enabled for this trial · {data.window.used}/{data.limit} queries.
                <button
                  onClick={() =>
                    void act(async () => {
                      await researchRequest('search_trial_stop', project, { window: data.window!.id })
                      await refresh()
                    })
                  }
                >
                  End search trial
                </button>
              </p>
            )}
            <button
              onClick={() =>
                void act(async () => {
                  await refresh()
                })
              }
            >
              Refresh search trials
            </button>
          </fieldset>
        )}
      </section>
    </details>
  )
}
