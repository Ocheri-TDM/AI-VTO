import type { Product } from "@/lib/types";
import { categoryLabel, formatPrice } from "@/lib/format";
import { ProductCard } from "../product-card";
export function SelectedView({
  products,
  disabled,
  onRemove,
  onClear,
  onDetails,
  onAsk,
}: {
  products: Product[];
  disabled: boolean;
  onRemove: (id: string) => void;
  onClear: () => void;
  onDetails: (p: Product) => void;
  onAsk: () => void;
}) {
  const prices = products.flatMap((p) =>
    p.price_kzt === null ? [] : [p.price_kzt],
  );
  const categories = new Map<string, number>();
  products.forEach((p) =>
    categories.set(
      categoryLabel(p.category),
      (categories.get(categoryLabel(p.category)) ?? 0) + 1,
    ),
  );
  return (
    <div className="selected-view">
      {!products.length ? (
        <div className="workspace-empty">
          <h3>Здесь будет ваш выбор</h3>
          <p>
            Отметьте товары в подборке или попросите AI выбрать лучшие варианты.
          </p>
          <button className="studio-button" onClick={onAsk}>
            Обсудить с AI
          </button>
        </div>
      ) : (
        <>
          <div className="selection-summary">
            <h3>Выбрано {products.length} товаров</h3>
            <p>
              {[...categories].map(([name, n]) => `${name}: ${n}`).join(" · ")}
            </p>
            {prices.length > 0 && (
              <dl>
                <div>
                  <dt>Средняя цена</dt>
                  <dd>
                    {formatPrice(
                      Math.round(
                        prices.reduce((a, b) => a + b, 0) / prices.length,
                      ),
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Диапазон</dt>
                  <dd>
                    {formatPrice(Math.min(...prices))} —{" "}
                    {formatPrice(Math.max(...prices))}
                  </dd>
                </div>
              </dl>
            )}
            <div className="button-row">
              <button
                className="studio-button"
                disabled={disabled}
                onClick={onAsk}
              >
                Изменить с AI
              </button>
              <button
                className="text-button"
                disabled={disabled}
                onClick={onClear}
              >
                Очистить выбор
              </button>
            </div>
          </div>
          <p className="muted">
            Все выбранные товары, включая скрытые текущими фильтрами.
          </p>
          <div className="product-grid">
            {products.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                selected
                disabled={disabled}
                onSelect={() => onRemove(p.id)}
                onDetails={() => onDetails(p)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
