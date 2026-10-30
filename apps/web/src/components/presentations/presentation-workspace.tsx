'use client';
/* eslint-disable @next/next/no-img-element -- Authenticated PDFium page PNGs must use the browser session directly. */
import {useCallback,useEffect,useRef,useState} from 'react';
import {presentationApi,presentationStatus,type PresentationDocument,type PresentationDraft,type PresentationItem} from '@/lib/presentations';
import {colorLabel} from '@/lib/format';

export function PresentationList({projectId,onOpen}:{projectId:string;onOpen:(id:string)=>void}){
  const [items,setItems]=useState<{id:string;title:string;version:number}[]>([]);
  useEffect(()=>{let alive=true;presentationApi.list(projectId).then(values=>{if(alive)setItems(values);}).catch(()=>{});return()=>{alive=false;};},[projectId]);
  return items.length?<details className="presentation-list"><summary>Презентации проекта · {items.length}</summary>{items.map(item=><button className="studio-button" key={item.id} onClick={()=>onOpen(item.id)}>{item.title} · v{item.version}</button>)}</details>:null;
}

export function PresentationWorkspace({id,onBack,onProject}:{id:string;onBack:()=>void;onProject?:(project:string)=>void}){
  const [doc,setDoc]=useState<PresentationDocument|null>(null),[draft,setDraft]=useState<PresentationDraft|null>(null);
  const [dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [revisionId,setRevisionId]=useState(''),[page,setPage]=useState(1),[zoom,setZoom]=useState(85);
  const changes=useRef(0),savingRef=useRef(false),generation=useRef(0);
  const heading=useRef<HTMLHeadingElement>(null);
  const accept=useCallback((value:PresentationDocument)=>{setDoc(value);setDraft(value.draft);setDirty(false);},[]);
  useEffect(()=>{const token=++generation.current;presentationApi.get(id).then(value=>{if(token===generation.current)accept(value);}).catch(e=>setError(e.message));return()=>{generation.current=token+1;};},[id,accept]);
  useEffect(()=>{heading.current?.focus();},[]);
  useEffect(()=>{if(doc?.project_id)onProject?.(doc.project_id);},[doc?.project_id,onProject]);
  const active=doc?.revisions.find(r=>['QUEUED','PREPARING_ASSETS','RENDERING','VALIDATING'].includes(r.status));
  useEffect(()=>{
    if(!active)return;const token=generation.current;
    const timer=setInterval(()=>presentationApi.get(id).then(value=>{if(token===generation.current)setDoc(value);}).catch(e=>setError(e.message)),1000);
    return()=>clearInterval(timer);
  },[id,active]);
  useEffect(()=>{if(!dirty&&!saving)return;function warn(e:BeforeUnloadEvent){e.preventDefault();}window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty,saving]);
  function edit(next:PresentationDraft){changes.current++;setDraft(next);setDirty(true);setError('');}
  const save=useCallback(async()=>{
    if(!doc||!draft||!dirty||savingRef.current)return;
    const stamp=changes.current,token=generation.current;savingRef.current=true;setSaving(true);
    try{const value=await presentationApi.save(id,doc.version,draft);if(token!==generation.current)return false;setDoc(value);if(stamp===changes.current){setDraft(value.draft);setDirty(false);return true;}return false;}
    catch(e){if((e as {status?:number}).status===409){const fresh=await presentationApi.get(id);if(token===generation.current)setDoc(fresh);}setError((e as Error).message);return false;}finally{savingRef.current=false;setSaving(false);}
  },[id,doc,draft,dirty]);
  useEffect(()=>{if(!dirty||saving||error)return;const timer=setTimeout(()=>void save(),650);return()=>clearTimeout(timer);},[dirty,saving,error,save]);
  async function action(call:()=>Promise<unknown>){setBusy(true);setError('');const token=generation.current;try{await call();const value=await presentationApi.get(id);if(token===generation.current)setDoc(value);}catch(e){if(token===generation.current)setError((e as Error).message);}finally{setBusy(false);}}
  async function upload(file:File,offer?:string){await action(async()=>{const value=await presentationApi.upload(id,doc!.version,file,offer);accept(value);});}
  async function back(){if(saving)return;if(dirty&&!await save())return;onBack();}
  const revision=doc?.revisions.find(r=>r.id===revisionId)??doc?.revisions[0];
  const pages=revision?.result.pages??[];
  const shown=pages.find(p=>p.page===page)??pages[0];
  const previous=!!revision&&(dirty||revision.source_version!==doc?.version);
  const locked=busy||saving||dirty;
  function itemEdit(offer:string,patch:Partial<PresentationItem>){if(draft)edit({...draft,items:draft.items.map(item=>item.offer_id===offer?{...item,...patch}:item)});}
  function move(values:string[],index:number,delta:number){const result=[...values];[result[index],result[index+delta]]=[result[index+delta],result[index]];return result;}
  return <div className="presentation-workspace">
    <header className="presentation-header"><button className="studio-button" onClick={()=>void back()} disabled={saving}>← Вернуться к подборке</button><strong>Souvenir Studio</strong><span role="status">{saving?'Сохраняю…':dirty?'Есть несохранённые изменения':'Черновик сохранён'}</span></header>
    <h1 ref={heading} tabIndex={-1}>Презентация проекта</h1>
    {error&&<p className="presentation-error" role="alert">{error} <button className="studio-button" onClick={()=>{setError('');void save();}}>Повторить сохранение</button></p>}
    {!doc||!draft?<p role="status">Открываю документ…</p>:<>
      <div className="presentation-columns">
        <section className="presentation-composition"><h2>Состав · {draft.items.length}</h2><p className="presentation-help">Snapshot выбранных вариантов. Изменения здесь сохраняются только в документе.</p>
          <details><summary>Порядок категорий</summary>{draft.category_order.map((key,index)=><div className="presentation-category-order" key={key}><span>{draft.items.find(i=>i.category_key===key)?.category_label}</span><button aria-label="Категория выше" disabled={index===0} onClick={()=>edit({...draft,category_order:move(draft.category_order,index,-1)})}>↑</button><button aria-label="Категория ниже" disabled={index===draft.category_order.length-1} onClick={()=>edit({...draft,category_order:move(draft.category_order,index,1)})}>↓</button></div>)}</details>
          <ol className="presentation-items">{draft.items.map((item,index)=><li key={item.offer_id}>
            <div className="presentation-item-meta">{item.image_url&&<img src={item.image_url} alt={item.display_name}/>}<span>{item.category_label}<small>{[item.brand,...item.colors.map(c=>colorLabel(c.normalized_color))].filter(Boolean).join(' · ')}</small></span><div><button aria-label="Товар выше" disabled={!index} onClick={()=>{const ids=move(draft.items.map(i=>i.offer_id),index,-1);edit({...draft,items:ids.map(id=>draft.items.find(i=>i.offer_id===id)!)});}}>↑</button><button aria-label="Товар ниже" disabled={index===draft.items.length-1} onClick={()=>{const ids=move(draft.items.map(i=>i.offer_id),index,1);edit({...draft,items:ids.map(id=>draft.items.find(i=>i.offer_id===id)!)});}}>↓</button></div></div>
            <label>Название<input maxLength={220} value={item.display_name} onChange={e=>itemEdit(item.offer_id,{display_name:e.target.value})}/></label>
            <label>Краткая подпись<input maxLength={220} value={item.caption} onChange={e=>itemEdit(item.offer_id,{caption:e.target.value})}/></label>
            <p className={item.unit_price_kzt===null?'presentation-warning':''}>{item.unit_price_kzt===null?'Цена отсутствует: в PDF будет «Цена по запросу»':`${Number(item.unit_price_kzt).toLocaleString('ru-RU',{maximumFractionDigits:0})} ₸ / шт.`}</p>
            {item.images.length>1&&<label>Фото этого варианта<select value={item.image_url??''} onChange={e=>itemEdit(item.offer_id,{image_url:e.target.value})}>{item.images.map((url,n)=><option value={url} key={url}>Фото {n+1}</option>)}</select></label>}
            <label className="presentation-upload">Загрузить подготовленное фото<input type="file" accept="image/png,image/webp" disabled={locked} onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file,item.offer_id);}}/></label>
            <label className="presentation-check"><input type="checkbox" checked={draft.original_fallback_ids.includes(item.offer_id)} onChange={e=>edit({...draft,original_fallback_ids:e.target.checked?[...draft.original_fallback_ids,item.offer_id]:draft.original_fallback_ids.filter(id=>id!==item.offer_id)})}/>Разрешить исходное фото с фоном</label>
          </li>)}</ol>
        </section>
        <section className="presentation-settings"><h2>Оформление</h2>
          {(['title','client','subtitle'] as const).map(field=><label key={field}>{{title:'Название',client:'Клиент / компания',subtitle:'Подзаголовок'}[field]}<input maxLength={field==='subtitle'?220:field==='client'?100:160} value={draft.settings[field]} onChange={e=>edit({...draft,settings:{...draft.settings,[field]:e.target.value}})}/></label>)}
          <fieldset><legend>Палитра</legend><div className="presentation-palette">{draft.settings.palette.map((color,index)=><label key={index}>Цвет {index+1}<input type="color" value={color} onChange={e=>edit({...draft,settings:{...draft.settings,palette:draft.settings.palette.map((c,n)=>n===index?e.target.value:c)}})}/></label>)}</div><div className="presentation-buttons"><button className="studio-button" disabled={draft.settings.palette.length===4} onClick={()=>edit({...draft,settings:{...draft.settings,palette:[...draft.settings.palette,'#B79B73']}})}>Добавить цвет</button><button className="studio-button" disabled={draft.settings.palette.length===1} onClick={()=>edit({...draft,settings:{...draft.settings,palette:draft.settings.palette.slice(0,-1)}})}>Убрать цвет</button></div><small>Фоны и текст используют безопасные нейтральные оттенки. Фотографии и логотип не перекрашиваются.</small></fieldset>
          {(['cover','show_brand','use_logo'] as const).map(field=><label className="presentation-check" key={field}><input type="checkbox" checked={draft.settings[field]} onChange={e=>edit({...draft,settings:{...draft.settings,[field]:e.target.checked}})}/>{{cover:'Добавить обложку',show_brand:'Показывать бренд товара',use_logo:'Использовать логотип'}[field]}</label>)}
          <label className="presentation-upload">Логотип PNG / JPEG / WebP<input type="file" accept="image/png,image/jpeg,image/webp" disabled={locked} onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file);}}/></label>
          {draft.settings.logo&&<img className="presentation-logo" src={`/api/presentations/${id}/logo?v=${doc.version}`} alt="Логотип компании"/>}
          <label>Завершающее сообщение (необязательно)<textarea maxLength={500} value={draft.settings.ending_note} onChange={e=>edit({...draft,settings:{...draft.settings,ending_note:e.target.value}})}/></label>
          <button className="studio-button primary" disabled={locked||!!active||!draft.settings.title.trim()} onClick={()=>void action(()=>presentationApi.generate(id,doc.version))}>Сгенерировать PDF</button>
          {active&&<><p role="status">{presentationStatus(active.status)}</p><button className="studio-button" onClick={()=>void action(()=>presentationApi.cancel(id,active.id))}>Отменить генерацию</button></>}
          <button className="studio-button" disabled={locked||!!active} onClick={()=>void action(async()=>accept(await presentationApi.refresh(id,doc.version)))}>Обновить цены и фото из индекса</button><small>Явное обновление создаёт новую версию черновика. Готовые PDF сохраняются.</small>
        </section>
        <section className="presentation-preview"><div className="presentation-preview-heading"><h2>Предпросмотр PDF</h2>{doc.revisions.length>0&&<select aria-label="Версия презентации" value={revision?.id??''} onChange={e=>{setRevisionId(e.target.value);setPage(1);}}>{doc.revisions.map(r=><option value={r.id} key={r.id}>v{r.source_version} · {presentationStatus(r.status)}</option>)}</select>}</div>
          {previous&&<p className="presentation-warning">Предпросмотр относится к предыдущей версии. Сгенерируйте PDF с текущими настройками.</p>}
          {revision?.error&&<p role="alert">{revision.error}</p>}
          {revision?.result.issues?.map((issue,n)=><p className="presentation-warning" key={n}>{draft.items.find(i=>i.offer_id===issue.offer_id)?.display_name}: {issue.message}</p>)}
          {pages.length>0?<><div className="presentation-thumbnails">{pages.map(value=><button key={value.page} aria-label={`Страница ${value.page}`} aria-pressed={shown?.page===value.page} onClick={()=>setPage(value.page)}><img src={value.thumbnail} alt={`Страница ${value.page}`}/><span>{value.page}</span></button>)}</div><div className="presentation-preview-controls"><button disabled={(shown?.page??1)<=1} onClick={()=>setPage(p=>p-1)}>←</button><span>{shown?.page} / {pages.length}</span><button disabled={(shown?.page??1)>=pages.length} onClick={()=>setPage(p=>p+1)}>→</button><label>Масштаб<input aria-label="Масштаб PDF" type="range" min="40" max="160" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/>{zoom}%</label></div>
            <div className="presentation-page-scroll"><img className="presentation-page" style={{width:`${960*zoom/100}px`}} src={shown?.preview} alt={`PDF, страница ${shown?.page}`}/></div></>:<p className="presentation-preview-empty">Настройте оформление и сгенерируйте PDF. Здесь появятся страницы самого файла.</p>}
          {revision?.status==='NEEDS_REVIEW'&&revision.result.pdf_url&&!revision.result.issues?.length&&<div className="presentation-review"><h3>Проверьте вырезанные фотографии</h3><p>Убедитесь, что сохранены цвет, маркировка, ремни и тонкие детали. При ошибке загрузите подготовленное фото либо явно разрешите исходный фон.</p><div>{Object.entries(revision.result.assets??{}).map(([offer,asset])=><figure key={offer}><img src={asset.url} alt={draft.items.find(i=>i.offer_id===offer)?.display_name??'Фото варианта'}/><figcaption>{draft.items.find(i=>i.offer_id===offer)?.display_name}{asset.warnings.map((w,n)=><small key={n}>{w}</small>)}</figcaption></figure>)}</div><button className="studio-button primary" disabled={locked||previous} onClick={()=>void action(()=>presentationApi.approve(id,revision.id,Object.values(revision.result.assets??{}).filter(a=>a.review_required).map(a=>a.hash)))}>Фотографии проверены — разрешить скачивание</button></div>}
          {revision?.status==='READY'&&revision.result.pdf_url&&<a className="studio-button primary" aria-disabled={dirty||saving} href={dirty||saving?undefined:revision.result.pdf_url+(previous?'?historical=true':'')} download>{previous?`Скачать предыдущую v${revision.source_version}`:'Скачать PDF'}</a>}
        </section>
      </div>
    </>}
  </div>;
}
