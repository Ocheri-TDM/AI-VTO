import { request } from './api';
import type { Research } from './research';

export interface ProjectQuery {
  id: string; project_id: string; type: string; research_session_id: string | null;
  query_text: string; status: string; is_active: boolean; created_at: string;
}
export interface RequestReceipt {
  id:string;request_id:string;status:string;research_id:string|null;error_code:string|null;active:boolean;
}
export const workspaceApi = {
  universal: (project: string, query: string,request_id?:string) => request<{mode:'GRID';research:Research|null;active?:boolean;turn?:{id:string;status:string;request_id:string}}>(`/projects/${project}/requests`,{method:'POST',body:JSON.stringify({query,request_id})},true),
  receipt: (project:string,request_id:string) => request<RequestReceipt>(`/projects/${project}/requests/${request_id}`),
  queries: (project: string) => request<ProjectQuery[]>(`/projects/${project}/queries`),
  activate: (project: string, query: string) => request<{research:Research}>(`/projects/${project}/queries/${query}/activate`,{method:'POST',body:'{}'}),
};
