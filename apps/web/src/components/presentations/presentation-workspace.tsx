'use client';
/* eslint-disable @next/next/no-img-element -- Authenticated PDFium page PNGs must use the browser session directly. */
import {useCallback,useEffect,useRef,useState} from 'react';
import {presentationApi,presentationStatus,presentationActive,designFallback,type PresentationDocument,type PresentationDraft,type PresentationItem} from '@/lib/presentations';
import {colorLabel} from '@/lib/format';
import {collectionApi} from '@/lib/project-collection';
import type {MaterialRole} from '@/lib/presentations';
import {mergePreparedMaterials,normalizePresentationDraft} from '@/lib/presentation-materials';
import {CustomMaterials} from './custom-materials';
import {PaletteExplorer} from './palette-explorer';
import {manualPalette} from '@/lib/presentation-palettes';
import {ThemeSwitch} from '../theme-switch';
import {Select} from '@/components/common/select';

export function PresentationList({projectId,onOpen}:{projectId:string;onOpen:(id:string)=>void}){
  const [items,setItems]=useState<{id:string;title:string;version:number}[]>([]);
  useEffect(()=>{let alive=true;presentationApi.list(projectId).then(values=>{if(alive)setItems(values);}).catch(()=>{});return()=>{alive=false;};},[projectId]);
  return items.length?<details className="presentation-list"><summary>Презентации проекта · {items.length}</summary>{items.map(item=><button className="studio-button" key={item.id} onClick={()=>onOpen(item.id)}>{item.title} · v{item.version}</button>)}</details>:null;
}

export function PresentationWorkspace({id,onBack,onProject}:{id:string;onBack:()=>void;onProject?:(project:string)=>void}){
  const [doc,setDoc]=useState<PresentationDocument|null>(null),[draft,setDraft]=useState<PresentationDraft|null>(null);
  const [dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [revisionId,setRevisionId]=useState(''),[page,setPage]=useState(1),[zoom,setZoom]=useState(85);
  const [paletteOpen,setPaletteOpen]=useState(false);
  const changes=useRef(0),savingRef=useRef(false),generation=useRef(0);
  const generationRequest=useRef<{key:string;version:number;pending:boolean}|null>(null);
  const dirtyRef=useRef(false),confirmed=useRef<PresentationDocument|null>(null);
  const heading=useRef<HTMLHeadingElement>(null);
  const accept=useCallback((value:PresentationDocument)=>{confirmed.current=value;dirtyRef.current=false;setDoc(value);setDraft(normalizePresentationDraft(value.draft));setDirty(false);},[]);
  const receive=useCallback((value:PresentationDocument)=>{const base=confirmed.current?.draft??value.draft;confirmed.current=value;setDoc(value);setDraft(current=>current&&(dirtyRef.current||savingRef.current)?mergePreparedMaterials(current,value.draft,base):normalizePresentationDraft(value.draft));},[]);
  useEffect(()=>{const token=++generation.current;presentationApi.get(id).then(value=>{if(token===generation.current){accept(value);try{const stored=sessionStorage.getItem('presentation-revision:'+id);setRevisionId(value.revisions.some(r=>r.id===stored)?stored!:'');}catch{setRevisionId('');}}}).catch(e=>setError(e.message));return()=>{generation.current=token+1;};},[id,accept]);
  useEffect(()=>{heading.current?.focus();},[]);
  useEffect(()=>{if(doc?.project_id)onProject?.(doc.project_id);},[doc?.project_id,onProject]);
  const active=doc?.revisions.find(r=>presentationActive.includes(r.status));
  useEffect(()=>{
    if(!active&&!doc?.materials_preparing)return;const token=generation.current;
    const timer=setInterval(()=>presentationApi.get(id).then(value=>{if(token===generation.current)receive(value);}).catch(e=>setError(e.message)),1000);
    return()=>clearInterval(timer);
  },[id,active,doc?.materials_preparing,receive]);
  useEffect(()=>{if(!dirty&&!saving)return;function warn(e:BeforeUnloadEvent){e.preventDefault();}window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty,saving]);
  function edit(next:PresentationDraft){changes.current++;dirtyRef.current=true;setDraft(next);setDirty(true);setError('');}
  const save=useCallback(async()=>{
    if(!doc||!draft||!dirty||savingRef.current||busy)return;
    const stamp=changes.current,token=generation.current;savingRef.current=true;setSaving(true);
    try{const value=await presentationApi.save(id,doc.version,draft);if(token!==generation.current)return false;if(stamp===changes.current){accept(value);return true;}receive(value);return false;}
    catch(e){if((e as {status?:number}).status===409){const fresh=await presentationApi.get(id);if(token===generation.current)receive(fresh);}setError((e as Error).message);return false;}finally{savingRef.current=false;setSaving(false);}
  },[id,doc,draft,dirty,busy,accept,receive]);
  useEffect(()=>{if(!dirty||saving||error||busy)return;const timer=setTimeout(()=>void save(),650);return()=>clearTimeout(timer);},[dirty,saving,error,busy,save]);
  async function action(call:()=>Promise<unknown>){setBusy(true);setError('');const token=generation.current;try{await call();const value=await presentationApi.get(id);if(token===generation.current)setDoc(value);}catch(e){if(token===generation.current)setError((e as Error).message);}finally{setBusy(false);}}
  async function generate(alternate=false){
    if(!doc||generationRequest.current?.pending)return;
    const failed=doc.revisions.find(r=>r.source_version===doc.version&&r.status==='FAILED');
    const previous=generationRequest.current;
    const key=failed?.request_id??(previous?.version===doc.version?previous.key:crypto.randomUUID());
    generationRequest.current={key,version:doc.version,pending:true};
    setRevisionId('');try{sessionStorage.removeItem('presentation-revision:'+id);}catch{}
    try{await action(async()=>{if(alternate)accept(await presentationApi.alternate(id,doc.version,key));else await presentationApi.generate(id,doc.version,key);generationRequest.current=null;});}
    finally{if(generationRequest.current)generationRequest.current.pending=false;}
  }
  async function upload(file:File,offer?:string){await action(async()=>{const value=await presentationApi.upload(id,doc!.version,file,offer);accept(value);});}
  async function uploadMaterials(files:File[],role:MaterialRole,replace?:string){
    const token=generation.current,stamp=changes.current;setBusy(true);setError('');
    try{for(const file of files){let value:PresentationDocument|undefined;const deadline=Date.now()+60000;
      while(!value){if(token!==generation.current)return;const fresh=await presentationApi.get(id);
        try{value=await presentationApi.uploadMaterial(id,fresh.version,file,role,replace);}catch(e){const status=(e as {status?:number}).status;if((status!==409&&status!==429)||Date.now()>deadline)throw e;await new Promise(resolve=>setTimeout(resolve,600));}
      }
      if(token!==generation.current)return;if(stamp===changes.current)accept(value);else receive(value);
    }}catch(e){if(token===generation.current)setError((e as Error).message);throw e;}finally{setBusy(false);}
  }
  async function back(){if(saving)return;if(dirty&&!await save())return;onBack();}
  const newest=doc?.revisions[0];
  const revision=doc?.revisions.find(r=>r.id===revisionId)??(newest?.result.pages?.length?newest:doc?.revisions.find(r=>r.result.pages?.length)??newest);
  const pages=revision?.result.pages??[];
  const shown=pages.find(p=>p.page===page)??pages[0];
  const previous=!!revision&&(dirty||revision.source_version!==doc?.version);
  const locked=busy||saving||dirty;
  function itemEdit(offer:string,patch:Partial<PresentationItem>){if(draft)edit({...draft,items:draft.items.map(item=>item.offer_id===offer?{...item,...patch}:item)});}
  function move(values:string[],index:number,delta:number){const result=[...values];[result[index],result[index+delta]]=[result[index+delta],result[index]];return result;}
  return <div className="presentation-workspace">
    <header className="presentation-header"><button className="studio-button" onClick={()=>void back()} disabled={saving}>← Вернуться к подборке</button><strong>Souvenir Studio</strong><span role="status">{saving?'Сохраняю…':dirty?'Есть несохранённые изменения':'Черновик сохранён'}</span><ThemeSwitch/></header>
    <h1 ref={heading} tabIndex={-1}>Презентация проекта</h1>
    {error&&<p className="presentation-error" role="alert">{error} <button className="studio-button" onClick={()=>{setError('');void save();}}>Повторить сохранение</button></p>}
    {!doc||!draft?<p role="status">Открываю документ…</p>:<>
      <div className="presentation-columns">
        <section className="presentation-composition"><h2>Состав · {draft.items.length}</h2><p className="presentation-help">Выбранные варианты зафиксированы в документе. Изменения не влияют на каталог.</p>
          <details><summary>Порядок категорий</summary>{draft.category_order.map((key,index)=><div className="presentation-category-order" key={key}><span>{draft.items.find(i=>i.category_key===key)?.category_label}</span><button aria-label="Категория выше" disabled={index===0} onClick={()=>edit({...draft,category_order:move(draft.category_order,index,-1)})}>↑</button><button aria-label="Категория ниже" disabled={index===draft.category_order.length-1} onClick={()=>edit({...draft,category_order:move(draft.category_order,index,1)})}>↓</button></div>)}</details>
          <ol className="presentation-items">{draft.items.map((item,index)=><li key={item.offer_id}>
            <div className="presentation-item-meta">{item.image_url&&<img src={item.image_url} alt={item.display_name}/>}<span>{item.category_label}<small>{[item.brand,...item.colors.map(c=>colorLabel(c.normalized_color))].filter(Boolean).join(' · ')}</small></span><div><button aria-label="Товар выше" disabled={!index} onClick={()=>{const ids=move(draft.items.map(i=>i.offer_id),index,-1);edit({...draft,items:ids.map(id=>draft.items.find(i=>i.offer_id===id)!)});}}>↑</button><button aria-label="Товар ниже" disabled={index===draft.items.length-1} onClick={()=>{const ids=move(draft.items.map(i=>i.offer_id),index,1);edit({...draft,items:ids.map(id=>draft.items.find(i=>i.offer_id===id)!)});}}>↓</button></div></div>
            <label>Название<input maxLength={220} value={item.display_name} onChange={e=>itemEdit(item.offer_id,{display_name:e.target.value})}/></label>
            <label>Краткая подпись<input maxLength={220} value={item.caption} onChange={e=>itemEdit(item.offer_id,{caption:e.target.value})}/></label>
            <p className={item.unit_price_kzt===null?'presentation-warning':''}>{item.unit_price_kzt===null?'Цена отсутствует: в PDF будет «Цена по запросу»':`${Number(item.unit_price_kzt).toLocaleString('ru-RU',{maximumFractionDigits:0})} ₸ / шт.`}</p>
            {item.images.length>1&&<Select label="Фото этого варианта" value={item.image_url??''} onChange={value=>itemEdit(item.offer_id,{image_url:value})} options={item.images.map((url,n)=>({value:url,label:`Фото ${n+1}`}))}/>}
            <label className="presentation-upload">Загрузить подготовленное фото<input type="file" accept="image/png,image/webp" disabled={locked} onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file,item.offer_id);}}/></label>
            <label className="presentation-check"><input type="checkbox" checked={draft.original_fallback_ids.includes(item.offer_id)} onChange={e=>edit({...draft,original_fallback_ids:e.target.checked?[...draft.original_fallback_ids,item.offer_id]:draft.original_fallback_ids.filter(id=>id!==item.offer_id)})}/>Разрешить исходное фото с фоном</label>
          </li>)}</ol>
        </section>
        <section className="presentation-settings"><h2>Оформление</h2>
          <Select label="Визуальное направление" value={draft.settings.style==='classic'?'editorial':draft.settings.style} onChange={value=>edit({...draft,settings:{...draft.settings,style:value as PresentationDraft['settings']['style'],style_version:2}})} options={[{value:'editorial',label:'Editorial — журнальная композиция'},{value:'premium-dark',label:'Premium Dark — контраст и крупные предметы'},{value:'modern-graphic',label:'Modern Graphic — цвет и модульная сетка'}]}/>
          <label className="presentation-check"><input type="checkbox" checked={draft.settings.auto_design} onChange={e=>edit({...draft,settings:{...draft.settings,auto_design:e.target.checked}})}/>Автодизайн · локальная Qwen</label><small>AI выбирает разрешённые композиции. Цены, варианты и фотографии сохраняются. При недоступной модели работает выбранный стиль.</small>
          {(['client','subtitle'] as const).map(field=><label key={field}>{{client:'Клиент / компания',subtitle:'Подзаголовок'}[field]}<input maxLength={field==='subtitle'?220:100} value={draft.settings[field]} onChange={e=>edit({...draft,settings:{...draft.settings,[field]:e.target.value}})}/></label>)}
          <fieldset><legend>Палитра</legend><button className="studio-button" disabled={busy||saving} onClick={()=>setPaletteOpen(true)}>Подобрать сочетание</button><p className="presentation-help">{draft.settings.palette_plan?.manual?'Своя палитра':draft.settings.palette_plan?.name??'Своя палитра'}{draft.settings.palette_plan?.locked?` · основной ${draft.settings.palette_plan.primary} зафиксирован`:''}</p><div className="presentation-palette">{draft.settings.palette.map((color,index)=><label key={index}>{draft.settings.palette.length===6?['Основной','Тёмный фон','Светлый фон','Акцент','Поверхность','Текст'][index]:`Цвет ${index+1}`}<input type="color" value={color} onChange={e=>edit({...draft,settings:manualPalette(draft.settings,draft.settings.palette.map((c,n)=>n===index?e.target.value:c))})}/></label>)}</div><div className="presentation-buttons"><button className="studio-button" disabled={draft.settings.palette.length===6} onClick={()=>edit({...draft,settings:manualPalette(draft.settings,[...draft.settings.palette,'#B79B73'])})}>Добавить цвет</button><button className="studio-button" disabled={draft.settings.palette.length===1} onClick={()=>edit({...draft,settings:manualPalette(draft.settings,draft.settings.palette.slice(0,-1))})}>Убрать цвет</button></div><small>Фон и текст адаптируются для контраста; основной HEX сохраняется точно. Фотографии и логотип не перекрашиваются.</small></fieldset>
          <fieldset><legend>Автооформление</legend><label className="presentation-check"><input type="checkbox" checked={draft.settings.auto_decoration?.enabled??false} onChange={e=>edit({...draft,settings:{...draft.settings,auto_decoration:{version:1,intensity:draft.settings.auto_decoration?.intensity??'SUBTLE',enabled:e.target.checked}}})}/>Добавлять декоративные детали автоматически</label><Select label="Выразительность" disabled={!draft.settings.auto_decoration?.enabled} value={draft.settings.auto_decoration?.intensity??'SUBTLE'} onChange={value=>edit({...draft,settings:{...draft.settings,auto_decoration:{version:1,enabled:true,intensity:value as 'SUBTLE'|'NORMAL'|'BOLD'}}})} options={[{value:'SUBTLE',label:'Деликатно'},{value:'NORMAL',label:'Умеренно'},{value:'BOLD',label:'Выразительно'}]}/><small>Работает без AI. Учитывает товары, текст и свои материалы на каждой странице.</small></fieldset>
          <p className="presentation-help">Первая страница — титульный лист без товаров. Если логотип клиента не выбран, используется проектный VTO.</p>
          {(['show_brand','use_logo'] as const).map(field=><label className="presentation-check" key={field}><input type="checkbox" checked={draft.settings[field]} disabled={field==='use_logo'&&!draft.settings.logo} onChange={e=>edit({...draft,settings:{...draft.settings,[field]:e.target.checked}})}/>{{show_brand:'Показывать бренд товара',use_logo:'Использовать загруженный логотип клиента'}[field]}</label>)}
          <label className="presentation-upload">Логотип PNG / JPEG / WebP<input type="file" accept="image/png,image/jpeg,image/webp" disabled={locked} onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file);}}/></label>
          {draft.settings.logo&&<img className="presentation-logo" src={`/api/presentations/${id}/logo?v=${doc.version}`} alt="Логотип компании"/>}
          {draft.settings.style==='premium-dark'?<CustomMaterials materials={draft.materials??[]} usage={revision?.result.material_usage??[]} previous={previous} disabled={locked} maxBytes={doc.material_limits?.max_bytes??12582912} maxDecorations={doc.material_limits?.max_decorations??6} onChange={materials=>edit({...draft,materials})} onUpload={uploadMaterials}/>:!!draft.materials?.length&&<p className="presentation-warning">Свои материалы сохранены. Для их применения выберите Premium Dark.</p>}
          <label>Завершающее сообщение (необязательно)<textarea maxLength={500} value={draft.settings.ending_note} onChange={e=>edit({...draft,settings:{...draft.settings,ending_note:e.target.value}})}/></label>
          {doc.materials_preparing&&<p role="status">Подготавливаю свои материалы… Поиск и подборка доступны.</p>}
          <button className="studio-button primary" disabled={locked||!!active||doc.materials_preparing} onClick={()=>void generate()}>Сгенерировать PDF</button>
          <button className="studio-button" disabled={locked||!!active||doc.materials_preparing} onClick={()=>void generate(true)}>Другой вариант оформления</button>
          {active&&<><p role="status">{presentationStatus(active.status)}</p><button className="studio-button" onClick={()=>void action(()=>presentationApi.cancel(id,active.id))}>Отменить генерацию</button></>}
          <button className="studio-button" disabled={locked||!!active} onClick={()=>void action(async()=>accept(await presentationApi.refresh(id,doc.version)))}>Обновить цены и фото из индекса</button><small>Явное обновление создаёт новую версию черновика. Готовые PDF сохраняются.</small>
          <button className="studio-button" disabled={locked||!!active} onClick={()=>{if(window.confirm('Заменить состав документа текущей подборкой проекта? Готовые версии сохранятся.'))void action(async()=>{const collection=await collectionApi.get(doc.project_id);accept(await presentationApi.composition(id,doc.version,collection.version));});}}>Обновить состав из подборки проекта</button>
        </section>
        <section className="presentation-preview"><div className="presentation-preview-heading"><h2>Предпросмотр PDF</h2>{doc.revisions.length>0&&<Select label="Версия презентации" hideLabel value={revision?.id??''} onChange={value=>{setRevisionId(value);setPage(1);try{sessionStorage.setItem('presentation-revision:'+id,value);}catch{}}} options={doc.revisions.map(r=>({value:r.id,label:`v${r.source_version} · ${presentationStatus(r.status)}`}))}/>}</div>
          {revision?.result.ai_used===true&&<p className="presentation-help">Оформление подготовлено локальной Qwen.</p>}
          {revision?.result.ai_used===false&&revision.result.fallback_reason!=='DISABLED'&&<p className="presentation-warning">Автодизайн не применён: {designFallback(revision.result.fallback_reason)}. Использован выбранный стиль.</p>}
          {previous&&<p className="presentation-warning">Предпросмотр относится к предыдущей версии. Сгенерируйте PDF с текущими настройками.</p>}
          {revision?.error&&<p role="alert">{revision.error}</p>}
          {revision?.result.design_signature?.status==='CONSTRAINED'&&<p className="presentation-warning">Безопасные композиции для этих настроек повторяются. Товары и читаемость сохранены; заметно новый вариант не найден.</p>}
          {revision?.result.issues?.map((issue,n)=><p className="presentation-warning" key={n}>{issue.offer_id?`${draft.items.find(i=>i.offer_id===issue.offer_id)?.display_name??'Товар'}: `:''}{issue.message}</p>)}
          {pages.length>0?<><div className="presentation-thumbnails">{pages.map(value=><button key={value.page} aria-label={`Страница ${value.page}`} aria-pressed={shown?.page===value.page} onClick={()=>setPage(value.page)}><img src={value.thumbnail} alt={`Страница ${value.page}`}/><span>{value.page}</span></button>)}</div><div className="presentation-preview-controls"><button disabled={(shown?.page??1)<=1} onClick={()=>setPage(p=>p-1)}>←</button><span>{shown?.page} / {pages.length}</span><button disabled={(shown?.page??1)>=pages.length} onClick={()=>setPage(p=>p+1)}>→</button><label>Масштаб<input aria-label="Масштаб PDF" type="range" min="40" max="160" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/>{zoom}%</label></div>
            <div className="presentation-page-scroll"><img className="presentation-page" style={{width:`${960*zoom/100}px`}} src={shown?.preview} alt={`PDF, страница ${shown?.page}`}/></div></>:<p className="presentation-preview-empty">Настройте оформление и сгенерируйте PDF. Здесь появятся страницы самого файла.</p>}
          {revision?.status==='NEEDS_REVIEW'&&revision.result.pdf_url&&!revision.result.issues?.length&&<div className="presentation-review"><h3>Проверьте вырезанные фотографии</h3><p>Убедитесь, что сохранены цвет, маркировка, ремни и тонкие детали. При ошибке загрузите подготовленное фото либо явно разрешите исходный фон.</p><div>{Object.entries(revision.result.assets??{}).map(([offer,asset])=><figure key={offer}><img src={asset.url} alt={draft.items.find(i=>i.offer_id===offer)?.display_name??'Фото варианта'}/><figcaption>{draft.items.find(i=>i.offer_id===offer)?.display_name}{asset.warnings.map((w,n)=><small key={n}>{w}</small>)}</figcaption></figure>)}</div><button className="studio-button primary" disabled={locked||previous} onClick={()=>void action(()=>presentationApi.approve(id,revision.id,Object.values(revision.result.assets??{}).filter(a=>a.review_required).map(a=>a.hash)))}>Фотографии проверены — разрешить скачивание</button></div>}
          {revision?.status==='READY'&&revision.result.pdf_url&&<a className="studio-button primary" aria-disabled={dirty||saving} href={dirty||saving?undefined:revision.result.pdf_url+(previous?'?historical=true':'')} download>{previous?`Скачать предыдущую v${revision.source_version}`:'Скачать PDF'}</a>}
        </section>
      </div>
    </>}
    {paletteOpen&&draft&&<PaletteExplorer current={draft.settings.palette_plan} dark={draft.settings.style==='premium-dark'} onClose={()=>setPaletteOpen(false)} onApply={palette=>{edit({...draft,settings:{...draft.settings,palette:[...palette.colors],palette_plan:palette}});setPaletteOpen(false);}}/>}
  </div>;
}
