import type {ConversationMessage} from './conversation';

export interface LocalSubmission {chat:string|null;id:string;text:string;created_at:string;phase:'SENDING'|'ACKNOWLEDGED'|'ERROR'}
export interface ChatDraft {text:string;id?:string;created_at?:string;creation_id?:string}
export function draftKey(user:string,chat:string){return 'chat-draft:'+user+':'+chat;}
export function readDraft(user:string,chat:string):ChatDraft|null{
  try{const value=JSON.parse(sessionStorage.getItem(draftKey(user,chat))??'null');return value&&typeof value.text==='string'?value:null;}catch{return null;}
}
export function saveDraft(user:string,chat:string,draft:ChatDraft|null){
  try{if(draft)sessionStorage.setItem(draftKey(user,chat),JSON.stringify(draft));else sessionStorage.removeItem(draftKey(user,chat));}catch{}
}
export function restoredSubmission(chat:string|null,draft:ChatDraft|null):LocalSubmission|null{
  return draft?.id?{chat,id:draft.id,text:draft.text,created_at:draft.created_at??new Date().toISOString(),phase:'ERROR'}:null;
}

export function workspacePanels({projectsReady,chat,historyReady,researchReady,collectionReady,hasResearch,hasMessages,knownMessages,submission}:{projectsReady:boolean;chat:string|null;historyReady:boolean;researchReady:boolean;collectionReady:boolean;hasResearch:boolean;hasMessages:boolean;knownMessages:boolean;submission:LocalSubmission|null}){
  const local=submission?.chat===chat?submission:null;
  const started=hasResearch||hasMessages||knownMessages||!!local;
  const restored=projectsReady&&(!chat||(historyReady&&researchReady&&collectionReady));
  return {started,restored,showInitialComposer:restored&&!started};
}

export function visibleConversation(messages:ConversationMessage[],submission:LocalSubmission|null,chat:string|null):ConversationMessage[]{
  if(!submission||submission.chat!==chat||messages.some(m=>m.role==='user'&&m.request_id===submission.id))return messages;
  return [...messages,{id:'local:'+submission.id,role:'user',content:submission.text,created_at:submission.created_at,
    operation_id:null,request_id:submission.id,status:submission.phase==='SENDING'?'LOCAL_PENDING':submission.phase==='ERROR'?'LOCAL_ERROR':'ACKNOWLEDGED',research_id:null,origin:'LOCAL_DRAFT'}];
}

export function panelPreferenceKey(user:string,chat:string|null){return 'workspace-chat-open:'+user+':'+(chat??'initial');}

export interface PanelChoice {key:string;open:boolean}
export function transferPanelChoice(choice:PanelChoice|null,from:string,to:string):PanelChoice{
  return {key:to,open:choice?.key===from?choice.open:true};
}
