import {offerRequest} from './commercial-offers';

export interface ManagerNotification {id:string;document_id:string;title:string;visitor_label:string;selected_count:number;sequence:number;read_sequence:number;unread:boolean;updated_at:string;available:boolean}
export interface NotificationPage {cursor:number;unread_count:number;items:ManagerNotification[];next_before:number|null}
export interface ClientInterest {visitor_count:number;item_count:number;mark_count:number;historical_mark_count:number}
export interface ClientFeedback {document_id:string;cursor:number;published_revision_id:string|null;summary:ClientInterest;visitors:{id:string;label:string;current_round:boolean;updated_at:string;sequence:number;selected_count:number;items:{item_id:string;title:string;sku:string;image_url:string|null;published_revision_id:string;source_version:number;unit_price:string|null;quantity:number;selected_at:string;conditions_changed:boolean;removed:boolean;previous_revision:boolean}[]}[];events:{sequence:number;visitor_label:string;title:string;selected:boolean;published_revision_id:string;created_at:string}[];next_before:number|null;identity_notice:string}
export const notificationApi={
  get:(before?:number)=>offerRequest<NotificationPage>('/api/notifications'+(before?'?before='+before:'')),
  read:(id:string,sequence:number)=>offerRequest<NotificationPage>(`/api/notifications/${id}/read`,'POST',{sequence}),
  feedback:(id:string,before?:number)=>offerRequest<ClientFeedback>(`/api/offers/${id}/client-selection`+(before?'?before='+before:'')),
  interest:(ids:string[])=>offerRequest<{cursor:number;offers:Record<string,ClientInterest>;unavailable_ids:string[]}>('/api/notifications/interest?'+new URLSearchParams({ids:ids.join(',')})),
};
export function notificationText(value:ManagerNotification){return value.selected_count===0?'Клиент снял все отметки':`${value.visitor_label}: сейчас выбрано позиций — ${value.selected_count}.`;}
export function mergeNotificationPage(current:NotificationPage|null,next:NotificationPage,append=false):NotificationPage|null{
  if(current&&next.cursor<current.cursor)return current;
  if(!append)return next;
  const values=new Map(current?.items.map(i=>[i.id,i])??[]);
  for(const item of next.items){const previous=values.get(item.id);if(!previous||item.sequence>=previous.sequence)values.set(item.id,item);}
  return {...next,items:[...values.values()].sort((a,b)=>b.sequence-a.sequence)};
}
