'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { researchApi, type ResearchProduct } from '@/lib/research';
import type { Project } from '@/lib/types';
import { useResearch } from '@/hooks/use-research';
import { ProductCard } from '../product-card';
import { ProjectList } from '../projects/project-list';
import { ProjectForm } from '../projects/project-form';
import { Sheet } from '../common/sheet';
import { ResearchFilterPanel } from './research-filters';
import { ResearchProgress } from './research-progress';
import { ProductDetails } from '../product-details';
import { useAuthSession } from '../auth-gate';

export function ResearchWorkspace(){
  const {user,logout}=useAuthSession();
  const [projects,setProjects]=useState<Project[]>([]);
  const [chat,setChat]=useState<string|null>(null);
  const [form,setForm]=useState<Project|'new'|null>(null);
  const [drawer,setDrawer]=useState<'chat'|'projects'|'filters'|'selected'|null>(null);
  const [query,setQuery]=useState('');
  const [limit,setLimit]=useState(50);
  const [details,setDetails]=useState<ResearchProduct|null>(null);
  const [family,setFamily]=useState<ResearchProduct[]|null>(null);
  const [grouped,setGrouped]=useState(true);
  const state=useResearch(chat);
  const {research,busy,running,error}=state;
  const {setError}=state;
  useEffect(()=>{api.projects().then(values=>{
    setProjects(values);const saved=localStorage.getItem('forma-chat-id');
    setChat(values.some(p=>p.chat_id===saved)?saved:values[0]?.chat_id??null);
  }).catch(e=>setError(e.message));},[setError]);
  function open(id:string){setChat(id);localStorage.setItem('forma-chat-id',id);setDrawer(null);setLimit(50);}
  async function submit(){if(!query.trim()||busy)return;const value=query.trim();setQuery('');
    if(!chat){try{const created=await api.createChat('Новое исследование');
      await state.perform(()=>researchApi.start(created.id,value));open(created.id);setProjects(await api.projects());}catch(e){state.setError((e as Error).message);}}
    else await state.send(value);
  }
  function select(p:ResearchProduct){if(!research)return;const ids=research.view.selected_ids;
    state.change({selected_ids:ids.includes(p.id)?ids.filter(id=>id!==p.id):[...ids,p.id]});}
  const project=projects.find(p=>p.chat_id===chat);
  const representatives=new Set(research?.groups.map(group=>group.offer_ids[0])??[]);
  const groupedIds=new Set(research?.groups.flatMap(group=>group.offer_ids)??[]);
  const displayProducts=research?.products.filter(p=>!grouped||representatives.has(p.id)||!groupedIds.has(p.id))??[];
  const projectList=<ProjectList projects={projects} active={chat??undefined} disabled={busy} onOpen={open}
    onNew={()=>setForm('new')} onEdit={setForm}/>;
  const composer=<form className="research-composer" onSubmit={e=>{e.preventDefault();submit();}}>
    <label htmlFor={research?'research-message':'research-query'}>{research?'Уточните исследование':'Что нужно исследовать?'}</label>
    <textarea id={research?'research-message':'research-query'} value={query} onChange={e=>setQuery(e.target.value)}
      placeholder={research?'Покажи металлические. Найди похожие на 3 и 8.':'Синие бутылки, 300 шт.'}
      rows={3} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit();}}}/>
    <button className="studio-button primary" disabled={busy||!query.trim()}>{busy?'Обрабатываю…':research?'Отправить':'Начать исследование'}</button>
  </form>;
  const chatPane=<div className="research-chat-content"><h2>AI Research</h2>
    <div className="research-messages">{research?.messages.map((m,i)=><div key={i} className={`research-message ${m.role}`}><small>{m.role==='user'?'Вы':'Souvenir Studio'}</small><p>{m.content}</p></div>)}
      {busy&&<p role="status">Анализирую уточнение…</p>}
      {research&&<ResearchProgress research={research}/>}</div>
    {research&&<div className="research-suggestions">{['Более премиальные','Убери пластиковые','Выбери 5 лучших'].map(text=><button disabled={busy} key={text} onClick={()=>state.send(text)}>{text}</button>)}</div>}
    {composer}</div>;
  function cards(products:ResearchProduct[]){return <div className="research-grid">{products.map((p,index)=><div key={p.id}>
    <div className="research-match"><span>#{research?research.products.findIndex(value=>value.id===p.id)+1:index+1}</span><span>{p.match==='exact'?'Точное совпадение':'Близкий оттенок'}</span></div>
    <ProductCard product={p} selected={research?.view.selected_ids.includes(p.id)??false} disabled={busy} onSelect={()=>select(p)} onDetails={()=>setDetails(p)}/>
    {(p.material||p.capacity)&&<p className="research-attributes">{[p.material,p.capacity].filter(Boolean).join(' · ')}</p>}
    {!family&&research?.groups.find(group=>group.offer_ids[0]===p.id&&group.offer_ids.length>1)&&<button className="studio-button" onClick={()=>{
      const group=research.groups.find(value=>value.offer_ids.includes(p.id));setFamily(research.products.filter(value=>group?.offer_ids.includes(value.id)));}}>Варианты модели · {research.groups.find(group=>group.offer_ids.includes(p.id))?.offer_ids.length}</button>}
  </div>)}</div>;}
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
      {!research?<div className="research-intro"><small>6 поставщиков · Реальные данные</small><h1>Исследуйте варианты.<br/>Найдите своё.</h1>
        <p>Опишите задачу. AI найдёт варианты в обновляемом каталоге и поможет уточнить подборку.</p>{composer}
        <div className="research-suggestions">{['Синие бутылки, 300 шт.','Синий мерч для IT-конференции, 300 человек'].map(text=><button key={text} onClick={()=>setQuery(text)}>{text}</button>)}</div></div>:
      <><div className="research-heading"><small>{running?'Исследование продолжается':'Результаты исследования'}</small><h1>{research.intent.raw_query}</h1>
        <div className="research-stats"><span>Найдено <strong>{research.pool_count}</strong></span><span>Показано <strong>{research.products.length}</strong></span>
          <span>Источников <strong>{research.source_count}</strong></span>{research.source_mode!=='index'&&<span>Покрытие <strong>{research.completed_sources}/{research.source_count}</strong></span>}</div>
        {research.new_matches_count>0&&<p className="research-notice" role="status">Найдено {research.new_matches_count} новых вариантов. <button className="studio-button" disabled={busy} onClick={()=>state.perform(()=>researchApi.acceptMatches(research.id))}>Показать</button></p>}
        <div className="research-toolbar"><button className="studio-button" onClick={()=>setDrawer('filters')}>Фильтры</button>
          <select aria-label="Сортировка" disabled={busy} value={research.view.sorting} onChange={e=>state.change({sorting:e.target.value})}>
            <option value="relevance">По соответствию</option><option value="price_asc">Сначала дешевле</option><option value="price_desc">Сначала дороже</option></select>
          <button className="studio-button" onClick={()=>setDrawer('selected')}>Выбрано {research.view.selected_ids.length}</button>
          <button className="studio-button" aria-pressed={grouped} onClick={()=>setGrouped(value=>!value)}>{grouped?'По моделям':'Все варианты'}</button>
          <button className="studio-button" disabled={busy||!research.can_undo} onClick={()=>state.change({restore:'undo'})}>Отменить</button>
          <button className="studio-button" disabled={busy} onClick={()=>state.perform(()=>running?researchApi.cancel(research.id):researchApi.continue(research.id))}>{running?'Остановить':'Продолжить исследование'}</button>
          </div>
        {(research.view.filters.max_price!==null||research.view.filters.required_terms.length>0||research.view.filters.excluded_terms.length>0)&&<p className="research-notice">
          {research.view.filters.max_price!==null?`До ${research.view.filters.max_price.toLocaleString('ru-RU')} ₸. `:''}
          {research.view.filters.required_terms.join(', ')} {research.view.filters.excluded_terms.length?`Без: ${research.view.filters.excluded_terms.join(', ')}`:''}
          <button className="studio-button" disabled={busy} onClick={()=>state.change({restore:'reset'})}>Сбросить уточнения</button></p>}
      </div>
      {research.products.length?cards(displayProducts.slice(0,limit)):<div className="research-empty"><h2>{running?'Исследую каталоги':'Пока нет подходящих вариантов'}</h2><p>{research.pool_count?'Уточнения скрыли товары. Можно вернуться к исходной подборке.':'Товары появятся после проверки цвета, цены и доступного количества.'}</p>
        {research.pool_count>0&&<button className="studio-button" onClick={()=>state.change({restore:'reset'})}>Сбросить уточнения</button>}</div>}
      {(research.next_cursor||displayProducts.length>limit)&&<button className="studio-button" disabled={busy} onClick={()=>{setLimit(n=>n+50);if(research.next_cursor)state.loadMore();}}>Показать ещё · всего {research.matching_count}</button>}</>}
    </main>
    <aside className="research-chat">{research?chatPane:<><h2>AI Research</h2><p>После первого исследования здесь можно уточнять материалы, стиль и бюджет, искать похожие товары и продолжать обход каталогов.</p></>}</aside>
    <nav className="research-mobile-nav"><button onClick={()=>setDrawer(null)}>Подборка</button><button onClick={()=>setDrawer('selected')}>Выбрано {research?.view.selected_ids.length??0}</button><button onClick={()=>setDrawer('chat')}>AI</button><button onClick={()=>setDrawer('projects')}>Проект</button></nav>
    {error&&<div className="research-error" role="alert">{error}<button onClick={()=>state.setError('')} aria-label="Закрыть уведомление">×</button></div>}
    {drawer&&<Sheet title={{chat:'AI Research',projects:'Исследования',filters:'Фильтры',selected:'Выбранные товары'}[drawer]} onClose={()=>setDrawer(null)}>
      {drawer==='chat'?chatPane:drawer==='projects'?projectList:drawer==='filters'&&research?<ResearchFilterPanel research={research} disabled={busy} onChange={filters=>state.change({filters})}/>:
        drawer==='selected'&&research?<><button className="studio-button" disabled={busy} onClick={()=>state.change({selected_ids:[]})}>Очистить выбор</button>{research.selected_products.length?cards(research.selected_products):<p>Выберите товары в подборке или попросите AI.</p>}</>:null}</Sheet>}
    {form&&<ProjectForm project={form==='new'?undefined:form} onClose={()=>setForm(null)} onSave={async(name,client)=>{
      try{if(form==='new'){const value=await api.createChat(name);open(value.id);}else await api.updateProject(form.id,name,client);
        setProjects(await api.projects());return true;}catch(e){state.setError((e as Error).message);return false;}}}
      onDelete={form==='new'?undefined:async()=>{try{await api.deleteProject(form.id);setProjects(await api.projects());setChat(null);return true;}catch(e){state.setError((e as Error).message);return false;}}}/>}
    {details&&research&&<ProductDetails product={details} sessionId={research.id} researchMode onClose={()=>setDetails(null)}/>}
    {family&&<Sheet title="Варианты модели" onClose={()=>setFamily(null)}>{cards(family)}</Sheet>}
  </div>;
}
