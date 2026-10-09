'use client';
import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { Check, ExternalLink, ImageOff } from 'lucide-react';
import { apiPath } from '@/lib/api-config';
import { workspaceApi, type HardCase, type HardRequest, type ProjectQuery } from '@/lib/hard-requests';
import type { Research } from '@/lib/research';
import { Sheet } from '../common/sheet';

export function WorkspaceQueries({projectId,onResearch,onError,onComposite,showComposite}:{projectId:string;onResearch:(value:Research)=>void;onError:(value:string)=>void;onComposite:(active:boolean)=>void;showComposite:boolean}){
  const [dialog,setDialog]=useState<'history'|null>(null);
  const [queries,setQueries]=useState<ProjectQuery[]>([]); const [requests,setRequests]=useState<HardRequest[]>([]);
  const [active,setActive]=useState<HardRequest|null>(null); const [cases,setCases]=useState<HardCase[]>([]); const [offset,setOffset]=useState(0);
  const pendingSelections=useRef(0);
  async function refresh(){const [q,h]=await Promise.all([workspaceApi.queries(projectId),workspaceApi.hardRequests(projectId)]);setQueries(q);setRequests(h);}
  useEffect(()=>{let cancelled=false;Promise.all([workspaceApi.queries(projectId),workspaceApi.hardRequests(projectId),workspaceApi.messages(projectId),workspaceApi.activeComposite(projectId)]).then(([q,h,m,a])=>{if(!cancelled){setQueries(q);setRequests(h);window.dispatchEvent(new CustomEvent('workspace:messages',{detail:m}));if(a)openHard(a).catch(e=>onError(e.message));else onComposite(false);}}).catch(e=>onError(e.message));return()=>{cancelled=true;};},[projectId,onError]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{const listener=(event:Event)=>{const value=(event as CustomEvent<HardRequest>).detail;openHard(value).catch(e=>onError(e.message));};window.addEventListener('workspace:composite',listener);return()=>window.removeEventListener('workspace:composite',listener);});
  useEffect(()=>{if(!active||!(active.status==='RUNNING'||Number(active.metrics.pending_availability_recovery??0)>0))return;const events=new EventSource(apiPath(`/hard-requests/${active.id}/events`),{withCredentials:true});events.addEventListener('hard-request.updated',e=>{const value=JSON.parse((e as MessageEvent).data) as HardRequest;setActive(value);workspaceApi.cases(value.id,offset).then(page=>setCases(page.cases));});return()=>events.close();},[active?.id,active?.status,active?.metrics.pending_availability_recovery,offset]); // eslint-disable-line react-hooks/exhaustive-deps
  async function openHard(value:HardRequest,page=0){const current=value.status==='STALE_ASSEMBLY'?await workspaceApi.rebuild(value.id):value;setActive(current);onComposite(true);setOffset(page);setCases((await workspaceApi.cases(current.id,page)).cases);setDialog(null);}
  async function selectCandidate(positionId:string,candidateId:string){
    setCases(current=>current.map(item=>({...item,positions:item.positions.map(position=>position.id!==positionId?position:{...position,candidates:position.candidates.map(candidate=>({...candidate,selected:candidate.id===candidateId,user_locked:candidate.id===candidateId}))})})));
    pendingSelections.current+=1;
    try{await workspaceApi.select(active!.id,candidateId);}
    catch(e){onError(e instanceof Error?e.message:'Не удалось сохранить выбор.');}
    finally{pendingSelections.current-=1;if(pendingSelections.current===0&&active){const latest=await workspaceApi.hard(active.id);setActive(latest);setCases((await workspaceApi.cases(latest.id,offset)).cases);}}
  }
  const tier:{[key:string]:string}={A:'В наличии',B:'С подтверждённым приходом',C:'Альтернативная поставка',D:'Наличие не подтверждено'};
  return <>
    <div className="workspace-query-actions"><button className="studio-button" onClick={()=>setDialog('history')}>История</button></div>
    {showComposite&&active&&<section className="hard-workspace" data-testid="hard-request"><header><div><small>ПОДБОРКА · {active.status==='COMPLETED'?'ГОТОВО':active.status}</small><h1>{active.original_text}</h1><p>{active.completed_cases} из {active.target_cases} кейсов · категории: {active.plan.categories.join(', ')}</p></div></header>
    {active.plan.category_count_mismatch&&<p className="research-notice">Число перечисленных категорий отличается от заявленного. Использованы только явно указанные категории.</p>}
      {active.status==='COMPLETED'&&active.completed_cases===0&&<div className="research-notice"><strong>Не удалось собрать полный набор.</strong>{Object.entries((active.metrics.category_pools??{}) as Record<string,{eligible_unique?:number}>).map(([name,value])=><span key={name}> {name}: {value.eligible_unique??0} подходящих.</span>)}</div>}
      {Number(active.metrics.pending_availability_recovery??0)>0&&<p className="research-notice" role="status">Проверяем наличие ещё у подходящих вариантов. Подборка обновится автоматически после подтверждения.</p>}
      <div className="hard-cases">{cases.map(item=><article className="hard-case" key={item.id}><h2>Кейс {item.number} <small>{item.status}</small></h2>{item.positions.map(position=><section className="hard-position" key={position.id}><h3>{position.position}. {position.category}</h3><div className="hard-candidate-grid">{position.candidates.map(candidate=><article className={`hard-candidate ${candidate.selected?'selected':''}`} key={candidate.id}>
        <button className="hard-candidate-select" onClick={()=>selectCandidate(position.id,candidate.id)} aria-pressed={candidate.selected}>
          <span className="hard-candidate-image">{candidate.image?<Image src={candidate.image} alt={candidate.name} width={420} height={320} unoptimized/>:<span className="hard-image-placeholder"><ImageOff size={30}/></span>}{candidate.selected&&<i className="hard-selected-indicator"><Check size={15}/></i>}</span>
          <span className="hard-candidate-info"><strong>{candidate.name}</strong><b>{candidate.price?`${Number(candidate.price).toLocaleString('ru-RU')} ₸`:'Цена не указана'}</b><small>{tier[candidate.tier]}</small></span>
        </button>
        {candidate.source_url&&<a className="hard-source-link" href={candidate.source_url} target="_blank" rel="noopener noreferrer" aria-label={`Открыть ${candidate.name} у поставщика`}>Открыть у поставщика <ExternalLink size={13}/></a>}
      </article>)}</div></section>)}</article>)}</div>
      <div className="hard-pagination"><button className="studio-button" disabled={offset===0||active.completed_cases===0} onClick={()=>openHard(active,Math.max(0,offset-10))}>Назад</button><span>{active.completed_cases===0?'0':`${offset+1}–${Math.min(offset+10,active.completed_cases)}`}</span><button className="studio-button" disabled={active.completed_cases===0||offset+10>=active.completed_cases} onClick={()=>openHard(active,offset+10)}>Далее</button><button className="studio-button" onClick={async()=>openHard(await workspaceApi.rebuild(active.id),0)}>Пересобрать</button></div>
    </section>}
    {dialog==='history'&&<Sheet title="История запросов" onClose={()=>setDialog(null)}><div className="workspace-history"><h3>Запросы</h3>{queries.filter(q=>q.type==='STANDARD').map(q=><div key={q.id}><button onClick={async()=>{if(q.research_session_id){onResearch(await import('@/lib/research').then(m=>m.researchApi.get(q.research_session_id!)));onComposite(false);setActive(null);setDialog(null);}}}><strong>{q.query_text}</strong><small>{q.is_active?'Текущий':new Date(q.created_at).toLocaleDateString('ru-RU')}</small></button>{!q.is_active&&<button className="studio-button" onClick={async()=>{await workspaceApi.activate(projectId,q.id);await refresh();}}>Сделать основным</button>}</div>)}<h3>Подборки</h3>{requests.map(r=><button key={r.id} onClick={()=>openHard(r)}><strong>{r.original_text}</strong><small>{r.completed_cases}/{r.target_cases} · {r.status}</small></button>)}</div></Sheet>}
  </>;
}

