import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {api} from './client';import type * as T from './types';
const V='/api/v1';const ACTIVE=['QUEUED','VALIDATING','RUNNING_BASELINE','INJECTING','OBSERVING','ABORTING','CLEANING_UP','VERIFYING_RECOVERY'];
export const useEngines=()=>useQuery({queryKey:['engines'],queryFn:({signal})=>api<T.Engine[]>(`${V}/engines`,{signal})});
export const useEnvs=()=>useQuery({queryKey:['envs'],queryFn:({signal})=>api<T.Environment[]>(`${V}/environments`,{signal})});
export const useExperiments=(q='')=>useQuery({queryKey:['exps',q],queryFn:({signal})=>api<T.Experiment[]>(`${V}/experiments?q=${encodeURIComponent(q)}`,{signal})});
export const useRuns=(qs='')=>useQuery({queryKey:['runs',qs],queryFn:({signal})=>api<T.Run[]>(`${V}/runs?${qs}`,{signal}),refetchInterval:qs.includes('active')?3000:false});
export const useRun=(id:string)=>useQuery({queryKey:['run',id],queryFn:({signal})=>api<T.Run>(`${V}/runs/${id}`,{signal}),refetchInterval:q=>q.state.data&&ACTIVE.includes(q.state.data.status)?3000:false});
export const useRunEvents=(id:string,on:boolean)=>useQuery({queryKey:['events',id],queryFn:({signal})=>api<T.RunEvent[]>(`${V}/runs/${id}/events`,{signal}),refetchInterval:on?3000:false});
export const useProbes=(id:string)=>useQuery({queryKey:['probes',id],queryFn:({signal})=>api<T.ProbeResult[]>(`${V}/runs/${id}/probes`,{signal})});
export const useActiveFaults=()=>useQuery({queryKey:['faults'],queryFn:({signal})=>api<T.Fault[]>(`${V}/faults/active`,{signal}),refetchInterval:10000});
export function useCreateExperiment(){const qc=useQueryClient();return useMutation({mutationFn:(b:T.ExperimentCreateRequest)=>api<T.Experiment>(`${V}/experiments`,{method:'POST',json:b}),onSuccess:()=>qc.invalidateQueries({queryKey:['exps']})})}
export const useValidate=()=>useMutation({mutationFn:(id:string)=>api<{valid:boolean;errors?:string[];blast_radius?:string}>(`${V}/experiments/${id}/validate`,{method:'POST'})});
export function useStartRun(){const qc=useQueryClient();return useMutation({mutationFn:(id:string)=>api<T.Run>(`${V}/experiments/${id}/runs`,{method:'POST'}),onSuccess:()=>qc.invalidateQueries({queryKey:['runs']})})}
export function useCancelRun(){const qc=useQueryClient();return useMutation({mutationFn:(id:string)=>api<T.Run>(`${V}/runs/${id}/cancel`,{method:'POST'}),onSuccess:(_,id)=>qc.invalidateQueries({queryKey:['run',id]})})}
export function useRetryCleanup(){const qc=useQueryClient();return useMutation({mutationFn:(id:string)=>api<T.Run>(`${V}/runs/${id}/retry-cleanup`,{method:'POST'}),onSuccess:()=>{qc.invalidateQueries({queryKey:['faults']});qc.invalidateQueries({queryKey:['run']})}})}
