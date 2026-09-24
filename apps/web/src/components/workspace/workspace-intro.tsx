"use client";
import { useState } from "react";
import { ArrowUpRight, Sparkles } from "lucide-react";
export function WorkspaceIntro({
  loading,
  onSearch,
}: {
  loading: boolean;
  onSearch: (text: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <section className="workspace-intro">
      <div className="intro-mark">
        <Sparkles size={32} strokeWidth={1.3} />
      </div>
      <span className="eyebrow">ОТ ИДЕИ К ТОЧНОМУ ВЫБОРУ</span>
      <h2>
        Хороший подарок
        <br />
        начинается с понимания.
      </h2>
      <p>
        Опишите, что нужно подобрать.
        <br />
        Мы найдём реальные товары и поможем выбрать подходящие.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim() && !loading) onSearch(text.trim());
        }}
      >
        <label className="sr-only" htmlFor="initial-query">
          Что нужно подобрать?
        </label>
        <textarea
          id="initial-query"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Что нужно подобрать?"
          maxLength={2000}
          rows={3}
          disabled={loading}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              if (text.trim() && !loading) onSearch(text.trim());
            }
          }}
        />
        <div>
          <span>Идея, событие, тираж — в свободной форме</span>
          <button
            className="send-button"
            disabled={loading || !text.trim()}
            aria-label="Начать подбор"
          >
            <ArrowUpRight size={22} />
          </button>
        </div>
      </form>
      <div className="initial-examples">
        {[
          "Темно-синие рюкзаки и термокружки, 300 шт.",
          "Подарки для IT-конференции до 15 000 ₸",
          "Премиальные подарки для руководителей",
        ].map((example) => (
          <button
            disabled={loading}
            key={example}
            onClick={() => {
              setText(example);
              document.getElementById("initial-query")?.focus();
            }}
          >
            {example}
            <ArrowUpRight size={14} />
          </button>
        ))}
      </div>
      <div className="intro-footnote">
        <span>Реальные товары</span>
        <i />
        <span>Проверенное наличие</span>
        <i />
        <span>Цены в тенге</span>
      </div>
    </section>
  );
}
