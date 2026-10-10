import {useState} from 'react';import {useForm} from 'react-hook-form';import {zodResolver} from '@hookform/resolvers/zod';import {z} from 'zod';import {useNavigate} from 'react-router-dom';
import {useCreateExperiment,useEngines,useEnvs,useStartRun,useValidate} from '../api/hooks';import {ErrorState,Loading} from '../components/ui';
export const schema=z.object({name:z.string().min(3,'Enter at least 3 characters'),description:z.string().optional(),environment_id:z.string().min(1,'Select an environment'),target:z.string().min(1,'Select a target'),engine:z.enum(['chaos-toolkit','pumba','toxiproxy','chaos-mesh','chaos_toolkit','chaos_mesh']),fault_type:z.string().min(1,'Select a fault'),duration_seconds:z.coerce.number().min(5,'Minimum 5 s').max(300,'Maximum 300 s'),max_error_rate:z.coerce.number().min(0,'0–100').max(100,'0–100'),parameters_json:z.string().refine(v=>{try{JSON.parse(v);return true}catch(e){return false}},'Must be valid JSON')});
type F=z.infer<typeof schema>;const STEPS=['Basics','Target','Engine','Fault','Hypothesis','Review'];
const FIELDS:(keyof F)[][]=[['name'],['environment_id','target'],['engine'],['fault_type','duration_seconds','parameters_json'],['max_error_rate'],[]];
const DEFAULT_PARAMS:Record<string,any>={
  'network-latency':{proxy:'payment-http',latencyMs:100,jitterMs:0},
  'bandwidth-limit':{proxy:'payment-http',rateKbps:1024},
  'connection-timeout':{proxy:'payment-http',timeoutMs:5000},
  'connection-reset':{proxy:'payment-http',timeoutMs:0},
  'data-limit':{proxy:'payment-http',bytes:1048576},
  'container-kill':{signal:'SIGKILL'},
  'container-stop':{},
  'container-pause':{},
  'network-delay':{timeMs:100,jitterMs:10,latencyMs:100,mode:'one'},
  'network-loss':{percent:10},
  'cpu-stress':{workers:1},
  'pod-kill':{mode:'one'},
  'native-experiment':{native:{title:'Steady State Test',description:'Validate resilience',method:[]}}
};
export default function NewExperiment(){const [s,setS]=useState(0);const eng=useEngines(),envs=useEnvs(),create=useCreateExperiment(),val=useValidate(),run=useStartRun(),nav=useNavigate();const [msg,setMsg]=useState('');
 const f=useForm<F>({resolver:zodResolver(schema),defaultValues:{engine:'toxiproxy',duration_seconds:60,max_error_rate:5,parameters_json:'{\n  "proxy": "payment-http",\n  "latencyMs": 100\n}'} as Partial<F> as F});const v=f.watch(),er=f.formState.errors;
 if(eng.isLoading||envs.isLoading)return <Loading/>;if(eng.isError||envs.isError)return <ErrorState e={eng.error??envs.error}/>;
 const env=envs.data!.find(e=>e.id===v.environment_id),cur=eng.data!.find(e=>e.name===v.engine||e.name.replace('_','-')===v.engine||e.name===v.engine?.replace('-','_'));
 const next=async()=>{if(await f.trigger(FIELDS[s]))setS(s+1)};
 const onFaultChange=(ft:string)=>{
   f.setValue('fault_type',ft);
   let p={...(DEFAULT_PARAMS[ft]||{})};
   if(v.engine==='pumba'&&ft==='network-delay'){p={timeMs:100,jitterMs:10}}
   else if((v.engine==='chaos_mesh'||v.engine==='chaos-mesh')&&ft==='network-delay'){p={latencyMs:100,jitterMs:10,mode:'one'}}
   f.setValue('parameters_json',JSON.stringify(p,null,2));
 };
 const save=async(d:F,andRun:boolean)=>{
   setMsg('');
   try{
     const slug=d.name.toLowerCase().trim().replace(/[^a-z0-9-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,63)||'experiment';
     const payload = {
       apiVersion: "chaos.example.io/v1",
       kind: "Experiment",
       metadata: { name: slug, description: d.description || "", version: "1", tags: [] },
       spec: {
         target: { environment: d.environment_id, service: d.target },
         fault: { engine: d.engine, type: d.fault_type, parameters: JSON.parse(d.parameters_json), durationSeconds: d.duration_seconds },
         hypothesis: { maxErrorRate: d.max_error_rate > 1 ? d.max_error_rate / 100 : d.max_error_rate, requireRecovery: true },
         safety: { environmentAllowlist: [d.environment_id], maxDurationSeconds: Math.max(300, d.duration_seconds) },
         cleanup: { removeFaults: true, verifyRecovery: true },
         probes: [],
         limits: { runTimeoutSeconds: 300 }
       }
     };
     const e=await create.mutateAsync(payload as any);
     const r=await val.mutateAsync(e.id);
     if(!r.valid)return setMsg('Validation failed: '+(r.errors??[]).join(', '));
     if(!andRun)return nav('/experiments');
     if(confirm(`Run now? This injects ${d.fault_type} into ${d.target} for ${d.duration_seconds} s.`)){
       const x=await run.mutateAsync(e.id);
       nav(`/runs/${x.run_id}`);
     }
   }catch(e){setMsg((e as Error).message)}
 };
 const E=({k}:{k:keyof F})=>er[k]?<div className="err" role="alert">{er[k]?.message as string}</div>:null;
 return <><h2>Create Experiment</h2><div className="steps">{STEPS.map((n,i)=><span key={n} className={i===s?'on':''} aria-current={i===s}>{n}</span>)}</div>
 <form className="card" onSubmit={f.handleSubmit(d=>save(d,false))}>
 {s===0&&<><label htmlFor="n">Experiment name</label><input id="n" {...f.register('name')}/><E k="name"/><label htmlFor="d">Description</label><input id="d" {...f.register('description')}/></>}
 {s===1&&<><label htmlFor="e">Environment</label><select id="e" {...f.register('environment_id')}><option value="">Select an environment</option>{envs.data!.map(e=><option key={e.id} value={e.id}>{e.name} ({e.runtime})</option>)}</select><E k="environment_id"/><label htmlFor="t">Target</label><select id="t" {...f.register('target')}><option value="">Select a target</option>{env?.targets.map(t=><option key={t}>{t}</option>)}</select><E k="target"/></>}
 {s===2&&<div className="g2">{eng.data!.map(e=>{const isConfigured=e.configured??(e as any).enabled??false;const isReachable=e.reachable??(e as any).available??false;const allowedEngines=(env?.engines??[]).flatMap(n=>[n,n.replace('_','-'),n.replace('-','_')]);const ok=isConfigured&&isReachable&&(!env||allowedEngines.includes(e.name));const caps=e.capabilities??[];return <button type="button" key={e.name} disabled={!ok} className={`eng ${v.engine===e.name||v.engine===e.name.replace('_','-')?'sel':''}`} onClick={()=>{f.setValue('engine',e.name as any);if(e.capabilities?.[0])onFaultChange(e.capabilities[0])}}><b>{e.name}</b><div className="mu">{ok?`Capabilities: ${caps.join(', ')}`:isConfigured?'Not reachable or not enabled for this environment.':'Not configured for this environment.'}</div></button>})}</div>}
 {s===3&&<><label htmlFor="ft">Fault type</label><select id="ft" value={v.fault_type||''} onChange={e=>onFaultChange(e.target.value)}><option value="">Select a fault</option>{cur?.capabilities?.map(c=><option key={c} value={c}>{c}</option>)}</select><E k="fault_type"/><label htmlFor="du">Duration (seconds)</label><input id="du" type="number" {...f.register('duration_seconds')}/><E k="duration_seconds"/><label htmlFor="pj">Parameters (JSON)</label><textarea id="pj" rows={4} {...f.register('parameters_json')}/><E k="parameters_json"/></>}
 {s===4&&<><label htmlFor="m">Maximum error rate during fault (%)</label><input id="m" type="number" {...f.register('max_error_rate')}/><E k="max_error_rate"/><p className="mu">A passing health endpoint alone does not prove business correctness. The backend enforces safety policies again at run time.</p></>}
 {s===5&&<dl><dt>Name</dt><dd>{v.name}</dd><dt>Target (blast radius: 1 target)</dt><dd>{v.target} in {env?.name}</dd><dt>Fault</dt><dd>{v.engine}: {v.fault_type} for {v.duration_seconds} s</dd><dt>Hypothesis</dt><dd>Error rate ≤ {v.max_error_rate}%, recovery verified after fault removal</dd></dl>}
 {msg&&<div className="err" role="alert">{msg}</div>}<br/>
 <button type="button" className="btn s" disabled={!s} onClick={()=>setS(s-1)}>Back</button> {s<5?<button type="button" className="btn" onClick={next}>Next</button>:<><button className="btn s" disabled={create.isPending}>Save and validate</button> <button type="button" className="btn" disabled={create.isPending||run.isPending} onClick={f.handleSubmit(d=>save(d,true))}>Save and run</button></>}</form></>}
