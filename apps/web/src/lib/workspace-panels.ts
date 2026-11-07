import type {ConversationMessage} from './conversation';

export interface LocalSubmission {chat:string|null;id:string;text:string;created_at:string;phase:'SENDING'|'ACKNOWLEDGED'|'ERROR'}

export function workspacePanels({projectsReady,chat,historyReady,researchReady,collectionReady,hasResearch,hasMessages,knownMessages,submission,selectedCount}:{projectsReady:boolean;chat:string|null;historyReady:boolean;researchReady:boolean;collectionReady:boolean;hasResearch:boolean;hasMessages:boolean;knownMessages:boolean;submission:LocalSubmission|null;selectedCount:number|null}){
  const local=submission?.chat===chat?submission:null;
  const started=hasResearch||hasMessages||knownMessages||!!local;
  const restored=projectsReady&&(!chat||(historyReady&&researchReady&&collectionReady));
  return {started,restored,showInitialComposer:restored&&!started,showCollectionShortcut:!hasResearch&&(selectedCount??0)>0};
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
