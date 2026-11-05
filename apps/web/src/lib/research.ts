import { request } from './api';
import type { Product } from './types';

export interface ResearchProduct extends Product {
  unavailable?:boolean;
  material: string | null; brand: string | null; capacity: string | null;
  freshness: 'FRESH' | 'STALE'; match: string;
  category_match: 'EXACT' | 'STRONG_RELATED' | 'RELATED' | 'EXPLORATORY' | 'REJECTED';
}
export type BranchAction={action:'hide'|'show'|'show_all'|'remove'|'restore';branch_id?:string;removal_id?:string};
export interface QueryCategory {id:string;concept:string|null;label:string;source_text:string;order:number;hidden:boolean;count:number;resolved:boolean;
  constraints:{colors?:string[];brands?:string[];excluded_brands?:string[];models?:string[];materials?:string[];quantity?:number|null;min_price?:number|null;max_price?:number|null;
    constraints?:{quantity_min_exclusive?:boolean;price_min_exclusive?:boolean;price_max_exclusive?:boolean;capacity_ml?:number|null;capacity_mah?:number|null;sizes?:string[];sku?:string|null;allow_near_colors?:boolean}}}
export interface ResearchFilters {
  brands: string[]; materials: string[]; sizes: string[]; minimum_quantity: number|null;
  excluded_brands?:string[];models?:string[];branch_brands?:Record<string,string[]>;branch_models?:Record<string,string[]>;
  excluded_materials: string[]; origins: Record<string,string>;
  categories: string[]; colors: string[]; min_price: number | null; max_price: number | null;
  attributes: Record<string, string[]>; excluded_terms: string[]; required_terms: string[]; selected_only: boolean;
}
export interface Research {
  source_mode?: 'index' | 'live';
  id: string; chat_id: string; intent: {raw_query: string; quantity: number | null; brands?:string[];excluded_brands?:string[];models?:string[]; excluded_materials?:string[]; excluded_categories?:string[]; branches?:{category:string;label:string;colors:string[];quantity:number|null;materials:string[];min_price:number|null;max_price:number|null;brands?:string[];excluded_brands?:string[];models?:string[]}[]};
  brand_labels?:Record<string,string>;identity_notices?:string[];
  query_categories?:QueryCategory[];categories_unrestricted?:boolean;effective_title?:string;
  category_notice?:{id:string;label:string;branch_id:string}|null;
  result_state?:string;eligible_total?:number;visible_total?:number;
  base_total?:number; filtered_total?:number; selected_count?:number; hidden_selected_count?:number; empty_branches?:string[];
  products: ResearchProduct[]; selected_products: ResearchProduct[]; pool_count: number;
  matching_count: number; next_cursor: string | null; new_matches_count: number;
  status: string; completed_sources: number; source_count: number; stale_count: number;
  view: {filters: ResearchFilters; hidden_branch_ids?:string[]; preferences?:string[]; sorting: string; selected_ids: string[]; version: number};
  coverage: {id: string; supplier: string; category: string; query: string; status: string; pages_scanned: number;
    products_seen: number; products_validated: number; error_code: string | null}[];
  facets: Record<string, {value: string; label?:string; count: number}[]>;
  groups: {id: string; offer_ids: string[]}[];
  messages: {role: string; content: string}[]; can_undo: boolean; revision: number;
}
export const researchApi = {
  latest: (chat: string) => request<Research | null>(`/researches/chat/${chat}`),
  get: (id: string) => request<Research>(`/researches/${id}`),
  start: (chat: string, query: string) => request<Research>('/researches', {method: 'POST', body: JSON.stringify({chat_id:chat,query})}),
  change: (id: string, body: object) => request<Research>(`/researches/${id}/view`, {method:'PATCH',body:JSON.stringify(body)},true),
  message: (id: string, message: string) => request<Research>(`/researches/${id}/messages`, {method:'POST',body:JSON.stringify({message})}),
  continue: (id: string) => request<Research>(`/researches/${id}/continue`, {method:'POST'}),
  cancel: (id: string) => request<Research>(`/researches/${id}/cancel`, {method:'POST'}),
  page: (id: string, cursor: string) => request<{products: ResearchProduct[]; matching_count: number; next_cursor: string | null}>(`/researches/${id}/products?limit=100&cursor=${encodeURIComponent(cursor)}`),
  acceptMatches: (id: string) => request<Research>(`/researches/${id}/new-matches`, {method:'POST'}),
};
