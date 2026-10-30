'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { researchApi, type ResearchProduct } from '@/lib/research';
import type { Project } from '@/lib/types';
import { useResearch } from '@/hooks/use-research';
import { ProductCard } from '../product-card';
import { ProjectList } from '../projects/project-list';
import { ProjectForm } from '../projects/project-form';
import { Sheet } from '../common/sheet';
import { ResearchFilterPanel } from './research-filters';
import { ResearchHud, hasAdditionalFilters } from './research-hud';
import { matchCaption } from '@/lib/product-match';
import { ResearchProgress } from './research-progress';
import { ProductDetails } from '../product-details';
import { useAuthSession } from '../auth-gate';
import { WorkspaceQueries } from './workspace-queries';
import { workspaceApi } from '@/lib/project-queries';
import { SelectedProducts } from './selected-products';
import { presentationApi } from '@/lib/presentations';
import { PresentationWorkspace, PresentationList } from '../presentations/presentation-workspace';

export function ResearchWorkspace(){
  const {user,logout}=useAuthSession();
  const [projects,setProjects]=useState<Project[]>([]);
  const [chat,setChat]=useState<string|null>(null);
  const [form,setForm]=useState<Project|'new'|null>(null);
  const [drawer,setDrawer]=useState<'chat'|'projects'|'filters'|'selected'|null>(null);
  const [query,setQuery]=useState('');
  const [limit,setLimit]=useState(100);
  const [details,setDetails]=useState<ResearchProduct|null>(null);
  const [family,setFamily]=useState<ResearchProduct[]|null>(null);
  const [grouped,setGrouped]=useState(false);
  const requestGeneration=useRef(0);
  const [requestBusy,setRequestBusy]=useState(false);
  const [presentation,setPresentation]=useState<string|null>(null);
  const [creatingPresentation,setCreatingPresentation]=useState(false);
  useEffect(()=>{const id=window.location.hash.match(/^#presentation=([0-9a-f-]{36})$/i)?.[1];if(id)queueMicrotask(()=>setPresentation(id));},[]);
  function openPresentation(id:string){setPresentation(id);setDrawer(null);window.history.replaceState(null,'',`#presentation=${id}`);}
  function closePresentation(){setPresentation(null);window.history.replaceState(null,'',window.location.pathname+window.location.search);}

  const state=useResearch(chat);
  const {research,busy,running,error}=state;
  const {setError}=state;
  useEffect(()=>{api.projects().then(values=>{
    setProjects(values);const saved=localStorage.getItem('forma-chat-id');
    setChat(values.some(p=>p.chat_id===saved)?saved:values[0]?.chat_id??null);
  }).catch(e=>setError(e.message));},[setError]);
  function open(id:string){requestGeneration.current+=1;closePresentation();setChat(id);localStorage.setItem('forma-chat-id',id);setDrawer(null);setLimit(100);}
  async function submit(){
    if(!query.trim()||busy||requestBusy)return;
    const value=query.trim();setQuery('');setRequestBusy(true);
    let target=project;
    try {
      if(!target){const created=await api.createChat('Product research');const values=await api.projects();target=values.find(p=>p.chat_id===created.id);open(created.id);setProjects(values);}
      if(!target)throw new Error('Project not found');
      const generation=++requestGeneration.current;
      const result=await workspaceApi.universal(target.id,value);
      if(generation===requestGeneration.current){state.setResearch(result.research);setProjects(await api.projects());setLimit(100);}
    }catch(e){state.setError((e as Error).message);}finally{setRequestBusy(false);}
  }
  function select(p:ResearchProduct){state.selectProduct(p.id);}
  const project=projects.find(p=>p.chat_id===chat);
  const syncDocumentProject=useCallback((projectId:string)=>{const target=projects.find(p=>p.id===projectId);if(target&&target.chat_id!==chat){requestGeneration.current++;setChat(target.chat_id);localStorage.setItem('forma-chat-id',target.chat_id);}},[projects,chat]);
  async function createPresentation(){
    if(!project||!research||busy||creatingPresentation)return;
    const generation=requestGeneration.current;setCreatingPresentation(true);
    try{const value=await presentationApi.create(project.id,research.id,research.view.version);if(generation===requestGeneration.current)openPresentation(value.id);}
    catch(e){state.setError((e as Error).message);}finally{setCreatingPresentation(false);}
  }
  const representatives=new Set(research?.groups.map(group=>group.offer_ids[0])??[]);
  const groupedIds=new Set(research?.groups.flatMap(group=>group.offer_ids)??[]);
  const displayProducts=research?.products.filter(p=>!grouped||representatives.has(p.id)||!groupedIds.has(p.id))??[];
  const projectList=<ProjectList projects={projects} active={chat??undefined} disabled={busy} onOpen={open}
    onNew={()=>setForm('new')} onEdit={setForm}/>;
  const composer=<form className="research-composer" onSubmit={e=>{e.preventDefault();submit();}}>
    <label htmlFor={research?'research-message':'research-query'}>{research?'Уточните исследование':'Что нужно исследовать?'}</label>
    <textarea id={research?'research-message':'research-query'} value={query} onChange={e=>setQuery(e.target.value)}
      placeholder={research?'Оставь только Molti. Ручки сделай чёрными.':'Синие бутылки, 300 шт.'}
      rows={3} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit();}}}/>
    <button className="studio-button primary" disabled={busy||requestBusy||!query.trim()}>{busy||requestBusy?'Обрабатываю…':research?'Отправить':'Начать исследование'}</button>
  </form>;
  const chatPane=<div className="research-chat-content"><h2>AI Research</h2>
    <div className="research-messages">{(research?.messages??[]).map((m,i)=><div key={`${i}-${m.role}`} className={`research-message ${m.role}`}><small>{m.role==='user'?'Вы':'Souvenir Studio'}</small><p>{m.content}</p></div>)}
      {busy&&<p role="status">Анализирую уточнение…</p>}
      {research&&<ResearchProgress research={research}/>}</div>
    {research&&<div className="research-suggestions">{['Более премиальные','Убери пластиковые','Выбери 5 лучших'].map(text=><button disabled={busy} key={text} onClick={()=>state.send(text)}>{text}</button>)}</div>}
    {composer}</div>;
  function cards(products:ResearchProduct[]){return <div className="research-grid">{products.map((p,index)=><div key={p.id}>
    <div className="research-match"><span>#{research?research.products.findIndex(value=>value.id===p.id)+1:index+1}</span><span>{matchCaption(p)}</span></div>
    <ProductCard product={p} selected={research?.view.selected_ids.includes(p.id)??false} onSelect={()=>select(p)} onDetails={()=>setDetails(p)}/>
    {(p.material||p.capacity)&&<p className="research-attributes">{[p.material,p.capacity].filter(Boolean).join(' · ')}</p>}
    {!family&&research?.groups.find(group=>group.offer_ids[0]===p.id&&group.offer_ids.length>1)&&<button className="studio-button" onClick={()=>{
      const group=research.groups.find(value=>value.offer_ids.includes(p.id));setFamily(research.products.filter(value=>group?.offer_ids.includes(value.id)));}}>Варианты модели · {research.groups.find(group=>group.offer_ids.includes(p.id))?.offer_ids.length}</button>}
  </div>)}</div>;}
  if(presentation)return <PresentationWorkspace key={presentation} id={presentation} onBack={closePresentation} onProject={syncDocumentProject}/>;
  return <div className="research-shell">
    <header className="research-top">
      <strong className="research-top-brand" data-testid="header-brand">Souvenir Studio <span>Research</span></strong>
      <span className="research-top-project" data-testid="header-project-title" title={project?.name??'Исследование каталогов'}>{project?.name??'Исследование каталогов'}</span>
      <div className="research-top-account" data-testid="header-account">
        <button className="studio-button research-project-button" onClick={()=>setDrawer('projects')}>Проекты</button>
        <span className="research-top-username" title={user.name}>{user.name}</span>
        <button className="studio-button research-logout" data-testid="header-logout" onClick={logout}>Выйти</button>
      </div>
    </header>
    <aside className="research-projects"><h2>Исследования</h2>{projectList}</aside>
    <main className="research-main">
      {project&&<WorkspaceQueries projectId={project.id} onResearch={state.setResearch} onError={state.setError}/>}
      {project&&<PresentationList projectId={project.id} onOpen={openPresentation}/>}
      {(requestBusy&&!research?<div className="research-empty" role="status"><h2>Исследую каталог…</h2></div>:!research?<div className="research-intro"><small>VTO · Реальные данные</small><h1>Исследуйте варианты.<br/>Найдите своё.</h1>
        <p>Опишите задачу. AI найдёт варианты в обновляемом каталоге и поможет уточнить подборку.</p>{composer}
        <div className="research-suggestions">{['Синие бутылки, 300 шт.','Синий мерч для IT-конференции, 300 человек'].map(text=><button key={text} onClick={()=>setQuery(text)}>{text}</button>)}</div></div>:
      <><div className="research-heading"><small>{running?'Исследование продолжается':'Результаты исследования'}</small><h1>{research.intent.raw_query}</h1>
        <div className="research-stats"><span>Найдено <strong>{research.pool_count}</strong></span>{research.filtered_total!==research.base_total&&<span>По фильтрам <strong>{research.filtered_total}</strong></span>}<span>Показано <strong>{displayProducts.slice(0,limit).length}</strong></span>
{research.source_mode!=='index'&&<span>Покрытие <strong>{research.completed_sources}/{research.source_count}</strong></span>}</div>
        {research.new_matches_count>0&&<p className="research-notice" role="status">Найдено {research.new_matches_count} новых вариантов. <button className="studio-button" disabled={busy} onClick={()=>state.perform(()=>researchApi.acceptMatches(research.id))}>Показать</button></p>}
        <ResearchHud research={research} pending={busy||requestBusy} disabled={requestBusy} grouped={grouped} onGrouped={setGrouped}
          onFilters={filters=>state.change({filters})} onReset={()=>state.change({restore:'reset'})}
          onSelected={()=>setDrawer('selected')} onMobileFilters={()=>setDrawer('filters')}/>
        {research.empty_branches?.length?<p className="research-notice">Нет результатов: {research.empty_branches.join(', ')}</p>:null}
      </div>
      {research.products.length?cards(displayProducts.slice(0,limit)):<div className="research-empty"><h2>{running?'Исследую каталоги':'Пока нет подходящих вариантов'}</h2><p>{research.pool_count?'Уточнения скрыли товары. Можно вернуться к исходной подборке.':'Товары появятся после проверки цвета, цены и доступного количества.'}</p>
        {hasAdditionalFilters(research.view.filters)&&<button className="studio-button" onClick={()=>state.change({restore:'reset'})}>Сбросить уточнения</button>}</div>}
      {(research.next_cursor||displayProducts.length>limit)&&<button className="studio-button" disabled={busy} onClick={()=>{setLimit(n=>n+100);if(research.next_cursor)state.loadMore();}}>Показать ещё 100 · всего {research.matching_count}</button>}</>)}
    </main>
    <aside className="research-chat">{chatPane}</aside>
    <nav className="research-mobile-nav"><button onClick={()=>setDrawer(null)}>Подборка</button><button onClick={()=>setDrawer('selected')}>Выбранные</button><button onClick={()=>setDrawer('chat')}>AI</button><button onClick={()=>setDrawer('projects')}>Проект</button></nav>
    {error&&<div className="research-error" role="alert">{error}<button onClick={()=>state.setError('')} aria-label="Закрыть уведомление">×</button></div>}
    {drawer&&<Sheet className={drawer==='selected'?'selected-products-sheet':''} title={{chat:'AI Research',projects:'Исследования',filters:'Фильтры',selected:'Выбранные товары'}[drawer]} onClose={()=>setDrawer(null)}>
      {drawer==='chat'?chatPane:drawer==='projects'?projectList:drawer==='filters'&&research?<ResearchFilterPanel research={research} disabled={requestBusy} onChange={filters=>state.change({filters})}/>:
        drawer==='selected'&&research?<SelectedProducts products={research.selected_products} count={research.view.selected_ids.length} busy={busy||creatingPresentation}
          onRemove={state.selectProduct} onDetails={setDetails} onClear={()=>state.change({selected_ids:[]})} onCreate={()=>void createPresentation()}/>:null}</Sheet>}
    {form&&<ProjectForm project={form==='new'?undefined:form} onClose={()=>setForm(null)} onSave={async(name,client)=>{
      try{if(form==='new'){const value=await api.createChat(name);open(value.id);}else await api.updateProject(form.id,name,client);
        setProjects(await api.projects());return true;}catch(e){state.setError((e as Error).message);return false;}}}
      onDelete={form==='new'?undefined:async()=>{try{await api.deleteProject(form.id);setProjects(await api.projects());setChat(null);return true;}catch(e){state.setError((e as Error).message);return false;}}}/>}
    {details&&research&&<ProductDetails product={details} sessionId={research.id} researchMode onClose={()=>setDetails(null)}/>}
    {family&&<Sheet title="Варианты модели" onClose={()=>setFamily(null)}>{cards(family)}</Sheet>}
  </div>;
}
