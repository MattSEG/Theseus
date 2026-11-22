import { TrailPanel } from './trail'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { researchRequest } from './rpc'

interface Crawl {
  id: number
  question: string
  state: string
  pages: number
  seconds: number
  spent: number
  queue: { url: string; state: string; detail: string; edition?: number }[]
}
export function CrawlPanel({ project }: { project: string }) {
  const [url, setUrl] = useState('')
  const [question, setQuestion] = useState('')
  const [pages, setPages] = useState(5)
  const [depth, setDepth] = useState(1)
  const [seconds, setSeconds] = useState(30)
  const [list, setList] = useState<Pick<Crawl, 'id' | 'question' | 'state'>[]>([])
  const [job, setJob] = useState<Crawl | null>(null)
  const [driving, setDriving] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const current = useRef<number | null>(null)
  async function reload() {
    const data = await researchRequest<{ crawls: typeof list }>('crawl_list', project)
    setList(data.crawls)
  }
  useEffect(() => {
    void reload().catch(reason => setError(String(reason)))
    return () => {
      generation.current++
    }
  }, [project])
  async function drive(existing?: number) {
    const turn = ++generation.current
    setDriving(true)
    setError('')
    try {
      const id =
        existing ??
        (await researchRequest<Crawl>('crawl_create', project, { url, question, pages, depth, seconds }))
          .id
      if (turn !== generation.current) {
        return
      }
      current.current = id
      let next = await researchRequest<Crawl>('crawl_resume', project, { crawl: id })
      while (turn === generation.current && next.state === 'running') {
        setJob(next)
        next = await researchRequest<Crawl>('crawl_step', project, { crawl: id })
        if (turn === generation.current) {
          setJob(next)
        }
        if (next.state === 'running') {
          await new Promise(resolve => setTimeout(resolve, 450))
        }
      }
      if (turn === generation.current) {
        setJob(next)
        await reload()
      }
    } catch (reason) {
      if (turn === generation.current) {
        setError(String(reason))
      }
    } finally {
      if (turn === generation.current) {
        setDriving(false)
      }
    }
  }
  async function pause() {
    generation.current++
    setDriving(false)
    if (current.current !== null) {
      try {
        setJob(await researchRequest<Crawl>('crawl_pause', project, { crawl: current.current }))
        await reload()
      } catch (reason) {
        setError(String(reason))
      }
    }
  }
  async function select(id: number) {
    try {
      setJob(await researchRequest<Crawl>('crawl_status', project, { crawl: id }))
      setError('')
    } catch (reason) {
      setError(String(reason))
    }
  }
  return (
    <div className="research-columns">
      <section>
        <h3>Collect web pages</h3>
        <label>
          Research question
          <Input value={question} onChange={e => setQuestion(e.target.value)} />
        </label>
        <label>
          Starting URL
          <Input type="url" value={url} onChange={e => setUrl(e.target.value)} />
        </label>
        <div className="research-limits">
          <label>
            Pages
            <Input
              type="number"
              min={1}
              max={40}
              value={pages}
              onChange={e => setPages(Number(e.target.value))}
            />
          </label>
          <label>
            Link depth
            <Input
              type="number"
              min={0}
              max={3}
              value={depth}
              onChange={e => setDepth(Number(e.target.value))}
            />
          </label>
          <label>
            Seconds
            <Input
              type="number"
              min={1}
              max={300}
              value={seconds}
              onChange={e => setSeconds(Number(e.target.value))}
            />
          </label>
        </div>
        <Button disabled={driving || !question.trim() || !url.trim()} onClick={() => void drive()}>
          Start crawl
        </Button>
        {error && <p role="alert">{error}</p>}
      </section>
      <section>
        <h3>Crawl history</h3>
        {list.map(item => (
          <button
            className="research-source"
            key={item.id}
            disabled={driving}
            onClick={() => void select(item.id)}
          >
            {item.question} · {item.state}
          </button>
        ))}
        {job && (
          <article className="research-reading">
            <h4>{job.question}</h4>
            <p role="status">
              {job.state} ·{' '}
              {job.queue.filter(p => ['saved', 'failed', 'duplicate'].includes(p.state)).length} /{' '}
              {job.pages} pages · {Math.ceil(job.spent)} seconds used
            </p>
            {driving ? (
              <Button variant="outline" onClick={() => void pause()}>
                Stop crawl
              </Button>
            ) : (
              ['running', 'paused'].includes(job.state) && (
                <Button variant="outline" onClick={() => void drive(job.id)}>
                  Resume crawl
                </Button>
              )
            )}
            <TrailPanel
              key={job.id}
              project={project}
              id={job.id}
              progress={job.state + job.queue.filter(p => p.edition).length}
            />
            <ul>
              {job.queue.map(page => (
                <li key={page.url}>
                  <strong>{page.state}</strong> · {page.url}
                  {page.edition ? ` · edition ${page.edition}` : ''}
                  <br />
                  {page.detail}
                </li>
              ))}
            </ul>
          </article>
        )}
      </section>
    </div>
  )
}
