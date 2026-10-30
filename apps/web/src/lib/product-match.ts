import type { Product } from './types';

export function matchCaption(product:{category_match:string;match:string}){
  if(product.category_match==='REJECTED')return 'Вне текущей выдачи';
  if(product.category_match==='RELATED'||product.category_match==='EXPLORATORY')return 'Связанное предложение';
  if(product.category_match==='STRONG_RELATED')return 'Связанный тип';
  return product.match==='exact'?'Точное совпадение':'Близкий оттенок';
}

export function primaryProductLabel(product:Product,fallback:string){
  const identity=product.purpose_match?.identity;
  if(identity?.role==='SET')return 'Готовый комплект';
  if(identity?.role==='PACKAGING')return identity.subject==='case'?'Футляр':'Упаковка';
  if(identity?.role==='ACCESSORY')return 'Аксессуар';
  return fallback;
}
