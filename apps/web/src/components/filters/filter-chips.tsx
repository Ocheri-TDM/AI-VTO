import type { ViewFilters } from "@/lib/types";
import { categoryLabel, colorLabel, formatPrice } from "@/lib/format";
import { X } from "lucide-react";
export function FilterChips({
  filters: f,
  disabled,
  onChange,
  onReset,
}: {
  filters: ViewFilters;
  disabled: boolean;
  onChange: (f: ViewFilters) => void;
  onReset: () => void;
}) {
  const chips: { label: string; change: ViewFilters }[] = [];
  for (const c of f.categories ?? [])
    chips.push({
      label: categoryLabel(c),
      change: {
        ...f,
        categories:
          f.categories!.length === 1
            ? null
            : f.categories!.filter((x) => x !== c),
      },
    });
  for (const c of [...new Set((f.colors ?? []).map(colorLabel))])
    chips.push({
      label: c,
      change: { ...f, colors: f.colors!.filter((x) => colorLabel(x) !== c) },
    });
  for (const c of f.excluded_categories)
    chips.push({
      label: `Без ${categoryLabel(c).toLowerCase()}`,
      change: {
        ...f,
        excluded_categories: f.excluded_categories.filter((x) => x !== c),
      },
    });
  for (const c of [...new Set(f.excluded_colors.map(colorLabel))])
    chips.push({
      label: `Исключён: ${c}`,
      change: {
        ...f,
        excluded_colors: f.excluded_colors.filter((x) => colorLabel(x) !== c),
      },
    });
  if (f.hidden_ids.length)
    chips.push({
      label: `Скрыто вручную: ${f.hidden_ids.length}`,
      change: { ...f, hidden_ids: [] },
    });
  if (f.budget?.min != null)
    chips.push({
      label: `От ${formatPrice(f.budget.min)}`,
      change: { ...f, budget: { ...f.budget, min: null } },
    });
  if (f.budget?.max != null)
    chips.push({
      label: `До ${formatPrice(f.budget.max)}`,
      change: { ...f, budget: { ...f.budget, max: null } },
    });
  if (f.selection !== "all")
    chips.push({
      label: f.selection === "selected" ? "Выбранные" : "Не выбранные",
      change: { ...f, selection: "all" },
    });
  return chips.length ? (
    <div className="filter-chips">
      {chips.map((c, i) => (
        <button
          disabled={disabled}
          key={`${c.label}-${i}`}
          onClick={() => onChange(c.change)}
          aria-label={`Убрать фильтр: ${c.label}`}
        >
          {c.label}
          <X size={12} />
        </button>
      ))}
      <button className="reset-chip" disabled={disabled} onClick={onReset}>
        Сбросить
      </button>
    </div>
  ) : null;
}
