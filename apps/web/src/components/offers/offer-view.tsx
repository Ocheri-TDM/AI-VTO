'use client';
/* eslint-disable @next/next/no-img-element -- Capability images must bypass the shared Next image cache. */

import {useState} from 'react';
import type {ClientOffer,ClientOfferItem,OfferEditSlot} from '@/lib/commercial-offers';
import {offerMoney} from '@/lib/commercial-offers';

function Gallery({item}:{item:ClientOfferItem}){
  const [selected,setSelected]=useState(item.main_image);
  const [failed,setFailed]=useState<string[]>([]);
  const image=item.images.find(i=>i.id===selected);
  return <div className="offer-gallery">
    <div className="offer-main-photo">{image&&!failed.includes(image.id)?<img src={image.url} alt={item.title} loading="lazy" referrerPolicy="no-referrer" onError={()=>setFailed(ids=>[...ids,image.id])}/>:<p>Фотография недоступна</p>}</div>
    {item.images.length>1&&<div className="offer-thumbnails" aria-label={`Фотографии: ${item.title}`}>{item.images.map((photo,index)=><button key={photo.id} type="button" aria-label={`Фото ${index+1}: ${item.title}`} aria-pressed={selected===photo.id} onClick={()=>setSelected(photo.id)}><img src={photo.url} alt={`Вариант ракурса ${index+1}`} loading="lazy" referrerPolicy="no-referrer"/></button>)}</div>}
  </div>;
}

export function OfferView({value,editSlot}:{value:ClientOffer;editSlot?:OfferEditSlot}){
  return <main className="offer-document" data-testid="offer-document">
    <header className="offer-cover">
      <div className={`offer-banner${value.banner_url?' has-image':''}`}>{value.banner_url&&<img src={value.banner_url} alt="Баннер предложения" referrerPolicy="no-referrer"/>}<span className="offer-banner-mark" aria-hidden="true"/></div>
      <div className="offer-cover-copy"><img className="offer-logo" style={{background:value.logo_background??'#151918',padding:10,borderRadius:6}} src={value.logo_url} alt="Логотип" referrerPolicy="no-referrer"/><p className="offer-eyebrow">Коммерческое предложение</p><h1>{value.title}</h1>{value.company&&!value.title.includes(value.company)&&<p className="offer-company">{value.company}</p>}<p className="offer-intro">Выберите то, что подходит вашей задаче. Каждая позиция — самостоятельный вариант.</p></div>
    </header>
    <div className="offer-products">{value.items.map((item,index)=><section className="offer-product" key={`${item.id}:${item.main_image}`} data-offer-item={item.id}>
      <div className="offer-product-heading"><span className="offer-number">{String(index+1).padStart(2,'0')}</span><span>{item.category}</span></div>
      <div className="offer-product-layout"><Gallery item={item}/><div className="offer-product-copy">
        <h2>{item.title}</h2><p className="offer-sku">Артикул: <strong>{item.sku||'Не указан'}</strong></p>
        {editSlot?.(item,index)}
        <div className="offer-calculation"><div><span>Цена за единицу</span><strong>{offerMoney(item.unit_price)}</strong></div><span aria-hidden="true">×</span><div><span>Тираж</span><strong>{item.quantity.toLocaleString('ru-RU')} шт.</strong></div></div>
        <div className="offer-line-amount"><span>Стоимость позиции</span><strong>{offerMoney(item.line_amount)}</strong></div>
        <p className="offer-availability">{item.availability}</p>
        {!!item.characteristics.length&&<div className="offer-characteristics"><h3>Характеристики</h3><dl>{item.characteristics.map((a,n)=><div key={n}><dt>{a.name}</dt><dd>{a.value}</dd></div>)}</dl></div>}
        {item.description&&<div className="offer-description"><h3>Описание</h3>{item.description.length>1200?<details><summary>Полное описание</summary>{item.description.split(/\n\n+/).map((p,n)=><p key={n}>{p}</p>)}</details>:item.description.split(/\n\n+/).map((p,n)=><p key={n}>{p}</p>)}</div>}
      </div></div>
    </section>)}</div>
    <footer className="offer-footer"><strong>Souvenir Studio</strong><span>Наличие и возможность поставки уточняются перед заказом. Предложение не резервирует товары.</span></footer>
  </main>;
}
