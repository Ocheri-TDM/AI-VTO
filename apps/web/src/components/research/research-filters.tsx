'use client';
import { useEffect, useRef, useState } from 'react';
import type { Research, ResearchFilters } from '@/lib/research';
import { categoryLabel, colorLabel } from '@/lib/format';

function NumberFilter({label,value,min,onChange,disabled}:{label:string;value:number|null;min:number;onChange:(value:number|null)=>void;disabled:boolean}) {
  const [draft,setDraft]=useState({saved:value,text:value===null?'':String(value)});
  if(draft.saved!==value)setDraft({saved:value,text:value===null?'':String(value)});
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current);},[value]);
  return <label className="research-filter-field">{label}<input type="number" min={min} disabled={disabled} value={draft.text}
    onChange={e=>{const next=e.target.value;setDraft({saved:value,text:next});if(timer.current)clearTimeout(timer.current);
      if(e.target.validity.valid)timer.current=setTimeout(()=>onChange(next===''?null:Number(next)),300);}}/>
  </label>;
}

export function ResearchFilterPanel({research,onChange,disabled,inline=false}:{research:Research;onChange:(filters:ResearchFilters)=>void;disabled:boolean;inline?:boolean}) {
  const [search,setSearch]=useState('');
  const filters=research.view.filters;
  const brands=research.facets.brand??[];
  const latest=useRef(filters);useEffect(()=>{latest.current=filters;},[filters]);
  const brandOptions=<>
    <label>Поиск бренда<input aria-label="Поиск бренда" placeholder="Найти бренд" value={search} onChange={e=>setSearch(e.target.value)}/></label>
    <div className="research-brand-options">{brands.filter(b=>(b.label??b.value).toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(b=><label key={b.value}>
      <input type="checkbox" disabled={disabled} checked={filters.brands.includes(b.value)} onChange={e=>onChange({...filters,brands:e.target.checked?[...filters.brands,b.value]:filters.brands.filter(v=>v!==b.value)})}/>
      {b.label??b.value} · {b.count}</label>)}</div></>;
  return <div className={inline?'research-inline-filters':'research-filters'}>
    {inline?<div className="research-filter-field research-brand-field"><span>Бренд</span><details className="research-brand-select"><summary>{filters.brands.length?`Выбрано ${filters.brands.length}`:'Все бренды'}</summary><div className="research-brand-popover">{brandOptions}</div></details></div>:brandOptions}
    {(['color','material','size'] as const).map(field=><label key={field}>{{color:'Цвет',material:'Материал',size:'Размер'}[field]}
      <select disabled={disabled} value={(field==='color'?filters.colors:field==='material'?filters.materials:filters.sizes)[0]??''} onChange={e=>onChange({...filters,[field==='color'?'colors':field==='material'?'materials':'sizes']:e.target.value?[e.target.value]:[]})}>
        <option value="">Все</option>{(research.facets[field]??[]).map(v=><option key={v.value} value={v.value}>{field==='color'?colorLabel(v.value):v.label??v.value} · {v.count}</option>)}
      </select></label>)}
    {(['min_price','max_price','minimum_quantity'] as const).map(field=><NumberFilter key={field}
      label={{min_price:'Цена от, ₸',max_price:'Цена до, ₸',minimum_quantity:'Тираж от, шт.'}[field]}
      min={field==='minimum_quantity'?1:0} value={filters[field]} disabled={disabled}
      onChange={value=>{if(value!==latest.current[field])onChange({...latest.current,[field]:value});}}/>)}
  </div>;
}

export function ResearchConditions({research}:{research:Research}) {
  const conditions:{text:string;source:string}[]=[];
  for(const branch of research.intent.branches??[]) {
    const values=[...branch.colors.map(colorLabel),...branch.materials,branch.quantity!==null?`от ${branch.quantity} шт.`:'',branch.max_price!==null?`до ${branch.max_price} ₸`:'',branch.min_price!==null?`от ${branch.min_price} ₸`:''].filter(Boolean);
    if(values.length)conditions.push({text:`${branch.category.startsWith('dynamic:')?branch.label:categoryLabel(branch.category)}: ${values.join(', ')}`,source:'Требование запроса / его ревизии'});
  }
  for(const brand of research.intent.brands??[])conditions.push({text:`Бренд: ${brand}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_materials??[])conditions.push({text:`Без: ${value}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_categories??[])conditions.push({text:`Исключено: ${value}`,source:'Из исходного запроса'});
  const f=research.view.filters;
  for(const value of research.view.preferences??[])conditions.push({text:`Сначала: ${colorLabel(value)}`,source:'Предпочтение · не обязательный фильтр'});
  for(const [field,label,values] of [['brands','Бренд',f.brands],['colors','Цвет',f.colors.map(colorLabel)],['materials','Материал',f.materials],['sizes','Размер',f.sizes],['excluded_materials','Без',f.excluded_materials],['required_terms','Обязательно',f.required_terms]] as [string,string,string[]][]) {
    if(values.length)conditions.push({text:`${label}: ${values.map(v=>v||'Бренд не указан').join(', ')}`,source:f.origins[field]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  }
  for(const [key,label,suffix] of [['min_price','Цена от','₸'],['max_price','Цена до','₸'],['minimum_quantity','Тираж от','шт.']] as const)if(f[key]!==null)conditions.push({text:`${label}: ${f[key]} ${suffix}`,source:f.origins[key]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  return <div className="research-suggestions" aria-label="Активные условия">{conditions.map((c,i)=><span className="studio-button" title={c.source} key={i}>{c.text}</span>)}</div>;
}
