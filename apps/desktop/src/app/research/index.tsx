import { useEffect, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import {
  BookOpen,
  Bookmark,
  ChartNoAxesCombined,
  ChevronDown,
  ClipboardCheck,
  Compass,
  FlaskConical,
  FolderOpen,
  Inbox,
  Library as LibraryIcon,
  Network,
  PackageOpen,
  Search,
  Workflow
} from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { $activeProfile } from '@/store/profile'
import { $activeConnectionId } from '@/store/connections'
import ship from '../../../assets/theseus/welcome.png'
import { researchRequest } from './rpc'
import { Analysis } from './analysis'
import { Attention } from './attention'
import { Assistance, ChatAssist } from './assistance'
import { BriefPanel } from './brief'
import { Collections } from './collections'
import { CrawlPanel } from './crawl'
import { Discovery } from './discovery'
import { Handoff } from './handoff'
import { Library } from './library'
import { Paired } from './paired'
import { Scenarios } from './scenarios'
import { Workflows } from './workflows'
import './research.css'

const sections = [
  {
    name: 'Sources',
    views: [
      { name: 'Library', icon: LibraryIcon },
      { name: 'Web crawl', icon: Network },
      { name: 'Discover', icon: Compass },
      { name: 'Collections', icon: Bookmark }
    ]
  },
  {
    name: 'Workspace',
    views: [
      { name: 'Briefs', icon: BookOpen },
      { name: 'Scenarios', icon: ChartNoAxesCombined },
      { name: 'Analysis', icon: FlaskConical },
      { name: 'Workflows', icon: Workflow }
    ]
  },
  {
    name: 'Review',
    views: [
      { name: 'Attention', icon: Inbox },
      { name: 'Assistance', icon: ClipboardCheck },
      { name: 'Trials', icon: ClipboardCheck },
      { name: 'Handoff', icon: PackageOpen }
    ]
  }
]
const knownViews = new Set(sections.flatMap(s => s.views.map(v => v.name)))
function remembered(key: string) {
  try {
    const s = JSON.parse(localStorage.getItem(key) || '{}')
    return {
      project:
        typeof s.project === 'string' && s.project.trim() && s.project.length <= 100
          ? s.project
          : 'Inbox',
      tab: knownViews.has(s.tab) ? String(s.tab) : 'Library'
    }
  } catch {
    return { project: 'Inbox', tab: 'Library' }
  }
}
export function ResearchWorkbench() {
  const profile = useStore($activeProfile),
    connection = useStore($activeConnectionId)
  const scope = 'theseus.research.workspace.' + JSON.stringify([connection, profile])
  return <ProfileWorkbench key={scope} scope={scope} />
}
function ProfileWorkbench({ scope }: { scope: string }) {
  const [saved] = useState(() => remembered(scope)),
    [tab, setTab] = useState(saved.tab),
    [project, setProject] = useState<string>(saved.project)
  const [open, setOpen] = useState(false),
    [switching, setSwitching] = useState(false),
    [draft, setDraft] = useState(''),
    [projects, setProjects] = useState<string[]>([]),
    [projectError, setProjectError] = useState('')
  const [assistId,setAssistId]=useState(0),[assistVersion,setAssistVersion]=useState(0)
  const main = useRef<HTMLElement>(null)
  useEffect(() => {
    try {
      localStorage.setItem(scope, JSON.stringify({ tab, project }))
    } catch {}
  }, [scope, tab, project])
  useEffect(() => {
    if (!open) return
    let active = true
    researchRequest<{ projects: string[] }>('projects', project)
      .then(r => {
        if (active) {
          setProjects(r.projects)
          setProjectError('')
        }
      })
      .catch(e => {
        if (active) setProjectError(String(e))
      })
    return () => {
      active = false
    }
  }, [open, project, switching])
  useEffect(() => {
    main.current?.scrollTo(0, 0)
  }, [project, tab])
  function select(p: string) {
    if (!p.trim()) return
    setAssistId(0)
    setProject(p.trim())
    setSwitching(false)
    setDraft('')
  }
  function show(on: boolean) {
    setOpen(on)
    if (!on) {
      setSwitching(false)
      setDraft('')
    }
  }
  const matches = projects.filter(p => p.toLowerCase().includes(draft.trim().toLowerCase()))
  return (
    <>
      <button type="button" className="research-launch" onClick={() => show(true)}>
        <Compass aria-hidden="true" />
        Research
      </button>
      <button
        type="button"
        className="research-launch"
        onClick={() => {
          setTab('Attention')
          show(true)
        }}
      >
        <Inbox aria-hidden="true" />
        Needs my attention
      </button>
      <ChatAssist refreshKey={assistVersion} onOpen={(project,id)=>{setProject(project);setTab('Assistance');setAssistId(id);setSwitching(false);show(true)}}/>
      <Dialog open={open} onOpenChange={show}>
        <DialogContent
          className="research-workbench"
          bodyClassName="research-shell"
          aria-describedby="research-description"
        >
          <DialogHeader className="research-masthead">
            <div className="research-signature">
              <img src={ship} alt="" />
              <DialogTitle>Theseus Research</DialogTitle>
            </div>
            <DialogDescription id="research-description" className="sr-only">
              Project sources, comparisons, investigations and planning.
            </DialogDescription>
            <button
              className="research-project-toggle"
              type="button"
              aria-expanded={switching}
              onClick={() => {
                setSwitching(!switching)
                setDraft('')
              }}
            >
              <FolderOpen aria-hidden="true" />
              Projects
              <ChevronDown aria-hidden="true" />
            </button>
          </DialogHeader>
          {switching && (
            <section className="research-picker" aria-label="Select a research project">
              <form
                onSubmit={e => {
                  e.preventDefault()
                  select(draft)
                }}
              >
                <label>
                  <Search aria-hidden="true" />
                  <Input
                    aria-label="Research project"
                    placeholder="Find or create a project"
                    autoFocus
                    maxLength={100}
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                  />
                </label>
                <button disabled={!draft.trim()}>Open project</button>
              </form>
              <div className="research-project-list">
                {matches.map(p => (
                  <button
                    type="button"
                    key={p}
                    aria-current={p === project ? 'true' : undefined}
                    onClick={() => select(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
              {projectError && <p role="alert">{projectError}</p>}
            </section>
          )}
          <div className="research-layout">
            <nav className="research-rail" aria-label="Research views">
              {sections.map(s => (
                <section key={s.name}>
                  <h3>{s.name}</h3>
                  {s.views.map(({ name, icon: Icon }) => (
                    <button
                      type="button"
                      key={name}
                      aria-pressed={tab === name}
                      onClick={() => {
                        setTab(name)
                        setSwitching(false)
                      }}
                    >
                      <Icon aria-hidden="true" />
                      {name}
                    </button>
                  ))}
                </section>
              ))}
            </nav>
            <main ref={main} className="research-main" aria-label={tab + ' in ' + project}>
              <div className="research-project-heading">
                <h2>{project}</h2>
              </div>
              {open && tab === 'Library' && (
                <Library key={project} project={project} close={() => show(false)} />
              )}
              {open && tab === 'Web crawl' && <CrawlPanel key={project} project={project} />}
              {open && tab === 'Briefs' && (
                <BriefPanel key={project} project={project} close={() => show(false)} />
              )}
              {open && tab === 'Scenarios' && <Scenarios key={project} project={project} />}
              {open && tab === 'Analysis' && <Analysis key={project} project={project} />}
              {open && tab === 'Workflows' && <Workflows key={project} project={project} />}
              {open && tab === 'Assistance' && <Assistance key={project} project={project} selected={assistId} onChanged={()=>setAssistVersion(v=>v+1)}/>}
              {open && tab === 'Attention' && <Attention key={project} project={project} />}
              {open && tab === 'Discover' && <Discovery key={project} project={project} />}
              {open && tab === 'Trials' && <Paired key={project} project={project} close={() => show(false)} />}
              {open && tab === 'Collections' && <Collections key={project} project={project} />}
              {open && tab === 'Handoff' && (
                <Handoff key={project} project={project} close={() => show(false)} />
              )}
            </main>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
