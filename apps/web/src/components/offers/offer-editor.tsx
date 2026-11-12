'use client';

import {useCallback,useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import {Select} from '@/components/common/select';
import {copyOfferLink,downloadOffer,offerApi,offerForm,offerMoney,offerExpiry,OfferError,type OfferDocument,type OfferForm} from '@/lib/commercial-offers';
import {OfferView} from './offer-view';
import {OwnerStockNotice} from './owner-stock';
import {ClientFeedbackPanel} from './client-feedback';
import {NotificationBell} from '../notifications/notification-bell';
import {useClientNotifications} from '../notifications/notification-provider';

const active=['QUEUED','PREPARING_ASSETS','RENDERING','VALIDATING'];

export function OfferEditor({id}:{id:string}){
  const notifications=useClientNotifications();
  const [unavailable,setUnavailable]=useState(false);
  const [copying,setCopying]=useState(false),[copyError,setCopyError]=useState('');
  const copyPending=useRef(false);
  const [doc,setDoc]=useState<OfferDocument|null>(null),[form,setForm]=useState<OfferForm|null>(null),[dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[preview,setPreview]=useState(false),[conflict,setConflict]=useState(false),[notice,setNotice]=useState('');
  const current=useRef<OfferDocument|null>(null),draft=useRef<OfferForm|null>(null),stamp=useRef(0),generation=useRef(0),reads=useRef(0),savePromise=useRef<Promise<boolean>|null>(null),operation=useRef(false),dirtyRef=useRef(false);
  const invalidate=useCallback(()=>{generation.current++;},[]);
  const guard=notifications?.guard;
  useEffect(()=>guard?.(id,dirty||saving),[guard,id,dirty,saving]);
  const accept=useCallback((value:OfferDocument)=>{if(current.current&&(value.version<current.current.version||value.share.epoch<current.current.share.epoch))return;current.current=value;draft.current=offerForm(value);dirtyRef.current=false;setDoc(value);setForm(draft.current);setDirty(false);setConflict(false);},[]);
  const readError=useCallback((e:unknown)=>{if(e instanceof OfferError&&e.status===404){generation.current++;current.current=null;draft.current=null;dirtyRef.current=false;setUnavailable(true);setDoc(null);setForm(null);}else setError((e as Error).message);},[]);
  useEffect(()=>{const epoch=++generation.current;offerApi.get(id).then(v=>{if(epoch===generation.current)accept(v);}).catch(e=>{if(epoch===generation.current)readError(e);});return invalidate;},[id,accept,invalidate,readError]);
  function edit(value:OfferForm){stamp.current++;draft.current=value;dirtyRef.current=true;setForm(value);setDirty(true);setNotice('');if(!conflict)setError('');}
  const save=useCallback(async():Promise<boolean>=>{
    if(savePromise.current)return savePromise.current;
    if(!dirtyRef.current)return true;
    if(!current.current||!draft.current)return false;
    reads.current++;const epoch=generation.current,at=stamp.current,body={...draft.current,version:current.current.version};
    setSaving(true);setError('');
    const pending=(async()=>{try{const value=await offerApi.save(id,body);if(epoch!==generation.current)return false;current.current=value;setDoc(value);if(at===stamp.current)accept(value);return true;}catch(e){if(epoch===generation.current){setError((e as Error).message);if(e instanceof OfferError&&e.status===409)setConflict(true);}return false;}finally{savePromise.current=null;if(epoch===generation.current)setSaving(false);}})();
    savePromise.current=pending;return pending;
  },[id,accept]);
  useEffect(()=>{if(!dirty||saving||busy||conflict||error)return;const timer=setTimeout(()=>void save(),700);return()=>clearTimeout(timer);},[dirty,saving,busy,conflict,error,save,form]);
  useEffect(()=>{let loading=false;const epoch=generation.current;const timer=setInterval(async()=>{if(loading||savePromise.current||operation.current||!current.current)return;loading=true;const read=reads.current;try{const value=await offerApi.get(id);if(epoch!==generation.current||read!==reads.current)return;if(dirtyRef.current){if(value.version!==current.current?.version){setConflict(true);setError('Документ изменён в другой вкладке. Правки сохранены на экране; откройте последнюю версию перед повторным изменением.');}}else accept(value);}catch{}finally{loading=false;}},doc?.materials_preparing||doc?.revisions.some(r=>active.includes(r.status))?1400:10000);return()=>clearInterval(timer);},[id,accept,doc?.materials_preparing,doc?.revisions]);
  async function flush(){if(savePromise.current&&!await savePromise.current)return false;return !dirtyRef.current||await save();}
  async function run(task:()=>Promise<void>){if(operation.current)return;reads.current++;operation.current=true;setBusy(true);setError('');try{if(await flush())await task();}catch(e){setError((e as Error).message);if(e instanceof OfferError&&e.status===409)setConflict(true);}finally{operation.current=false;setBusy(false);}}
  async function refresh(){if(dirtyRef.current&&!window.confirm('Открыть серверную версию и отменить несохранённые правки этой вкладки?'))return;try{accept(await offerApi.get(id));setError('');}catch(e){setError((e as Error).message);}}
  function requestId(value:OfferDocument,publish:boolean){return value.revisions.find(r=>r.source_version===value.version&&r.status==='FAILED'&&r.publication_requested===publish&&(!publish||!value.share.expired&&r.publication_epoch===value.share.epoch))?.request_id??crypto.randomUUID();}
  async function publish(){await run(async()=>{const value=current.current!;accept(await offerApi.action(id,'publish',value.version,requestId(value,true),value.share.epoch));setNotice('Подготавливаю опубликованную версию. Ссылка станет активной после готовности файлов.');});}
  async function exports(kind:'pdf'|'xlsx'){await run(async()=>{let value=current.current!;let revision=value.revisions.find(r=>(value.is_primary===false||r.source_version===value.version)&&r.status==='READY');const epoch=generation.current;
    if(!revision&&value.is_primary===false)throw new Error('В архивном предложении нет готовых файлов.');if(!revision){value=await offerApi.action(id,'exports',value.version,requestId(value,false),value.share.epoch);accept(value);const requested=value.version,deadline=Date.now()+330000;
      while(Date.now()<deadline){await new Promise(r=>setTimeout(r,1400));if(epoch!==generation.current)return;value=await offerApi.get(id);accept(value);revision=value.revisions.find(r=>r.source_version===requested&&r.status==='READY');if(revision)break;const failed=value.revisions.find(r=>r.source_version===requested&&['FAILED','CANCELLED'].includes(r.status));if(failed)throw new Error(failed.error||'Подготовка не завершилась. Повторите действие.');}
    }
    if(!revision)throw new Error('Подготовка ещё не завершена. Проверьте состояние версии.');
    await downloadOffer(kind==='pdf'?revision.pdf_url!:revision.xlsx_url!,`commercial-offer-v${revision.source_version}.${kind}`);
  });}
  async function upload(kind:'logo'|'banner',file:File){if(file.size>12*1024*1024){setError('Максимальный размер изображения — 12 МБ');return;}await run(async()=>accept(await offerApi.upload(id,kind,current.current!.version,file)));}
  async function copy(){if(copyPending.current)return;copyPending.current=true;setCopying(true);setNotice('');setCopyError('');const epoch=generation.current;
    try{const fresh=await offerApi.get(id);if(epoch!==generation.current)return;setDoc(previous=>previous?{...previous,share:fresh.share}:previous);if(current.current)current.current={...current.current,share:fresh.share};if(fresh.share.expired)throw new Error(`Срок действия ссылки истёк. Создайте ссылку на ${fresh.share.lifetime_days} дней.`);await copyOfferLink(fresh.share);if(epoch===generation.current)setNotice('Ссылка скопирована');}
    catch(e){if(epoch===generation.current){if(e instanceof OfferError&&e.status===404)readError(e);else setCopyError((e as Error).message);}}
    finally{copyPending.current=false;if(epoch===generation.current)setCopying(false);}
  }
  async function composition(){await run(async()=>{const delta=await offerApi.composition(id);if(!delta.added.length&&!delta.removed.length){setNotice('Состав уже соответствует подборке. Исключённые позиции сохранены.');return;}const describe=(rows:{title:string}[])=>rows.map(i=>i.title).join('\n');if(!window.confirm(`Обновить состав?\nДобавить: ${delta.added.length}\n${describe(delta.added)}\nУбрать: ${delta.removed.length}\n${describe(delta.removed)}\nРучные правки ${delta.retained} оставшихся позиций и исключённые товары сохранятся.`))return;accept(await offerApi.updateComposition(id,delta.version,delta.collection_version));setNotice('Состав черновика обновлён. Опубликованная версия сохранена.');});}
  const archived=doc?.is_primary===false;
  const locked=busy||saving||!!doc?.materials_preparing||archived,processing=!!doc?.revisions.some(r=>active.includes(r.status));
  const shown=doc&&form?{...doc.preview,items:form.items.flatMap(i=>{const value=doc.preview.items.find(p=>p.id===i.id);return value?[value]:[]})}:null;
  function item(id:string,patch:Partial<OfferForm['items'][number]>){if(form)edit({...form,items:form.items.map(i=>i.id===id?{...i,...patch}:i)});}
  function move(index:number,delta:number){if(!form)return;const items=[...form.items];[items[index],items[index+delta]]=[items[index+delta],items[index]];edit({...form,items});}
  if(unavailable)return <div className="commercial-public offer-editor" data-testid="offer-unavailable"><nav className="offer-toolbar"><Link href="/offers">Предложения</Link><Link href="/">К подбору товаров</Link></nav><section className="offer-error" role="alert"><h1>Предложение недоступно</h1><p>Оно удалено или у вас нет доступа. Чат и выбранные товары сохраняются в проекте.</p></section></div>;
  return <div className="commercial-public offer-editor" data-testid="offer-editor">
    <div className="offer-toolbar"><Link href="/">← К подборке</Link><Link href="/offers">Предложения</Link><strong>{doc?.draft.settings.title??'Предложение'}</strong><div><NotificationBell/><button disabled={locked||conflict} onClick={()=>void save()}>Сохранить</button><button disabled={locked} onClick={()=>setPreview(v=>!v)}>{preview?'Вернуться в редактор':'Клиентский вид'}</button><button disabled={busy||saving} onClick={()=>void exports('pdf')}>PDF</button><button disabled={busy||saving} onClick={()=>void exports('xlsx')}>Excel</button><button disabled={locked||dirty} onClick={()=>window.print()}>Печать</button></div></div>
    {error&&<div className="offer-error" role="alert"><p>{error}</p><button onClick={()=>void refresh()}>{conflict?'Открыть последнюю версию':'Обновить состояние'}</button></div>}
    {!doc||!form||!shown?<p className="offer-loading" role="status">Открываю редактор…</p>:<>
      <section className="offer-manager-panel"><div><strong>{saving?'Сохраняю…':dirty?'Есть несохранённые изменения':'Черновик сохранён'}</strong><p>{doc.share.active?'Клиент видит опубликованный снимок. Правки черновика не меняют его.':'Приватный документ. Доступ по ссылке появится после явной публикации.'}</p>{notice&&<p role="status">{notice}</p>}{processing&&<p role="status">Подготавливаю файлы…</p>}{doc.materials_preparing&&<p role="status">Подготавливаю изображение…</p>}</div>
        {archived?<div className="offer-archive-note"><strong>Предыдущее предложение · только просмотр</strong>{doc.primary_offer_id&&<Link href={`/offers/${doc.primary_offer_id}/edit`}>Открыть основное предложение проекта</Link>}</div>:<div className="offer-publish-actions">
          <p className="offer-publication-state">Публикация: {processing?'подготавливаются файлы':doc.share.active?'готова':doc.revisions[0]?.status==='FAILED'?'ошибка подготовки':'не опубликована'}.</p>
          <button className="offer-primary" aria-busy={busy||processing} disabled={locked||processing||conflict||doc.issues.length>0} onClick={()=>void publish()}>{doc.share.active?'Обновить опубликованную версию':`Создать ссылку на ${doc.share.lifetime_days} дней`}</button>
          {doc.share.expires_at&&<p className="offer-link-expiry">{doc.share.expired?'Срок действия ссылки истёк':'Ссылка действует до'} {offerExpiry(doc.share.expires_at)}</p>}
          {doc.share.active&&doc.share.public_url&&<><a href={doc.share.public_url} target="_blank" rel="noreferrer">Открыть клиентскую версию</a><button disabled={copying||busy} aria-busy={copying} onClick={()=>void copy()}>Копировать ссылку</button></>}
          {copyError&&<p className="offer-copy-error" role="alert">{copyError}</p>}
          {doc.share.active&&!doc.share.public_url&&<p role="alert">Публичный HTTPS-адрес не настроен. Настройте PUBLIC_FRONTEND_ORIGIN на backend.</p>}
        </div>}
        {!!doc.issues.length&&<ul className="offer-validation">{doc.issues.map((issue,n)=><li key={n}>{doc.draft.items.find(i=>i.id===issue.item_id)?.title}: {issue.message}</li>)}</ul>}
        {doc.revisions.filter(r=>r.status==='FAILED').slice(0,1).map(r=><p className="offer-error" key={r.id}>{r.error} Предыдущая публикация сохранена.</p>)}
        {doc.draft.materials.filter(m=>m.status==='FAILED').map(m=><p className="offer-error" key={m.id}>{m.error||'Не удалось подготовить изображение. Загрузите другой файл.'} Предыдущий готовый баннер сохранён.</p>)}
        {doc.revisions.find(r=>r.source_version===doc.version)?.warnings.map((warning,n)=><p className="offer-warning" key={n}>{doc.draft.items.find(i=>i.id===warning.item_id)?.title}: {warning.message}</p>)}
        {!preview&&!archived&&<details className="offer-settings"><summary>Настройки предложения</summary><div><button disabled={locked||processing||dirty} onClick={()=>void composition()}>Обновить состав из подборки</button><label>Название предложения<input maxLength={200} disabled={locked} value={form.title} onChange={e=>edit({...form,title:e.target.value})}/></label><label>Компания<input maxLength={200} disabled={locked} value={form.company} onChange={e=>edit({...form,company:e.target.value})}/></label><label>Логотип PNG / JPEG / WebP<input type="file" accept="image/png,image/jpeg,image/webp" disabled={locked} onChange={e=>{if(e.target.files?.[0])void upload('logo',e.target.files[0]);}}/></label><label><input type="checkbox" checked={form.use_logo} disabled={locked||!doc.draft.settings.logo} onChange={e=>edit({...form,use_logo:e.target.checked})}/>Использовать загруженный логотип; иначе — проектный</label><label>Баннер PNG / JPEG / WebP<input type="file" accept="image/png,image/jpeg,image/webp" disabled={locked} onChange={e=>{if(e.target.files?.[0])void upload('banner',e.target.files[0]);}}/></label><label><input type="checkbox" checked={form.use_banner} disabled={locked} onChange={e=>edit({...form,use_banner:e.target.checked})}/>Использовать загруженный баннер</label><button disabled={locked||processing||dirty} onClick={()=>void run(async()=>{accept(await offerApi.action(id,'refresh',current.current!.version,crypto.randomUUID(),current.current!.share.epoch));setNotice('Данные каталога обновлены. Ручная цена сохранена; проверьте список изменений.');})}>Обновить данные из каталога</button></div>{doc.draft.last_catalog_changes?.length?<ul>{doc.draft.last_catalog_changes.map((c,n)=><li key={n}>{doc.draft.items.find(i=>i.id===c.item_id)?.title}: {c.field} — {typeof c.before==='object'?JSON.stringify(c.before):String(c.before??'—')} → {typeof c.after==='object'?JSON.stringify(c.after):String(c.after??'—')}</li>)}</ul>:null}</details>}
      </section>
      {!preview&&<ClientFeedbackPanel identifier={id} publication={doc.share.published_revision_id}/>}
      <OfferView value={shown} editSlot={preview||archived?undefined:(value,index)=>{const row=form.items.find(i=>i.id===value.id)!;const source=doc.draft.items.find(i=>i.id===value.id)!;return <div className="offer-edit-fields"><OwnerStockNotice value={doc.owner_stock?.[row.id]} quantity={row.quantity}/><label>Название товара<input value={row.title} maxLength={500} disabled={locked} onChange={e=>item(row.id,{title:e.target.value})}/></label><div className="offer-price-inputs"><label>Цена за единицу, ₸<input type="text" inputMode="decimal" aria-label={`Цена: ${value.sku}`} value={row.unit_price??''} disabled={locked} onChange={e=>item(row.id,{unit_price:e.target.value.replace(',','.')||null})}/></label><label>Тираж, шт.<input type="number" min={1} max={1000000} step={1} aria-label={`Тираж: ${value.sku}`} value={row.quantity} disabled={locked} onChange={e=>item(row.id,{quantity:Number(e.target.value)})}/></label></div><small>Исходная цена VTO: {offerMoney(source.catalog_price)} · стартовый тираж {source.quantity_context.quantity??1}. {dirty?'Расчёт обновится после сохранения.':''}</small>
        <label>Артикул<input maxLength={200} disabled={locked} value={row.sku} onChange={e=>item(row.id,{sku:e.target.value})}/></label>{row.sku!==source.catalog_sku&&<label>Источник достоверного артикула<input maxLength={500} disabled={locked} value={row.sku_note} onChange={e=>item(row.id,{sku_note:e.target.value})}/></label>}
        {source.photos.length>1&&<Select label="Главное фото варианта" disabled={locked} value={row.main_image??''} options={source.photos.map((p,n)=>({value:p.id,label:`Фото ${n+1}`}))} onChange={main_image=>item(row.id,{main_image})}/>}
        <div className="offer-item-actions"><button disabled={locked||index===0} onClick={()=>move(index,-1)}>Выше</button><button disabled={locked||index===form.items.length-1} onClick={()=>move(index,1)}>Ниже</button><button disabled={locked||form.items.length===1} onClick={()=>edit({...form,items:form.items.filter(i=>i.id!==row.id)})}>Исключить позицию</button></div>
        {source.warnings.map((warning,n)=><p className="offer-warning" key={n}>{warning}</p>)}
      </div>;}}/>
    </>}
  </div>;
}

export function OfferList({project}:{project:string}){
  const [items,setItems]=useState<{id:string;title:string;version:number;is_primary?:boolean}[]>([]),[error,setError]=useState('');
  useEffect(()=>{let live=true;offerApi.list(project).then(v=>{if(live){setItems(v);setError('');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[project]);
  if(error)return <p role="alert">Не удалось прочитать предложения: {error}</p>;
  if(!items.length)return null;
  const primary=items.find(i=>i.is_primary),archive=items.filter(i=>!i.is_primary);
  return <section className="offer-document-list" aria-label="Коммерческое предложение">{primary&&<Link href={`/offers/${primary.id}/edit`}>Открыть предложение · {primary.title}</Link>}{!!archive.length&&<details><summary>Предыдущие предложения ({archive.length})</summary>{archive.map(i=><Link key={i.id} href={`/offers/${i.id}/edit`}>{i.title} · v{i.version}</Link>)}</details>}</section>;
}
