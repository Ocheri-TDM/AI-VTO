'use client';
import Image from 'next/image';
import {useState} from 'react';
import type {ResearchProduct} from '@/lib/research';
import {colorLabel,formatPrice,safeWebUrl} from '@/lib/format';

export function briefAvailability(product:ResearchProduct){
  const available=product.availability?.availableNow;
  return available!=null?`${available.toLocaleString('ru-RU')} шт.${product.freshness==='STALE'?' · уточнить':''}`:
    product.availability?.incoming.length?'Ожидается поступление':'Наличие уточняется';
}

function SelectedImage({product}:{product:ResearchProduct}){
  const [failed,setFailed]=useState(false);
  return !failed&&safeWebUrl(product.primary_image)?<Image src={safeWebUrl(product.primary_image)!} alt={product.name} width={500} height={380} onError={()=>setFailed(true)}/>:<span>Фото недоступно</span>;
}

export function SelectedProducts({products,count,busy,onRemove,onDetails,onClear,onCreate}:{
  products:ResearchProduct[];count:number;busy:boolean;onRemove:(id:string)=>void;
  onDetails:(product:ResearchProduct)=>void;onClear:()=>void;onCreate:()=>void;
}){
  return <div className="selected-products-panel">
    <div className="selected-products-scroll"><div className="selected-products-grid">
      {products.map(product=><article className="selected-product" key={product.id}>
        <button className="selected-product-image" onClick={()=>onDetails(product)} aria-label={`Подробнее: ${product.name}`}>
          <SelectedImage product={product}/>
        </button>
        <div className="selected-product-info"><button className="selected-product-name" onClick={()=>onDetails(product)}>{product.name}</button>
          <p>{[product.brand,product.colors[0]&&colorLabel(product.colors[0].normalized_color)].filter(Boolean).join(' · ')}</p>
          <strong>{product.price_kzt===null?'Цена по запросу':formatPrice(product.price_kzt)}</strong>
          <small title="Полные сведения и дата наличия доступны в деталях товара">{briefAvailability(product)}</small>
          <button className="studio-button" disabled={busy} onClick={()=>onRemove(product.id)} aria-label={`Убрать из выбранных: ${product.name}`}>Убрать</button>
        </div>
      </article>)}
    </div>{!count&&<p className="research-empty">Выберите товары в сетке — здесь будут именно выбранные цвета и варианты.</p>}</div>
    <footer className="selected-products-actions"><div><strong>Выбрано: {count}</strong><button className="studio-button" disabled={busy||!count} onClick={onClear}>Очистить выбор</button></div>
      <button className="studio-button primary" disabled={busy||!count} onClick={onCreate}>Создать презентацию</button></footer>
  </div>;
}
