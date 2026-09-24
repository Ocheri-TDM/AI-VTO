import {
  AlertCircle,
  Check,
  Circle,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import type { SupplierProgress } from "@/lib/types";

const waiting: SupplierProgress[] = ["gifts", "ucontay"].map((supplier) => ({
  supplier: supplier as SupplierProgress["supplier"],
  status: "pending",
  products_discovered: 0,
  products_accepted: 0,
  pages_scanned: 0,
  duration_ms: 0,
}));

function supplierMessage(item: SupplierProgress) {
  if (item.error_code === "SUPPLIER_CAPTCHA_REQUIRED")
    return "На сайте требуется CAPTCHA. Доступ к поиску ограничен.";
  if (item.error_code === "CURRENCY_CONVERSION_ERROR")
    return "Не удалось перевести цены в тенге. Проверьте настройку курса RUB → KZT.";
  if (item.message) return item.message;
  if (item.status === "failed")
    return "Поставщик временно недоступен. Остальные результаты сохранены.";
  return null;
}

export function SupplierProgressPanel({
  suppliers,
  started,
}: {
  suppliers?: SupplierProgress[];
  started: boolean;
}) {
  return (
    <section
      className="suppliers"
      aria-label="Статус поиска по поставщикам"
      aria-live="polite"
    >
      <div className="eyebrow">
        ИСТОЧНИКИ ПОИСКА <span>02</span>
      </div>
      {(suppliers?.length ? suppliers : waiting).map((supplier) => {
        const message = supplierMessage(supplier);
        return (
          <div key={supplier.supplier} className="supplier-row-wrap">
            <div className="supplier-row">
              <span className={`supplier-logo ${supplier.supplier}`}>
                {supplier.supplier === "gifts"
                  ? "g"
                  : supplier.supplier === "oasis"
                    ? "o"
                    : "u"}
              </span>
              <div className="supplier-copy">
                <strong>
                  {supplier.supplier === "gifts"
                    ? "Gifts.ru"
                    : supplier.supplier === "oasis"
                      ? "Oasis"
                      : "Ucontay"}
                </strong>
                <span>
                  {supplier.status === "completed"
                    ? "Проверка завершена"
                    : supplier.status === "running"
                      ? "Проверяем товары на сайте"
                      : supplier.status === "failed"
                        ? "Поиск недоступен"
                        : started
                          ? "В очереди на поиск"
                          : "Готов к поиску"}
                </span>
              </div>
              {supplier.status === "running" ? (
                <LoaderCircle size={16} className="spin status-progress" />
              ) : supplier.status === "completed" ? (
                <Check size={17} className="status-success" />
              ) : supplier.status === "failed" ? (
                <AlertCircle size={17} className="status-warning" />
              ) : (
                <Circle size={9} className="status-pending" />
              )}
            </div>
            {message && (
              <p className="supplier-warning">
                <AlertCircle size={13} />
                <span>{message}</span>
              </p>
            )}
            {!!supplier.warnings?.length && (
              <p className="supplier-warning">
                <AlertCircle size={13} />
                <span>
                  Поиск ограничен: проверена часть доступных карточек. Найденные
                  варианты показаны ниже.
                </span>
              </p>
            )}
          </div>
        );
      })}
      <div className="verification-note">
        <ShieldCheck size={14} />
        <span>Проверяем цвет, цену и наличие</span>
      </div>
    </section>
  );
}
