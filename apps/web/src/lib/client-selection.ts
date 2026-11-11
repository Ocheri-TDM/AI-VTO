import {offerRequest,OfferError} from './commercial-offers';

export interface VisitorSelection {scope:string;initialized:boolean;version:number;published_revision_id:string;csrf_token:string|null;selected_items:{item_id:string;published_revision_id:string}[];previous_item_count:number;commit_observed_at?:string}
export interface ChoiceMutation {item_id:string;selected:boolean;published_revision_id:string;mutation_id:string;version:number}
export const clientSelectionApi={
  get:(token:string)=>offerRequest<VisitorSelection>(`/api/public/offers/${encodeURIComponent(token)}/selection`),
  start:(token:string,revision:string)=>offerRequest<VisitorSelection>(`/api/public/offers/${encodeURIComponent(token)}/selection/session`,'POST',{published_revision_id:revision},{'X-Offer-Selection':'1'}),
  mutate:(token:string,body:ChoiceMutation,csrf:string)=>offerRequest<VisitorSelection>(`/api/public/offers/${encodeURIComponent(token)}/selection`,'PUT',body,{'X-Offer-Selection':'1','X-Offer-CSRF':csrf}),
};
export interface SelectionView {state:VisitorSelection|null;pending_ids:string[];status:'loading'|'saving'|'saved'|'error';error:string;blocked:boolean}
type Intent={item_id:string;selected:boolean;mutation_id:string};
type SessionLock=<T>(scope:string,call:()=>Promise<T>)=>Promise<T>;
export const visitorSessionLock:SessionLock=async(scope,call)=>{
  if(!navigator.locks)throw new Error('Обновите браузер, чтобы безопасно сохранить выбор.');
  return navigator.locks.request('offer-visitor-session:'+scope,call);
};

/** A serialized per-line writer, not a replace of a possibly stale array. */
export class ClientSelectionWriter {
  private base:VisitorSelection|null=null;
  private pending:Intent[]=[];private running=false;private disposed=false;private error='';private blocked=false;private loading:Promise<void>|null=null;
  constructor(private token:string,private revision:string,private api:typeof clientSelectionApi,private publish:(value:SelectionView)=>void,private confirmed:(value:VisitorSelection)=>void=()=>{},private sessionLock:SessionLock=visitorSessionLock){}
  private adopt(value:VisitorSelection){
    if(value.published_revision_id!==this.revision)throw new OfferError(409,'REVISION_CHANGED','Предложение обновилось. Обновите его перед выбором. Неподтверждённые отметки не применены к новым условиям.');
    if(this.base?.csrf_token===value.csrf_token&&value.version<this.base.version)return;
    this.base=value;
  }
  private emit(){
    if(this.disposed)return;
    const values=new Map(this.base?.selected_items.map(i=>[i.item_id,i])??[]);
    for(const intent of this.pending){if(intent.selected)values.set(intent.item_id,{item_id:intent.item_id,published_revision_id:this.revision});else values.delete(intent.item_id);}
    this.publish({state:this.base?{...this.base,selected_items:[...values.values()]}:null,pending_ids:[...new Set(this.pending.map(i=>i.item_id))],status:this.error?'error':this.pending.length?'saving':this.base?'saved':'loading',error:this.error,blocked:this.blocked});
  }
  private fail(e:unknown){this.error=e instanceof Error?e.message:'Не удалось сохранить. Повторите попытку.';this.blocked=e instanceof OfferError&&['REVISION_CHANGED','OFFER_LINK_EXPIRED','OFFER_NOT_FOUND','ITEM_NOT_PUBLISHED'].includes(e.code);this.emit();}
  load(){
    if(this.loading)return this.loading;
    const previous=this.base;
    const loading=this.api.get(this.token).then(value=>{if(this.disposed)return;if(this.base!==previous&&value.csrf_token!==this.base?.csrf_token)return;this.adopt(value);if(!this.pending.length)this.error='';this.emit();}).catch(e=>{if(!this.disposed)this.fail(e);}).finally(()=>{if(this.loading===loading)this.loading=null;});
    this.loading=loading;return loading;
  }
  set(item_id:string,selected:boolean){if(this.disposed||this.blocked)return;this.error='';this.pending.push({item_id,selected,mutation_id:crypto.randomUUID()});this.emit();void this.drain();}
  retry(){if(this.disposed||this.blocked)return;this.error='';this.emit();if(this.pending.length)void this.load().then(()=>{if(!this.error)void this.drain();});else void this.load();}
  dispose(){this.disposed=true;this.pending=[];}
  private async ensureSession(){
    if(this.base?.initialized)return;
    if(!this.base)await this.load();
    if(!this.base)throw new Error('Не удалось загрузить выбор. Повторите попытку.');
    const value=await this.sessionLock(this.base.scope,async()=>{
      // Re-read after acquiring the cross-tab lock: another tab may have set the
      // HttpOnly cookie while this one was waiting. No browser secret is stored in JS storage.
      const current=await this.api.get(this.token);
      return current.initialized?current:this.api.start(this.token,this.revision);
    });
    if(!this.disposed)this.adopt(value);
  }
  private async drain(){
    if(this.running||this.disposed||this.blocked)return;this.running=true;
    let conflicts=0;
    try{
      while(this.pending.length&&!this.disposed){
        await this.ensureSession();if(this.disposed||!this.base)return;
        const intent=this.pending[0],identity=this.base.csrf_token;
        try{
          const value=await this.api.mutate(this.token,{...intent,published_revision_id:this.revision,version:this.base.version},identity!);
          if(this.disposed)return;
          this.pending.shift();conflicts=0;
          if(this.base.csrf_token===identity)this.adopt(value);
          this.error='';this.emit();this.confirmed(value);
        }catch(e){
          if(e instanceof OfferError&&e.code==='SELECTION_CONFLICT'&&++conflicts<=3){
            this.adopt(await this.api.get(this.token));
            this.pending=this.pending.filter((value,index,all)=>!all.slice(index+1).some(later=>later.item_id===value.item_id));
            this.emit();continue;
          }
          throw e;
        }
      }
    }catch(e){if(!this.disposed)this.fail(e);}finally{this.running=false;}
  }
}
