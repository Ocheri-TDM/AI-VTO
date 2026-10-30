'use client';
import { useRef, useState, useEffect } from 'react';
import { workspaceApi, type ProjectQuery } from '@/lib/project-queries';
import type { Research } from '@/lib/research';
import { Sheet } from '../common/sheet';

export function WorkspaceQueries({projectId,onResearch,onError}:{projectId:string;onResearch:(value:Research)=>void;onError:(value:string)=>void}) {
  const [open,setOpen]=useState(false);
  const [queries,setQueries]=useState<ProjectQuery[]>([]);
  const generation=useRef(0);
  useEffect(()=>{generation.current+=1;},[projectId]);
  async function history(){const current=generation.current;try{const values=await workspaceApi.queries(projectId);if(current===generation.current){setQueries(values);setOpen(true);}}catch(e){onError((e as Error).message);}}
  async function activate(query:ProjectQuery){const current=generation.current;try{const result=await workspaceApi.activate(projectId,query.id);if(current===generation.current){onResearch(result.research);setOpen(false);}}catch(e){onError((e as Error).message);}}
  return <><div className="workspace-query-actions"><button className="studio-button" onClick={history}>История</button></div>
    {open&&<Sheet title="История запросов" onClose={()=>setOpen(false)}><div className="workspace-history">{queries.map(query=><button key={query.id} onClick={()=>activate(query)}>
      <strong>{query.query_text}</strong><small>{query.type==='HARD'?'Архив сборки · повторить как товарный поиск':query.is_active?'Текущий':new Date(query.created_at).toLocaleDateString('ru-RU')}</small>
    </button>)}</div></Sheet>}</>;
}
