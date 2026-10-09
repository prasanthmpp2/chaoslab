import {Link,useParams} from 'react-router-dom';import {useCancelRun,useProbes,useRetryCleanup,useRun,useRunEvents} from '../api/hooks';import {Badge,Confirm,ErrorState,Loading,Table} from '../components/ui';
export default function RunDetail(){const {runId=''}=useParams();const r=useRun(runId),active=!!r.data&&['QUEUED','VALIDATING','RUNNING_BASELINE','INJECTING','OBSERVING','ABORTING','CLEANING_UP','VERIFYING_RECOVERY'].includes(r.data.status);
 const ev=useRunEvents(runId,active),pr=useProbes(runId),cancel=useCancelRun(),retry=useRetryCleanup();
 if(r.isLoading)return <Loading/>;if(r.isError)return <ErrorState e={r.error}/>;const x=r.data!;
 const name=x.definition_snapshot.metadata.name;
 const engine=x.definition_snapshot.spec.fault.engine;
 const target=x.definition_snapshot.spec.target.environment+':'+x.definition_snapshot.spec.target.service;
 return <><h2>{x.id}</h2><p className="sub">{name} · {engine} · {target} <button className="btn s" onClick={()=>navigator.clipboard.writeText(x.id)}>Copy ID</button></p>
 <div className="grid"><div className="card">Execution<br/><Badge v={x.status}/></div><div className="card">Resilience outcome<br/><Badge v={x.outcome}/></div><div className="card">Cleanup<br/><Badge v={x.cleanup_status} cleanup/></div></div>
 {x.cleanup_status==='FAILED'&&<div className="card warn"><b>Cleanup needs attention.</b> The fault may still be active on {target}. <Confirm title="Retry cleanup?" text="Requests fault removal again. Status stays pending until the backend verifies recovery." action="Retry cleanup" onYes={()=>retry.mutate(x.id)}>Retry cleanup</Confirm></div>}
 {active&&<p>Stage: {x.status} {x.status==='ABORTING'?<span className="b wn">Cancellation pending</span>:<Confirm title="Cancel this run?" text="The run keeps being tracked until cleanup is confirmed." action="Cancel run" onYes={()=>cancel.mutate(x.id)}>Cancel run</Confirm>}</p>}
 <div className="g2"><div className="card"><h3>Probes</h3>{pr.isError?<ErrorState e={pr.error}/>:pr.data?.length?<Table head={['Probe','Phase','Tolerance','Measurement','Result']} rows={pr.data.map(p=>[p.probe_name,p.phase,p.tolerance,p.measurement,<span className={`b ${p.status==='PASS'||p.status==='PASSED'?'ok':'er'}`}>{p.status}</span>])}/>:<p>No probe results yet.</p>}</div>
 <div className="card"><h3>Events</h3><div className="log">{ev.data?.map(e=>`${e.created_at} [${e.event_type}] ${e.message}`).join('\n')||'No events yet.'}</div></div></div><Link to="/runs">Back to history</Link></>}
