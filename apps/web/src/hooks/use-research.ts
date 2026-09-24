'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { researchApi, type Research } from '@/lib/research';
import { apiPath } from '@/lib/api-config';

export function useResearch(chatId: string | null) {
  const [research,setResearch]=useState<Research|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const generation=useRef(0);
  const operation=useRef(false);
  useEffect(()=>{
    const current=++generation.current;
    queueMicrotask(()=>{if(current===generation.current)setResearch(null);});
    if(chatId) researchApi.latest(chatId).then(value=>{if(current===generation.current)setResearch(value);})
      .catch(e=>setError(e.message));
  },[chatId]);
  const running=!!research && ['QUEUED','RUNNING'].includes(research.status);
  const id=research?.id;
  useEffect(()=>{
    if(!id || running)return;
    const timer=setInterval(()=>researchApi.get(id).then(value=>setResearch(previous=>
      previous?.id===id && value.revision>=previous.revision?value:previous)).catch(()=>{}),60000);
    return ()=>clearInterval(timer);
  },[id,running]);
  useEffect(()=>{
    if(!id || !running)return;
    const events=new EventSource(apiPath(`/researches/${id}/events`), {withCredentials:true});
    events.addEventListener('research.updated',event=>{
      const delta=JSON.parse((event as MessageEvent).data) as {id: string};
      if(delta.id===id) researchApi.get(id).then(value=>setResearch(previous=>
        previous?.id===id && value.revision>=previous.revision ? value : previous)).catch(()=>{});
    });
    events.addEventListener('research.completed',()=>events.close());
    return ()=>events.close();
  },[id,running]);
  const perform=useCallback(async(call:()=>Promise<Research>)=>{
    if(operation.current)return;
    operation.current=true;setBusy(true);setError('');const current=generation.current;
    try {const value=await call();if(current===generation.current)setResearch(value);}
    catch(e){setError(e instanceof Error?e.message:'Не удалось изменить исследование.');}
    finally {operation.current=false;setBusy(false);}
  },[]);
  return {research,busy,running,error,setError,perform,
    loadMore: async()=>{
      if(!research?.next_cursor || operation.current)return;
      operation.current=true;setBusy(true);const current=generation.current;
      try{const page=await researchApi.page(research.id,research.next_cursor);
        if(current===generation.current)setResearch(previous=>previous?.id===research.id?
          {...previous,...page,products:[...previous.products,...page.products.filter(p=>!previous.products.some(old=>old.id===p.id))]}:previous);
      }catch(e){setError(e instanceof Error?e.message:'Не удалось загрузить страницу.');}
      finally{operation.current=false;setBusy(false);}
    },
    send:(text:string)=>chatId && perform(()=>research?researchApi.message(research.id,text):researchApi.start(chatId,text)),
    change:(body:object)=>research && perform(()=>researchApi.change(research.id,{version:research.view.version,...body})),
  };
}
