import { request } from './api';
import type { Research } from './research';

export interface ProjectQuery {
  id: string; project_id: string; type: string; research_session_id: string | null;
  query_text: string; status: string; is_active: boolean; created_at: string;
}
export const workspaceApi = {
  universal: (project: string, query: string) => request<{mode:'GRID';research:Research}>(`/projects/${project}/requests`,{method:'POST',body:JSON.stringify({query})}),
  queries: (project: string) => request<ProjectQuery[]>(`/projects/${project}/queries`),
  activate: (project: string, query: string) => request<{research:Research}>(`/projects/${project}/queries/${query}/activate`,{method:'POST'}),
};
