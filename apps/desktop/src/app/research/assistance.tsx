import { TableSetting } from './table-assist'
import { SearchFollowup } from './search-followup'
import { ImpactSetting } from './source-impact'
import { useEffect, useState } from 'react'
import { useStore } from '@nanostores/react'
import { Lightbulb } from 'lucide-react'
import { $busy } from '@/store/session'
import { $focusedStoredSessionId } from '@/store/session-focus'
import { researchRequest } from './rpc'

type Mode = 'automatic' | 'suggest' | 'off'
type Item = {
  id: number
  project: string
  brief: number
  revision: number
  title: string
  state: string
  kind: string | null
  reason: string | null
  trigger: string | null
  check: number | null
  actionable: boolean
  dismissed: boolean
}
type Project = { mode: Mode; items: Item[] }
type Proof = {
  cells: {
    row: string
    column: string
    title: string
    edition: number
    quote: string
    search: { original: boolean; current: boolean; focused: boolean }
    read: boolean
  }[]
  queries: string[]
  file_digest: string
  version: string
  choice: { id: number; focused: boolean }
}
const names: Record<string, string> = {
  gain: 'Contextual search recovered a passage',
  regression: 'Standard search retained a passage',
  'shared gap': 'Both searches missed a cited passage',
  'edition changed': 'A cited edition has changed',
  'unverified origin': 'Original retrieval unverified',
  tie: 'Both searches tied'
}
const title = (item: Item) =>
  item.state === 'offered'
    ? 'Compare this export’s searches'
    : item.kind
      ? (names[item.kind] ?? item.kind)
      : item.state === 'skipped'
        ? 'Check unavailable'
        : item.state === 'pending'
          ? 'Waiting for the export receipt'
          : 'Check cancelled'

export function ChatAssist({
  onOpen,
  refreshKey
}: {
  onOpen: (project: string, id: number) => void
  refreshKey: number
}) {
  const session = useStore($focusedStoredSessionId),
    busy = useStore($busy)
  const [notice, setNotice] = useState<{ session: string; item: Item }>()
  useEffect(() => {
    if (!session || busy) return
    let active = true,
      timer: ReturnType<typeof setTimeout> | undefined
    async function get(attempt: number) {
      try {
        const res = await researchRequest<{ items: Item[]; pending?: boolean }>('assist_chat', '', { session })
        if (!active) return
        setNotice(res.items[0] ? { session: session!, item: res.items[0] } : undefined)
        if (res.pending && attempt < 3) timer = setTimeout(() => void get(attempt + 1), 1200 * (attempt + 1))
      } catch {
        if (active) setNotice(undefined)
      }
    }
    void get(0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [session, busy, refreshKey])
  if (!notice || notice.session !== session || busy) return null
  return (
    <button
      className="research-launch theseus-assist-hint"
      onClick={() => onOpen(notice.item.project, notice.item.id)}
      aria-label={'Review suggestion for ' + notice.item.title}
    >
      <Lightbulb aria-hidden="true" />
      <span>{title(notice.item)}</span>
    </button>
  )
}

export function Assistance({
  project,
  selected,
  onChanged
}: {
  project: string
  selected: number
  onChanged: () => void
}) {
  const [revision, setRevision] = useState(0)
  const [data, setData] = useState<Project>(),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<{ id: number; proof: Proof }>()
  async function refresh() {
    const result = await researchRequest<Project>('assist_project', project)
    setData(result)
  }
  useEffect(() => {
    let active = true
    researchRequest<Project>('assist_project', project)
      .then(r => {
        if (active) setData(r)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await fn()
      await refresh()
      setRevision(v => v + 1)
      onChanged()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function inspect(id: number) {
    setDetail({ id, proof: await researchRequest<Proof>('assist_evidence', project, { id }) })
  }
  useEffect(() => {
    if (!selected) return
    let active = true
    researchRequest<Proof>('assist_evidence', project, { id: selected })
      .then(proof => {
        if (active) setDetail({ id: selected, proof })
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [project, selected])
  return (
    <section aria-label="Project assistance">
      <h3>Assistance</h3>
      {error && <p role="alert">{error}</p>}
      {data && (
        <fieldset disabled={busy}>
          <label>
            Saved-export checks
            <select
              aria-label="Saved-export checks"
              value={data.mode}
              onChange={e => {
                const value = e.target.value
                void act(async () => {
                  await researchRequest('assist_set_mode', project, { mode: value, expected: data.mode })
                })
              }}
            >
              <option value="automatic">Run automatically</option>
              <option value="suggest">Suggest a check</option>
              <option value="off">Off</option>
            </select>
          </label>
          <p>
            Checks compare the passages returned by local searches with the citations in a saved CSV. When you choose a
            search setting here, Theseus checks its prediction against later exports in this project.
          </p>
          <ImpactSetting project={project} />
          <TableSetting project={project} />
          <SearchFollowup project={project} revision={revision} />
          {!data.items.length && <p>No checks recorded for this project.</p>}
          {data.items.map(item => (
            <article className="theseus-assist-item" key={item.id}>
              <h4>
                {item.title} · revision {item.revision}
              </h4>
              <p>
                {title(item)}
                {item.dismissed ? ' · Dismissed' : ''}
              </p>
              {item.trigger && <small>Check recorded when {item.trigger}.</small>}
              {item.reason && item.kind !== 'tie' && <p>{item.reason}</p>}
              <div className="research-actions">
                {item.check && (
                  <button onClick={() => void act(() => inspect(item.id))}>Inspect evidence for {item.title}</button>
                )}
                {item.state === 'offered' && data.mode !== 'off' && (
                  <button
                    onClick={() =>
                      void act(async () => {
                        await researchRequest('assist_check', project, { id: item.id })
                      })
                    }
                  >
                    Run check for {item.title}
                  </button>
                )}
                {item.actionable && (
                  <button
                    onClick={() =>
                      void act(async () => {
                        await researchRequest('assist_dismiss', project, { id: item.id })
                      })
                    }
                  >
                    Dismiss suggestion for {item.title}
                  </button>
                )}
              </div>
              {detail?.id === item.id && (
                <div className="theseus-assist-proof">
                  {detail.proof.cells.map(c => (
                    <div key={JSON.stringify([c.row, c.column])}>
                      <strong>
                        {c.row} · {c.column}
                      </strong>
                      <blockquote>{c.quote}</blockquote>
                      <small>
                        {c.title} · edition {c.edition}. Standard search {c.search.current ? 'returned' : 'missed'} this
                        passage. Contextual search {c.search.focused ? 'returned' : 'missed'} it.
                      </small>
                    </div>
                  ))}
                  <details>
                    <summary>Search and file receipts</summary>
                    <ul>
                      {detail.proof.queries.map((q, i) => (
                        <li key={i}>{q || 'List library'}</li>
                      ))}
                    </ul>
                    <small>
                      CSV SHA-256 <code>{detail.proof.file_digest}</code>
                    </small>
                  </details>
                  {item.actionable && (item.kind === 'gain' || item.kind === 'regression') && (
                    <button
                      onClick={() =>
                        void act(async () => {
                          await researchRequest('assist_apply', project, {
                            id: item.id,
                            expected: detail.proof.choice.id
                          })
                          setDetail(undefined)
                        })
                      }
                    >
                      {item.kind === 'gain'
                        ? 'Use contextual passages in this project'
                        : 'Restore standard search in this project'}
                    </button>
                  )}
                </div>
              )}
            </article>
          ))}
        </fieldset>
      )}
    </section>
  )
}
