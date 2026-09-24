"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  AlertCircle,
  ExternalLink,
  ImageOff,
  LoaderCircle,
  X,
} from "lucide-react";
import { api, request } from "@/lib/api";
import {
  categoryLabel,
  colorLabel,
  formatPrice,
  safeWebUrl,
} from "@/lib/format";
import type { Product, ProductDetails as Details } from "@/lib/types";

export function ProductDetails({
  product,
  sessionId,
  researchMode = false,
  onClose,
}: {
  product: Product;
  sessionId: string;
  researchMode?: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [details, setDetails] = useState<Details | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageFailed, setImageFailed] = useState(false);
  const [activeImage, setActiveImage] = useState(product.primary_image);
  const image = safeWebUrl(activeImage);
  const gallery = [
    ...new Set([product.primary_image, ...product.images]),
  ].filter((url): url is string => !!safeWebUrl(url));

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const controller = new AbortController();
    (researchMode ? request<Details>(`/researches/${sessionId}/products/${encodeURIComponent(product.id)}`, {signal:controller.signal})
      : api.product(sessionId, product.id, controller.signal))
      .then((result) => {
        if (!controller.signal.aborted) setDetails(result);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted)
          setError(
            err instanceof Error
              ? err.message
              : "Не удалось загрузить информацию.",
          );
      });
    return () => {
      controller.abort();
      element?.close();
      document.body.style.overflow = previousOverflow;
    };
  }, [product.id, sessionId, researchMode]);

  const source = safeWebUrl(details?.source_url);
  const sources = (details?.offers ?? []).flatMap((offer) => {
    const productUrl = safeWebUrl(offer.product_url);
    return productUrl ? [{ ...offer, product_url: productUrl }] : [];
  });
  return (
    <dialog
      ref={dialog}
      className="product-dialog"
      aria-labelledby="product-title"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="dialog-content">
        <button
          className="dialog-close icon-button"
          onClick={onClose}
          aria-label="Закрыть карточку"
        >
          <X size={19} />
        </button>
        <div className="dialog-image">
          {image && !imageFailed ? (
            <Image
              width={800}
              height={800}
              src={image}
              alt={product.name}
              referrerPolicy="no-referrer"
              onError={() => setImageFailed(true)}
            />
          ) : (
            <ImageOff size={38} />
          )}
        </div>
        <div className="dialog-body">
          {gallery.length > 1 && (
            <div className="gallery-controls" aria-label="Фотографии товара">
              {gallery.map((url, index) => (
                <button
                  key={url}
                  className="studio-button"
                  aria-pressed={url === activeImage}
                  onClick={() => {
                    setActiveImage(url);
                    setImageFailed(false);
                  }}
                >
                  Фото {index + 1}
                </button>
              ))}
            </div>
          )}
          <span className="eyebrow">{categoryLabel(product.category)}</span>
          <h2 id="product-title">{product.name}</h2>
          {product.colors.length > 0 && (
            <p className="muted">
              Цвет в источнике:{" "}
              {product.colors.map((c) => c.original_color).join(", ")}
            </p>
          )}
          <p className="dialog-price">
            {formatPrice(product.price_kzt)}
            <span>за единицу</span>
          </p>
          {product.colors.length > 0 && (
            <div className="intent-tags">
              {product.colors.map((color, i) => (
                <span key={`${color.original_color}-${i}`}>
                  {colorLabel(color.normalized_color)}
                </span>
              ))}
            </div>
          )}
          {product.description && (
            <p className="product-description">{product.description}</p>
          )}
          {researchMode && (
            <dl>
              {product.material && <div><dt>Материал</dt><dd>{product.material}</dd></div>}
              {product.dimensions && <div><dt>Размеры</dt><dd>{product.dimensions}</dd></div>}
              {product.capacity && <div><dt>Объём</dt><dd>{product.capacity}</dd></div>}
              {product.brand && <div><dt>Бренд</dt><dd>{product.brand}</dd></div>}
              <div><dt>Каталог</dt><dd>Обновляется автоматически</dd></div>
            </dl>
          )}
          {details?.availability && (
            <div className="availability-details">
              <h3>Доступность</h3>
              {[...details.availability.current, ...details.availability.incoming,
                ...details.availability.remote, ...details.availability.alternatives].map((entry, index) => (
                <p key={`${entry.state}:${entry.locationCode ?? index}`} className="muted">
                  {entry.locationLabel ?? entry.state}: {entry.free === null
                    ? "количество не указано"
                    : `${entry.free.toLocaleString("ru-RU")} свободно`}
                  {entry.reserved === null ? "" : `, ${entry.reserved.toLocaleString("ru-RU")} в резерве`}
                  {entry.expectedAt === null ? "" : `, ожидается ${new Date(entry.expectedAt).toLocaleDateString("ru-RU")}`}
                </p>
              ))}
            </div>
          )}
          {(!researchMode || details?.supplier) && <details className="details-section">
            <summary>Техническая информация</summary>
            {!details && !error && (
              <p className="detail-loading">
                <LoaderCircle size={16} className="spin" />
                Загружаем данные…
              </p>
            )}
            {error && (
              <p className="field-error" role="alert">
                <AlertCircle size={14} />
                {error}
              </p>
            )}
            {details && (
              <>
                <dl>
                  <div>
                    <dt>Поставщик</dt>
                    <dd>
                      {details.supplier === "oasis"
                        ? "Oasis"
                        : details.supplier === "ucontay"
                          ? "Ucontay"
                          : details.supplier}
                    </dd>
                  </div>
                  <div>
                    <dt>Количество на складе</dt>
                    <dd>
                      {details.stock_quantity === null
                        ? "Не определено"
                        : `${new Intl.NumberFormat("ru-RU").format(details.stock_quantity)} шт.`}
                    </dd>
                  </div>
                  <div>
                    <dt>Цена в источнике</dt>
                    <dd>
                      {details.original_price === null
                        ? "Не определена"
                        : `${details.original_price} ${details.original_currency ?? ""}`}
                    </dd>
                  </div>
                  <div>
                    <dt>Проверено</dt>
                    <dd>
                      {new Date(details.fetched_at).toLocaleString("ru-RU", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </dd>
                  </div>
                </dl>
                {sources.length > 1 ? (
                  <div className="source-links">
                    {sources.map((offer) => (
                      <a key={`${offer.supplier}:${offer.product_url}`} className="source-link"
                        href={offer.product_url} target="_blank" rel="noopener noreferrer">
                        Открыть у {offer.supplier} <ExternalLink size={14} />
                      </a>
                    ))}
                  </div>
                ) : source && (
                  <a
                    className="source-link"
                    href={source}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Открыть на сайте поставщика
                    <ExternalLink size={14} />
                  </a>
                )}
              </>
            )}
          </details>}
        </div>
      </div>
    </dialog>
  );
}
