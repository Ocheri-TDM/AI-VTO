'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import {useRouter} from 'next/navigation';
import { api, ApiError } from '@/lib/api';
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
import {useConversation} from '@/hooks/use-conversation';
import {Conversation,ConversationComposer} from './conversation';
import { ProductDetails } from '../product-details';
import { useAuthSession } from '../auth-gate';
import { WorkspaceQueries } from './workspace-queries';
import { workspaceApi } from '@/lib/project-queries';
import { SelectedProducts } from './selected-products';
import { offerApi,type ProjectOfferRef } from '@/lib/commercial-offers';
import { PresentationWorkspace } from '../presentations/presentation-workspace';
import {useProjectCollection} from '@/hooks/use-project-collection';
import {AppNavbar} from '../app-navbar';
import {useChatPanel} from '@/hooks/use-chat-panel';
import {readDraft,restoredSubmission,saveDraft,visibleConversation,workspacePanels,type ChatDraft,type LocalSubmission} from '@/lib/workspace-panels';
import {ChatPanelToggle} from './chat-panel-toggle';
import {CollectionPanelToggle} from './collection-panel-toggle';

export function ResearchWorkspace(){
  const router=useRouter();
  const {user,logout,expire}=useAuthSession();
  const [projects,setProjects]=useState<Project[]>([]);
  const [projectsReady,setProjectsReady]=useState(false);
  const [chat,setChat]=useState<string|null>(null);
  const [form,setForm]=useState<Project|null>(null);
  const [drawer,setDrawer]=useState<'projects'|'filters'|null>(null);
  const [collectionPanel,setCollectionPanel]=useState<string|null>(null);
  const collectionOpener=useRef<HTMLButtonElement>(null);
  const [query,setQuery]=useState('');
  const [mobile,setMobile]=useState(false);
  const [creatingChat,setCreatingChat]=useState(false);
  const createBusy=useRef(false),creationKey=useRef<string|null>(null),sending=useRef(false);
  const drafts=useRef(new Map<string,ChatDraft>());
  useEffect(()=>{const media=matchMedia('(max-width: 1100px)');const update=()=>setMobile(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
  const [limit,setLimit]=useState(100);
  const [details,setDetails]=useState<ResearchProduct|null>(null);
  const [family,setFamily]=useState<ResearchProduct[]|null>(null);
  const [grouped,setGrouped]=useState(false);
  const requestGeneration=useRef(0);
  const [requestBusy,setRequestBusy]=useState(false);
  const [submission,setSubmission]=useState<LocalSubmission|null>(null);
  const [presentation,setPresentation]=useState<string|null>(null);
  const [creatingPresentation,setCreatingPresentation]=useState(false);
  useEffect(()=>{const id=window.location.hash.match(/^#presentation=([0-9a-f-]{36})$/i)?.[1];if(id)queueMicrotask(()=>setPresentation(id));},[]);
  function closePresentation(){setPresentation(null);window.history.replaceState(null,'',window.location.pathname+window.location.search);}

  const state=useResearch(chat);
  const conversation=useConversation(chat);
  const project=projects.find(p=>p.chat_id===chat);
  const [projectOffer,setProjectOffer]=useState<{project:string;value:ProjectOfferRef|null}|null>(null);
  const [offerReadError,setOfferReadError]=useState('');
  useEffect(()=>{let live=true;if(project?.id)offerApi.primary(project.id).then(value=>{if(live){setProjectOffer({project:project.id,value});setOfferReadError('');}}).catch(e=>{if(live)setOfferReadError(e.message);});return()=>{live=false;};},[project?.id]);
  const offerKnown=projectOffer?.project===project?.id&&!offerReadError;
  const existingOffer=offerKnown?projectOffer?.value:null;
  const collection=useProjectCollection(project?.id??null);
  const {research,busy,running,error}=state;
  const {setError,setResearch}=state;
  const forgetDraft=useCallback((key:string,id:string)=>{
    const draft=readDraft(user.id,key)??drafts.current.get(key);
    if(draft?.id!==id)return false;
    drafts.current.delete(key);saveDraft(user.id,key,null);return true;
  },[user.id]);
  const refreshProjects=useCallback(async(generation:number)=>{
    try{const values=await api.projects();if(generation===requestGeneration.current){setProjects(values);setProjectsReady(true);}}catch{}
  },[]);
  const refreshHistory=conversation.refresh;
  const acknowledge=useCallback((key:string,id:string,generation:number)=>{
    const cleared=forgetDraft(key,id);
    if(generation!==requestGeneration.current)return;
    if(cleared)setQuery('');
    setSubmission(previous=>previous?.id===id?{...previous,phase:'ACKNOWLEDGED'}:previous);
  },[forgetDraft]);
  const reconcile=useCallback(async(target:{id:string;chat_id:string},id:string,generation:number)=>{
    try{
      const receipt=await workspaceApi.receipt(target.id,id);
      acknowledge(target.chat_id,id,generation);
      if(generation!==requestGeneration.current)return true;
      if(receipt.error_code!=='PRICE_RANGE_CONFLICT')setError('');
      if(receipt.status!=='ACCEPTED'){
        try{const value=await researchApi.latest(target.chat_id);if(generation===requestGeneration.current)setResearch(value);}catch{}
      }
      void refreshProjects(generation);void refreshHistory();return true;
    }catch(e){if(generation===requestGeneration.current&&e instanceof ApiError&&e.status===401)expire();return false;}
  },[acknowledge,setError,setResearch,expire,refreshProjects,refreshHistory]);
  // A lost response is not proof that the POST failed. Read its owner-scoped
  // receipt on reconnect; never submit it automatically a second time.
  const projectId=project?.id,projectChat=project?.chat_id;
  const pendingId=submission?.phase==='ERROR'&&submission.chat===chat?submission.id:null;
  useEffect(()=>{
    if(!pendingId||!projectId||!projectChat||requestBusy||busy)return;
    const generation=requestGeneration.current;let inFlight=false,live=true;
    const check=async()=>{if(!live||inFlight||generation!==requestGeneration.current||sending.current)return;inFlight=true;try{await reconcile({id:projectId,chat_id:projectChat},pendingId,generation);}finally{inFlight=false;}};
    void check();const timer=setInterval(()=>void check(),10000);
    window.addEventListener('online',check);window.addEventListener('focus',check);
    return()=>{live=false;clearInterval(timer);window.removeEventListener('online',check);window.removeEventListener('focus',check);};
  },[pendingId,projectId,projectChat,requestBusy,busy,reconcile]);
  const completion=conversation.messages.filter(m=>m.role==='assistant').at(-1);
  const completionResearch=useRef(research);useEffect(()=>{completionResearch.current=research;},[research]);
  useEffect(()=>{if(!chat||completion?.status!=='COMPLETED'||!completion.research_id)return;const previous=completionResearch.current;
    if(previous?.id===completion.research_id&&previous.revision>=(completion.result_revision??0))return;
    const generation=requestGeneration.current;let live=true;researchApi.latest(chat).then(value=>{if(live&&generation===requestGeneration.current&&value){setResearch(value);void refreshProjects(generation);}}).catch(()=>{});return()=>{live=false;};
  },[chat,completion?.id,completion?.research_id,completion?.result_revision,completion?.status,setResearch,refreshProjects]);
  useEffect(()=>{const generation=requestGeneration.current;api.projects().then(values=>{
    if(generation!==requestGeneration.current)return;
    setProjects(values);setProjectsReady(true);const saved=localStorage.getItem('forma-chat-id');
    if(generation!==requestGeneration.current)return;
    const initial=readDraft(user.id,'initial');
    const id=initial?.id?null:values.some(p=>p.chat_id===saved)?saved:values[0]?.chat_id??null;setChat(id);
    const key=id??'initial',draft=readDraft(user.id,key);
    if(draft){drafts.current.set(key,draft);setQuery(draft.text);setSubmission(restoredSubmission(id,draft));}
  }).catch(e=>setError(e.message));},[setError,user.id]);
  function storeDraft(key:string,draft:ChatDraft){drafts.current.set(key,draft);saveDraft(user.id,key,draft);}
  function editQuery(text:string){setQuery(text);const key=chat??'initial';const old=drafts.current.get(key);storeDraft(key,{...old,text,id:old?.text===text?old.id:undefined});}
  function open(id:string){requestGeneration.current+=1;closePresentation();setChat(id);localStorage.setItem('forma-chat-id',id);const draft=drafts.current.get(id)??readDraft(user.id,id);if(draft)drafts.current.set(id,draft);setQuery(draft?.text??'');setSubmission(restoredSubmission(id,draft));setDrawer(null);setCollectionPanel(null);setLimit(100);state.setError('');}
  async function newChat(){if(createBusy.current)return;createBusy.current=true;setCreatingChat(true);creationKey.current??=crypto.randomUUID();const generation=++requestGeneration.current;
    try{const created=await api.createChat('Новый чат',creationKey.current);const values=await api.projects();if(!values.some(p=>p.chat_id===created.id))throw new Error('Не удалось открыть сохранённый чат');setProjects(values);setProjectsReady(true);creationKey.current=null;if(generation===requestGeneration.current)open(created.id);}
    catch(e){state.setError((e as Error).message);}finally{createBusy.current=false;setCreatingChat(false);}}
  async function submit(){
    if(!query.trim()||busy||sending.current)return;
    if(query.length>2000){state.setError('Сообщение должно содержать не более 2000 символов.');return;}
    const value=query;sending.current=true;setRequestBusy(true);state.setError('');
    const draftKey=chat??'initial';const old=drafts.current.get(draftKey)??readDraft(user.id,draftKey);const requestId=old?.text===value&&old.id?old.id:crypto.randomUUID();
    const draft:ChatDraft={text:value,id:requestId,created_at:old?.id===requestId?old.created_at??new Date().toISOString():new Date().toISOString(),creation_id:chat?undefined:old?.creation_id??crypto.randomUUID()};
    storeDraft(draftKey,draft);
    setSubmission({chat,id:requestId,text:value,created_at:draft.created_at!,phase:'SENDING'});
    if(!panels.started){setCollectionPanel(null);chatPanel.show();}
    let target=project;
    let key=draftKey;
    const generation=++requestGeneration.current;
    try {
      if(!target){
        let targetChat=chat;
        if(!targetChat){
          const created=await api.createChat('Новый чат',draft.creation_id);targetChat=created.id;key=targetChat;
          // Save the new location before the next network read: chat creation
          // may have succeeded even when loading projects subsequently fails.
          storeDraft(key,{...draft,creation_id:undefined});forgetDraft(draftKey,requestId);
          if(generation===requestGeneration.current){setSubmission(previous=>previous?.id===requestId?{...previous,chat:targetChat}:previous);chatPanel.transfer(targetChat);setChat(targetChat);localStorage.setItem('forma-chat-id',targetChat);}
        }
        const values=await api.projects();target=values.find(p=>p.chat_id===targetChat);
        if(generation===requestGeneration.current){setProjects(values);setProjectsReady(true);}
      }
      if(!target)throw new Error('Не удалось открыть сохранённый чат. Повторите подключение.');
      const result=await workspaceApi.universal(target.id,value,requestId);
      acknowledge(key,requestId,generation);
      if(generation===requestGeneration.current){
        setLimit(100);
        // The POST response may have waited in a proxy while a newer request
        // (e.g. from another tab) became active. Check the persisted receipt
        // before displaying that response, not just its historical active flag.
        try{
          const receipt=await workspaceApi.receipt(target.id,requestId);
          if(generation===requestGeneration.current){
            if(receipt.active&&result.active!==false&&result.research)setResearch(result.research);
            else if(receipt.status!=='ACCEPTED'){
              const value=await researchApi.latest(target.chat_id);if(generation===requestGeneration.current)setResearch(value);
            }
          }
        }catch(e){if(generation===requestGeneration.current){if(e instanceof ApiError&&e.status===401)expire();else setError('Сообщение сохранено. Не удалось загрузить актуальную выдачу.');}}
        if(generation===requestGeneration.current){void refreshProjects(generation);void refreshHistory();}
      }
    }catch(e){
      if(generation===requestGeneration.current){
        setError((e as Error).message);
        setSubmission(previous=>previous?.id===requestId?{...previous,phase:'ERROR'}:previous);
      }
      if(e instanceof ApiError&&e.status===401){if(generation===requestGeneration.current)expire();}
      else if(target)await reconcile(target,requestId,generation);
      if(generation===requestGeneration.current)void refreshHistory();
    }finally{sending.current=false;setRequestBusy(false);}
  }
  function select(p:ResearchProduct){collection.toggle(p,research?.id);}
  const syncDocumentProject=useCallback((projectId:string)=>{const target=projects.find(p=>p.id===projectId);if(target&&target.chat_id!==chat){requestGeneration.current++;setChat(target.chat_id);localStorage.setItem('forma-chat-id',target.chat_id);}},[projects,chat]);
  async function createPresentation(){
    if(!project||busy||creatingPresentation)return;
    const generation=requestGeneration.current;setCreatingPresentation(true);
    try{let identifier=existingOffer?.id;if(!identifier){const current=await offerApi.state(project.id);identifier=current.offer?.id;if(!identifier){const saved=await collection.flush();identifier=(await offerApi.create(project.id,saved.version,current.generation)).id;}}if(generation===requestGeneration.current)router.push(`/offers/${identifier}/edit`);}
    catch(e){state.setError((e as Error).message);}finally{setCreatingPresentation(false);}
  }
  const representatives=new Set(research?.groups.map(group=>group.offer_ids[0])??[]);
  const groupedIds=new Set(research?.groups.flatMap(group=>group.offer_ids)??[]);
  const displayProducts=research?.products.filter(p=>!grouped||representatives.has(p.id)||!groupedIds.has(p.id))??[];
  const projectList=<ProjectList projects={projects} active={project?.id} disabled={busy} onOpen={open}
    onNew={()=>void newChat()} onEdit={setForm} creating={creatingChat}/>;
  const localBusy=requestBusy&&submission?.chat===chat;
  const panels=workspacePanels({projectsReady,chat,historyReady:conversation.ready,researchReady:state.ready,collectionReady:!!collection.value,hasResearch:!!research,hasMessages:conversation.messages.length>0,knownMessages:!!project?.has_messages,submission});
  const chatPanel=useChatPanel(user.id,chat,panels.started);
  const collectionExpanded=!!project&&collectionPanel===project.id;
  const chatExpanded=chatPanel.expanded&&!collectionExpanded;
  function showCollection(){if(!project)return;chatPanel.hide();setDrawer(null);setCollectionPanel(project.id);requestAnimationFrame(()=>document.querySelector<HTMLElement>('#workspace-collection [data-collection-collapse]')?.focus({preventScroll:true}));}
  function hideCollection(){setCollectionPanel(null);requestAnimationFrame(()=>collectionOpener.current?.focus({preventScroll:true}));}
  function showChat(){setCollectionPanel(null);setDrawer(null);chatPanel.show();}
  const composer=<ConversationComposer value={query} onChange={editQuery} onSubmit={()=>void submit()} busy={busy||localBusy} disabled={requestBusy&&!localBusy} started={panels.started} focusOnMount={!mobile}/>;
  const chatPane=<Conversation key={chat} messages={visibleConversation(conversation.messages,submission,chat)} hasOlder={conversation.hasOlder} loading={conversation.loading} error={conversation.error} onOlder={conversation.older} onRetry={()=>void conversation.refresh()} busy={localBusy} restoring={!!chat&&!conversation.ready&&submission?.chat!==chat} showHeading={!mobile} onCollapse={chatPanel.hide} composer={panels.started?composer:null}/>;
  const collectionPane=collection.value?<SelectedProducts products={collection.value.products} count={collection.value.selected_ids.length} busy={collection.busy||creatingPresentation}
    error={collection.error||error} onRetry={collection.error?collection.retry:undefined}
    hasOffer={!!existingOffer} offerLoading={!offerKnown} offerReadError={offerReadError} onRemove={collection.remove} onDetails={setDetails} onClear={()=>void collection.clear()} onCreate={()=>void createPresentation()}/>:<div className="selected-products-panel"><p className="selected-products-error" role={collection.error?'alert':'status'}>{collection.error||'Загружаю подборку…'} {collection.error&&<button className="studio-button" onClick={()=>{collection.setError('');void collection.reload().catch(e=>collection.setError(e.message));}}>Повторить загрузку</button>}</p></div>;
  function cards(products:ResearchProduct[],context:'default'|'modelVariants'='default'){return <div className={`research-grid${context==='modelVariants'?' model-variants-grid':''}`}>{products.map((p,index)=><div key={p.id}>
    <div className="research-match"><span>#{research?research.products.findIndex(value=>value.id===p.id)+1:index+1}</span><span>{matchCaption(p)}</span></div>
    <ProductCard product={p} context={context} selected={collection.value?.selected_ids.includes(p.id)??false} onSelect={()=>select(p)} onDetails={()=>setDetails(p)}/>
    {(p.material||p.capacity)&&<p className="research-attributes">{[p.material,p.capacity].filter(Boolean).join(' · ')}</p>}
    {context==='default'&&research?.groups.find(group=>group.offer_ids[0]===p.id&&group.offer_ids.length>1)&&<button className="studio-button" onClick={()=>{
      const group=research.groups.find(value=>value.offer_ids.includes(p.id));setFamily(research.products.filter(value=>group?.offer_ids.includes(value.id)));}}>Варианты модели · {research.groups.find(group=>group.offer_ids.includes(p.id))?.offer_ids.length}</button>}
  </div>)}</div>;}
  if(presentation)return <PresentationWorkspace key={presentation} id={presentation} onBack={closePresentation} onProject={syncDocumentProject}/>;
  return <div className="research-shell" data-chat-expanded={!mobile&&chatExpanded?'true':'false'} data-collection-expanded={!mobile&&collectionExpanded?'true':'false'} data-chat-animate={chatPanel.animate?'true':'false'}>
    <AppNavbar name={user.name} logout={logout} onProjects={()=>setDrawer('projects')}/>
    <aside className="research-projects"><h2>Чаты</h2>{projectList}</aside>
    <div className="research-content">
      {(project||panels.started)&&<div className="research-workspace-actions">
        {project&&<WorkspaceQueries projectId={project.id} onResearch={value=>{requestGeneration.current++;state.setResearch(value);}} onError={state.setError}/>}
        <div className="workspace-panel-actions">
          {project&&<CollectionPanelToggle buttonRef={collectionOpener} expanded={collectionExpanded} mobile={mobile} count={collection.value?.selected_ids.length??null} onClick={collectionExpanded?hideCollection:showCollection}/>}
          {panels.started&&!chatExpanded&&<ChatPanelToggle buttonRef={node=>chatPanel.bindOpener(node)} expanded={false} mobile={mobile} onClick={showChat}/>}
        </div>
      </div>}
    <main className="research-main">
      {(localBusy&&!research?<div className="research-empty" role="status"><h2>Исследую каталог…</h2></div>:!research&&!panels.restored?<div className="research-empty" role="status"><p>Восстанавливаю рабочее пространство…</p>{(error||collection.error||conversation.error)&&<button className="studio-button" onClick={()=>window.location.reload()}>Повторить загрузку</button>}</div>:!research?<div className="research-intro"><small>VTO · Реальные данные</small><h1>Исследуйте варианты.<br/>Найдите своё.</h1>
        <p>Опишите задачу. AI найдёт варианты и поможет уточнить подборку.</p>{panels.showInitialComposer&&composer}</div>:
      <><div className="research-heading"><small>{running?'Исследование продолжается':'Результаты исследования'}</small><h1>{research.effective_title??research.intent.raw_query}</h1>
        {completion?.status==='FAILED'&&completion.error_code==='PRICE_RANGE_CONFLICT'&&<p className="research-notice" role="status">Предыдущая выдача — уточните диапазон цены.</p>}
        <div className="research-stats"><span>Найдено <strong>{research.matching_count}</strong></span><span>Показано <strong>{displayProducts.slice(0,limit).length}</strong></span>
{research.source_mode!=='index'&&<span>Покрытие <strong>{research.completed_sources}/{research.source_count}</strong></span>}</div>
        {research.new_matches_count>0&&<p className="research-notice" role="status">Найдено {research.new_matches_count} новых вариантов. <button className="studio-button" disabled={busy} onClick={()=>state.perform(()=>researchApi.acceptMatches(research.id))}>Показать</button></p>}
        <ResearchHud research={{...research,selected_count:collection.value?.selected_ids.length??0,view:{...research.view,selected_ids:collection.value?.selected_ids??[]}}} pending={busy||requestBusy} disabled={requestBusy} grouped={grouped} onGrouped={setGrouped}
          loading={localBusy}
          onFilters={filters=>state.change({filters})} onEffectiveChange={edit=>state.change({effective_changes:[edit]})} onReset={()=>state.change({restore:'reset'})}
          onCategoryAction={action=>{setLimit(100);state.change({branch_actions:[action]});}}
          onMobileFilters={()=>setDrawer('filters')}/>
        {research.empty_branches?.length?<p className="research-notice">Нет результатов: {research.empty_branches.join(', ')}</p>:null}
        {research.identity_notices?.map((notice,n)=><p className="research-notice" role="status" key={n}>{notice}</p>)}
      </div>
      {research.products.length?cards(displayProducts.slice(0,limit)):<div className="research-empty"><h2>{research.result_state==='ALL_CATEGORIES_HIDDEN'?'Все категории скрыты':research.result_state==='NO_QUERY_CATEGORIES'?'В запросе не осталось категорий':running?'Исследую каталоги':'Пока нет подходящих вариантов'}</h2><p>{research.result_state==='ALL_CATEGORIES_HIDDEN'?'Покажите категории снова — их условия сохранены.':research.result_state==='NO_QUERY_CATEGORIES'?'Верните категорию выше или введите новый запрос.':research.pool_count?'Уточнения скрыли товары. Можно вернуться к исходной подборке.':'Товары появятся после проверки цвета, цены и доступного количества.'}</p>
        {research.result_state==='ALL_CATEGORIES_HIDDEN'&&<button className="studio-button" onClick={()=>state.change({branch_actions:[{action:'show_all'}]})}>Показать все</button>}
        {hasAdditionalFilters(research.view.filters,research.view.effective_overrides)&&<button className="studio-button" onClick={()=>state.change({restore:'reset'})}>Сбросить уточнения</button>}</div>}
      {(research.next_cursor||displayProducts.length>limit)&&<button className="studio-button" disabled={busy} onClick={()=>{setLimit(n=>n+100);if(research.next_cursor)state.loadMore();}}>Показать ещё 100 · всего {research.matching_count}</button>}</>)}
    </main></div>
    {!mobile&&!collectionExpanded&&chatPanel.mounted&&<aside id="workspace-chat" className="research-chat" inert={!chatPanel.expanded} aria-hidden={!chatPanel.expanded}><div className="research-chat-inner">{chatPane}</div></aside>}
    {!mobile&&collectionExpanded&&<aside id="workspace-collection" className="research-collection" aria-label="Подборка" onKeyDown={event=>{if(event.key==='Escape'&&!event.defaultPrevented&&!document.querySelector('dialog[open]')){event.preventDefault();hideCollection();}}}><div className="research-collection-inner">
      <header className="conversation-heading"><h2>Подборка</h2><CollectionPanelToggle expanded collapse onClick={hideCollection}/></header>
      {collectionPane}
    </div></aside>}
    <nav className="research-mobile-nav"><button onClick={()=>{setDrawer(null);if(collectionExpanded)hideCollection();}}>Товары</button><button onClick={showCollection} aria-expanded={collectionExpanded} aria-controls="workspace-collection">Подборка</button><button onClick={()=>setDrawer('projects')}>Проект</button></nav>
    {(error||collection.error)&&<div className="research-error" role="alert">{error||collection.error}{collection.error&&<button onClick={collection.retry}>Повторить сохранение</button>}<button onClick={()=>{state.setError('');collection.setError('');}} aria-label="Закрыть уведомление">×</button></div>}
    {mobile&&chatExpanded&&<Sheet id="workspace-chat" className="workspace-chat-sheet" title="Чат" onClose={chatPanel.hide} closeControl={<ChatPanelToggle expanded onClick={chatPanel.hide}/>}>{chatPane}</Sheet>}
    {mobile&&collectionExpanded&&<Sheet id="workspace-collection" className="selected-products-sheet workspace-collection-sheet" title="Подборка" onClose={hideCollection} closeControl={<CollectionPanelToggle expanded collapse onClick={hideCollection}/>}>{collectionPane}</Sheet>}
    {drawer&&<Sheet title={{projects:'Чаты',filters:'Фильтры'}[drawer]} onClose={()=>setDrawer(null)}>
      {drawer==='projects'?projectList:research?<ResearchFilterPanel research={research} loading={localBusy} disabled={requestBusy} onChange={filters=>state.change({filters})} onEffectiveChange={edit=>state.change({effective_changes:[edit]})}/>:null}</Sheet>}
    {form&&<ProjectForm project={form} onClose={()=>setForm(null)} onSave={async(name,client)=>{
      try{await api.updateProject(form.id,name,client);
        setProjects(await api.projects());return true;}catch(e){state.setError((e as Error).message);return false;}}}
      onDelete={async()=>{try{await api.deleteProject(form.id);setProjects(await api.projects());setChat(null);return true;}catch(e){state.setError((e as Error).message);return false;}}}/>}
    {details&&research&&<ProductDetails product={details} sessionId={research.id} researchMode onClose={()=>setDetails(null)}/>}
    {family&&<Sheet variant="modelVariants" title="Варианты модели" onClose={()=>setFamily(null)}>{cards(family,'modelVariants')}</Sheet>}
  </div>;
}
