import {request} from './api';
import type {ResearchProduct} from './research';

export interface ProjectCollection {project_id:string;version:number;selected_ids:string[];products:(ResearchProduct&{unavailable?:boolean})[];unavailable_ids:string[]}
export type CollectionOperation={type:'add';offer:string;source?:string}|{type:'remove';offer:string}|{type:'clear';version:number};
export const collectionApi={
  get:(project:string)=>request<ProjectCollection>(`/projects/${project}/collection`,{},true),
  mutate:(project:string,op:CollectionOperation)=>op.type==='add'
    ?request<ProjectCollection>(`/projects/${project}/collection/items`,{method:'POST',body:JSON.stringify({offer_id:op.offer,source_research_id:op.source})},true)
    :op.type==='remove'?request<ProjectCollection>(`/projects/${project}/collection/items/${encodeURIComponent(op.offer)}`,{method:'DELETE'},true)
    :request<ProjectCollection>(`/projects/${project}/collection/clear`,{method:'POST',body:JSON.stringify({version:op.version,confirmed:true})},true),
};

/** Pending operations are transport state. The server collection is authoritative. */
export class CollectionWriter {
  private token=0;private project='';private base:ProjectCollection|null=null;
  private pending:CollectionOperation[]=[];private running=false;private failure:unknown=null;
  private known=new Map<string,ResearchProduct>();private waiters:{resolve:(v:ProjectCollection)=>void;reject:(e:unknown)=>void}[]=[];
  private lastFailed:CollectionOperation|null=null;
  private loading:Promise<void>|null=null;
  constructor(private api:typeof collectionApi,private publish:(v:ProjectCollection|null,busy:boolean)=>void,private error:(e:unknown)=>void){}
  reset(project:string){this.token++;this.project=project;this.base=null;this.pending=[];this.running=false;this.loading=null;this.failure=null;this.lastFailed=null;this.known.clear();this.finish(new Error('Проект изменился'));this.publish(null,false);}
  adopt(value:ProjectCollection){if(value.project_id!==this.project||value.version<(this.base?.version??-1))return;this.base=value;for(const p of value.products)this.known.set(p.id,p);this.emit();}
  reload(){if(this.loading)return this.loading;const token=this.token;const pending=this.api.get(this.project).then(value=>{if(token===this.token)this.adopt(value);}).finally(()=>{if(this.loading===pending)this.loading=null;});this.loading=pending;return pending;}
  enqueue(op:CollectionOperation,product?:ResearchProduct){if(!this.base)return;this.failure=null;if(product)this.known.set(product.id,product);this.pending.push(op);this.emit();void this.drain();}
  retry(){if(this.lastFailed){const op=this.lastFailed;this.lastFailed=null;this.enqueue(op);}}
  get retryNeedsConfirmation(){return this.lastFailed?.type==='clear';}
  discardFailure(){if(!this.running&&!this.pending.length){this.failure=null;this.lastFailed=null;}}
  flush():Promise<ProjectCollection>{if(this.failure)return Promise.reject(this.failure);if(!this.pending.length&&!this.running&&this.base)return Promise.resolve(this.base);return new Promise((resolve,reject)=>this.waiters.push({resolve,reject}));}
  private finish(error?:unknown){for(const waiter of this.waiters.splice(0)){if(error)waiter.reject(error);else if(this.base)waiter.resolve(this.base);}}
  private emit(){if(!this.base){this.publish(null,false);return;}let ids=[...this.base.selected_ids];for(const op of this.pending){if(op.type==='clear')ids=[];else if(op.type==='remove')ids=ids.filter(id=>id!==op.offer);else if(!ids.includes(op.offer))ids.push(op.offer);}this.publish({...this.base,selected_ids:ids,products:ids.flatMap(id=>this.known.has(id)?[this.known.get(id)!]:[]),unavailable_ids:this.base.unavailable_ids.filter(id=>ids.includes(id))},!!this.pending.length||this.running);}
  private async drain(){if(this.running)return;this.running=true;const token=this.token,project=this.project;
    while(this.pending.length&&token===this.token){const op=this.pending[0];try{let value:ProjectCollection;try{value=await this.api.mutate(project,op);}catch(e){const failure=e as {status?:number;retryAfter?:number};if(op.type==='clear'||failure?.status!==429||!failure.retryAfter||failure.retryAfter>60)throw e;await new Promise(resolve=>setTimeout(resolve,failure.retryAfter!*1000));if(token!==this.token)return;value=await this.api.mutate(project,op);}if(token!==this.token)return;this.pending.shift();this.adopt(value);}catch(e){if(token!==this.token)return;this.pending.shift();this.failure=e;this.lastFailed=op;this.error(e);try{const value=await this.api.get(project);if(token===this.token)this.adopt(value);}catch{}this.emit();}}
    if(token!==this.token)return;this.running=false;this.emit();this.finish(this.failure??undefined);
  }
}
