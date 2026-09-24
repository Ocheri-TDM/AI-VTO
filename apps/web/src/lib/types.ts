export interface ProductColor {
  original_color: string;
  normalized_color: string;
}

export interface AvailabilityEntry {
  locationCode: string | null;
  locationLabel: string | null;
  state: string;
  total: number | null;
  free: number | null;
  reserved: number | null;
  expectedAt: string | null;
  leadTimeMinDays: number | null;
  leadTimeMaxDays: number | null;
  observedAt: string;
  confidence: string;
}

export interface ProductAvailability {
  current: AvailabilityEntry[];
  incoming: AvailabilityEntry[];
  remote: AvailabilityEntry[];
  alternatives: AvailabilityEntry[];
  availableNow: number | null;
  incomingConfirmed: number | null;
  remoteAvailable: number | null;
  earliestIncomingAt: string | null;
  procurementTier: string | null;
  confidence: string;
}

export interface Product {
  id: string;
  name: string;
  category: string | null;
  description: string | null;
  price_kzt: number | null;
  colors: ProductColor[];
  primary_image: string | null;
  images: string[];
  duplicate_group_id?: string | null;
  material?: string | null;
  dimensions?: string | null;
  capacity?: string | null;
  brand?: string | null;
  freshness?: "FRESH" | "STALE";
  stock_quantity?: number | null;
  incoming_quantity?: number | null;
  incoming_date?: string | null;
  availability_status?: string | null;
  availability?: ProductAvailability;
}

export interface ProductDetails extends Product {
  supplier: string;
  source_url: string;
  offers?: { supplier: string; product_url: string }[];
  stock_quantity: number | null;
  original_price: string | null;
  original_currency: string | null;
  metadata: Record<string, unknown>;
  fetched_at: string;
}

export interface SupplierProgress {
  supplier: "oasis" | "gifts" | "ucontay";
  status: "pending" | "running" | "completed" | "failed";
  error_code?: string | null;
  message?: string | null;
  products_discovered: number;
  products_accepted: number;
  pages_scanned: number;
  duration_ms: number;
  warnings?: string[];
}

export interface SearchSession {
  id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  intent: {
    raw_query: string;
    quantity: number | null;
    categories: string[];
    colors: string[];
    budget: { min: number | null; max: number | null } | null;
  };
  products: Product[];
  suppliers: SupplierProgress[];
  created_at: string;
  expires_at: string;
  cache_hit: boolean;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
}
export interface ChatResult {
  active_state: SearchState;
  selected_products: Product[];
  pool_count: number;
  facets: { categories?: string[]; colors?: string[] };
  can_undo: boolean;
  assistant_message: string;
  search_session_id?: string | null;
  action: string;
  session?: SearchSession | null;
  selected_product_ids: string[];
  state_version: number;
  result_summary: { visible_count: number; selected_count: number };
}
export interface ViewFilters {
  categories: string[] | null;
  excluded_categories: string[];
  colors: string[] | null;
  excluded_colors: string[];
  budget: { min: number | null; max: number | null } | null;
  quantity: number | null;
  hidden_ids: string[];
  selection: "all" | "selected" | "unselected";
}
export type Sorting = "relevance" | "price_asc" | "price_desc" | "darker";
export interface SearchState {
  filters: ViewFilters;
  sorting: Sorting;
  selected_ids: string[];
  version: number;
}
export interface Project {
  id: string;
  name: string;
  client_name: string | null;
  chat_id: string;
  updated_at: string;
  summary: string;
}
export interface Chat {
  id: string;
  project: Pick<Project, "id" | "name" | "client_name">;
  expired: boolean;
  last_intent: SearchSession["intent"] | null;
  messages: ChatMessage[];
  current: ChatResult;
}
export interface WorkspaceChange {
  state_version: number;
  filters?: ViewFilters;
  sorting?: Sorting;
  restore?: "undo" | "reset";
}
export interface SelectionInput {
  product_ids?: string[];
  count?: number;
  mode: "add" | "remove" | "replace" | "clear";
}
export interface PresentationDraftInput {
  project_id: string;
  search_session_id: string;
  selected_product_ids: string[];
  client_name?: string;
  project_name?: string;
  logo_asset_id?: string;
}
