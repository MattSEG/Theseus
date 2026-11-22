import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'

export async function selectedFile(file: File, allow: boolean) {
  const bytes = await file.arrayBuffer()
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n =>
    n.toString(16).padStart(2, '0')
  ).join('')
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
  return { origin: window.hermesDesktop.getPathForFile(file), text, file_sha256: digest, allow_reimport: allow }
}
type Link = { link: number; source: number; edition: number; title: string; origin: string }
export function LinkedFiles({ project, revision }: { project: string; revision: number }) {
  const [files, setFiles] = useState<Link[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    researchRequest<{ files: Link[] }>('file_list', project)
      .then(r => {
        if (active) setFiles(r.files)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project, revision])
  async function revoke(file: Link) {
    setBusy(true)
    setError('')
    try {
      const r = await researchRequest<{ files: Link[] }>('file_revoke', project, {
        source: file.source,
        link: file.link
      })
      setFiles(r.files)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  if (!files.length && !error) return null
  return (
    <details>
      <summary>Files linked to this project ({files.length})</summary>
      {error && <p role="alert">{error}</p>}
      {files.map(f => (
        <article key={f.link}>
          <strong>{f.title}</strong>
          <p>{f.origin}</p>
          <button disabled={busy} onClick={() => void revoke(f)}>
            Remove link to {f.title}
          </button>
        </article>
      ))}
    </details>
  )
}
