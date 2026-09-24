import {
  ArrowUpRight,
  Boxes,
  FolderClosed,
  Layers2,
  Plus,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

export function Sidebar({
  onNew,
  hasSession,
}: {
  onNew: () => void;
  hasSession: boolean;
}) {
  return (
    <aside className="sidebar" aria-label="Основная навигация">
      <Link href="/" className="brand" aria-label="Forma — на главную">
        <span className="brand-mark">
          <Layers2 size={23} strokeWidth={2.1} />
        </span>
        forma<span className="brand-dot">.</span>
      </Link>
      <span className="workspace-label">РАБОЧЕЕ ПРОСТРАНСТВО</span>
      <button className="new-search" onClick={onNew}>
        <Plus size={17} />
        Новая подборка<span className="key-hint">↗</span>
      </button>
      <div className="nav-item active">
        <Sparkles size={17} />
        <span>Подбор мерча</span>
        <span className="active-dot" />
      </div>
      <div className="sidebar-projects">
        <div className="sidebar-section-label">
          ПРОЕКТЫ<span>СКОРО</span>
        </div>
        <div className="project-placeholder">
          <FolderClosed size={21} />
          <p>
            У каждой идеи
            <br />
            будет своё место
          </p>
          <span>
            Проекты появятся
            <br />
            на следующем этапе
          </span>
        </div>
      </div>
      <div className="sidebar-bottom">
        <div className="stage-note">
          <Boxes size={18} />
          <div>
            <strong>От идеи до мерча</strong>
            <p>Поиск · Подбор · Выбор</p>
          </div>
          <ArrowUpRight size={15} />
        </div>
        <div className="local-workspace">
          <span className="avatar">Л</span>
          <div>
            <strong>Локальное пространство</strong>
            <span>
              {hasSession ? "Подборка в работе" : "Готово к вашей идее"}
            </span>
          </div>
          <span className="online-dot" />
        </div>
      </div>
    </aside>
  );
}
