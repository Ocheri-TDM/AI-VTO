import type { Research, ResearchFilters } from './research';

export type ViewMutation = {filters?:ResearchFilters;selected_ids?:string[];sorting?:string;restore?:'undo'|'reset'};

export function draftResearch(base:Research, draft:ViewMutation, previous:Research=base):Research {
  const ids=draft.selected_ids??base.view.selected_ids;
  const known=new Map([...previous.products,...previous.selected_products,...base.products,...base.selected_products].map(p=>[p.id,p]));
  return {...base,view:{...base.view,...(draft.filters?{filters:draft.filters}:{}),...(draft.sorting?{sorting:draft.sorting}:{}),selected_ids:ids},
    selected_count:ids.length,selected_products:ids.flatMap(id=>known.has(id)?[known.get(id)!]:[])};
}

/** Serializes writes to the existing server view. There is one selection state;
 * pending edits are a coalesced transport draft, never another saved selection. */
export class ResearchViewWriter {
  private epoch=0;
  private base:Research|null=null;
  private pending:ViewMutation|null=null;
  private sending:ViewMutation|null=null;
  saving=false;
  constructor(private save:(id:string,body:object)=>Promise<Research>,private reload:(id:string)=>Promise<Research>,
    private publish:(value:Research,pending:ViewMutation|null,sent:ViewMutation)=>void,
    private busy:(value:boolean)=>void,private error:(value:unknown)=>void) {}
  reset(value:Research|null){this.epoch+=1;this.pending=null;this.sending=null;this.base=value;}
  enqueue(change:ViewMutation){if(!this.pending&&this.sending&&JSON.stringify(change)===JSON.stringify(this.sending))return;this.pending={...this.pending,...change};void this.flush();}
  private async flush(){
    if(this.saving||!this.base)return;
    this.saving=true;this.busy(true);
    let conflicts=0;
    try{
      while(this.pending&&this.base){
        const sent=this.pending,base=this.base,epoch=this.epoch;
        this.sending=sent;
        this.pending=null;
        try{
          const value=await this.save(base.id,{version:base.view.version,...sent});
          if(epoch!==this.epoch)continue;
          this.base=value;conflicts=0;this.publish(value,this.pending,sent);
        }catch(error){
          if(epoch!==this.epoch)continue;
          if((error as {status?:number}).status===409&&conflicts++<1){
            let fresh:Research;
            try{fresh=await this.reload(base.id);}catch(reloadError){if(epoch===this.epoch){this.error(reloadError);this.publish(base,this.pending,{});}continue;}
            if(epoch!==this.epoch)continue;
            this.base=fresh;this.pending={...sent,...(this.pending as ViewMutation|null??{})};continue;
          }
          this.error(error);
          try{const fresh=await this.reload(base.id);if(epoch===this.epoch){this.base=fresh;this.publish(fresh,this.pending,{});}}catch{if(epoch===this.epoch)this.publish(base,this.pending,{});}
        }
      }
    }finally{this.sending=null;this.saving=false;this.busy(false);if(this.pending&&this.base)void this.flush();}
  }
}
