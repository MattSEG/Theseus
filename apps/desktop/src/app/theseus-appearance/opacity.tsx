import {useEffect,useRef,useState} from 'react'
export function WindowOpacity(){
 const [state,setState]=useState<{percent:number|null;actual:number}|null>(null),[error,setError]=useState('')
 const generation=useRef(0),api=window.hermesDesktop.windowOpacity
 useEffect(()=>{let active=true;if(api && window.hermesDesktop.translucencySupported) api({action:'get'}).then(s=>{if(active)setState(s)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[api])
 async function save(percent:number|null){if(!api)return;const ticket=++generation.current;setState(s=>s?{...s,percent}:s);try{const s=await api({action:'set',percent});if(ticket===generation.current){setState(s);setError('')}}catch(e){if(ticket===generation.current)setError(String(e))}}
 if(!state&&!error)return null
 return <section className="my-3 grid gap-2 border-b border-(--stroke-nous) pb-4"><label htmlFor="theseus-opacity" className="font-semibold">Window opacity</label>
 {state&&<div className="flex items-center gap-3"><input className="min-w-20 flex-1" id="theseus-opacity" type="range" min={30} max={100} value={state.percent??state.actual} onChange={e=>void save(Number(e.target.value))}/><output className="tabular-nums">{state.actual}%</output><button disabled={state.percent===null} onClick={()=>void save(null)}>Use theme default</button></div>}{error&&<p role="alert">{error}</p>}</section>
}
