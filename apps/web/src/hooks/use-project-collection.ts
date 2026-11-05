'use client';
import {useEffect,useRef,useState} from 'react';
import {collectionApi,CollectionWriter,type ProjectCollection} from '@/lib/project-collection';
import type {ResearchProduct} from '@/lib/research';

export function useProjectCollection(project:string|null){
  const [value,setValue]=useState<ProjectCollection|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const latest=useRef<ProjectCollection|null>(null);
  // The constructor stores this callback; it runs only after an effect/event.
  // eslint-disable-next-line react-hooks/refs
  const [writer]=useState(()=>new CollectionWriter(collectionApi,(v,pending)=>{latest.current=v;setValue(v);setBusy(pending);},e=>setError((e as Error).message)));
  useEffect(()=>{let alive=true;queueMicrotask(()=>{if(!alive)return;writer.reset(project??'');setError('');if(project)void writer.reload().catch(e=>{if(alive)setError(e.message);});});
    const reload=()=>{if(project)void writer.reload().catch(()=>{});};const interval=setInterval(reload,15000);window.addEventListener('focus',reload);
    return()=>{alive=false;clearInterval(interval);window.removeEventListener('focus',reload);};},[project,writer]);
  function toggle(product:ResearchProduct,source?:string){const selected=latest.current?.selected_ids.includes(product.id);setError('');writer.enqueue({type:selected?'remove':'add',offer:product.id,source},product);}
  function remove(id:string){setError('');writer.enqueue({type:'remove',offer:id});}
  async function clear(){try{try{await writer.flush();}catch{await writer.reload();writer.discardFailure();}const current=await writer.flush();if(current.project_id!==project)return;if(window.confirm(`Убрать все выбранные товары (${current.selected_ids.length}) из подборки проекта?`)){setError('');writer.enqueue({type:'clear',version:current.version});await writer.flush();}}catch(e){setError((e as Error).message);}}
  return {value: value?.project_id===project?value:null,busy,error,setError,toggle,remove,clear,retry:()=>{setError('');if(writer.retryNeedsConfirmation)void clear();else writer.retry();},flush:()=>writer.flush(),reload:()=>writer.reload()};
}
