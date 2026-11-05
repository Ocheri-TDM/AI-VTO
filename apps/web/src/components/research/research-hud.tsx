'use client';
import type { BranchAction, Research, ResearchFilters } from '@/lib/research';
import { ResearchConditions, ResearchFilterPanel } from './research-filters';
import {QueryCategories} from './query-categories';
import {Select} from '@/components/common/select';

export function hasAdditionalFilters(filters:ResearchFilters){
  return Object.entries(filters).some(([key,value])=>key!=='origins'&&(Array.isArray(value)?value.length>0:
    typeof value==='object'&&value!==null?Object.values(value).some(v=>Array.isArray(v)&&v.length>0):typeof value==='number'||value===true));
}

export function CollectionSummary({count,hidden=0,onOpen}:{count:number;hidden?:number;onOpen:()=>void}){
  return <aside className="research-hud-selected" aria-label="Выбранные товары" data-testid="selected-products">
    <strong>Выбранные товары</strong><span className="research-selected-count">{count}</span>
    {!!hidden&&<small>Вне текущей выдачи: {hidden}</small>}
    <button className="studio-button primary" onClick={onOpen}>Открыть подборку</button>
  </aside>;
}

export function ResearchHud({research,pending,disabled,grouped,onGrouped,onFilters,onReset,onSelected,onMobileFilters,onCategoryAction}:{
  research:Research;pending:boolean;disabled:boolean;grouped:boolean;onGrouped:(value:boolean)=>void;
  onFilters:(value:ResearchFilters)=>void;onReset:()=>void;onSelected:()=>void;onMobileFilters:()=>void;
  onCategoryAction?:(action:BranchAction)=>void;
}) {
  return <section className="research-results-hud" aria-label="Управление результатами">
    <div className="research-hud-filters" data-testid="result-filters">
      <QueryCategories research={research} disabled={disabled} pending={pending} onAction={action=>onCategoryAction?.(action)}/>
      <div className="research-hud-topline"><strong>Фильтры</strong><div className="research-view-setting">Вид
        <Select label="Представление товаров" hideLabel compact value={grouped?'models':'variants'} onChange={value=>onGrouped(value==='models')} options={[{value:'variants',label:'Все варианты'},{value:'models',label:'По моделям'}]}/></div></div>
      <div className="research-desktop-filters"><ResearchFilterPanel research={research} inline disabled={disabled} onChange={onFilters}/></div>
      <button className="studio-button research-mobile-filters-button" onClick={onMobileFilters}>Фильтры</button>
      <ResearchConditions research={research}/>
      <div className="research-hud-status">{hasAdditionalFilters(research.view.filters)&&<button className="studio-button" onClick={onReset} disabled={disabled}>Сбросить фильтры</button>}
        {pending&&<small role="status">Обновляю результаты…</small>}</div>
    </div>
    <CollectionSummary count={research.view.selected_ids.length} hidden={research.hidden_selected_count} onOpen={onSelected}/>
  </section>;
}
