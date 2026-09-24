const API_PREFIX = "/api";

export function apiPath(path: string) {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${normalized}`;
}
