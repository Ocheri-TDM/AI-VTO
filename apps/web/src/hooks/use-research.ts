'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { researchApi, type Research } from '@/lib/research';
import { apiPath } from '@/lib/api-config';
import { draftResearch, ResearchViewWriter, type ViewMutation } from '@/lib/research-view-writer';

export function useResearch(chatId:string|null) {
  const [research,setResearch]=useState<Research|null>(null);
  const [readBusy,setReadBusy]=useState(false);
  const [writeBusy,setWriteBusy]=useState(false);
  const [error,setError]=useState('');
  const generation=useRef(0),operation=useRef(false);
  const latest=useRef<Research|null>(null);
  const commit=useCallback((value:Research|null)=>{latest.current=value;setResearch(value);},[]);
  // The constructor stores this callback; flush invokes it after a network write, never during render.
  // eslint-disable-next-line react-hooks/refs
  const [writer]=useState(()=>new ResearchViewWriter(researchApi.change,researchApi.get,(value,pending,sent)=>{
      const previous=latest.current;
      if(!previous||previous.id!==value.id)return;
      if(pending?.filters)return; // Never publish an older filter's grid under the newest draft.
      let next=value;
      if(sent.selected_ids&&Object.keys(sent).length===1){
        next={...value,products:previous.products,groups:previous.groups,next_cursor:previous.next_cursor};
      }
      commit(draftResearch(next,pending??{},previous));
  },setWriteBusy,e=>setError(e instanceof Error?e.message:'Не удалось сохранить подборку.')));
  const external=useCallback((value:Research|null)=>{generation.current+=1;writer.reset(value);commit(value);},[writer,commit]);
  useEffect(()=>{
    const current=++generation.current;writer.reset(null);
    queueMicrotask(()=>{if(current===generation.current)commit(null);});
    if(chatId)researchApi.latest(chatId).then(value=>{if(current===generation.current){writer.reset(value);commit(value);}})
      .catch(e=>{if(current===generation.current)setError(e.message);});
  },[chatId,writer,commit]);
  const running=!!research&&['QUEUED','RUNNING'].includes(research.status);
  const id=research?.id;
  const refresh=useCallback((identifier:string)=>{
    const current=generation.current;
    researchApi.get(identifier).then(value=>{
      if(current!==generation.current||writer.saving)return;
      const previous=latest.current;
      if(previous?.id!==identifier||value.revision<previous.revision)return;
      writer.reset(value);commit(value);
    }).catch(()=>{});
  },[writer,commit]);
  useEffect(()=>{if(!id||running)return;const timer=setInterval(()=>refresh(id),60000);return()=>clearInterval(timer);},[id,running,refresh]);
  useEffect(()=>{
    if(!id||!running)return;
    const events=new EventSource(apiPath(`/researches/${id}/events`),{withCredentials:true});
    events.addEventListener('research.updated',e=>{if(JSON.parse((e as MessageEvent).data).id===id)refresh(id);});
    events.addEventListener('research.completed',()=>events.close());return()=>events.close();
  },[id,running,refresh]);
  const perform=useCallback(async(call:()=>Promise<Research>)=>{
    if(operation.current||writer.saving)return;
    operation.current=true;setReadBusy(true);setError('');const current=generation.current;
    try{const value=await call();if(current===generation.current){writer.reset(value);commit(value);}}
    catch(e){if(current===generation.current)setError(e instanceof Error?e.message:'Не удалось изменить исследование.');}
    finally{operation.current=false;setReadBusy(false);}
  },[writer,commit]);
  function change(body:ViewMutation){
    setError('');
    const current=latest.current;
    if(!current)return;
    commit(draftResearch(current,body));writer.enqueue(body);
  }
  function selectProduct(productId:string){
    setError('');
      const current=latest.current;
      if(!current)return;
      const ids=new Set(current.view.selected_ids);
      if(ids.has(productId))ids.delete(productId);else ids.add(productId);
      change({selected_ids:[...ids]});
  }
  return {research,busy:readBusy||writeBusy,running,error,setError,perform,setResearch:external,
    openResearch:(identifier:string)=>perform(()=>researchApi.get(identifier)),
    loadMore:async()=>{
      if(!research?.next_cursor||operation.current||writer.saving)return;
      operation.current=true;setReadBusy(true);const current=generation.current;
      try{
        const page=await researchApi.page(research.id,research.next_cursor);
        if(current===generation.current&&!writer.saving){
          const previous=latest.current;
          if(previous?.id!==research.id||previous.view.version!==research.view.version)return;
          const groups=[...previous.groups,...((page as Partial<Research>).groups??[])].reduce<Research['groups']>((all,g)=>{
            const old=all.find(v=>v.id===g.id);if(old)old.offer_ids=[...new Set([...old.offer_ids,...g.offer_ids])];else all.push({...g,offer_ids:[...g.offer_ids]});return all;
          },[]);
          const value={...previous,...page,groups,products:[...previous.products,...page.products.filter(p=>!previous.products.some(old=>old.id===p.id))]};
          writer.reset(value);commit(value);
        }
      }catch(e){
        if(current===generation.current){setError(e instanceof Error?e.message:'Не удалось загрузить страницу.');if((e as {status?:number}).status===409)refresh(research.id);}
      }finally{operation.current=false;setReadBusy(false);}
    },
    send:(text:string)=>chatId&&perform(()=>research?researchApi.message(research.id,text):researchApi.start(chatId,text)),
    change,selectProduct,
  };
}
