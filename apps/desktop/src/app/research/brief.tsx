import { canApply, ChangeSummary, CitationReview, type Change } from './citation-review'
import { Audiences } from './audiences'
import { Evidence } from './evidence'
import { LayoutPreferences } from './preferences'
import { useEffect, useState } from 'react'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
import { researchRequest } from './rpc'

interface Cell {
  value: string
  note?: string
  edition?: number
  source?: number
  quote?: string
}
export interface Brief {
  brief: number
  title: string
  template: string
  question: string
  columns: string[]
  revision: number
  rows: { name: string; cells: Record<string, Cell> }[]
}
export function BriefPanel({ project, close }: { project: string; close: () => void }) {
  const [list, setList] = useState<Brief[]>([])
  const [doc, setDoc] = useState<Brief | null>(null)
  const [title, setTitle] = useState(''),
    [question, setQuestion] = useState('')
  const [template, setTemplate] = useState('Market comparison')
  const [row, setRow] = useState(''),
    [col, setCol] = useState('')
  const [cell, setCell] = useState<Cell>({ value: '' })
  const [column, setColumn] = useState(''),
    [error, setError] = useState(''),
    [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [changes, setChanges] = useState<Change[] | null>(null)
  const [usageRevision, setUsageRevision] = useState(0)
  const [passage, setPassage] = useState('')
  const query = <T,>(action: string, fields: Record<string, unknown> = {}) =>
    researchRequest<T>(action, project, fields)
  useEffect(() => {
    let active = true
    researchRequest<{ briefs: Brief[] }>('brief_list', project)
      .then(r => {
        if (active) setList(r.briefs)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function perform(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await work()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function loaded(result: Brief) {
    setChanges(null)
    setDoc(result)
    setList(old => [result, ...old.filter(b => b.brief !== result.brief)])
    setRow('')
    setCol('')
    setCell({ value: '' })
    setPath('')
    setColumn('')
    setPassage('')
  }
  function edit(fields: Record<string, unknown>) {
    return perform(async () =>
      loaded(
        await query<Brief>('brief_edit', {
          brief: doc?.brief,
          expected_revision: doc?.revision,
          ...fields
        })
      )
    )
  }
  return (
    <section className="research-editor">
      <fieldset disabled={busy}>
        <CitationReview
          project={project}
          onApplied={loaded}
          onOpen={async item => {
            const next = await query<Brief>('brief_read', { brief: item.brief })
            const target = next.rows.find(r => r.name === item.row)?.cells[item.column]
            if (!target || next.revision !== item.revision)
              throw new Error('The brief changed. Recheck the project citations.')
            loaded(next)
            setRow(item.row)
            setCol(item.column)
            setCell(target)
            requestAnimationFrame(() => document.getElementById('research-cell-value')?.focus())
          }}
        />
        <LayoutPreferences project={project} refresh={usageRevision} />
        <details open={!doc}>
          <summary>Create a brief</summary>
          <label>
            Brief title
            <input value={title} onChange={e => setTitle(e.target.value)} />
          </label>
          <label>
            Decision or question
            <input value={question} onChange={e => setQuestion(e.target.value)} />
          </label>
          <label>
            Brief template
            <select value={template} onChange={e => setTemplate(e.target.value)}>
              {['Market comparison', 'Scientific review', 'Personal decision'].map(t => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <button
            disabled={!title.trim() || !question.trim()}
            onClick={() =>
              void perform(async () => loaded(await query<Brief>('brief_create', { title, question, template })))
            }
          >
            Create brief
          </button>
        </details>
        {list.length > 0 && (
          <div className="research-actions">
            {list.map(b => (
              <button
                key={b.brief}
                onClick={() => void perform(async () => loaded(await query<Brief>('brief_read', { brief: b.brief })))}
              >
                {b.title}
              </button>
            ))}
          </div>
        )}
        {doc && (
          <>
            <h3>{doc.title}</h3>
            <p>
              {doc.question} · Revision {doc.revision}
            </p>
            <div className="research-table">
              <table>
                <thead>
                  <tr>
                    <th>Item</th>
                    {doc.columns.map(c => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {doc.rows.map(r => (
                    <tr key={r.name}>
                      <th>{r.name}</th>
                      {doc.columns.map(c => (
                        <td key={c}>
                          <button
                            aria-label={'Edit ' + r.name + ' ' + c}
                            onClick={() => {
                              setRow(r.name)
                              setCol(c)
                              setCell(r.cells[c] || { value: '' })
                              setPassage('')
                            }}
                          >
                            {r.cells[c]?.value || 'Not recorded'}
                          </button>
                          {r.cells[c]?.edition && <small>Edition {r.cells[c].edition}</small>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="research-actions">
              <label>
                New row
                <input
                  value={col ? '' : row}
                  onChange={e => {
                    setCol('')
                    setRow(e.target.value)
                  }}
                />
              </label>
              <button disabled={!!col || !row.trim()} onClick={() => void edit({ rows: [{ name: row }] })}>
                Add row
              </button>
              <label>
                New column
                <input value={column} onChange={e => setColumn(e.target.value)} />
              </label>
              <button disabled={!column.trim()} onClick={() => void edit({ add_column: column })}>
                Add column
              </button>
            </div>
            {col && (
              <div>
                <h4>
                  {row} · {col}
                </h4>
                <label>
                  Cell value
                  <input
                    id="research-cell-value"
                    value={cell.value}
                    onChange={e => setCell({ ...cell, value: e.target.value })}
                  />
                </label>
                <label>
                  Personal note
                  <input value={cell.note || ''} onChange={e => setCell({ ...cell, note: e.target.value })} />
                </label>
                <label>
                  Source edition
                  <input
                    inputMode="numeric"
                    value={cell.edition || ''}
                    onChange={e => setCell({ ...cell, edition: Number(e.target.value) || undefined })}
                  />
                </label>
                <label>
                  Exact passage
                  <textarea value={cell.quote || ''} onChange={e => setCell({ ...cell, quote: e.target.value })} />
                </label>
                <button onClick={() => void edit({ rows: [{ name: row, cells: { [col]: cell } }] })}>Save cell</button>
                {cell.source && cell.edition && (
                  <button
                    onClick={() =>
                      void perform(async () => {
                        const r = await query<{ title: string; text: string }>('brief_passage', {
                          brief: doc.brief,
                          revision: doc.revision,
                          row,
                          column: col
                        })
                        setPassage(r.title + '\n' + r.text)
                      })
                    }
                  >
                    Open cited passage
                  </button>
                )}
                {passage && <pre>{passage}</pre>}
                <Audiences
                  key={JSON.stringify([doc.brief, row, col, 'audience'])}
                  project={project}
                  brief={doc.brief}
                  revision={doc.revision}
                  row={row}
                  column={col}
                  value={doc.rows.find(r => r.name === row)?.cells[col]?.value || ''}
                  close={close}
                />
                <Evidence
                  key={JSON.stringify([doc.brief, row, col])}
                  project={project}
                  brief={doc.brief}
                  row={row}
                  column={col}
                  revision={doc.revision}
                  close={close}
                />
              </div>
            )}
            <button
              onClick={() =>
                void perform(async () =>
                  setChanges((await query<{ changes: Change[] }>('refresh_compare', { brief: doc.brief })).changes)
                )
              }
            >
              Compare source editions
            </button>
            {changes && (
              <section aria-label="Evidence changes">
                <h4>Source edition comparison</h4>
                {changes.length === 0 && <p>No cited local source has a different current edition.</p>}
                {changes.map(c => (
                  <article key={JSON.stringify([c.row, c.column])}>
                    <strong>
                      {c.row} · {c.column}
                    </strong>
                    <ChangeSummary change={c} />
                    {canApply(c) && (
                      <button
                        aria-label={'Accept ' + c.row + ' ' + c.column}
                        onClick={() =>
                          void perform(async () =>
                            loaded(
                              await query<Brief>('refresh_apply', {
                                brief: doc.brief,
                                expected_revision: doc.revision,
                                row: c.row,
                                column: c.column,
                                new_edition: c.new_edition
                              })
                            )
                          )
                        }
                      >
                        Accept field update
                      </button>
                    )}
                  </article>
                ))}
              </section>
            )}
            <div className="research-actions">
              <button
                onClick={() =>
                  void perform(async () => {
                    setPath(
                      (
                        await query<{ path: string }>('brief_export', {
                          brief: doc.brief,
                          revision: doc.revision
                        })
                      ).path
                    )
                    setUsageRevision(v => v + 1)
                  })
                }
              >
                Export CSV
              </button>
              {path && (
                <button
                  onClick={() => {
                    const p = localPreviewTarget(path)
                    if (p) {
                      close()
                      openPreview(p)
                    }
                  }}
                >
                  Open exported CSV
                </button>
              )}
            </div>
          </>
        )}
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </section>
  )
}
