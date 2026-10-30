import {request as apiRequest} from './api';
const request=<T>(path:string,options:RequestInit={})=>apiRequest<T>(path,options,true);

export interface PresentationItem {
  offer_id:string;product_id:string;display_name:string;source_name:string;caption:string;model_name:string|null;
  brand:string|null;category_key:string;category_label:string;colors:{original_color:string;normalized_color:string}[];
  unit_price_kzt:string|null;observed_at:string|null;images:string[];image_url:string|null;prepared_key:string|null;
}
export interface PresentationTheme {
  title:string;client:string;subtitle:string;palette:string[];cover:boolean;show_brand:boolean;
  use_logo:boolean;logo:string|null;ending_note:string;
}
export interface PresentationDraft {
  research_id:string;selection_version:number;items:PresentationItem[];settings:PresentationTheme;
  category_order:string[];original_fallback_ids:string[];
}
export interface PresentationRevision {
  id:string;source_version:number;status:string;error:string|null;
  result:{pdf_url?:string;page_count?:number;sha256?:string;bytes?:number;
    pages?:{page:number;preview:string;thumbnail:string}[];
    assets?:Record<string,{url:string;hash:string;review_required:boolean;warnings:string[]}>;
    issues?:{offer_id:string|null;message:string}[]};
}
export interface PresentationDocument {id:string;project_id:string;version:number;draft:PresentationDraft;revisions:PresentationRevision[]}
export const presentationApi={
  list:(project:string)=>request<{id:string;title:string;version:number}[]>(`/projects/${project}/presentations`),
  create:(project:string,research:string,version:number)=>request<PresentationDocument>(`/projects/${project}/presentations`,{method:'POST',body:JSON.stringify({research_id:research,selection_version:version})}),
  get:(id:string)=>request<PresentationDocument>(`/presentations/${id}`),
  save:(id:string,version:number,draft:PresentationDraft)=>request<PresentationDocument>(`/presentations/${id}`,{method:'PATCH',body:JSON.stringify({version,settings:draft.settings,category_order:draft.category_order,original_fallback_ids:draft.original_fallback_ids,
    items:draft.items.map(({offer_id,display_name,caption,image_url})=>({offer_id,display_name,caption,image_url}))})}),
  refresh:(id:string,version:number)=>request<PresentationDocument>(`/presentations/${id}/refresh`,{method:'POST',body:JSON.stringify({version})}),
  generate:(id:string,version:number)=>request<PresentationRevision>(`/presentations/${id}/generate`,{method:'POST',body:JSON.stringify({version})}),
  approve:(id:string,revision:string,hashes:string[])=>request<PresentationDocument>(`/presentations/${id}/revisions/${revision}/approve`,{method:'POST',body:JSON.stringify({asset_hashes:hashes})}),
  cancel:(id:string,revision:string)=>request<PresentationDocument>(`/presentations/${id}/revisions/${revision}/cancel`,{method:'POST'}),
  upload:(id:string,version:number,file:File,offer?:string)=>request<PresentationDocument>(`/presentations/${id}/${offer?`items/${encodeURIComponent(offer)}/image`:'logo'}?version=${version}`,{method:'POST',body:file,headers:{'Content-Type':file.type}}),
};
export function presentationStatus(status:string){return ({DRAFT:'Черновик',QUEUED:'В очереди',PREPARING_ASSETS:'Подготавливаю фотографии',RENDERING:'Создаю PDF',VALIDATING:'Проверяю PDF',NEEDS_REVIEW:'Нужна проверка',READY:'Готово',FAILED:'Ошибка генерации',CANCELLED:'Отменено'} as Record<string,string>)[status]??status;}
