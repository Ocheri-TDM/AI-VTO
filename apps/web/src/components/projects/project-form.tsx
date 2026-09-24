"use client";
import { useState } from "react";
import type { Project } from "@/lib/types";
import { Sheet } from "../common/sheet";
export function ProjectForm({
  project,
  onSave,
  onDelete,
  onClose,
}: {
  project?: Project;
  onSave: (name: string, client: string) => Promise<boolean>;
  onDelete?: () => Promise<boolean>;
  onClose: () => void;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [client, setClient] = useState(project?.client_name ?? "");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  return (
    <Sheet
      title={project ? "Настройки проекта" : "Новый проект"}
      onClose={() => {
        if (!busy) onClose();
      }}
      className="compact-sheet"
    >
      <form
        className="project-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!name.trim() || busy) return;
          setBusy(true);
          if (await onSave(name.trim(), client.trim())) onClose();
          setBusy(false);
        }}
      >
        <label>
          Название проекта
          <input
            autoFocus
            required
            maxLength={300}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Halyk Tech Gifts"
          />
        </label>
        {project && (
          <label>
            Клиент
            <input
              maxLength={300}
              value={client}
              onChange={(e) => setClient(e.target.value)}
              placeholder="Название компании"
            />
          </label>
        )}
        <p className="muted">Отдельный диалог и подборка для каждой задачи.</p>
        <button
          className="studio-button primary"
          disabled={busy || !name.trim()}
        >
          {busy ? "Сохраняю…" : project ? "Сохранить" : "Создать проект"}
        </button>
        {onDelete && !confirm && (
          <button
            type="button"
            className="text-button danger"
            onClick={() => setConfirm(true)}
          >
            Удалить проект
          </button>
        )}
        {confirm && (
          <div className="delete-confirm" role="alert">
            <strong>Удалить проект и его диалог?</strong>
            <p>Это действие нельзя отменить.</p>
            <button
              type="button"
              className="studio-button danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                if (await onDelete!()) onClose();
                setBusy(false);
              }}
            >
              Удалить проект
            </button>
            <button
              type="button"
              className="text-button"
              onClick={() => setConfirm(false)}
            >
              Оставить проект
            </button>
          </div>
        )}
      </form>
    </Sheet>
  );
}
