import { request } from './api';
import type { Research } from './research';

export interface ProjectQuery { id:string; project_id:string; research_session_id:string|null; type:'STANDARD'|'HARD'; query_text:string; status:string; is_active:boolean; created_at:string; updated_at:string }
export interface HardRequest { id:string; project_id:string; query_id:string; original_text:string; status:string; target_cases:number; completed_cases:number; revision:number; metrics:Record<string,unknown>; plan:{categories:string[]; target_sets:number; candidates_per_position:number; quantity:{minimum:number|null}; category_count_mismatch:boolean}; created_at:string; updated_at:string }
export interface WorkspaceMessage {id:string;role:'user'|'assistant'|'system';content:string;hard_request_id:string|null;result_revision:number|null;created_at:string}
export interface HardCandidate { id:string; offer_id:string; product_id:string; name:string; supplier:string; source_url:string|null; image:string|null; price:number|null; tier:'A'|'B'|'C'|'D'; selected:boolean; user_locked:boolean }
export interface HardPosition { id:string; position:number; category:string; candidates:HardCandidate[] }
export interface HardCase { id:string; number:number; status:string; positions:HardPosition[] }
export interface HardCases { cases:HardCase[]; total:number; offset:number; next_offset:number|null }

export const workspaceApi={
  universal:(project:string,query:string)=>request<{mode:'SIMPLE'|'COMPOSITE'|'REFINEMENT';research?:Research;hard_request?:HardRequest}>(`/projects/${project}/requests`,{method:'POST',body:JSON.stringify({query})}),
  queries:(project:string)=>request<ProjectQuery[]>(`/projects/${project}/queries`),
  createQuery:(project:string,query:string)=>request<{query:ProjectQuery;research:Research}>(`/projects/${project}/queries`,{method:'POST',body:JSON.stringify({query})}),
  activate:(project:string,query:string)=>request<ProjectQuery>(`/projects/${project}/queries/${query}/activate`,{method:'POST'}),
  hardRequests:(project:string)=>request<HardRequest[]>(`/projects/${project}/hard-requests`),
  createHard:(project:string,text:string)=>request<HardRequest>(`/projects/${project}/hard-requests`,{method:'POST',body:JSON.stringify({request:text})}),
  hard:(id:string)=>request<HardRequest>(`/hard-requests/${id}`),
  cases:(id:string,offset=0)=>request<HardCases>(`/hard-requests/${id}/cases?offset=${offset}&limit=10`),
  select:(id:string,candidate_id:string)=>request<{selected:string}>(`/hard-requests/${id}/select`,{method:'POST',body:JSON.stringify({candidate_id})}),
  rebuild:(id:string)=>request<HardRequest>(`/hard-requests/${id}/rebuild`,{method:'POST'}),
  messages:(project:string)=>request<WorkspaceMessage[]>(`/projects/${project}/messages`),
  activeComposite:(project:string)=>request<HardRequest|null>(`/projects/${project}/active-composite`),
};
