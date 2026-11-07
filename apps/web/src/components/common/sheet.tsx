"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
export function Sheet({
  title,
  children,
  onClose,
  className = "",
  variant = "default",
  id,
  closeControl,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  className?: string;
  variant?: "default" | "modelVariants";
  id?: string;
  closeControl?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      id={id}
      className={`sheet ${variant === "modelVariants" ? "model-variants-sheet" : ""} ${className}`}
      aria-label={title}
      onCancel={(event)=>{event.preventDefault();onClose();}}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        {closeControl??<button
          className="icon-button"
          aria-label="Закрыть панель"
          onClick={onClose}
        >
          <X size={20} />
        </button>}
      </header>
      <div className="sheet-body">{children}</div>
    </dialog>
  );
}
