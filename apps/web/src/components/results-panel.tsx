"use client";

import { useMemo, useState } from "react";
import {
  ArrowRight,
  Backpack,
  Bookmark,
  Check,
  Clock3,
  Coffee,
  Layers2,
  LoaderCircle,
  PackageSearch,
  Pencil,
  RefreshCw,
  SearchCheck,
  Sparkles,
} from "lucide-react";
import type { Product, SearchSession } from "@/lib/types";
import { ProductCard } from "./product-card";

interface Props {
  session: SearchSession | null;
  running: boolean;
  selected: Set<string>;
  onSelect: (id: string) => void;
  onDetails: (product: Product) => void;
  onRefresh: () => void;
  onSearch: () => void;
}

export function ResultsPanel({
  session,
  running,
  selected,
  onSelect,
  onDetails,
  onRefresh,
  onSearch,
}: Props) {
  const [tab, setTab] = useState<"all" | "selected">("all");
  const [sort, setSort] = useState("relevance");
  const products = useMemo(() => {
    const items = (session?.products ?? []).filter(
      (product) => tab !== "selected" || selected.has(product.id),
    );
    if (sort === "price")
      items.sort(
        (a, b) => (a.price_kzt ?? Infinity) - (b.price_kzt ?? Infinity),
      );
    return items;
  }, [session?.products, selected, tab, sort]);

  return (
    <section className="results-panel" aria-labelledby="results-heading">
      <div className="panel-heading">
        <div>
          <Layers2 size={17} />
          <h2 id="results-heading">Подборка</h2>
          {session && (
            <span className="count-badge">{session.products.length}</span>
          )}
        </div>
        <span className="step-count">02 / 02</span>
      </div>
      <div className="results-toolbar">
        <div className="result-tabs" role="tablist" aria-label="Товары">
          <button
            role="tab"
            aria-selected={tab === "all"}
            onClick={() => setTab("all")}
            className={tab === "all" ? "active" : ""}
          >
            Все варианты
          </button>
          <button
            role="tab"
            aria-selected={tab === "selected"}
            onClick={() => setTab("selected")}
            className={tab === "selected" ? "active" : ""}
          >
            <Bookmark size={13} />
            Выбранное{selected.size > 0 && <span>{selected.size}</span>}
          </button>
        </div>
        {session ? (
          <button
            className="icon-button"
            aria-label="Обновить цены и наличие"
            title="Повторить поиск на сайтах"
            disabled={running}
            onClick={onRefresh}
          >
            <RefreshCw size={15} className={running ? "spin" : ""} />
          </button>
        ) : (
          <span className="currency-label">
            Цены в KZT <span>₸</span>
          </span>
        )}
      </div>
      {!session && !running && (
        <div className="empty-intro">
          <div className="product-illustration" aria-hidden="true">
            <div className="illustration-orbit" />
            <div className="outline-product outline-notebook">
              <Bookmark size={45} strokeWidth={1} />
              <span>01</span>
            </div>
            <div className="outline-product outline-mug">
              <Coffee size={65} strokeWidth={1} />
              <span className="illustration-check">
                <Check size={12} />
              </span>
            </div>
            <div className="outline-product outline-backpack">
              <Backpack size={47} strokeWidth={1} />
              <span>03</span>
            </div>
            <Sparkles
              className="illustration-spark"
              size={21}
              strokeWidth={1.4}
            />
          </div>
          <span className="eyebrow intro-eyebrow">
            ХОРОШИЙ МЕРЧ НАЧИНАЕТСЯ С ИДЕИ
          </span>
          <h3>
            Ваша следующая
            <br />
            подборка — здесь.
          </h3>
          <p>
            Опишите задачу в чате. Мы найдём товары,
            <br className="desktop-break" /> проверим наличие и соберём варианты
            для вас.
          </p>
          <button className="primary-button" onClick={onSearch}>
            Найти по моему запросу <ArrowRight size={16} />
          </button>
          <div className="category-examples">
            <span>
              <Coffee size={14} />
              Термокружки
            </span>
            <span>
              <Backpack size={14} />
              Рюкзаки
            </span>
            <span>
              <Pencil size={14} />
              Канцелярия
            </span>
          </div>
          <div className="search-principles">
            <div>
              <span>01</span>
              <strong>Реальные товары</strong>
              <p>Напрямую с сайтов</p>
            </div>
            <div>
              <span>02</span>
              <strong>Проверенное наличие</strong>
              <p>Под ваш тираж</p>
            </div>
            <div>
              <span>03</span>
              <strong>Единая валюта</strong>
              <p>Все цены в тенге</p>
            </div>
          </div>
        </div>
      )}
      {running && (
        <div className="search-status" role="status">
          <LoaderCircle size={17} className="spin" />
          <div>
            <strong>Работаю с вашей подборкой</strong>
            <p>Ход выполнения отображается в диалоге.</p>
          </div>
        </div>
      )}
      {session && (
        <div className="results-summary">
          <span>
            {session.cache_hit ? (
              <>
                <Clock3 size={13} />
                Сохранённый поиск · до 60 минут
              </>
            ) : running ? (
              <>
                <SearchCheck size={13} />
                Проверяем данные на сайтах
              </>
            ) : (
              <>
                <SearchCheck size={13} />
                {session.status === "partial"
                  ? "Поиск завершён частично"
                  : session.status === "failed"
                    ? "Поиск не завершён"
                    : "Поиск завершён"}
              </>
            )}
          </span>
          {products.length > 0 && (
            <select
              aria-label="Сортировка товаров"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              <option value="relevance">По соответствию</option>
              <option value="price">Сначала дешевле</option>
            </select>
          )}
        </div>
      )}
      {products.length > 0 && (
        <div className="product-grid">
          {products.map((product) => (
            <ProductCard
              key={product.id}
              product={product}
              selected={selected.has(product.id)}
              onSelect={() => onSelect(product.id)}
              onDetails={() => onDetails(product)}
            />
          ))}
        </div>
      )}
      {running && products.length === 0 && (
        <div className="product-grid skeleton-grid" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div className="skeleton-card" key={i}>
              <div className="skeleton-image" />
              <div className="skeleton-line" />
              <div className="skeleton-line short" />
            </div>
          ))}
        </div>
      )}
      {session && !running && products.length === 0 && (
        <div className="empty-results">
          <span className="empty-results-icon">
            {tab === "selected" ? (
              <Bookmark size={27} />
            ) : (
              <PackageSearch size={30} />
            )}
          </span>
          <h3>
            {tab === "selected"
              ? "Сохраните то, что нравится"
              : session.status === "failed"
                ? "Не удалось получить товары"
                : "Подходящих товаров пока нет"}
          </h3>
          <p>
            {tab === "selected"
              ? "Отметьте товары в подборке — они появятся здесь."
              : session.status === "failed"
                ? "Посмотрите статусы источников и попробуйте обновить поиск."
                : "По текущим условиям нет подтверждённых вариантов. Попробуйте изменить цвет, тираж или категории."}
          </p>
          {tab === "selected" && (
            <button className="text-button" onClick={() => setTab("all")}>
              Ко всем вариантам <ArrowRight size={14} />
            </button>
          )}
        </div>
      )}
      {selected.size > 0 && (
        <div className="selection-bar">
          <span>
            <Check size={15} />
            <strong>Выбрано: {selected.size}</strong>
          </span>
          <p>Выбор сохранён в подборке</p>
        </div>
      )}
      <div className="results-footnote">
        <Clock3 size={13} />
        Результаты хранятся 60 минут. Цена и наличие могут измениться.
      </div>
    </section>
  );
}
