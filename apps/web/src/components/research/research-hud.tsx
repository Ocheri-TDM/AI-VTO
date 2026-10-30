'use client';
import type { Research, ResearchFilters } from '@/lib/research';
import { ResearchConditions, ResearchFilterPanel } from './research-filters';

export function hasAdditionalFilters(filters:ResearchFilters){
  return Object.entries(filters).some(([key,value])=>key!=='origins'&&(Array.isArray(value)?value.length>0:
    typeof value==='object'&&value!==null?Object.values(value).some(v=>Array.isArray(v)&&v.length>0):typeof value==='number'||value===true));
}

export function ResearchHud({research,pending,disabled,grouped,onGrouped,onFilters,onReset,onSelected,onMobileFilters}:{
  research:Research;pending:boolean;disabled:boolean;grouped:boolean;onGrouped:(value:boolean)=>void;
  onFilters:(value:ResearchFilters)=>void;onReset:()=>void;onSelected:()=>void;onMobileFilters:()=>void;
}) {
  return <section className="research-results-hud" aria-label="Управление результатами">
    <div className="research-hud-filters" data-testid="result-filters">
      <div className="research-hud-topline"><strong>Фильтры</strong><label className="research-view-setting">Вид
        <select aria-label="Представление товаров" value={grouped?'models':'variants'} onChange={e=>onGrouped(e.target.value==='models')}><option value="variants">Все варианты</option><option value="models">По моделям</option></select></label></div>
      <div className="research-desktop-filters"><ResearchFilterPanel research={research} inline disabled={disabled} onChange={onFilters}/></div>
      <button className="studio-button research-mobile-filters-button" onClick={onMobileFilters}>Фильтры</button>
      <ResearchConditions research={research}/>
      <div className="research-hud-status">{hasAdditionalFilters(research.view.filters)&&<button className="studio-button" onClick={onReset} disabled={disabled}>Сбросить фильтры</button>}
        {pending&&<small role="status">Обновляю результаты…</small>}</div>
    </div>
    <aside className="research-hud-selected" aria-label="Выбранные товары" data-testid="selected-products">
      <strong>Выбранные товары</strong><span className="research-selected-count">{research.view.selected_ids.length}</span>
      {!!research.hidden_selected_count&&<small>Скрыто фильтрами: {research.hidden_selected_count}</small>}
      <button className="studio-button primary" onClick={onSelected}>Открыть подборку</button>
    </aside>
  </section>;
}
