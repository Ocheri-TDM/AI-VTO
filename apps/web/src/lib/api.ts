import type {
  Chat,
  ChatResult,
  ProductDetails,
  SearchSession,
  Project,
  WorkspaceChange,
  SelectionInput,
} from "./types";
import { apiPath } from "./api-config";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
function failure(status: number) {
  return new ApiError(
    status,
    (
      {
        404: "Проект или подборка больше не доступны.",
        410: "Результаты поиска устарели. История диалога сохранена.",
        409: "Подборка уже изменяется. Дождитесь завершения действия.",
        429: "Сервис занят. Повторите действие немного позже.",
        422: "Проверьте заполненные поля.",
        503: "Backend API не настроен. Укажите API_BASE_URL и повторно разверните frontend.",
      } as Record<number, string>
    )[status] ?? "Не удалось выполнить действие. Попробуйте ещё раз.",
  );
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiPath(path), {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
      cache: "no-store",
      credentials: "include",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error(
      "Не удалось подключиться к серверу. Проверьте подключение и попробуйте ещё раз.",
    );
  }
  if (!response.ok) {
    throw failure(response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<{ id: string; name: string }>("/auth/me"),
  register: (name: string, password: string) =>
    request<{ id: string; name: string }>("/auth/register", {
      method: "POST", body: JSON.stringify({ name, password }),
    }),
  login: (name: string, password: string) =>
    request<{ id: string; name: string }>("/auth/login", {
      method: "POST", body: JSON.stringify({ name, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  createChat: (name = "Новый проект") =>
    request<{ id: string }>("/chats", {
      method: "POST",
      body: JSON.stringify({ project_name: name }),
    }),
  projects: () => request<Project[]>("/projects"),
  updateProject: (id: string, name: string, client_name: string) =>
    request(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name, client_name }),
    }),
  deleteProject: (id: string) =>
    request(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  workspace: (id: string, body: WorkspaceChange) =>
    request<ChatResult>(`/chats/${encodeURIComponent(id)}/workspace`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  selection: (id: string, body: SelectionInput) =>
    request<ChatResult>(`/chats/${encodeURIComponent(id)}/selection`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  chat: (id: string) => request<Chat>(`/chats/${encodeURIComponent(id)}`),
  select: (id: string, productId: string, remove: boolean) =>
    request<ChatResult>(`/chats/${encodeURIComponent(id)}/selection`, {
      method: "POST",
      body: JSON.stringify({
        product_ids: [productId],
        mode: remove ? "remove" : "add",
      }),
    }),
  search: (query: string, signal?: AbortSignal) =>
    request<SearchSession>("/searches", {
      method: "POST",
      body: JSON.stringify({ query }),
      signal,
    }),
  session: (id: string, signal?: AbortSignal) =>
    request<SearchSession>(`/searches/${encodeURIComponent(id)}`, { signal }),
  refresh: (id: string, signal?: AbortSignal) =>
    request<SearchSession>(`/searches/${encodeURIComponent(id)}/refresh`, {
      method: "POST",
      signal,
    }),
  filter: (id: string, maxPrice: number, signal?: AbortSignal) =>
    request<SearchSession>(`/searches/${encodeURIComponent(id)}/filter`, {
      method: "POST",
      body: JSON.stringify({ max_price_kzt: maxPrice }),
      signal,
    }),
  product: (session: string, id: string, signal?: AbortSignal) =>
    request<ProductDetails>(
      `/searches/${encodeURIComponent(session)}/products/${encodeURIComponent(id)}`,
      { signal },
    ),
};

export async function streamMessage(
  id: string,
  message: string,
  signal: AbortSignal,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  const response = await fetch(
    apiPath(`/chats/${encodeURIComponent(id)}/messages/stream`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
      credentials: "include",
    },
  );
  if (!response.ok || !response.body) throw failure(response.status);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = block
        .split("\n")
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim();
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (event && data) {
        onEvent(event, JSON.parse(data));
        if (event === "agent.completed" || event === "agent.error")
          completed = true;
      }
    }
  }
  if (!completed)
    throw new Error(
      "Соединение прервано. Откройте диалог снова, чтобы проверить состояние.",
    );
}
