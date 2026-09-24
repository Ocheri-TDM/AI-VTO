"use client";
import { useState } from "react";
import type { ChatResult, ViewFilters } from "@/lib/types";
import { categoryLabel, colorLabel, colorSwatch } from "@/lib/format";
export function FilterPanel({
  result,
  onApply,
  onReset,
}: {
  result: ChatResult;
  onApply: (filters: ViewFilters) => Promise<boolean>;
  onReset: () => void;
}) {
  const [draft, setDraft] = useState<ViewFilters>(
    structuredClone(result.active_state.filters),
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function toggle(field: "categories" | "colors", value: string) {
    const values = draft[field] ?? [];
    setDraft({
      ...draft,
      [field]: values.includes(value)
        ? values.filter((x) => x !== value)
        : [...values, value],
    });
  }
  return (
    <form
      className="filter-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (
          draft.budget?.min != null &&
          draft.budget.max != null &&
          draft.budget.min > draft.budget.max
        ) {
          setError("Минимальная цена не может быть выше максимальной.");
          return;
        }
        setBusy(true);
        await onApply(draft);
        setBusy(false);
      }}
    >
      <fieldset>
        <legend>Категории</legend>
        <button
          className="text-button"
          type="button"
          onClick={() =>
            setDraft({ ...draft, categories: null, excluded_categories: [] })
          }
        >
          Все категории
        </button>
        {(result.facets.categories ?? []).map((c) => (
          <label className="filter-option" key={c}>
            <input
              type="checkbox"
              checked={
                draft.categories === null || draft.categories.includes(c)
              }
              onChange={() => {
                if (draft.categories === null)
                  setDraft({
                    ...draft,
                    categories: (result.facets.categories ?? []).filter(
                      (x) => x !== c,
                    ),
                  });
                else toggle("categories", c);
              }}
            />
            {categoryLabel(c)}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Цвет</legend>
        <button
          className="text-button"
          type="button"
          onClick={() =>
            setDraft({ ...draft, colors: null, excluded_colors: [] })
          }
        >
          Любой цвет
        </button>
        {(result.facets.colors ?? [])
          .filter(
            (c, i, all) =>
              all.findIndex((x) => colorLabel(x) === colorLabel(c)) === i,
          )
          .map((c) => (
            <label className="filter-option" key={c}>
              <input
                type="checkbox"
                checked={
                  !!draft.colors?.some((x) => colorLabel(x) === colorLabel(c))
                }
                onChange={() => {
                  const equivalent = (result.facets.colors ?? []).filter(
                    (x) => colorLabel(x) === colorLabel(c),
                  );
                  const enabled = draft.colors?.some((x) =>
                    equivalent.includes(x),
                  );
                  setDraft({
                    ...draft,
                    colors: enabled
                      ? (draft.colors ?? []).filter(
                          (x) => !equivalent.includes(x),
                        )
                      : [...(draft.colors ?? []), ...equivalent],
                  });
                }}
              />
              <i className="color-dot" style={{ background: colorSwatch(c) }} />
              {colorLabel(c)}
            </label>
          ))}
      </fieldset>
      <fieldset>
        <legend>Цена за единицу, ₸</legend>
        <div className="price-inputs">
          {(["min", "max"] as const).map((key) => (
            <label key={key}>
              {key === "min" ? "От" : "До"}
              <input
                inputMode="numeric"
                type="number"
                min={0}
                step={1}
                value={draft.budget?.[key] ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    budget: {
                      min: draft.budget?.min ?? null,
                      max: draft.budget?.max ?? null,
                      [key]:
                        e.target.value === "" ? null : Number(e.target.value),
                    },
                  })
                }
              />
            </label>
          ))}
        </div>
      </fieldset>
      <label>
        Выбор
        <select
          value={draft.selection}
          onChange={(e) =>
            setDraft({
              ...draft,
              selection: e.target.value as ViewFilters["selection"],
            })
          }
        >
          <option value="all">Все товары</option>
          <option value="selected">Выбранные</option>
          <option value="unselected">Не выбранные</option>
        </select>
      </label>
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
      <div className="filter-actions">
        <button className="studio-button primary" disabled={busy}>
          Применить фильтры
        </button>
        <button
          type="button"
          className="text-button"
          disabled={busy}
          onClick={onReset}
        >
          Сбросить фильтры
        </button>
      </div>
    </form>
  );
}
