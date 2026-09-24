import { request } from './api';
import type { Product } from './types';

export interface ResearchProduct extends Product {
  material: string | null; brand: string | null; capacity: string | null;
  freshness: 'FRESH' | 'STALE'; match: string;
}
export interface ResearchFilters {
  categories: string[]; colors: string[]; min_price: number | null; max_price: number | null;
  attributes: Record<string, string[]>; excluded_terms: string[]; required_terms: string[]; selected_only: boolean;
}
export interface Research {
  source_mode?: 'index' | 'live';
  id: string; chat_id: string; intent: {raw_query: string; quantity: number | null};
  products: ResearchProduct[]; selected_products: ResearchProduct[]; pool_count: number;
  matching_count: number; next_cursor: string | null; new_matches_count: number;
  status: string; completed_sources: number; source_count: number; stale_count: number;
  view: {filters: ResearchFilters; sorting: string; selected_ids: string[]; version: number};
  coverage: {id: string; supplier: string; category: string; query: string; status: string; pages_scanned: number;
    products_seen: number; products_validated: number; error_code: string | null}[];
  facets: Record<string, {value: string; count: number}[]>;
  groups: {id: string; offer_ids: string[]}[];
  messages: {role: string; content: string}[]; can_undo: boolean; revision: number;
}
export const researchApi = {
  latest: (chat: string) => request<Research | null>(`/researches/chat/${chat}`),
  get: (id: string) => request<Research>(`/researches/${id}`),
  start: (chat: string, query: string) => request<Research>('/researches', {method: 'POST', body: JSON.stringify({chat_id:chat,query})}),
  change: (id: string, body: object) => request<Research>(`/researches/${id}/view`, {method:'PATCH',body:JSON.stringify(body)}),
  message: (id: string, message: string) => request<Research>(`/researches/${id}/messages`, {method:'POST',body:JSON.stringify({message})}),
  continue: (id: string) => request<Research>(`/researches/${id}/continue`, {method:'POST'}),
  cancel: (id: string) => request<Research>(`/researches/${id}/cancel`, {method:'POST'}),
  page: (id: string, cursor: string) => request<{products: ResearchProduct[]; matching_count: number; next_cursor: string | null}>(`/researches/${id}/products?cursor=${encodeURIComponent(cursor)}`),
  acceptMatches: (id: string) => request<Research>(`/researches/${id}/new-matches`, {method:'POST'}),
};
