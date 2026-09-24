"use client";
import { useEffect, useRef, type RefObject } from "react";
import {
  ArrowUp,
  LoaderCircle,
  MessageSquareText,
  Sparkles,
  Undo2,
} from "lucide-react";
import type { ChatMessage, SearchSession, SupplierProgress } from "@/lib/types";
export const SAMPLE_QUERY =
  "Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт.";
interface Props {
  session: SearchSession | null;
  messages: ChatMessage[];
  suppliers: SupplierProgress[];
  progress: string;
  running: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onUndo: () => void;
  canUndo?: boolean;
  scrollPositionRef?: RefObject<number | null>;
}
export function SearchPanel({
  session,
  messages,
  progress,
  running,
  query,
  onQueryChange,
  onSearch,
  onUndo,
  canUndo = false,
  scrollPositionRef,
}: Props) {
  const log = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (!log.current || !log.current.clientHeight) return;
      const saved = scrollPositionRef?.current;
      log.current.scrollTop = saved == null || saved < 0 ? log.current.scrollHeight : saved;
      nearBottom.current = log.current.scrollHeight - log.current.scrollTop - log.current.clientHeight < 80;
    });
    return () => cancelAnimationFrame(frame);
  }, [scrollPositionRef]);
  useEffect(() => {
    if (log.current && nearBottom.current)
      log.current.scrollTop = log.current.scrollHeight;
  }, [messages.length, progress]);
  useEffect(() => {
    if (composer.current) {
      composer.current.style.height = "auto";
      composer.current.style.height = `${Math.min(composer.current.scrollHeight, 180)}px`;
    }
  }, [query]);
  return (
    <section className="search-panel" aria-labelledby="request-heading">
      <div className="panel-heading">
        <div>
          <MessageSquareText size={17} />
          <h2 id="request-heading">Диалог о подборке</h2>
        </div>
        {session && (
          <button
            className="icon-button"
            onClick={onUndo}
            disabled={running || !canUndo}
            aria-label="Отменить последнее действие"
          >
            <Undo2 size={17} />
          </button>
        )}
      </div>
      <div className="search-panel-body">
        {!messages.length && (
          <div className="assistant-intro">
            <span className="assistant-icon">
              <Sparkles size={17} />
            </span>
            <div>
              <strong>Начнём с вашей идеи</strong>
              <p>
                Расскажите о подарках, команде или событии. Подборку можно
                уточнять здесь же.
              </p>
            </div>
          </div>
        )}
        <div
          ref={log}
          onScroll={() => {
            if (log.current && log.current.clientHeight > 0) {
              nearBottom.current =
                log.current.scrollHeight -
                  log.current.scrollTop -
                  log.current.clientHeight <
                80;
              if (scrollPositionRef)
                scrollPositionRef.current = nearBottom.current ? -1 : log.current.scrollTop;
            }
          }}
          className="conversation"
          role="log"
          aria-label="История диалога"
          aria-live="polite"
        >
          {messages.map((message) => (
            <div
              key={message.id}
              className={
                message.role === "user" ? "user-message" : "intent-message"
              }
            >
              {message.content}
            </div>
          ))}
          {running && (
            <div className="filter-confirmation" role="status">
              <LoaderCircle size={15} className="spin" />
              {progress || "Сохраняю изменения"}
            </div>
          )}
        </div>
      </div>
      <form
        className="query-form chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch();
        }}
      >
        <label htmlFor="search-query" className="sr-only">
          Сообщение о подборке
        </label>
        <textarea
          ref={composer}
          id="search-query"
          rows={2}
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              if (!running && query.trim()) {
                nearBottom.current = true;
                onSearch();
              }
            }
          }}
          placeholder={
            session
              ? "Что изменить в подборке?"
              : "Подарки для IT конференции, 300 человек"
          }
          maxLength={2000}
          disabled={running}
        />
        <div className="query-form-footer">
          <span>
            {session
              ? "Уточняйте в свободной форме"
              : "Товары, идея или событие"}
          </span>
          <button
            className="send-button"
            aria-label="Отправить сообщение"
            disabled={running || !query.trim()}
          >
            <ArrowUp size={19} />
          </button>
        </div>
      </form>
    </section>
  );
}
