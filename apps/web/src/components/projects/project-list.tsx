import { Folder, Plus, Settings2 } from "lucide-react";
import type { Project } from "@/lib/types";
export function ProjectList({
  projects,
  active,
  disabled,
  creating=false,
  onOpen,
  onNew,
  onEdit,
}: {
  projects: Project[];
  active?: string;
  disabled: boolean;
  creating?:boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onEdit: (p: Project) => void;
}) {
  return (
    <div className="project-list">
      <button
        className="studio-button primary"
        onClick={onNew}
        disabled={disabled||creating}
      >
        <Plus size={17} />
        Новый чат
      </button>
      <p className="section-label">
        Ваши чаты <span>{projects.length}</span>
      </p>
      <nav aria-label="Чаты">
        {projects.map((p) => (
          <div
            className={`project-item ${active === p.id ? "active" : ""}`}
            key={p.id}
          >
            <button
              className="project-open"
              title={p.name}
              disabled={disabled}
              aria-current={active === p.id ? "page" : undefined}
              onClick={() => onOpen(p.chat_id)}
            >
              <Folder size={16} />
                <strong>{p.name}</strong>
                <small>
                  {new Date(p.updated_at).toLocaleDateString("ru-RU", {
                    day: "numeric",
                    month: "short",
                  })}{" "}
                  · {(p.has_messages??!!p.summary) ? "Есть переписка" : "Пустой чат"}
                </small>
            </button>
            <button
              className="icon-button project-settings"
              disabled={disabled}
              aria-label={`Настройки проекта ${p.name}`}
              onClick={() => onEdit(p)}
            >
              <Settings2 size={15} />
            </button>
          </div>
        ))}
      </nav>
      {!projects.length && (
        <p className="muted">
          Создайте чат для клиента или события. Подборки и диалоги будут
          здесь.
        </p>
      )}
      <div className="local-mode">
        <span className="status-dot" />
        Личное рабочее пространство
      </div>
    </div>
  );
}
