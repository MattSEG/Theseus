import { TableAssist, type TableHint } from './table-assist'
import { useStore } from '@nanostores/react'
import { $connection } from '@/store/session'
import { LinkedFiles, selectedFile } from './local-files'
import { SourceImpact, type Impact } from './source-impact'
import { Reporting } from './reporting'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'
import { researchRequest, type Source, type SourcePage } from './rpc'

interface ArchiveEntry {
  edition: number
  created: string
  status: 'Current' | 'Archived' | 'Pinned' | 'Previously pinned'
}
function ArchivedEditions({
  project,
  source,
  selected,
  disabled,
  onRead
}: {
  project: string
  source: number
  selected: number
  disabled: boolean
  onRead: (edition: number) => void
}) {
  const [entries, setEntries] = useState<ArchiveEntry[] | null>(null)
  const [before, setBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  async function load(cursor: number | null) {
    setLoading(true)
    setError('')
    try {
      const page = await researchRequest<{ editions: ArchiveEntry[]; next_before: number | null }>(
        'source_editions',
        project,
        { source, ...(cursor ? { before: cursor } : {}) }
      )
      setEntries(previous => [...(cursor && previous ? previous : []), ...page.editions])
      setBefore(page.next_before)
    } catch (reason) {
      setError(String(reason))
    } finally {
      setLoading(false)
    }
  }
  return (
    <section aria-label="Archived source editions">
      {entries === null ? (
        <Button variant="outline" disabled={loading} onClick={() => void load(null)}>
          Show source editions
        </Button>
      ) : (
        <>
          <h4>Source editions</h4>
          {entries.map(item => (
            <button
              className="research-source"
              key={item.edition}
              aria-current={item.edition === selected ? 'true' : undefined}
              disabled={disabled}
              onClick={() => onRead(item.edition)}
            >
              <strong>
                {item.status} · Edition {item.edition}
              </strong>
              <small>First saved {item.created} UTC</small>
            </button>
          ))}
          {before !== null && (
            <Button variant="outline" disabled={loading} onClick={() => void load(before)}>
              Earlier editions
            </Button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}

export function Library({ project, close }: { project: string; close: () => void }) {
  const local = useStore($connection)?.mode === 'local'
  const [allowReimport, setAllowReimport] = useState(false)
  const [tableHint, setTableHint] = useState<TableHint>()
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const [rows, setRows] = useState<Source[]>([])
  const [page, setPage] = useState<SourcePage | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [capture, setCapture] = useState<{ source: number; edition: number; impact?: Impact } | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    let active = true
    researchRequest<{ sources: Source[] }>('search', project, { query: appliedQuery })
      .then(result => {
        if (active) {
          setRows(result.sources)
          setError('')
        }
      })
      .catch(reason => {
        if (active) {
          setError(String(reason))
          setRows([])
        }
      })
    return () => {
      active = false
    }
  }, [project, appliedQuery, revision])

  async function save(file?: File) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (file && (file.size > 1_000_000 || !/\.(txt|md|csv|tsv|json)$/i.test(file.name))) {
        throw new Error('Choose a text, Markdown, CSV, TSV or JSON file up to 1 MB.')
      }
      const fields = file
        ? await selectedFile(file, local && allowReimport)
        : { title, kind: url ? 'web' : 'note', origin: url, text }
      const result = await researchRequest<Source & { affected_briefs?: Impact; analysis_suggestion?: TableHint }>(
        file ? 'capture_selected_file' : 'capture',
        project,
        fields
      )
      setTableHint(result.analysis_suggestion)
      if (file) setAllowReimport(false)
      if (!file) {
        setTitle('')
        setText('')
        setUrl('')
      }
      setPage(null)
      setQuery('')
      setAppliedQuery('')
      setRevision(value => value + 1)
      setCapture({ source: result.source, edition: result.edition, impact: result.affected_briefs })
      setMessage(`Saved ${result.title} · edition ${result.edition}`)
    } catch (reason) {
      setError(String(reason))
    } finally {
      setBusy(false)
      if (fileInput.current) {
        fileInput.current.value = ''
      }
    }
  }

  async function read(source: number, edition: number, start = 0) {
    setBusy(true)
    setError('')
    try {
      const data = await researchRequest<SourcePage>('read', project, { source, edition, start })
      setPage(previous =>
        start && previous?.edition === edition ? { ...data, text: previous.text + data.text } : data
      )
    } catch (reason) {
      setError(String(reason))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="research-columns">
      <section
        aria-label="Capture source"
        onDragOver={event => event.preventDefault()}
        onDrop={event => {
          event.preventDefault()
          const file = event.dataTransfer.files[0]
          if (file && !busy) {
            void save(file)
          }
        }}
      >
        <h3>Capture a source</h3>
        <label>
          Source title
          <Input value={title} onChange={event => setTitle(event.target.value)} />
        </label>
        <label>
          Passage or note
          <Textarea value={text} onChange={event => setText(event.target.value)} />
        </label>
        <label>
          Original URL (optional)
          <Input type="url" value={url} onChange={event => setUrl(event.target.value)} />
        </label>
        <div className="research-actions">
          <Button disabled={busy || !title.trim() || !(text.trim() || url.trim())} onClick={() => void save()}>
            Save source
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => fileInput.current?.click()}>
            Import file
          </Button>
          <input
            hidden
            type="file"
            ref={fileInput}
            accept=".txt,.md,.csv,.tsv,.json"
            onChange={event => {
              const file = event.target.files?.[0]
              if (file) {
                void save(file)
              }
            }}
          />
        </div>
        {local && (
          <label>
            <input type="checkbox" checked={allowReimport} onChange={e => setAllowReimport(e.target.checked)} />
            Allow chat to reimport the next selected file
          </label>
        )}
        <LinkedFiles project={project} revision={revision} />
        {message && <p role="status">{message}</p>}
        <SourceImpact project={project} captured={capture} />
        {tableHint && <TableAssist key={tableHint.sha256 + tableHint.source} project={project} hint={tableHint} />}
      </section>
      <section aria-label="Saved sources">
        <h3>Saved sources</h3>
        <form
          className="research-actions"
          onSubmit={event => {
            event.preventDefault()
            setAppliedQuery(query)
            setPage(null)
          }}
        >
          <Input aria-label="Search saved sources" value={query} onChange={event => setQuery(event.target.value)} />
          <Button variant="outline">Search</Button>
        </form>
        {error && <p role="alert">{error}</p>}
        {!rows.length && !error && <p>No matching sources.</p>}
        {rows.map(row => (
          <button
            type="button"
            className="research-source"
            disabled={busy}
            key={row.source}
            data-passage-preview={row.matched_passage || row.excerpt_start != null ? 'expanded' : undefined}
            onClick={() => void read(row.source, row.edition)}
          >
            <strong>{row.title}</strong>
            <small>
              {row.shared_from ? 'Pinned from ' + row.shared_from : row.kind} · Edition {row.edition}
              {row.pin_status === 'origin uses a different edition' &&
                ` · Original project uses edition ${row.origin_current_edition}`}
            </small>
            <span>{row.excerpt}</span>
            {row.matched_passage && (
              <>
                <small>Matching passage · edition {row.edition}</small>
                <span>{row.matched_passage.text}</span>
              </>
            )}
            {row.matched_passage_deferred && <small>Open this edition to inspect another match.</small>}
          </button>
        ))}
        <Reporting key={project + appliedQuery} project={project} query={appliedQuery} />
        {page && (
          <article className="research-reading">
            <h4>{page.title}</h4>
            <small>
              Source {page.source} · Edition {page.edition} · {page.created} UTC
            </small>
            {page.file_proof && (
              <details>
                <summary>
                  {page.file_proof.status === 'verified at import'
                    ? 'File bytes verified at import'
                    : 'Archive has no original-file byte receipt'}
                </summary>
                <p>{page.file_proof.scope}</p>
                {page.file_proof.sha256 && (
                  <small>
                    {page.file_proof.bytes} bytes · SHA-256 <code>{page.file_proof.sha256}</code>
                  </small>
                )}
              </details>
            )}
            <ArchivedEditions
              key={project + ':' + page.source}
              project={project}
              source={page.source}
              selected={page.edition}
              disabled={busy}
              onRead={edition => void read(page.source, edition)}
            />
            <pre>{page.text || 'URL saved without a passage.'}</pre>
            {page.next_start !== null && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void read(page.source, page.edition, page.next_start!)}
              >
                Read more
              </Button>
            )}
            {page.kind !== 'note' && (
              <Button
                variant="outline"
                onClick={() => {
                  if (page.kind === 'web') {
                    void window.hermesDesktop.openExternal(page.origin)
                  } else {
                    const target = localPreviewTarget(page.origin)
                    if (target) {
                      close()
                      openPreview(target)
                    }
                  }
                }}
              >
                Open original
              </Button>
            )}
          </article>
        )}
      </section>
    </div>
  )
}
