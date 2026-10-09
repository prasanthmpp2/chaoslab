const BASE=(import.meta.env.VITE_API_BASE_URL as string)||'http://localhost:8000';
export class ApiError extends Error{constructor(public status:number,msg:string,public requestId?:string){super(msg)}}
const MSG:Record<number,string>={401:'Your session has expired or credentials are missing.',403:'You do not have permission to do this.',404:'Not found.',409:'This conflicts with the current state.',422:'The request was not valid.',429:'Too many requests. Try again shortly.',500:'The server hit an error.'};
export async function api<T>(path:string,init:RequestInit&{json?:unknown}={}):Promise<T>{
 const ctl=new AbortController();const t=setTimeout(()=>ctl.abort(),15000);
 init.signal?.addEventListener('abort',()=>ctl.abort());
 const tok=import.meta.env.VITE_API_TOKEN as string|undefined;
 try{const r=await fetch(BASE+path,{...init,signal:ctl.signal,headers:{'Content-Type':'application/json',...(tok?{"X-API-Key": tok}:{}),...init.headers},body:init.json!==undefined?JSON.stringify(init.json):init.body});
  if(!r.ok){let d='';try{const b=await r.json();d=typeof b.detail==='string'?b.detail:b.message??''}catch{}
   throw new ApiError(r.status,d||MSG[r.status]||`Request failed (${r.status})`,r.headers.get('x-request-id')??undefined)}
  return r.status===204?(undefined as T):await r.json()}
 catch(e){if(e instanceof ApiError)throw e;throw new ApiError(0,'Backend unavailable. Check VITE_API_BASE_URL and CORS settings.')}
 finally{clearTimeout(t)}}
export const apiBase=BASE;
