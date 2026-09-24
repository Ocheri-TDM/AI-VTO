import { Folder, Plus, Settings2 } from "lucide-react";
import type { Project } from "@/lib/types";
export function ProjectList({
  projects,
  active,
  disabled,
  onOpen,
  onNew,
  onEdit,
}: {
  projects: Project[];
  active?: string;
  disabled: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onEdit: (p: Project) => void;
}) {
  return (
    <div className="project-list">
      <button
        className="studio-button primary"
        onClick={onNew}
        disabled={disabled}
      >
        <Plus size={17} />
        Новый проект
      </button>
      <p className="section-label">
        ВАШИ ПРОЕКТЫ <span>{projects.length}</span>
      </p>
      <nav aria-label="Проекты">
        {projects.map((p) => (
          <div
            className={`project-item ${active === p.id ? "active" : ""}`}
            key={p.id}
          >
            <button
              disabled={disabled}
              aria-current={active === p.id ? "page" : undefined}
              onClick={() => onOpen(p.chat_id)}
            >
              <Folder size={16} />
              <span>
                <strong>{p.name}</strong>
                <small>
                  {new Date(p.updated_at).toLocaleDateString("ru-RU", {
                    day: "numeric",
                    month: "short",
                  })}{" "}
                  · {p.summary ? "Есть подборка" : "Новый проект"}
                </small>
              </span>
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
          Создайте проект для клиента или события. Подборки и диалоги будут
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
