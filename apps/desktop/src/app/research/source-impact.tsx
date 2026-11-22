import { useEffect, useState } from 'react'
import { ChangeSummary, type Change } from './citation-review'
import { researchRequest } from './rpc'

type Mode = 'automatic' | 'suggest' | 'off'
type SourceKey = { source: number; edition: number; title: string }
type Claim = Change & { brief: number; revision: number; title: string }
export type Impact = SourceKey & {
  digest: string
  items: Claim[]
  offered: boolean
  checked: number
  pending_index: boolean
  next_after: [number, number, string] | null
}

export function ImpactSetting({ project }: { project: string }) {
  const [mode, setMode] = useState<Mode>(),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    researchRequest<{ mode: Mode }>('impact_mode', project)
      .then(r => {
        if (active) setMode(r.mode)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function choose(value: Mode) {
    setBusy(true)
    setError('')
    try {
      const r = await researchRequest<{ mode: Mode }>('impact_mode', project, { mode: value, expected: mode })
      setMode(r.mode)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <label>
        Revised-source checks
        <select
          aria-label="Revised-source checks"
          disabled={!mode || busy}
          value={mode ?? 'automatic'}
          onChange={e => void choose(e.target.value as Mode)}
        >
          <option value="automatic">Run automatically</option>
          <option value="suggest">Suggest a check</option>
          <option value="off">Off</option>
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
    </>
  )
}

export function SourceImpact({
  project,
  captured
}: {
  project: string
  captured: { impact?: Impact; source: number; edition: number } | null
}) {
  const [recent, setRecent] = useState<SourceKey[]>([]),
    [report, setReport] = useState<Impact>(),
    [selected, setSelected] = useState<Claim>()
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function loadRecent() {
    setRecent((await researchRequest<{ items: SourceKey[] }>('impact_recent', project)).items)
  }
  useEffect(() => {
    let active = true
    setReport(captured?.impact)
    setSelected(undefined)
    setError('')
    researchRequest<{ items: SourceKey[] }>('impact_recent', project)
      .then(r => {
        if (active) setRecent(r.items)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project, captured])
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
  async function check(key: SourceKey, after?: Impact['next_after']) {
    const page = await researchRequest<Impact>('impact_check', project, {
      source: key.source,
      edition: key.edition,
      ...(after ? { after } : {})
    })
    setReport(page)
    setSelected(undefined)
  }
  async function dismiss(key: SourceKey) {
    await researchRequest('impact_dismiss', project, { source: key.source, edition: key.edition })
    setReport(undefined)
    setSelected(undefined)
    await loadRecent()
  }
  if (!report && !recent.length && !error) return null
  return (
    <section aria-label="Affected brief cells" className="theseus-impact">
      <h4>Saved claims to review</h4>
      {error && <p role="alert">{error}</p>}
      <fieldset disabled={busy}>
        {recent
          .filter(r => r.source !== report?.source)
          .map(r => (
            <button key={r.source} onClick={() => void act(() => check(r))}>
              Check briefs citing {r.title}
            </button>
          ))}
        {report && (
          <>
            <p>
              {report.title} · edition {report.edition}
            </p>
            {report.offered ? (
              <button onClick={() => void act(() => check(report))}>Check affected briefs</button>
            ) : (
              <>
                {report.items.map(c => (
                  <div className="theseus-assist-item" key={JSON.stringify([c.brief, c.row, c.column])}>
                    <strong>{c.title}</strong>
                    <p>
                      {c.row} · {c.column}
                    </p>
                    <p>
                      {c.state === 'changed'
                        ? c.prior + ' → ' + c.value
                        : c.state === 'context changed'
                          ? 'Record conditions changed'
                          : c.state === 'nearby wording changed'
                            ? 'Wording around the citation changed'
                            : c.state === 'passage repeated'
                              ? 'The quotation now has multiple possible locations'
                              : 'Review the original quotation'}
                    </p>
                    {c.context_changes.map(f => (
                      <p key={f.field}>
                        {f.field} · {f.before} → {f.after}
                      </p>
                    ))}
                    <button onClick={() => setSelected(c)}>
                      Review {c.row} · {c.column}
                    </button>
                    {selected === c && (
                      <>
                        <ChangeSummary change={c} />
                        <details open>
                          <summary>Evidence for this review</summary>
                          <blockquote>{c.prior_quote}</blockquote>
                          {c.quote && <blockquote>{c.quote}</blockquote>}
                          <small>
                            Edition {report.edition} · SHA-256 {report.digest}
                          </small>
                        </details>
                        {c.state === 'changed' && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await researchRequest('refresh_apply', project, {
                                  brief: c.brief,
                                  expected_revision: c.revision,
                                  row: c.row,
                                  column: c.column,
                                  new_edition: report.edition
                                })
                                await check(report)
                              })
                            }
                          >
                            Apply reviewed value
                          </button>
                        )}
                      </>
                    )}
                  </div>
                ))}
                {!report.items.length && (
                  <p>
                    {report.checked
                      ? 'No unresolved change in the cited cells checked on this page.'
                      : 'No affected cells found on this page.'}
                  </p>
                )}
                {report.pending_index ? (
                  <button onClick={() => void act(() => check(report))}>Check remaining older briefs</button>
                ) : (
                  report.next_after && (
                    <button onClick={() => void act(() => check(report, report.next_after))}>
                      Check next cited cells
                    </button>
                  )
                )}
              </>
            )}
            <button onClick={() => void act(() => dismiss(report))}>Dismiss this edition</button>
          </>
        )}
      </fieldset>
    </section>
  )
}
