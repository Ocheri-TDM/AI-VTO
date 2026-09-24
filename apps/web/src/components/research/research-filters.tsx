'use client';
import type { Research, ResearchFilters } from '@/lib/research';
import { categoryLabel, colorLabel } from '@/lib/format';

const labels: Record<string,string>={category:'Категория',color:'Оттенок',material:'Материал',brand:'Бренд',capacity:'Объём'};
export function ResearchFilterPanel({research,onChange,disabled}:{research:Research;onChange:(filters:ResearchFilters)=>void;disabled:boolean}){
  const filters=research.view.filters;
  return <div className="research-filters">
    <label>От, ₸<input type="number" min="0" defaultValue={filters.min_price??''} key={`min-${research.view.version}`}
      onBlur={e=>{const v=e.target.value?Number(e.target.value):null;if(v!==filters.min_price)onChange({...filters,min_price:v});}} disabled={disabled}/></label>
    <label>До, ₸<input type="number" min="0" defaultValue={filters.max_price??''} key={`max-${research.view.version}`}
      onBlur={e=>{const v=e.target.value?Number(e.target.value):null;if(v!==filters.max_price)onChange({...filters,max_price:v});}} disabled={disabled}/></label>
    {Object.entries(research.facets).map(([field,values])=><label key={field}>{labels[field]??field}
      <select disabled={disabled} value={field==='category'?filters.categories[0]??'':field==='color'?filters.colors[0]??'':filters.attributes[field]?.[0]??''}
        onChange={e=>{const value=e.target.value?[e.target.value]:[];onChange({...filters,...(field==='category'?{categories:value}:field==='color'?{colors:value}:{attributes:{...filters.attributes,[field]:value}})});}}>
        <option value="">Все</option>{values.map(v=><option key={v.value} value={v.value}>{field==='category'?categoryLabel(v.value):field==='color'?colorLabel(v.value):v.value} · {v.count}</option>)}
      </select></label>)}
  </div>;
}
