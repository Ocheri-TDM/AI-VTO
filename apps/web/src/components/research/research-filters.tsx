'use client';
import { useEffect, useRef, useState } from 'react';
import type { EffectiveEdit, EffectiveField, EffectiveValue, Research, ResearchFilters } from '@/lib/research';
import { categoryLabel, colorLabel } from '@/lib/format';
import {MultiSelect, Select, SearchableSelect} from '@/components/common/select';

function NumberFilter({label,value,min,onChange,disabled,step=1}:{label:string;value:number|null;min:number;onChange:(value:number|null)=>void;disabled:boolean;step?:number|'any'}) {
  const [draft,setDraft]=useState({saved:value,text:value===null?'':String(value)});
  if(draft.saved!==value)setDraft({saved:value,text:value===null?'':String(value)});
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current);},[value]);
  return <label className="research-filter-field">{label}<input type="number" min={min} step={step} disabled={disabled} value={draft.text}
    onChange={e=>{const next=e.target.value;setDraft({saved:value,text:next});if(timer.current)clearTimeout(timer.current);
      if(e.target.validity.valid)timer.current=setTimeout(()=>onChange(next===''?null:Number(next)),300);}}/>
  </label>;
}

function LegacyResearchFilterPanel({research,onChange,disabled,inline=false}:{research:Research;onChange:(filters:ResearchFilters)=>void;disabled:boolean;inline?:boolean}) {
  const filters=research.view.filters;
  const brands=research.facets.brand??[];
  const sourceBrands=research.intent.brands??[];
  const sourceKeys=Object.entries(research.brand_labels??{}).filter(([,label])=>sourceBrands.includes(label)).map(([key])=>key);
  const colorState=research.color_state;
  const hasSourceColor=!!colorState&&colorState.source_mode!=='none';
  const singleSourceColor=colorState?.source_mode==='uniform'&&colorState.source_values.length===1?colorState.source_values[0]:null;
  const sourceColorLabel=colorState?.source_mode==='mixed'?'Разные по категориям':colorState?.source_values.map(colorLabel).join(', ')??'';
  const colorValues=Array.from(new Set([...(research.facets.color??[]).map(v=>v.value),...(colorState?.source_values??[]),...filters.colors]));
  const latest=useRef(filters);useEffect(()=>{latest.current=filters;},[filters]);
  return <div className={inline?'research-inline-filters':'research-filters'}>
    <MultiSelect label="Бренд" searchLabel="Поиск бренда" disabled={disabled}
      value={[...filters.brands,...sourceKeys.filter(v=>!filters.brands.includes(v))]}
      options={brands.map(b=>({value:b.value,label:b.label??b.value,count:b.count,disabled:sourceKeys.includes(b.value),note:sourceKeys.includes(b.value)?'из запроса':undefined}))}
      summary={sourceBrands.length?sourceBrands.join(', '):undefined}
      help={!!sourceBrands.length&&'Бренд задан в запросе. Команда «бренд не важен» снимает это условие.'}
      onChange={values=>onChange({...filters,brands:[...filters.brands.filter(v=>sourceKeys.includes(v)),...values.filter(v=>!sourceKeys.includes(v))]})}/>
    <div className="research-filter-field">
      <Select label="Цвет" disabled={disabled} value={filters.colors[0]??(hasSourceColor?'__source_color__':'')}
        onChange={value=>onChange({...filters,colors:value&&value!=='__source_color__'&&value!==singleSourceColor?[value]:[]})}
        options={[
          ...(hasSourceColor?[{value:'__source_color__',label:sourceColorLabel,count:singleSourceColor?(research.facets.color?.find(v=>v.value===singleSourceColor)?.count??0):undefined}]:[]),
          {value:'',label:hasSourceColor?'Без дополнительных ограничений':'Все'},
          ...colorValues.filter(value=>value!==singleSourceColor).map(value=>({value,label:colorLabel(value),count:research.facets.color?.find(v=>v.value===value)?.count??0})),
        ]}/>
      {hasSourceColor&&<small className="muted" title="Изменить: «теперь красные»; снять: «цвет не важен».">Из запроса</small>}
      {!!colorState?.conflicting_branch_ids.length&&<small role="status">Дополнительный цвет конфликтует с запросом. Снимите дополнительный фильтр или замените исходный цвет в чате.</small>}
    </div>
    {(['material','size'] as const).map(field=>{
      const Control=field==='material'?SearchableSelect:Select;
      return <Control key={field} label={{material:'Материал',size:'Размер'}[field]}
        disabled={disabled} value={(field==='material'?filters.materials:filters.sizes)[0]??''}
        onChange={value=>onChange({...filters,[field==='material'?'materials':'sizes']:value?[value]:[]})}
        options={[{value:'',label:'Все'},...(research.facets[field]??[]).map(v=>({value:v.value,label:v.label??v.value,count:v.count}))]}/>;
    })}
    {(['min_price','max_price','minimum_quantity'] as const).map(field=><NumberFilter key={`${research.id}:${field}`}
      label={{min_price:'Цена от, ₸',max_price:'Цена до, ₸',minimum_quantity:'Тираж от, шт.'}[field]}
      min={field==='minimum_quantity'?1:0} value={filters[field]} disabled={disabled}
      onChange={value=>{if(value!==latest.current[field])onChange({...latest.current,[field]:value});}}/>)}
  </div>;
}

const fieldLabels:Record<EffectiveField,string>={brands:'Бренд',colors:'Цвет',materials:'Материал',sizes:'Размер',min_price:'Цена от, ₸',max_price:'Цена до, ₸',minimum_quantity:'Тираж от, шт.'};
const materialNames:Record<string,string>={METAL:'Металл',PLASTIC:'Пластик',WOOD:'Дерево',COTTON:'Хлопок',LEATHER:'Кожа'};
const allFields=Object.keys(fieldLabels) as EffectiveField[];
const emptyValue:EffectiveValue={mode:'none',values:[],number:null,exclusive:false};

function EffectiveControl({research,field,state,onEdit,disabled,branch}:{research:Research;field:EffectiveField;state:EffectiveValue;onEdit:(edit:EffectiveEdit)=>void;disabled:boolean;branch?:{id:string;label:string}}){
  const label=fieldLabels[field]+(branch?` · ${branch.label}`:'');
  const edit=(action:EffectiveEdit['action'],value?:string[]|number)=>onEdit({field,action,...(branch?{branch_id:branch.id}:{}),...(Array.isArray(value)?{values:value}:typeof value==='number'?{number:value}:{})});
  const changed=(research.view.effective_overrides??[]).some(e=>e.field===field&&(!e.branch_id||e.branch_id===branch?.id))||(!branch&&(research.view.filters[field] instanceof Array?(research.view.filters[field] as string[]).length>0:research.view.filters[field]!==null));
  const mixed=state.mode==='mixed',complex=state.mode==='complex';
  const caption=mixed?(field==='minimum_quantity'?'Разный по категориям':'Разные по категориям'):complex?'Несколько условий':state.mode==='conflict'?'Несовместимые условия':null;
  const numeric=field==='minimum_quantity'||field==='min_price'||field==='max_price';
  const display=(value:string)=>field==='colors'?(value==='NAVY'?'Тёмно-синий (Navy)':colorLabel(value)):field==='materials'?(materialNames[value]??value):field==='brands'?(research.brand_labels?.[value]??research.intent.brands?.find(v=>v.toLocaleLowerCase()===value)??value)||'Бренд не указан':value;
  const facet=field==='brands'?'brand':field==='colors'?'color':field==='materials'?'material':'size';
  const values=Array.from(new Set([...(research.facets[facet]??[]).map(v=>v.value),...(research.effective_filters?.options?.[field]??[]),...state.values]));
  const options=values.map(value=>({value,label:display(value),count:research.facets[facet]?.find(v=>v.value===value)?.count??(state.values.includes(value)?0:undefined)}));
  const value=caption?'__effective_state__':state.values.length>1?'__effective_multiple__':state.values[0]??'';
  const Control=field==='materials'?SearchableSelect:Select;
  return <div className={`research-filter-field effective-filter-control${caption?' is-mixed':''}`}>
    {numeric?<NumberFilter label={label} value={mixed||complex?null:state.number} min={field==='minimum_quantity'?1:0} step={field==='minimum_quantity'?1:'any'} disabled={disabled} onChange={n=>n===null?edit('clear'):edit('set',n)}/>:
      field==='brands'?<MultiSelect label={label} disabled={disabled} value={state.values} summary={caption??undefined} options={options} onChange={v=>v.length?edit('set',v):edit('clear')}/>:
      <Control label={label} disabled={disabled} value={value} options={[
        ...(caption?[{value:'__effective_state__',label:caption,disabled:true}]:state.values.length>1?[{value:'__effective_multiple__',label:state.values.map(display).join(', '),disabled:true}]:[]),
        {value:'__inherit__',label:'Из исходного запроса'}, {value:'',label:'Все'},...options,
      ]} onChange={v=>v==='__inherit__'?edit('inherit'):v?edit('set',[v]):edit('clear')}/>}
    {numeric&&caption&&<small className="muted">{caption}</small>}
    {!!state.requirements?.length&&<small className="muted">Одновременно: {state.requirements.map(group=>`(${group.map(display).join(' или ')})`).join(' и ')}</small>}
    {state.exclusive&&<small className="muted">{field==='max_price'?'Строго меньше':'Строго больше'} {state.number}</small>}
    {state.mode==='conflict'&&<small role="status">Условия несовместимы. Измените поле или сбросьте дополнительные фильтры.</small>}
    <div className="effective-filter-actions">
      {changed&&<button type="button" disabled={disabled} className="effective-filter-action" onClick={()=>edit('inherit')}>Из запроса</button>}
      {(numeric&&mixed||field==='brands'&&(mixed||state.values.length>0))&&<button type="button" disabled={disabled} className="effective-filter-action" onClick={()=>edit('clear')}>Снять</button>}
    </div>
  </div>;
}

export function ResearchFilterPanel({research,onChange,onEffectiveChange,disabled,inline=false}:{research:Research;onChange:(filters:ResearchFilters)=>void;onEffectiveChange?:(edit:EffectiveEdit)=>void;disabled:boolean;inline?:boolean}){
  const state=research.effective_filters;
  if(!state)return <LegacyResearchFilterPanel research={research} onChange={onChange} disabled={disabled} inline={inline}/>;
  const localFields=allFields.filter(field=>['mixed','complex','conflict'].includes(state.fields[field].mode));
  return <div className={inline?'research-inline-filters':'research-filters'}>
    {allFields.map(field=><EffectiveControl key={`${research.id}:${field}`} research={research} field={field} state={state.fields[field]??emptyValue} disabled={disabled} onEdit={edit=>onEffectiveChange?.(edit)}/>)}
    {!!localFields.length&&<details className="effective-branch-filters"><summary>Условия по категориям</summary>
      {state.branches.map(branch=><div className="effective-branch-row" key={branch.id} data-filter-branch={branch.id}>
        <strong>{branch.label}</strong><div className="effective-branch-fields">{localFields.map(field=><EffectiveControl key={`${research.id}:${field}`} research={research} field={field} state={branch.fields[field]} branch={branch} disabled={disabled} onEdit={edit=>onEffectiveChange?.(edit)}/>)}</div>
      </div>)}
    </details>}
  </div>;
}

export function ResearchConditions({research}:{research:Research}) {
  const conditions:{text:string;source:string}[]=[];
  for(const branch of research.intent.branches??[]) {
    const values=[...(!research.effective_filters?[...branch.colors.map(colorLabel),...branch.materials,...(branch.brands??[]).map(v=>`Бренд: ${v}`),branch.quantity!==null?`от ${branch.quantity} шт.`:'',branch.max_price!==null?`до ${branch.max_price} ₸`:'',branch.min_price!==null?`от ${branch.min_price} ₸`:'']:[]),...(branch.excluded_brands??[]).map(v=>`Без бренда: ${v}`),...(branch.models??[]).map(v=>`Модель: ${v}`)].filter(Boolean);
    const label=research.query_categories?.find(b=>b.id===branch.id)?.label??(branch.category.startsWith('dynamic:')?branch.label:categoryLabel(branch.category));
    if(values.length)conditions.push({text:`${label}: ${values.join(', ')}`,source:'Требование запроса / его ревизии'});
  }
  if(!research.effective_filters)for(const brand of research.intent.brands??[])conditions.push({text:`Бренд: ${brand}`,source:'Из исходного запроса'});
  for(const brand of research.intent.excluded_brands??[])conditions.push({text:`Исключён бренд: ${brand}`,source:'Из исходного запроса'});
  for(const model of research.intent.models??[])conditions.push({text:`Модель: ${model}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_materials??[])conditions.push({text:`Без: ${value}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_categories??[])conditions.push({text:`Исключено: ${value}`,source:'Из исходного запроса'});
  for(const value of research.intent.excluded_colors??[])conditions.push({text:`Без цвета: ${colorLabel(value)}`,source:'Из исходного запроса'});
  const f=research.view.filters;
  if(f.categories?.length)conditions.push({text:`Категории выдачи: ${f.categories.map(categoryLabel).join(', ')}`,source:'Дополнительный фильтр'});
  if(f.selected_only)conditions.push({text:'Только выбранные товары',source:'Дополнительный фильтр'});
  for(const term of f.excluded_terms??[])conditions.push({text:`Исключить: ${term}`,source:'Дополнительный фильтр'});
  for(const model of f.models??[])conditions.push({text:`Модель: ${model}`,source:'Уточнение в чате'});
  for(const brand of f.excluded_brands??[])conditions.push({text:`Исключён бренд: ${research.brand_labels?.[brand]??brand}`,source:'Уточнение в чате'});
  if(!research.effective_filters)for(const [category,values] of Object.entries(f.branch_brands??{}))conditions.push({text:`${categoryLabel(category)}: Бренд: ${values.map(v=>research.brand_labels?.[v]??v).join(', ')}`,source:'Уточнение товарной ветки'});
  for(const [category,values] of Object.entries(f.branch_models??{}))conditions.push({text:`${categoryLabel(category)}: Модель: ${values.join(', ')}`,source:'Уточнение товарной ветки'});
  for(const value of research.view.preferences??[])conditions.push({text:`Сначала: ${colorLabel(value)}`,source:'Предпочтение · не обязательный фильтр'});
  for(const [field,label,values] of [['brands','Бренд',f.brands],['colors','Цвет',f.colors.map(colorLabel)],['materials','Материал',f.materials],['sizes','Размер',f.sizes],['excluded_materials','Без',f.excluded_materials],['required_terms','Обязательно',f.required_terms]] as [string,string,string[]][]) {
    if(values.length&&(!research.effective_filters||field==='excluded_materials'||field==='required_terms'))conditions.push({text:`${label}: ${values.map(v=>field==='brands'?(research.brand_labels?.[v]??v)||'Бренд не указан':v).join(', ')}`,source:f.origins[field]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  }
  if(!research.effective_filters)for(const [key,label,suffix] of [['min_price','Цена от','₸'],['max_price','Цена до','₸'],['minimum_quantity','Тираж от','шт.']] as const)if(f[key]!==null)conditions.push({text:`${label}: ${f[key]} ${suffix}`,source:f.origins[key]==='chat'?'Уточнение в чате':'Дополнительный фильтр'});
  for(const branch of research.query_categories??[]){const a=branch.constraints?.constraints;if(!a)continue;
    const features:Record<string,string>={ETERNAL:'Вечный пишущий элемент',HYBRID_2_IN_1:'2 в 1'};
    const extras=[a.capacity_ml?`${a.capacity_ml} мл`:'',a.capacity_mah?`${a.capacity_mah} мАч`:'',a.sku?`Артикул: ${a.sku}`:'',a.quantity_max!=null?`${a.quantity_max_exclusive?'меньше':'не более'} ${a.quantity_max} шт.`:'',a.availability_only?'Только в наличии':'',a.allow_near_colors?'Близкие оттенки разрешены':'',...(a.features??[]).map(v=>features[v]??v),...(a.excluded_colors??[]).filter(v=>!research.intent.excluded_colors?.includes(v)).map(v=>`Без цвета: ${colorLabel(v)}`)].filter(Boolean);
    if(extras.length)conditions.push({text:`${branch.label}: ${extras.join(', ')}`,source:'Техническое требование запроса'});
    if(!branch.resolved)conditions.push({text:`Не распознано: ${branch.source_text}`,source:'Уточните категорию'});
  }
  if(!conditions.length)return null;
  return <div className="research-suggestions" aria-label="Активные условия">{conditions.map((c,i)=><span className="studio-button" title={c.source} key={i}>{c.text}</span>)}</div>;
}
