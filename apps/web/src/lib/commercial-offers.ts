import type {ReactNode} from 'react';

export interface OfferImage {id:string;url:string}
export interface ProjectOfferRef {id:string;title:string;version:number;is_primary?:boolean}
export interface OfferComposition {version:number;collection_version:number;added:{offer_id:string;title:string}[];removed:{offer_id:string;title:string}[];retained:number;excluded_retained:number}
export interface ClientOfferItem {id:string;title:string;sku:string;brand:string|null;category:string;description:string;characteristics:{name:string;value:string}[];unit_price:string|null;quantity:number;line_amount:string|null;availability:string;images:OfferImage[];main_image:string|null}
export interface ClientOffer {schema_version:number;title:string;company:string;logo_url:string;vto_logo_url?:string;logo_background?:string;banner_url:string|null;items:ClientOfferItem[];revision_id?:string;snapshot_hash?:string;pdf_url?:string;xlsx_url?:string}
export interface OfferItem extends ClientOfferItem {offer_id:string;catalog_price:string|null;catalog_sku:string;price_source:string;quantity_source:string;quantity_context:{rule:string;quantity:number;research_id?:string;view_version?:number};sku_source:string;sku_note:string;warnings:string[];photos:{id:string;source_url:string}[]}
export interface OfferForm {version:number;title:string;company:string;use_logo:boolean;use_banner:boolean;items:{id:string;title:string;sku:string;sku_note:string;unit_price:string|null;quantity:number;main_image:string|null}[]}
export interface OwnerStock {source:string;quantity:number|null;observed_at:string|null;incoming_quantity:number|null;label:string;snapshot_quantity:number|null;snapshot_observed_at:string|null}
export interface OfferDocument {owner_stock:Record<string,OwnerStock>;id:string;project_id:string;version:number;kind:string;is_primary:boolean;primary_offer_id:string|null;draft:{settings:{title:string;client:string;use_logo:boolean;use_banner:boolean;logo:string|null};items:OfferItem[];materials:{id:string;status:string;role:string;error?:string}[];last_catalog_changes?:{item_id:string;field:string;before:unknown;after:unknown}[]};preview:ClientOffer;issues:{item_id:string;code:string;message:string}[];materials_preparing:boolean;share:{active:boolean;epoch:number;public_url:string|null;local_preview_path:string|null;published_revision_id:string|null;pending_revision_id:string|null;activated_at:string|null;expires_at:string|null;expired:boolean;state:string;lifetime_days:number};revisions:{id:string;source_version:number;status:string;error:string|null;request_id:string;publication_requested:boolean;publication_epoch?:number|null;snapshot_hash:string;pdf_url:string|null;xlsx_url:string|null;warnings:{item_id:string;message:string}[]}[]}
export type OfferEditSlot=(item:ClientOfferItem,index:number)=>ReactNode;

export class OfferError extends Error {constructor(public status:number,public code:string,message:string){super(message);}}

async function response<T>(r:Response):Promise<T>{
  if(!r.ok){const body=await r.json().catch(()=>null);const d=body?.detail;throw new OfferError(r.status,typeof d==='object'&&!Array.isArray(d)?d?.code??'':'',typeof d==='string'?d:typeof d?.message==='string'?d.message:r.status===404?'Предложение недоступно. Ссылка неверна или отозвана.':r.status===401?'Необходимо войти в аккаунт.':'Проверьте заполненные поля или повторите действие позже.');}
  return r.json();
}
async function request<T>(path:string,method='GET',body?:unknown,headers:Record<string,string>={}):Promise<T>{return response<T>(await fetch(path,{method,body:body===undefined?undefined:JSON.stringify(body),headers:{'Content-Type':'application/json',...headers},cache:'no-store',credentials:'include',referrerPolicy:'no-referrer'}));}
export {request as offerRequest};
export interface OfferSummary {id:string;version:number;title:string;project_id:string;project_name:string;chat_id:string;item_count:number;updated_at:string;is_primary:boolean;draft_state:string;publication_state:string;expires_at:string|null;can_copy:boolean;pdf_url:string|null;xlsx_url:string|null}
export const offerApi={
  delete:(id:string,version:number)=>request<{id:string;deleted:boolean;generation:number}>(`/api/offers/${id}`,'DELETE',{version,confirmed:true}),
  state:(project:string)=>request<{offer:ProjectOfferRef|null;generation:number}>(`/api/projects/${project}/offer-state`),
  all:(cursor?:string,archive=false)=>request<{items:OfferSummary[];next_cursor:string|null}>(`/api/${archive?'offers/archive':'offers'}?${new URLSearchParams(cursor?{cursor}:{})}`),
  link:(id:string)=>request<{public_url:string|null;active:boolean}>(`/api/offers/${id}/link`),
  primary:(project:string)=>request<ProjectOfferRef|null>(`/api/projects/${project}/offer`),
  composition:(id:string)=>request<OfferComposition>(`/api/offers/${id}/composition`),
  updateComposition:(id:string,version:number,collection_version:number)=>request<OfferDocument>(`/api/offers/${id}/composition`,'POST',{version,collection_version,confirmed:true}),
  list:(project:string)=>request<ProjectOfferRef[]>(`/api/projects/${project}/offers`),
  create:(project:string,version:number,offer_generation=0)=>request<OfferDocument>(`/api/projects/${project}/offers`,'POST',{collection_version:version,offer_generation}),
  get:(id:string)=>request<OfferDocument>(`/api/offers/${id}`),
  save:(id:string,body:OfferForm)=>request<OfferDocument>(`/api/offers/${id}`,'PATCH',body),
  action:(id:string,action:'publish'|'exports'|'refresh',version:number,request_id:string,share_epoch:number)=>request<OfferDocument>(`/api/offers/${id}/${action}`,'POST',{version,request_id,share_epoch}),
  share:(id:string,action:'revoke'|'rotate',version:number,epoch:number)=>request<OfferDocument>(`/api/offers/${id}/${action}`,'POST',{version,epoch}),
  upload:async(id:string,kind:'logo'|'banner',version:number,file:File)=>response<OfferDocument>(await fetch(`/api/offers/${id}/${kind}?version=${version}`,{method:'POST',body:file,headers:{'Content-Type':file.type||'application/octet-stream'},credentials:'include',cache:'no-store',referrerPolicy:'no-referrer'})),
  published:async(key:string)=>response<ClientOffer>(await fetch(`/api/public/offers/${encodeURIComponent(key)}`,{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'})),
};

export function offerForm(doc:OfferDocument):OfferForm{return {version:doc.version,title:doc.draft.settings.title,company:doc.draft.settings.client,use_logo:doc.draft.settings.use_logo,use_banner:doc.draft.settings.use_banner,items:doc.draft.items.map(i=>({id:i.id,title:i.title,sku:i.sku,sku_note:i.sku_note,unit_price:i.unit_price,quantity:i.quantity,main_image:i.main_image}))};}
export function offerMoney(value:string|null):string{if(value===null)return 'Цена не указана';const [whole,fraction='00']=value.split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+','+fraction.padEnd(2,'0')+' ₸';}
export function offerExpiry(value:string):string{return new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Almaty',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value))+' (Алматы)';}

export async function downloadOffer(url:string,filename:string,guest=false){
  const r=await fetch(url,{cache:'no-store',credentials:guest?'omit':'include',referrerPolicy:'no-referrer'});
  if(!r.ok){await response(r);return;}
  const object=URL.createObjectURL(await r.blob());
  const anchor=document.createElement('a');anchor.href=object;anchor.download=filename;anchor.click();setTimeout(()=>URL.revokeObjectURL(object),1000);
}
