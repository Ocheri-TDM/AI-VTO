"use client";
import { useEffect, useState } from "react";
import { Clock3, LoaderCircle } from "lucide-react";
import type { SearchSession } from "@/lib/types";
export function SessionStatus({
  session,
  running,
  progress,
}: {
  session: SearchSession | null;
  running: boolean;
  progress: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  const minutes = session
    ? Math.max(
        0,
        Math.floor((new Date(session.expires_at).getTime() - now) / 60000),
      )
    : 0;
  return (
    <div className="session-status">
      {running ? (
        <span role="status">
          <LoaderCircle size={15} className="spin" />
          {progress || "Сохраняю изменения"}
        </span>
      ) : (
        session && (
          <span>
            <Clock3 size={14} />
            {minutes
              ? `Актуально · истекает через ${minutes} мин.`
              : "Данные устаревают"}
          </span>
        )
      )}
      {session?.status === "partial" && (
        <details className="partial-warning">
          <summary>Часть источников доступна с ограничениями</summary>
          <p>
            Подтверждённые товары остаются в подборке. Проверен ограниченный
            объём результатов; можно обновить поиск позже.
          </p>
        </details>
      )}
      {session?.status === "failed" && (
        <span className="partial-warning">Источники временно недоступны</span>
      )}
    </div>
  );
}
