'use client';

import {useCallback,useEffect,useRef,useState} from 'react';
import {downloadOffer,offerApi,type ClientOffer,OfferError} from '@/lib/commercial-offers';
import {OfferView} from './offer-view';
import {useClientSelection} from '@/hooks/use-client-selection';

export function PublicOffer({token}:{token:string}){
  const [value,setValue]=useState<ClientOffer|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const selection=useClientSelection(token,value?.revision_id??null);
  const selected=new Map(selection.state?.selected_items.map(i=>[i.item_id,i])??[]),pending=new Set(selection.pending_ids);
  const generation=useRef(0);
  const invalidate=useCallback(()=>{generation.current++;},[]);
  const load=useCallback(async()=>{const epoch=++generation.current;setError('');setValue(null);try{const next=await offerApi.published(token);if(epoch===generation.current)setValue(next);}catch(e){if(epoch===generation.current)setError((e as Error).message);}},[token]);
  useEffect(()=>{let mounted=true;queueMicrotask(()=>{if(mounted)void load();});return()=>{mounted=false;invalidate();};},[load,invalidate]);
  async function download(kind:'pdf'|'xlsx'){
    if(!value||busy)return;setBusy(true);setError('');const epoch=generation.current;
    try{await downloadOffer(kind==='pdf'?value.pdf_url!:value.xlsx_url!,`commercial-offer.${kind}`,true);}catch(e){if(epoch===generation.current)setError(e instanceof OfferError&&e.code==='REVISION_CHANGED'?'Предложение обновилось. Обновите страницу перед скачиванием.':(e as Error).message);}finally{setBusy(false);}
  }
  return <div className="commercial-public" data-testid="public-offer">
    {value&&<div className="offer-toolbar"><strong>{value.title}</strong><div><span className="offer-selection-count">Выбрано: {selection.state?selected.size:'…'}</span><button disabled={busy} onClick={()=>void download('pdf')}>PDF</button><button disabled={busy} onClick={()=>void download('xlsx')}>Excel</button><button onClick={()=>window.print()}>Печать</button></div></div>}
    {error&&<div className="offer-error" role="alert"><p>{error}</p><button onClick={()=>void load()}>Обновить страницу</button></div>}
    {value?<><section className="offer-selection-notice"><p>Отметьте понравившиеся товары — менеджер увидит ваш выбор.</p><p role="status">{selection.status==='loading'?'Загружаю выбор…':selection.status==='saving'?'Сохраняю…':selection.status==='saved'?'Выбор сохранён':selection.error}</p>
      {selection.status==='error'&&<button onClick={selection.blocked?()=>void load():selection.retry}>{selection.blocked?'Обновить предложение':'Не удалось сохранить. Повторить'}</button>}
      {!!selection.state?.previous_item_count&&<p>Часть прежних отметок относится к позициям предыдущей версии.</p>}
    </section><OfferView value={value} selectionSlot={item=><div className="offer-selection-actions" aria-busy={pending.has(item.id)}>
      <label><input type="checkbox" checked={selected.has(item.id)} disabled={!selection.state||selection.blocked} onChange={e=>selection.set(item.id,e.target.checked)} aria-label={'Выбрать: '+item.title}/><span>{selected.has(item.id)?'Выбрано':'Выбрать'}</span></label>
      {pending.has(item.id)&&<small role="status">{selection.status==='error'?'Отметка ещё не сохранена':'Сохраняю…'}</small>}
      {selected.has(item.id)&&selected.get(item.id)?.published_revision_id!==value.revision_id&&!pending.has(item.id)&&<small>Отмечено в предыдущей версии. Проверьте обновлённые условия.</small>}
    </div>}/></>:!error&&<p className="offer-loading" role="status">Открываю предложение…</p>}
  </div>;
}
