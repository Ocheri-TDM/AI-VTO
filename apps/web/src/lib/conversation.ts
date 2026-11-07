import {request} from './api';

export interface ConversationMessage {id:string;role:string;content:string;created_at:string;operation_id:string|null;request_id:string|null;status:string;research_id:string|null;result_revision?:number|null;origin:string}
export interface ConversationPage {chat_id:string;messages:ConversationMessage[];next_before:string|null;next_after:string|null;has_more_after:boolean}
export const conversationApi={page:(chat:string,options:{before?:string;after?:string}={})=>request<ConversationPage>(`/chats/${chat}/history?${new URLSearchParams(options)}`)};
export function mergeMessages(previous:ConversationMessage[],incoming:ConversationMessage[]){
  const values=new Map(previous.map(m=>[m.id,m]));
  for(const message of incoming)values.set(message.id,message);
  const statuses=new Map(incoming.filter(m=>m.role==='assistant'&&m.operation_id).map(m=>[m.operation_id,m.status]));
  return [...values.values()].map(m=>statuses.has(m.operation_id)?{...m,status:statuses.get(m.operation_id)!}:m)
    .sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id));
}
