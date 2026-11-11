'use client';
/* eslint-disable @next/next/no-img-element -- Owner-scoped revision photos must bypass the shared Next image cache. */
import {useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import {notificationApi,type ClientFeedback,type ClientInterest} from '@/lib/client-notifications';
import {offerMoney} from '@/lib/commercial-offers';
import {useClientNotifications} from '../notifications/notification-provider';

export function OfferClientInterest({identifier,value}:{identifier:string;value:ClientInterest}){
  return <div className="offer-client-interest" data-client-interest={identifier}>
    <p>Посетителей с выбором: {value.visitor_count} · Позиций: {value.item_count} · Отметок: {value.mark_count}</p>
    {!!value.historical_mark_count&&<small>Есть отметки предыдущих версий или ссылок</small>}
    <Link href={`/offers/${identifier}/edit#client-selection`}>Посмотреть выбор</Link>
  </div>;
}

export function ClientFeedbackPanel({identifier,publication}:{identifier:string;publication:string|null}){
  const notifications=useClientNotifications(),cursor=notifications?.cursor??0;
  const [value,setValue]=useState<ClientFeedback|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const generation=useRef(0);
  useEffect(()=>{
    const epoch=++generation.current;let live=true;
    notificationApi.feedback(identifier).then(next=>{if(live&&epoch===generation.current){setValue(old=>old&&old.document_id===next.document_id&&old.cursor>next.cursor?old:next);setError('');}}).catch(e=>{if(live&&epoch===generation.current){setValue(null);setError((e as Error).message);}});
    return()=>{live=false;};
  },[identifier,publication,cursor]);
  useEffect(()=>{if(location.hash==='#client-selection')document.getElementById('client-selection')?.scrollIntoView();},[identifier]);
  async function more(){if(!value?.next_before||loading)return;const epoch=generation.current;setLoading(true);try{const next=await notificationApi.feedback(identifier,value.next_before);if(epoch===generation.current)setValue(old=>old?{...next,visitors:[...old.visitors,...next.visitors.filter(v=>!old.visitors.some(previous=>previous.id===v.id))]}:next);}catch(e){setError((e as Error).message);}finally{setLoading(false);}}
  return <section id="client-selection" className="client-feedback-panel" data-client-selection-version={value?.cursor??''}>
    <header><h2>Выбор клиента</h2>{value&&<p>Посетителей с выбором: {value.summary.visitor_count} · Уникальных позиций: {value.summary.item_count} · Отметок: {value.summary.mark_count}</p>}</header>
    {error?<p role="alert">{error}</p>:!value?<p role="status">Загружаю выбор клиента…</p>:<>
      <p className="client-feedback-identity">{value.identity_notice}</p>
      {!value.visitors.length&&<p>Клиент пока не отмечал товары.</p>}
      {value.visitors.map(visitor=><section className="client-feedback-visitor" key={visitor.id} data-visitor={visitor.id}>
        <h3>{visitor.label} {!visitor.current_round&&<small>Предыдущее поколение ссылки</small>}</h3>
        <p>{visitor.selected_count?`Отмечено позиций: ${visitor.selected_count}`:'Выбранных позиций нет'} · {new Date(visitor.updated_at).toLocaleString('ru-RU')}</p>
        <div className="client-feedback-items">{visitor.items.map(item=><article key={item.item_id} data-client-item={item.item_id}>
          {item.image_url?<img src={item.image_url} alt="" referrerPolicy="no-referrer"/>:<span className="client-photo-placeholder">Нет фото</span>}
          <div><strong>{item.title}</strong><p>Артикул: {item.sku}</p><small>Версия {item.source_version} · {offerMoney(item.unit_price)} · тираж {item.quantity.toLocaleString('ru-RU')}</small>
            <time dateTime={item.selected_at}>{new Date(item.selected_at).toLocaleString('ru-RU')}</time>
            {item.conditions_changed&&<p className="offer-warning">Цена или тираж после отметки изменились. Прежняя отметка не подтверждает новые условия.</p>}
            {item.removed&&<p className="offer-warning">Позиция была в предыдущей публикации и сейчас исключена.</p>}
            {item.previous_revision&&!item.conditions_changed&&!item.removed&&<small>Отмечено в предыдущей опубликованной версии.</small>}
          </div>
        </article>)}</div>
      </section>)}
      {value.next_before&&<button disabled={loading} onClick={()=>void more()}>{loading?'Загружаю…':'Другие посетители'}</button>}
      {!!value.events.length&&<details><summary>История изменений выбора</summary><ol>{value.events.map(event=><li key={event.sequence}><time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString('ru-RU')}</time> · {event.visitor_label}: {event.selected?'выбрал':'снял отметку'} — {event.title}</li>)}</ol></details>}
    </>}
  </section>;
}
