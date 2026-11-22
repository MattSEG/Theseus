import { useState } from 'react'

type Brief = { id: number; title: string; revision: number }
type Arm = 'original' | 'current' | 'focused'
type Cell = {
  row: string
  column: string
  title: string
  edition: number
  current: boolean
  search: Record<Arm, boolean>
  original_read: boolean
}
export type ExportCheck = {
  id: number
  created: number
  result: {
    brief: number
    revision: number
    title: string
    file: string
    digest: string
    version: string
    later_searches?: number
    queries: string[]
    cells: Cell[]
    editions: Record<Arm, number>
  }
}
const arms: Arm[] = ['original', 'current', 'focused']
const armName = { original: 'Original chat', current: 'Current search', focused: 'Focused search' }

function Report({ item }: { item: ExportCheck }) {
  const r = item.result
  return (
    <article aria-label="Saved export comparison">
      <h4>
        {r.title} · revision {r.revision}
      </h4>
      <p>
        CSV verified {new Date(item.created * 1000).toLocaleString()}. Both searches replay the original queries against
        the library as it stands at the time of the check.
      </p>
      <div className="research-table">
        <table>
          <thead>
            <tr>
              <th>Search</th>
              <th>Cited passages returned</th>
              <th>Distinct editions returned</th>
            </tr>
          </thead>
          <tbody>
            {arms.map(a => (
              <tr key={a}>
                <th>{armName[a]}</th>
                <td>
                  {r.cells.filter(c => c.search[a]).length}/{r.cells.length}
                </td>
                <td>{r.editions[a]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Inspect passages and queries</summary>
        {!!r.later_searches && (
          <p>{r.later_searches} later search(es) occurred after the revision was written and were excluded.</p>
        )}
        <ol>
          {r.queries.map((q, i) => (
            <li key={i}>{q || 'List library'}</li>
          ))}
        </ol>
        {r.cells.map(c => (
          <p key={JSON.stringify([c.row, c.column])}>
            <strong>
              {c.row} · {c.column}
            </strong>{' '}
            · {c.title}, edition {c.edition}.{' '}
            {c.search.original
              ? 'The original search returned the passage.'
              : c.original_read
                ? 'The original chat obtained the passage by reading the edition.'
                : 'The available search and read receipts do not show this passage.'}{' '}
            {c.current ? '' : 'This edition is outside the current collection. '}
            Current search {c.search.current ? 'returned' : 'missed'} the passage. Focused search{' '}
            {c.search.focused ? 'returned' : 'missed'} it.
          </p>
        ))}
        <p>
          CSV <code>{r.file}</code>
          <br />
          SHA-256 <code>{r.digest}</code>
          <br />
          Search version <code>{r.version}</code>
        </p>
      </details>
      <p>
        This checks retrieval of the passages the author cited. Relevance of other records, support for the conclusions,
        and what a new chat would save still need separate tests.
      </p>
    </article>
  )
}

export type Scan = {
  before: number | null
  recommended: number | null
  entries: {
    title: string
    brief: number
    revision: number
    check?: number
    reason?: string
    finding?: { kind: string; reason: string }
  }[]
}
export function SearchExportCheck({
  briefs,
  checks,
  scan,
  inspect
}: {
  briefs: Brief[]
  checks: ExportCheck[]
  scan: (before?: number) => Promise<Scan | undefined>
  inspect: (brief: number, revision: number) => Promise<number | undefined>
}) {
  const [selected, setSelected] = useState(0),
    [revision, setRevision] = useState(''),
    [show, setShow] = useState(0)
  const [batch, setBatch] = useState<Scan>()
  async function page(before?: number) {
    const next = await scan(before)
    if (next) {
      setBatch(next)
      if (next.recommended) setShow(next.recommended)
    }
  }
  const brief = briefs.find(b => b.id === selected)
  const picked = checks.find(c => c.id === show) ?? checks[0]
  return (
    <details>
      <summary>Check a saved CSV against both searches</summary>
      {briefs.length ? (
        <>
          <button onClick={() => void page()}>Find the next search test</button>
          {batch && (
            <section aria-label="Suggested search test">
              <p>Checked the latest revision of {batch.entries.length} recent briefs in this project.</p>
              <ul>
                {batch.entries.map(e => (
                  <li key={e.brief}>
                    <strong>{e.title}</strong> · {e.finding?.kind ?? 'Skipped'} · {e.finding?.reason ?? e.reason}
                  </li>
                ))}
              </ul>
              {!batch.recommended && <p>No discriminating retrieval test emerged from this group.</p>}
              {batch.before && <button onClick={() => void page(batch.before!)}>Check the next three briefs</button>}
            </section>
          )}
          <label>
            Saved brief
            <select
              aria-label="Saved brief to check"
              value={selected}
              onChange={e => {
                const b = briefs.find(x => x.id === Number(e.target.value))
                setSelected(b?.id ?? 0)
                setRevision(b ? String(b.revision) : '')
              }}
            >
              <option value={0}>Choose a brief</option>
              {briefs.map(b => (
                <option key={b.id} value={b.id}>
                  {b.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Exported revision
            <input
              type="number"
              min={1}
              max={brief?.revision}
              value={revision}
              onChange={e => setRevision(e.target.value)}
            />
          </label>
          <button
            disabled={
              !brief || !Number.isInteger(Number(revision)) || Number(revision) < 1 || Number(revision) > brief.revision
            }
            onClick={async () => {
              if (brief) {
                const id = await inspect(brief.id, Number(revision))
                if (id) setShow(id)
              }
            }}
          >
            Check saved CSV
          </button>
        </>
      ) : (
        <p>No saved briefs in this project.</p>
      )}
      {picked && (
        <>
          <label>
            Recorded check
            <select
              aria-label="Recorded export check"
              value={picked.id}
              onChange={e => setShow(Number(e.target.value))}
            >
              {checks.map(c => (
                <option key={c.id} value={c.id}>
                  {c.result.title} · r{c.result.revision} · check {c.id}
                </option>
              ))}
            </select>
          </label>
          <Report item={picked} />
        </>
      )}
    </details>
  )
}
