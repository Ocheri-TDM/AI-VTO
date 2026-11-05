'use client';
import { useEffect, useRef, useState } from 'react';
import type { Research, ResearchFilters } from '@/lib/research';
import { categoryLabel, colorLabel } from '@/lib/format';
import {MultiSelect, Select, SearchableSelect} from '@/components/common/select';

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
  const filters=research.view.filters;
  const brands=research.facets.brand??[];
  const sourceBrands=research.intent.brands??[];
  const sourceKeys=Object.entries(research.brand_labels??{}).filter(([,label])=>sourceBrands.includes(label)).map(([key])=>key);
  const latest=useRef(filters);useEffect(()=>{latest.current=filters;},[filters]);
  return <div className={inline?'research-inline-filters':'research-filters'}>
    <MultiSelect label="Бренд" searchLabel="Поиск бренда" disabled={disabled}
      value={[...filters.brands,...sourceKeys.filter(v=>!filters.brands.includes(v))]}
      options={brands.map(b=>({value:b.value,label:b.label??b.value,count:b.count,disabled:sourceKeys.includes(b.value),note:sourceKeys.includes(b.value)?'из запроса':undefined}))}
      summary={sourceBrands.length?sourceBrands.join(', '):undefined}
      help={!!sourceBrands.length&&'Бренд задан в запросе. Команда «бренд не важен» снимает это условие.'}
      onChange={values=>onChange({...filters,brands:[...filters.brands.filter(v=>sourceKeys.includes(v)),...values.filter(v=>!sourceKeys.includes(v))]})}/>
    {(['color','material','size'] as const).map(field=>{
      const Control=field==='material'?SearchableSelect:Select;
      return <Control key={field} label={{color:'Цвет',material:'Материал',size:'Размер'}[field]}
        disabled={disabled} value={(field==='color'?filters.colors:field==='material'?filters.materials:filters.sizes)[0]??''}
        onChange={value=>onChange({...filters,[field==='color'?'colors':field==='material'?'materials':'sizes']:value?[value]:[]})}
        options={[{value:'',label:'Все'},...(research.facets[field]??[]).map(v=>({value:v.value,label:field==='color'?colorLabel(v.value):v.label??v.value,count:v.count}))]}/>;
    })}
    {(['min_price','max_price','minimum_quantity'] as const).map(field=><NumberFilter key={field}
      label={{min_price:'Цена от, ₸',max_price:'Цена до, ₸',minimum_quantity:'Тираж от, шт.'}[field]}
      min={field==='minimum_quantity'?1:0} value={filters[field]} disabled={disabled}
      onChange={value=>{if(value!==latest.current[field])onChange({...latest.current,[field]:value});}}/>)}
  </div>;
}

export function ResearchConditions({research}:{research:Research}) {
  const conditions:{text:string;source:string}[]=[];
  for(const branch of research.intent.branches??[]) {
    const values=[...branch.colors.map(colorLabel),...branch.materials,...(branch.brands??[]).map(v=>`Бренд: ${v}`),...(branch.excluded_brands??[]).map(v=>`Без бренда: ${v}`),...(branch.models??[]).map(v=>`Модель: ${v}`),branch.quantity!==null?`от ${branch.quantity} шт.`:'',branch.max_price!==null?`до ${branch.max_price} ₸`:'',branch.min_price!==null?`от ${branch.min_price} ₸`:''].filter(Boolean);
    if(values.length)conditions.push({text:`${branch.category.startsWith('dynamic:')?branch.label:categoryLabel(branch.category)}: ${values.join(', ')}`,source:'Требование запроса / его ревизии'});
  }
  for(const brand of research.intent.brands??[])conditions.push({text:`Бренд: ${brand}`,source:'Из исходного запроса'});
  for(const brand of research.intent.excluded_brands??[])conditions.push({text:`Исключён бренд: ${brand}`,source:'Из исходного запроса'});
  for(const model of research.intent.models??[])conditions.push({text:`Модель: ${model}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_materials??[])conditions.push({text:`Без: ${value}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_categories??[])conditions.push({text:`Исключено: ${value}`,source:'Из исходного запроса'});
  const f=research.view.filters;
  for(const model of f.models??[])conditions.push({text:`Модель: ${model}`,source:'Уточнение в чате'});
  for(const brand of f.excluded_brands??[])conditions.push({text:`Исключён бренд: ${research.brand_labels?.[brand]??brand}`,source:'Уточнение в чате'});
  for(const [category,values] of Object.entries(f.branch_brands??{}))conditions.push({text:`${categoryLabel(category)}: Бренд: ${values.map(v=>research.brand_labels?.[v]??v).join(', ')}`,source:'Уточнение товарной ветки'});
  for(const [category,values] of Object.entries(f.branch_models??{}))conditions.push({text:`${categoryLabel(category)}: Модель: ${values.join(', ')}`,source:'Уточнение товарной ветки'});
  for(const value of research.view.preferences??[])conditions.push({text:`Сначала: ${colorLabel(value)}`,source:'Предпочтение · не обязательный фильтр'});
  for(const [field,label,values] of [['brands','Бренд',f.brands],['colors','Цвет',f.colors.map(colorLabel)],['materials','Материал',f.materials],['sizes','Размер',f.sizes],['excluded_materials','Без',f.excluded_materials],['required_terms','Обязательно',f.required_terms]] as [string,string,string[]][]) {
    if(values.length)conditions.push({text:`${label}: ${values.map(v=>field==='brands'?(research.brand_labels?.[v]??v)||'Бренд не указан':v).join(', ')}`,source:f.origins[field]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  }
  for(const [key,label,suffix] of [['min_price','Цена от','₸'],['max_price','Цена до','₸'],['minimum_quantity','Тираж от','шт.']] as const)if(f[key]!==null)conditions.push({text:`${label}: ${f[key]} ${suffix}`,source:f.origins[key]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  return <div className="research-suggestions" aria-label="Активные условия">{conditions.map((c,i)=><span className="studio-button" title={c.source} key={i}>{c.text}</span>)}</div>;
}
