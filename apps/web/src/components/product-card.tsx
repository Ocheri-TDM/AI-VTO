"use client";

import { useState } from "react";
import Image from "next/image";
import { Check, ImageOff, MoveUpRight } from "lucide-react";
import {
  categoryLabel,
  colorLabel,
  colorSwatch,
  formatPrice,
  safeWebUrl,
} from "@/lib/format";
import type { Product } from "@/lib/types";

export function ProductCard({
  product,
  selected,
  disabled = false,
  onSelect,
  onDetails,
}: {
  product: Product;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
  onDetails: () => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const image = safeWebUrl(product.primary_image);
  const color = product.colors[0];
  return (
    <article className={`product-card ${selected ? "selected" : ""}`}>
      <div
        className={`product-image-wrap ${image && !imageLoaded && !imageFailed ? "image-loading" : ""}`}
      >
        <button
          className="image-details-button"
          onClick={onDetails}
          aria-label={`Подробнее: ${product.name}`}
        >
          {image && !imageFailed ? (
            <Image
              width={600}
              height={600}
              src={image}
              alt={product.name}
              loading="lazy"
              referrerPolicy="no-referrer"
              onLoad={() => setImageLoaded(true)}
              onError={() => setImageFailed(true)}
            />
          ) : (
            <div className="missing-image">
              <ImageOff size={27} />
              <span>Нет изображения</span>
            </div>
          )}
          <span className="details-arrow">
            <MoveUpRight size={16} />
          </span>
        </button>
        <label className="product-checkbox">
          <input
            type="checkbox"
            checked={selected}
            disabled={disabled || product.price_kzt === null}
            onChange={onSelect}
            aria-label={`Выбрать ${product.name}`}
          />
          <span>{selected && <Check size={14} />}</span>
        </label>
      </div>
      <div className="product-info">
        <span className="product-category">
          {categoryLabel(product.category)}
        </span>
        <button className="product-name" onClick={onDetails}>
          {product.name}
        </button>
        <div className="product-meta">
          <strong>{formatPrice(product.price_kzt)}</strong>
          {color && (
            <span className="product-color" title={color.original_color}>
              <i
                style={{ backgroundColor: colorSwatch(color.normalized_color) }}
              />
              {colorLabel(color.normalized_color)}
            </span>
          )}
        </div>
        {product.availability &&
        (product.availability.current.length > 0 || product.availability.incoming.length > 0) ? (
          <small className="product-availability">
            {product.availability.current.length > 0
              ? product.availability.availableNow === null
                ? "В наличии"
                : `${product.availability.availableNow.toLocaleString("ru-RU")} свободно`
              : ""}
            {product.availability.current.length > 0 && product.availability.incoming.length > 0 ? " · " : ""}
            {product.availability.incoming.length > 0
              ? product.availability.incomingConfirmed === null
                ? "Ожидается поставка"
                : `${product.availability.incomingConfirmed.toLocaleString("ru-RU")} в пути`
              : ""}
          </small>
        ) : null}
      </div>
    </article>
  );
}
