import {useRef,type ReactNode} from 'react';import type {ApiError} from '../api/client';
const L:Record<string,[string,string]>={SUCCEEDED:['Succeeded','ok'],RUNNING_BASELINE:['Running baseline','run'],INJECTING:['Injecting','run'],OBSERVING:['Observing','run'],CLEANING_UP:['Cleaning up','run'],VERIFYING_RECOVERY:['Verifying recovery','run'],QUEUED:['Queued','mu'],VALIDATING:['Validating','run'],ABORTING:['Aborting','wn'],ABORTED:['Aborted','wn'],FAILED:['Failed','er'],PASSED:['Passed','ok'],FAILED_HYPOTHESIS:['Hypothesis failed','er'],INCONCLUSIVE:['Inconclusive','wn'],DRY_RUN:['Dry run','mu'],ERROR:['Error','er'],PENDING:['Pending','mu'],PENDING_APPROVAL:['Ready to start','wn'],TEARING_DOWN:['Tearing down','run'],TORN_DOWN:['Torn down','mu'],TEARDOWN_FAILED:['Teardown failed','er'],CLEANUP_FAILED:['Cleanup failed','er'],NOT_STARTED:['Not started','mu'],NOT_REQUIRED:['Not required','mu'],ACTIVE:['Active','wn'],REMOVED:['Removed','ok'],REMOVAL_FAILED:['Removal failed','er'],UNVERIFIED:['Unverified','wn'],NOT_SCORED:['Not scored','mu'],SCORED:['Scored','ok']};
export const Badge=({v,cleanup}:{v:string;cleanup?:boolean})=>{const [t,c]=v==='FAILED'&&cleanup?['Cleanup failed','er']:L[v]??[v,'mu'];return <span className={`b ${c}`}>{t}</span>};
export const Loading=()=><div className="card" role="status">Loading…</div>;
export const ErrorState=({e}:{e:unknown})=><div className="card warn" role="alert"><b>Something went wrong</b><p>{(e as ApiError)?.message??'Unknown error'}</p></div>;
export const Empty=({children}:{children:ReactNode})=><div className="card">{children}</div>;
export const Table=({head,rows}:{head:string[];rows:ReactNode[][]})=><div className="tw"><table><thead><tr>{head.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{r.map((c,j)=><td key={j}>{c}</td>)}</tr>)}</tbody></table></div>;
export function RecoveryScorecard({card}:{card:import('../api/types').RecoveryScorecard|null|undefined}){
 if(!card)return <div className="card"><h3>Recovery scorecard</h3><Badge v="NOT_SCORED"/><p className="mu">Score unavailable. This run has no persisted scorecard evidence.</p></div>;
 const names:[string,string][]=[['fault_impact','Fault impact'],['recovery_speed','Recovery speed'],['cleanup','Confirmed cleanup']];
 const formatRate=(v:number|undefined|null)=>v==null?'Not measured':`${(v*100).toFixed(1)}%`;
 return <section className="card scorecard"><div className="score-head"><div><h3>Recovery scorecard</h3><p className="mu">Version {card.version} · comparative score, separate from the resilience verdict</p></div><div className="score-total"><Badge v={card.status}/><strong>{card.score==null?'Score unavailable':`${card.score.toFixed(1)} / 100`}</strong></div></div>
  <div className="score-parts">{names.map(([key,label])=>{const c=card.components?.[key];return <div className="score-part" key={key}><b>{label}</b><strong>{c?.score==null?'—':`${c.score.toFixed(1)} / ${c.weight}`}</strong>
    {key==='fault_impact'&&<small>Observed errors {formatRate(c?.measured_error_rate)} · tolerance {formatRate(c?.max_error_rate)} · {c?.samples??0} samples</small>}
    {key==='recovery_speed'&&<small>Recovery {c?.duration_seconds==null?'Not measured':`${c.duration_seconds.toFixed(1)} s`} of {c?.deadline_seconds??'—'} s · errors {formatRate(c?.error_rate)}</small>}
    {key==='cleanup'&&<small>Cleanup {c?.status??'Not confirmed'}</small>}
  </div>})}</div>
  {!!card.reasons?.length&&<ul className="mu">{card.reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul>}
 </section>
}
export function Confirm({title,text,action,onYes,children}:{title:string;text:string;action:string;onYes:()=>void;children:string}){const d=useRef<HTMLDialogElement>(null);
 return <><button className="btn d" onClick={()=>d.current?.showModal()}>{children}</button><dialog ref={d} onClose={()=>d.current?.returnValue==='yes'&&onYes()}><form method="dialog"><h3>{title}</h3><p>{text}</p><button className="btn s" value="no">Cancel</button> <button className="btn d" value="yes">{action}</button></form></dialog></>}
