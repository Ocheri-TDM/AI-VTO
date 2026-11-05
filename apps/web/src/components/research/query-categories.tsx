'use client';
import {Eye,EyeOff,X} from 'lucide-react';
import type {BranchAction,Research} from '@/lib/research';
import {colorLabel,formatPrice} from '@/lib/format';

export function QueryCategories({research,disabled,pending,onAction}:{research:Research;disabled:boolean;pending:boolean;onAction:(action:BranchAction)=>void}){
  const branches=research.query_categories??[];
  const hidden=branches.some(b=>b.hidden);
  return <section className="query-categories" aria-label="Категории запроса">
    <div className="query-categories-heading"><strong>Категории запроса</strong>{hidden&&<button className="studio-button" disabled={disabled} onClick={()=>onAction({action:'show_all'})}>Показать все</button>}</div>
    {research.categories_unrestricted?<small>Категории не ограничены</small>:!branches.length?<small>В запросе не осталось категорий</small>:<div className="query-category-list">{branches.map(b=>{
      const attributes=b.constraints.constraints;
      const materialNames:Record<string,string>={METAL:'металл',PLASTIC:'пластик',WOOD:'дерево',COTTON:'хлопок',LEATHER:'кожа'};
      const details=[...(b.constraints.colors??[]).map(colorLabel),...(b.constraints.brands??[]),...(b.constraints.excluded_brands??[]).map(v=>`без ${v}`),...(b.constraints.models??[]),...(b.constraints.materials??[]).map(v=>materialNames[v]??v),b.constraints.quantity?`${attributes?.quantity_min_exclusive?'больше':'от'} ${b.constraints.quantity} шт.`:'',b.constraints.min_price!=null?`${attributes?.price_min_exclusive?'>':'от'} ${formatPrice(b.constraints.min_price)}`:'',b.constraints.max_price!=null?`${attributes?.price_max_exclusive?'<':'до'} ${formatPrice(b.constraints.max_price)}`:'',attributes?.capacity_ml?`${attributes.capacity_ml} мл`:'',attributes?.capacity_mah?`${attributes.capacity_mah} мАч`:'',...(attributes?.sizes??[]),attributes?.sku??'',attributes?.allow_near_colors?'близкие оттенки разрешены':''].filter(Boolean).join(' · ');
      const visibility=b.hidden?'Показать':'Скрыть';
      return <div key={b.id} className={`query-category-chip${b.hidden?' is-hidden':''}${!b.resolved?' is-unresolved':''}`} data-branch-id={b.id}>
        <span className="query-category-caption" title={`Исходный фрагмент: ${b.source_text}`}><strong>{b.label} <span className="query-category-count">{b.count}</span></strong>{details&&<small>{details}</small>}{b.hidden&&<small>Скрыта</small>}{!b.resolved&&<small>{b.source_text}</small>}</span>
        <button type="button" disabled={disabled} className="query-category-action" aria-label={`${visibility} категорию «${b.label}»${details?`: ${details}`:''}`} title={`${visibility} в выдаче`} aria-pressed={b.hidden} onClick={()=>onAction({action:b.hidden?'show':'hide',branch_id:b.id})}>{b.hidden?<EyeOff size={16}/>:<Eye size={16}/>}<span className="sr-only">{visibility}</span></button>
        <button type="button" disabled={disabled||pending} className="query-category-action" aria-label={`Убрать из запроса «${b.label}»${details?`: ${details}`:''}`} title="Убрать из запроса" onClick={()=>onAction({action:'remove',branch_id:b.id})}><X size={16}/><span className="sr-only">Убрать из запроса</span></button>
      </div>;
    })}</div>}
    {research.category_notice&&<p className="query-category-notice" role="status">Категория «{research.category_notice.label}» убрана. <button className="studio-button" disabled={disabled||pending} onClick={()=>onAction({action:'restore',removal_id:research.category_notice!.id})}>Вернуть</button></p>}
  </section>;
}
