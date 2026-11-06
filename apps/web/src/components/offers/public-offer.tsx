'use client';

import {useCallback,useEffect,useRef,useState} from 'react';
import {downloadOffer,offerApi,type ClientOffer,OfferError} from '@/lib/commercial-offers';
import {OfferView} from './offer-view';

export function PublicOffer({token}:{token:string}){
  const [value,setValue]=useState<ClientOffer|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const generation=useRef(0);
  const invalidate=useCallback(()=>{generation.current++;},[]);
  const load=useCallback(async()=>{const epoch=++generation.current;setError('');setValue(null);try{const next=await offerApi.published(token);if(epoch===generation.current)setValue(next);}catch(e){if(epoch===generation.current)setError((e as Error).message);}},[token]);
  useEffect(()=>{let mounted=true;queueMicrotask(()=>{if(mounted)void load();});return()=>{mounted=false;invalidate();};},[load,invalidate]);
  async function download(kind:'pdf'|'xlsx'){
    if(!value||busy)return;setBusy(true);setError('');const epoch=generation.current;
    try{await downloadOffer(kind==='pdf'?value.pdf_url!:value.xlsx_url!,`commercial-offer.${kind}`,true);}catch(e){if(epoch===generation.current)setError(e instanceof OfferError&&e.code==='REVISION_CHANGED'?'Предложение обновилось. Обновите страницу перед скачиванием.':(e as Error).message);}finally{setBusy(false);}
  }
  return <div className="commercial-public" data-testid="public-offer">
    {value&&<div className="offer-toolbar"><strong>{value.title}</strong><div><button disabled={busy} onClick={()=>void download('pdf')}>PDF</button><button disabled={busy} onClick={()=>void download('xlsx')}>Excel</button><button onClick={()=>window.print()}>Печать</button></div></div>}
    {error&&<div className="offer-error" role="alert"><p>{error}</p><button onClick={()=>void load()}>Обновить страницу</button></div>}
    {value?<OfferView value={value}/>:!error&&<p className="offer-loading" role="status">Открываю предложение…</p>}
  </div>;
}
