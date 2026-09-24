"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, streamMessage } from "@/lib/api";
import type {
  Chat,
  ChatMessage,
  ChatResult,
  Project,
  SelectionInput,
  WorkspaceChange,
} from "@/lib/types";

const labels: Record<string, string> = {
  search_products: "Ищу товары на сайтах",
  incremental_search: "Дополняю подборку",
  filter_products: "Применяю фильтры",
  sort_products: "Сортирую варианты",
  rank_products: "Сопоставляю варианты",
  select_products: "Выбираю лучшие варианты",
  restore_products: "Восстанавливаю подборку",
  refresh_search: "Обновляю цены и наличие",
};

export function useChat() {
  const [chat, setChat] = useState<Chat | null>(null);
  const [result, setResult] = useState<ChatResult | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [progress, setProgress] = useState("");
  const chatId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const locked = useRef(false);
  const generation = useRef(0);

  const apply = useCallback((value: ChatResult) => {
    setResult(value);
    setSelected(new Set(value.selected_product_ids));
    if (value.session) setExpired(false);
  }, []);
  const applyChat = useCallback(
    (value: Chat) => {
      chatId.current = value.id;
      localStorage.setItem("forma-chat-id", value.id);
      setChat(value);
      setMessages(value.messages);
      setExpired(value.expired);
      apply(value.current);
    },
    [apply],
  );
  function fail(err: unknown) {
    setError(
      err instanceof Error ? err.message : "Не удалось выполнить действие.",
    );
    if (err instanceof ApiError && err.status === 410) {
      setExpired(true);
      setResult(null);
      setSelected(new Set());
    }
  }
  useEffect(() => {
    let active = true;
    const saved = localStorage.getItem("forma-chat-id");
    Promise.all([
      api.projects(),
      saved
        ? api.chat(saved).catch((err) => {
            if (err instanceof ApiError && err.status === 404) return null;
            throw err;
          })
        : Promise.resolve(null),
    ])
      .then(([list, value]) => {
        if (!active) return;
        setProjects(list);
        if (value) applyChat(value);
      })
      .catch((err) => {
        if (active) fail(err);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.current?.abort();
    };
  }, [applyChat]);
  useEffect(() => {
    if (!result?.session) return;
    const remaining =
      new Date(result.session.expires_at).getTime() - Date.now();
    const timer = setTimeout(
      () => {
        setExpired(true);
        setResult(null);
        setSelected(new Set());
      },
      Math.max(0, remaining),
    );
    return () => clearTimeout(timer);
  }, [result?.session]);

  async function open(id: string) {
    if (locked.current) return false;
    const version = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const value = await api.chat(id);
      if (version === generation.current) applyChat(value);
      return true;
    } catch (err) {
      fail(err);
      return false;
    } finally {
      if (version === generation.current) setLoading(false);
    }
  }
  async function create(name: string) {
    if (locked.current) return false;
    setLoading(true);
    setError(null);
    try {
      const created = await api.createChat(name);
      applyChat(await api.chat(created.id));
      setProjects(await api.projects());
      return true;
    } catch (err) {
      fail(err);
      return false;
    } finally {
      setLoading(false);
    }
  }
  async function projectAction(
    action: "save" | "delete",
    id: string,
    name = "",
    client = "",
  ) {
    if (locked.current) return false;
    try {
      if (action === "save") {
        await api.updateProject(id, name, client);
        if (chatId.current) applyChat(await api.chat(chatId.current));
      } else {
        await api.deleteProject(id);
        if (chat?.project.id === id) {
          chatId.current = null;
          localStorage.removeItem("forma-chat-id");
          setChat(null);
          setResult(null);
          setMessages([]);
          setSelected(new Set());
          setExpired(false);
        }
      }
      setProjects(await api.projects());
      return true;
    } catch (err) {
      fail(err);
      return false;
    }
  }
  async function search(message: string) {
    if (locked.current || loading || !message.trim()) return null;
    locked.current = true;
    setRunning(true);
    setError(null);
    setProgress("Анализирую запрос");
    controller.current = new AbortController();
    let answer: ChatResult | null = null;
    try {
      if (!chatId.current) {
        const created = await api.createChat(message.slice(0, 60));
        applyChat(await api.chat(created.id));
      }
      setMessages((old) => [
        ...old,
        { id: crypto.randomUUID(), role: "user", content: message },
      ]);
      await streamMessage(
        chatId.current!,
        message,
        controller.current.signal,
        (event, data) => {
          if (event === "tool.started")
            setProgress(labels[String(data.tool)] ?? "Обновляю подборку");
          if (event === "supplier.started")
            setProgress(
              `Ищу на ${data.supplier === "gifts" ? "Gifts.ru" : "Ucontay"}`,
            );
          if (event === "agent.completed" || event === "agent.error") {
            answer = data as unknown as ChatResult;
            apply(answer);
            setMessages((old) => [
              ...old,
              {
                id: crypto.randomUUID(),
                role: "assistant",
                content: answer!.assistant_message,
              },
            ]);
          }
        },
      );
      setProjects(await api.projects());
    } catch (err) {
      if (!controller.current.signal.aborted) fail(err);
    } finally {
      locked.current = false;
      setRunning(false);
      setProgress("");
    }
    return answer;
  }
  async function mutate(change: Omit<WorkspaceChange, "state_version">) {
    if (!chatId.current || !result || locked.current) return false;
    locked.current = true;
    setRunning(true);
    setError(null);
    setProgress("Обновляю подборку");
    try {
      apply(
        await api.workspace(chatId.current, {
          ...change,
          state_version: result.state_version,
        }),
      );
      return true;
    } catch (err) {
      fail(err);
      if (err instanceof ApiError && err.status === 409) {
        try {
          applyChat(await api.chat(chatId.current));
        } catch {}
      }
      return false;
    } finally {
      locked.current = false;
      setRunning(false);
      setProgress("");
    }
  }
  async function selection(body: SelectionInput) {
    if (!chatId.current || locked.current) return;
    const before = new Set(selected);
    const next = new Set(selected);
    if (body.mode === "clear" || body.mode === "replace") next.clear();
    for (const id of body.product_ids ?? []) {
      if (body.mode === "remove") next.delete(id);
      else next.add(id);
    }
    if (body.product_ids || body.mode === "clear") setSelected(next);
    locked.current = true;
    setRunning(true);
    setError(null);
    try {
      const value = await api.selection(chatId.current, body);
      if (value.action === "NO_ACTION")
        throw new Error(value.assistant_message);
      apply(value);
    } catch (err) {
      setSelected(before);
      fail(err);
    } finally {
      locked.current = false;
      setRunning(false);
    }
  }
  return {
    chat,
    result,
    session: result?.session ?? null,
    projects,
    messages,
    selected,
    running,
    loading,
    error,
    expired,
    progress,
    search,
    open,
    create,
    projectAction,
    mutate,
    selection,
    dismissError: () => setError(null),
    select: (id: string) =>
      selection({
        product_ids: [id],
        mode: selected.has(id) ? "remove" : "add",
      }),
    refresh: () => search("Обнови поиск"),
    undo: () => mutate({ restore: "undo" }),
  };
}
