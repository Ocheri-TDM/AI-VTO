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
  style:'editorial'|'premium-dark'|'modern-graphic'|'classic';style_version:number;design_seed:number;auto_design:boolean;
  palette_plan?:PalettePlan|null;
  cover_policy?:1|null;renderer_version?:1|null;
  auto_decoration?:{version:1;enabled:boolean;intensity:'SUBTLE'|'NORMAL'|'BOLD'}|null;
}
export interface PalettePlan {
  version:1;preset_id:string|null;origin_preset_id:string|null;name:string;direction:string;direction_label?:string;
  primary:string;locked:boolean;manual:boolean;recipe:number;character:'calm'|'soft'|'saturated'|'contrast';
  edited_slots?:number[];
  application:'light'|'dark'|'universal';contrast_accent:boolean;colors:string[];roles:Record<string,string>;
}
export interface PaletteCatalog {version:number;published_total:number;total:number;page:number;has_more:boolean;
  directions:{id:string;label:string;published:number}[];items:PalettePlan[]}
export interface PresentationDraft {
  research_id:string|null;selection_version:number|null;collection_version?:number;items:PresentationItem[];settings:PresentationTheme;
  category_order:string[];original_fallback_ids:string[];
  materials?:PresentationMaterial[];
}
export type MaterialRole='COVER_PHOTO'|'BACKGROUND'|'DECORATION';
export interface PresentationMaterial {
  id:string;role:MaterialRole;filename:string;status:string;enabled:boolean;scope:'COVER'|'PRODUCTS'|'DOCUMENT';
  expression:'SUBTLE'|'NORMAL'|'BOLD';position:'AUTO'|'TOP'|'BOTTOM'|'LEFT'|'RIGHT';crop:boolean;
  focal_x:number;focal_y:number;opacity:number;harmonize:boolean;required:boolean;description:string;
  width?:number;height?:number;has_alpha?:boolean;thumbnail_url?:string;error?:string;replaces_id?:string;
}
export interface MaterialUsage {asset_ref:string;status:string;reason:string|null;pages:number[];filename:string}
export interface PresentationRevision {
  id:string;source_version:number;status:string;error:string|null;request_id?:string|null;
  result:{pdf_url?:string;page_count?:number;sha256?:string;bytes?:number;
    pages?:{page:number;preview:string;thumbnail:string}[];
    assets?:Record<string,{url:string;hash:string;review_required:boolean;warnings:string[]}>;
    issues?:{offer_id:string|null;asset_id?:string;message:string}[];ai_used?:boolean;fallback_reason?:string|null;ai_seconds?:number;
    material_usage?:MaterialUsage[];design_signature?:{status:string;minimum_feature_changes:number|null}};
}
export interface PresentationDocument {id:string;project_id:string;version:number;draft:PresentationDraft;revisions:PresentationRevision[];
  materials_preparing?:boolean;material_limits?:{max_bytes:number;max_files:number;max_decorations:number;max_total_bytes:number}}
export const presentationApi={
  palettes:(params:URLSearchParams,signal?:AbortSignal)=>request<PaletteCatalog>(`/presentations/palettes?${params}`,{signal}),
  list:(project:string)=>request<{id:string;title:string;version:number}[]>(`/projects/${project}/presentations`),
  create:(project:string,version:number)=>request<PresentationDocument>(`/projects/${project}/presentations`,{method:'POST',body:JSON.stringify({collection_version:version})}),
  get:(id:string)=>request<PresentationDocument>(`/presentations/${id}`),
  save:(id:string,version:number,draft:PresentationDraft)=>request<PresentationDocument>(`/presentations/${id}`,{method:'PATCH',body:JSON.stringify({version,settings:draft.settings,category_order:draft.category_order,original_fallback_ids:draft.original_fallback_ids,
    items:draft.items.map(({offer_id,display_name,caption,image_url})=>({offer_id,display_name,caption,image_url})),
    materials:draft.materials?.map(m=>Object.fromEntries(['id','enabled','scope','expression','position','crop','focal_x','focal_y','opacity','harmonize','required','description'].map(k=>[k,m[k as keyof PresentationMaterial]])))})}),
  refresh:(id:string,version:number)=>request<PresentationDocument>(`/presentations/${id}/refresh`,{method:'POST',body:JSON.stringify({version})}),
  composition:(id:string,version:number,collectionVersion:number)=>request<PresentationDocument>(`/presentations/${id}/composition`,{method:'POST',body:JSON.stringify({version,collection_version:collectionVersion})}),
  alternate:(id:string,version:number,request_id?:string)=>request<PresentationDocument>(`/presentations/${id}/alternate`,{method:'POST',body:JSON.stringify({version,request_id})}),
  generate:(id:string,version:number,request_id?:string)=>request<PresentationRevision>(`/presentations/${id}/generate`,{method:'POST',body:JSON.stringify({version,request_id})}),
  approve:(id:string,revision:string,hashes:string[])=>request<PresentationDocument>(`/presentations/${id}/revisions/${revision}/approve`,{method:'POST',body:JSON.stringify({asset_hashes:hashes})}),
  cancel:(id:string,revision:string)=>request<PresentationDocument>(`/presentations/${id}/revisions/${revision}/cancel`,{method:'POST'}),
  upload:(id:string,version:number,file:File,offer?:string)=>request<PresentationDocument>(`/presentations/${id}/${offer?`items/${encodeURIComponent(offer)}/image`:'logo'}?version=${version}`,{method:'POST',body:file,headers:{'Content-Type':file.type}}),
  uploadMaterial:(id:string,version:number,file:File,role:MaterialRole,replace?:string)=>request<PresentationDocument>(`/presentations/${id}/materials?version=${version}&role=${role}${replace?`&replace=${replace}`:''}`,{method:'POST',body:file,headers:{'Content-Type':file.type||'application/octet-stream','X-Filename':encodeURIComponent(file.name)}}),
};
export const presentationActive=['QUEUED','PREPARING_ASSETS','PLANNING_DESIGN','RENDERING','VALIDATING'];
export function designFallback(reason:string|null|undefined){return ({AI_TIMEOUT:'истёк лимит ожидания модели',INVALID_DESIGN_PLAN:'модель предложила недопустимую композицию',LOCAL_MODEL_UNAVAILABLE:'локальная модель недоступна',LLM_QUEUE_TIMEOUT:'модель занята другими запросами',LLM_QUEUE_FULL:'очередь модели занята',PLAN_INPUT_LIMIT:'подборка превышает бюджет одного запроса к модели'} as Record<string,string>)[reason??'']??'модель не завершила подготовку плана';}
export function presentationStatus(status:string){return ({DRAFT:'Черновик',QUEUED:'В очереди',PREPARING_ASSETS:'Подготавливаю фотографии',PLANNING_DESIGN:'Подготавливаю оформление',RENDERING:'Создаю PDF',VALIDATING:'Проверяю PDF',NEEDS_REVIEW:'Нужна проверка',READY:'Готово',FAILED:'Ошибка генерации',CANCELLED:'Отменено'} as Record<string,string>)[status]??status;}
