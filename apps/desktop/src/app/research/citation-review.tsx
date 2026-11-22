import { useState } from 'react'
import { researchRequest, type SourcePage } from './rpc'
import type { Brief } from './brief'

interface Locations {
  matches: { offset: number; before: string; after: string }[]
  more: boolean
}
export interface Change {
  row: string
  column: string
  prior: string
  prior_quote: string
  old_edition: number
  new_edition: number
  nearby?: { prior: Locations; current: Locations; radius: number }
  state:
    | 'nearby wording changed'
    | 'passage repeated'
    | 'unchanged'
    | 'changed'
    | 'context changed'
    | 'passage retained'
    | 'review'
  value: string | null
  quote: string | null
  context_changes: { field: string; before: string; after: string }[]
  source_title: string
  prior_review?: { choice: 'keep' | 'reopen'; reason: string; new_edition: number } | null
  decision: { id: number; choice: 'keep' | 'reopen'; reason: string; created: string } | null
}
interface Item extends Change {
  source: number
  brief: number
  revision: number
  title: string
}
type Cursor = [number, number, string]
interface Page {
  coverage: {
    briefs: number
    populated_cells: number
    previous_pin?: number
    current_local: number
    different_local: number
    uncited: number
    pinned: number
    unavailable: number
  }
  changes: Item[]
  next_after: Cursor | null
}
const same = (c: Change) => c.state === 'unchanged'
const kept = (c: Change) => c.decision?.choice === 'keep'
export const canApply = (c: Change) => !kept(c) && (c.state === 'changed' || same(c))
function Passage({ locations, quote }: { locations?: Locations; quote: string }) {
  if (!locations) return <pre>{quote || 'No unique replacement passage.'}</pre>
  if (!locations.matches.length) return <p>No exact occurrence in this edition.</p>
  return (
    <>
      {locations.matches.map(hit => (
        <blockquote key={hit.offset}>
          {hit.before}
          <mark>{quote}</mark>
          {hit.after}
        </blockquote>
      ))}
      {locations.more && <small>Showing the first two occurrences.</small>}
    </>
  )
}
export function ChangeSummary({ change: c }: { change: Change }) {
  return (
    <div className="citation-comparison">
      <p>
        {c.source_title} · Edition {c.old_edition} → {c.new_edition}
      </p>
      {c.state === 'nearby wording changed' && <strong>Wording around the quotation changed</strong>}
      {c.state === 'passage repeated' && <strong>Multiple possible quotation locations</strong>}
      {c.state === 'context changed' && <strong>Record context changed</strong>}
      {c.state === 'review' && <strong>No reliable replacement field</strong>}
      {c.state === 'passage retained' && (
        <strong>Original passage still present. Check whether it supports the claim.</strong>
      )}
      <div className="citation-values">
        <div>
          <small>Saved claim</small>
          <span>{c.prior || 'Blank'}</span>
        </div>
        {c.value !== null && (
          <div>
            <small>Current source field</small>
            <span>{c.value || 'Blank'}</span>
          </div>
        )}
      </div>
      {c.context_changes.length > 0 && (
        <ul>
          {c.context_changes.map(f => (
            <li key={f.field}>
              <b>{f.field}</b> · {f.before || 'Blank'} → {f.after || 'Blank'}
            </li>
          ))}
        </ul>
      )}
      {c.prior_review && (
        <p>
          Previous review against edition {c.prior_review.new_edition} ·{' '}
          {c.prior_review.choice === 'keep'
            ? 'Kept the earlier citation. ' + c.prior_review.reason
            : 'Reopened for review.'}
        </p>
      )}
      {kept(c) && <p>Earlier citation kept · {c.decision?.reason}</p>}
      <details>
        <summary>Compare passages</summary>
        <small>Edition {c.old_edition}</small>
        <Passage locations={c.nearby?.prior} quote={c.prior_quote} />
        <small>Edition {c.new_edition}</small>
        <Passage locations={c.nearby?.current} quote={c.quote || ''} />
        {c.nearby && <small>Up to {c.nearby.radius} characters on each side of the quotation.</small>}
      </details>
    </div>
  )
}
function ReadEdition({ project, item }: { project: string; item: Item }) {
  const [page, setPage] = useState<SourcePage | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function read(start = 0) {
    setBusy(true)
    setError('')
    try {
      const next = await researchRequest<SourcePage>('read', project, {
        source: item.source,
        edition: item.new_edition,
        start
      })
      setPage(previous => (start && previous ? { ...next, text: previous.text + next.text } : next))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      {page ? (
        <>
          <button onClick={() => setPage(null)}>Close source text</button>
          <pre>{page.text || 'No passage archived.'}</pre>
          {page.next_start !== null && (
            <button disabled={busy} onClick={() => void read(page.next_start!)}>
              Read more source text
            </button>
          )}
        </>
      ) : (
        <button disabled={busy} onClick={() => void read()}>
          Read current source
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
function ReviewItem({
  project,
  item,
  run,
  busy,
  onOpen
}: {
  project: string
  item: Item
  run: (action: string, item: Item, reason?: string) => Promise<void>
  busy: boolean
  onOpen: (item: Item) => void
}) {
  const [keeping, setKeeping] = useState(false),
    [reason, setReason] = useState('')
  return (
    <article className="citation-review-item" aria-label={item.title + ' · ' + item.row + ' · ' + item.column}>
      <h4>{item.title}</h4>
      <strong>
        {item.row} · {item.column}
      </strong>
      <ChangeSummary change={item} />
      {item.nearby && <ReadEdition project={project} item={item} />}
      <div className="research-actions">
        {canApply(item) && (
          <button disabled={busy} onClick={() => void run('refresh_apply', item)}>
            Accept field update
          </button>
        )}
        {kept(item) ? (
          <button disabled={busy} onClick={() => void run('refresh_reopen', item)}>
            Reopen review
          </button>
        ) : (
          <button disabled={busy} aria-expanded={keeping} onClick={() => setKeeping(!keeping)}>
            Keep earlier citation
          </button>
        )}
        <button disabled={busy} onClick={() => onOpen(item)}>
          Open brief cell
        </button>
      </div>
      {keeping && !kept(item) && (
        <div>
          <label>
            Why keep the earlier citation?
            <textarea value={reason} maxLength={600} onChange={e => setReason(e.target.value)} />
          </label>
          <button disabled={!reason.trim() || busy} onClick={() => void run('refresh_keep', item, reason.trim())}>
            Save review decision
          </button>
        </div>
      )}
    </article>
  )
}
export function CitationReview({
  project,
  onOpen,
  onApplied
}: {
  project: string
  onOpen: (item: Item) => Promise<void>
  onApplied: (doc: Brief) => void
}) {
  const [page, setPage] = useState<Page | null>(null),
    [cursor, setCursor] = useState<Cursor | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const fetchPage = (at: Cursor | null) => researchRequest<Page>('refresh_review', project, at ? { after: at } : {})
  async function load(at: Cursor | null) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await fetchPage(at)
      setPage(result)
      setCursor(at)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function change(action: string, item: Item, reason?: string) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const response = await researchRequest<Brief>(action, project, {
        brief: item.brief,
        row: item.row,
        column: item.column,
        expected_revision: item.revision,
        new_edition: item.new_edition,
        ...(item.decision ? { expected_decision: item.decision.id } : {}),
        ...(reason ? { reason } : {})
      })
      if (action === 'refresh_apply') onApplied(response)
      setMessage(
        action === 'refresh_apply'
          ? 'Saved field update to ' + item.title
          : action === 'refresh_keep'
            ? 'Saved the reason for keeping the earlier citation.'
            : 'Reopened the citation for review.'
      )
      setPage(await fetchPage(cursor))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  const render = (item: Item) => (
    <ReviewItem
      project={project}
      key={JSON.stringify([item.brief, item.row, item.column, item.revision, item.new_edition, item.decision?.id])}
      item={item}
      run={change}
      busy={busy}
      onOpen={i => {
        setError('')
        void onOpen(i).catch(e => setError(String(e)))
      }}
    />
  )
  const pending = page?.changes.filter(c => !kept(c) && !same(c)) ?? []
  const retained = page?.changes.filter(c => !kept(c) && same(c)) ?? []
  const reviewed = page?.changes.filter(kept) ?? []
  return (
    <section className="citation-review" aria-label="Project citation review">
      <div className="research-actions">
        <button disabled={busy} onClick={() => void load(null)}>
          {page ? 'Recheck project citations' : 'Review project citations'}
        </button>
        {page && (
          <button
            disabled={busy}
            onClick={() => {
              setPage(null)
              setMessage('')
              setError('')
            }}
          >
            Close review
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {page && (
        <>
          <p>
            Checked {page.coverage.briefs} saved briefs and {page.coverage.populated_cells} populated cells in {project}
            . {page.coverage.different_local} cite a different local edition. {page.coverage.current_local} cite the
            current edition. {page.coverage.uncited} have no citation.
            {!!page.coverage.previous_pin && ` Previously pinned citations: ${page.coverage.previous_pin}.`}
            {page.coverage.pinned > 0 && ' Shared pins: ' + page.coverage.pinned + '.'}
            {page.coverage.unavailable > 0 && ' Unavailable citations: ' + page.coverage.unavailable + '.'}
          </p>
          {!page.changes.length && (
            <p>
              {cursor
                ? 'No further comparisons on this page.'
                : 'All available citations use their project’s selected editions.'}
            </p>
          )}
          {pending.length > 0 && <h3>Needs review ({pending.length})</h3>}
          {pending.map(render)}
          {!!retained.length && (
            <details>
              <summary>Same field value ({retained.length})</summary>
              {retained.map(render)}
            </details>
          )}
          {!!reviewed.length && (
            <details>
              <summary>Earlier citation kept ({reviewed.length})</summary>
              {reviewed.map(render)}
            </details>
          )}
          {page.next_after && (
            <button disabled={busy} onClick={() => void load(page.next_after)}>
              Next comparisons
            </button>
          )}
          {cursor && (
            <button disabled={busy} onClick={() => void load(null)}>
              First comparisons
            </button>
          )}
        </>
      )}
    </section>
  )
}
