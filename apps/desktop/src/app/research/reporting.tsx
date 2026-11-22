import { useState } from 'react'
import { researchRequest } from './rpc'
type Member = { title: string; source: number; edition: number; basis?: string }
type Data = {
  reviewed: number
  scope: string
  groups: { kind: string; key: string; members: Member[] }[]
  unclassified: Member[]
}
export function Reporting({ project, query }: { project: string; query: string }) {
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  return (
    <div className="research-reading">
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError('')
          try {
            setData(await researchRequest<Data>('reporting_compare', project, { query }))
          } catch (e) {
            setError(String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        Compare reporting origins
      </button>
      {data && (
        <article>
          <h4>Reporting origins</h4>
          <p>
            {data.reviewed} sources reviewed. {data.scope}
          </p>
          {data.groups.map(g => (
            <section key={g.kind + g.key}>
              <strong>
                {g.kind} · {g.members.length} sources
              </strong>
              <p>{g.key}</p>
              {g.members.map(m => (
                <p key={m.source}>
                  {m.title} · Edition {m.edition}
                  <br />
                  {m.basis}
                </p>
              ))}
            </section>
          ))}
          {!!data.unclassified.length && (
            <details>
              <summary>Origin unestablished ({data.unclassified.length})</summary>
              {data.unclassified.map(m => (
                <p key={m.source}>
                  {m.title} · Edition {m.edition}
                </p>
              ))}
            </details>
          )}
        </article>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
