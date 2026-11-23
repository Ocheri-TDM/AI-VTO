'use client';
import type { BranchAction, EffectiveEdit, Research, ResearchFilters } from '@/lib/research';
import { ResearchConditions, ResearchFilterPanel } from './research-filters';
import {QueryCategories} from './query-categories';
import {Select} from '@/components/common/select';

export function hasAdditionalFilters(filters:ResearchFilters,overrides:EffectiveEdit[]=[]){
  const active=(value:unknown):boolean=>Array.isArray(value)?value.length>0:typeof value==='object'&&value!==null?Object.values(value).some(active):typeof value==='number'||value===true;
  return overrides.length>0||Object.entries(filters).some(([key,value])=>key!=='origins'&&(Array.isArray(value)?value.length>0:
    active(value)));
}

export function ResearchHud({research,pending,disabled,grouped,onGrouped,onFilters,onEffectiveChange,onReset,onMobileFilters,onCategoryAction,loading=false}:{
  research:Research;pending:boolean;disabled:boolean;grouped:boolean;onGrouped:(value:boolean)=>void;
  onFilters:(value:ResearchFilters)=>void;onReset:()=>void;onMobileFilters:()=>void;
  onCategoryAction?:(action:BranchAction)=>void;
  onEffectiveChange?:(edit:EffectiveEdit)=>void;
  loading?:boolean;
}) {
  return <section className="research-results-hud" aria-label="Управление результатами">
    <div className="research-hud-filters" data-testid="result-filters">
      <QueryCategories research={research} disabled={disabled} pending={pending} onAction={action=>onCategoryAction?.(action)}/>
      <div className="research-hud-topline"><strong>Фильтры</strong><div className="research-view-setting">Вид
        <Select label="Представление товаров" hideLabel compact value={grouped?'models':'variants'} onChange={value=>onGrouped(value==='models')} options={[{value:'variants',label:'Все варианты'},{value:'models',label:'По моделям'}]}/></div></div>
      <div className="research-desktop-filters"><ResearchFilterPanel research={research} inline loading={loading} disabled={disabled} onChange={onFilters} onEffectiveChange={onEffectiveChange}/></div>
      <button className="studio-button research-mobile-filters-button" disabled={loading} onClick={onMobileFilters}>Фильтры</button>
      <ResearchConditions research={research}/>
      <div className="research-hud-status">{hasAdditionalFilters(research.view.filters,research.view.effective_overrides)&&<button className="studio-button" onClick={onReset} disabled={disabled}>Сбросить фильтры</button>}
        {pending&&<small role="status">Обновляю результаты…</small>}</div>
    </div>
  </section>;
}
