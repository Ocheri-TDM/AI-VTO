"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { isRunning } from "@/lib/format";
import type { SearchSession } from "@/lib/types";

export function useSearch() {
  const [session, setSession] = useState<SearchSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const operation = useRef<AbortController | null>(null);
  const sessionId = session?.id;
  const sessionStatus = session?.status;

  useEffect(() => {
    if (!sessionId || !isRunning(sessionStatus)) return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    let failures = 0;
    const poll = async () => {
      try {
        const updated = await api.session(sessionId, controller.signal);
        if (controller.signal.aborted) return;
        failures = 0;
        setSession(updated);
        setError(null);
        if (isRunning(updated.status)) timeout = setTimeout(poll, 1800);
      } catch (err) {
        if (controller.signal.aborted) return;
        failures += 1;
        if (failures < 4) timeout = setTimeout(poll, 2500 * failures);
        else
          setError(
            err instanceof Error
              ? err.message
              : "Не удалось обновить статус поиска.",
          );
      }
    };
    timeout = setTimeout(poll, 1200);
    return () => {
      controller.abort();
      clearTimeout(timeout);
    };
  }, [sessionId, sessionStatus, pollKey]);

  useEffect(() => () => operation.current?.abort(), []);

  const run = useCallback(
    async (
      action: (signal: AbortSignal) => Promise<SearchSession>,
      clear: boolean,
    ) => {
      operation.current?.abort();
      const controller = new AbortController();
      operation.current = controller;
      setBusy(true);
      setError(null);
      if (clear) setSession(null);
      try {
        const result = await action(controller.signal);
        if (!controller.signal.aborted) setSession(result);
        return result;
      } catch (err) {
        if (!controller.signal.aborted)
          setError(
            err instanceof Error ? err.message : "Не удалось выполнить запрос.",
          );
        return null;
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    },
    [],
  );

  const reset = useCallback(() => {
    operation.current?.abort();
    setSession(null);
    setBusy(false);
    setError(null);
  }, []);

  return {
    session,
    busy,
    error,
    running: busy || isRunning(session?.status),
    search: (query: string) => run((signal) => api.search(query, signal), true),
    refresh: () =>
      session
        ? run((signal) => api.refresh(session.id, signal), true)
        : Promise.resolve(null),
    filter: (price: number) =>
      session
        ? run((signal) => api.filter(session.id, price, signal), false)
        : Promise.resolve(null),
    retryPolling: () => {
      setError(null);
      setPollKey((value) => value + 1);
    },
    reset,
  };
}
