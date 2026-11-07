'use client';
import {useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import type {ConversationMessage} from '@/lib/conversation';
import {ChatPanelToggle} from './chat-panel-toggle';

export function ConversationComposer({value,onChange,onSubmit,busy,started,disabled=false,focusOnMount=true}:{value:string;onChange:(value:string)=>void;onSubmit:()=>void;busy:boolean;started:boolean;disabled?:boolean;focusOnMount?:boolean}){
  return <form className="research-composer" onSubmit={e=>{e.preventDefault();onSubmit();}}>
    <label htmlFor={started?'research-message':'research-query'}>{started?'Сообщение':'Что нужно подобрать?'}</label>
    <textarea id={started?'research-message':'research-query'} value={value} onChange={e=>onChange(e.target.value)} maxLength={2000} autoFocus={started&&focusOnMount}
      placeholder={started?'Уточните условия или начните новый поиск':'Синие бутылки, 300 шт.'} rows={3}
      onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();onSubmit();}}}/>
    <button className="studio-button primary" disabled={busy||disabled||!value.trim()}>{busy?'Отправляю…':'Отправить'}</button>
  </form>;
}

export function Conversation({messages,hasOlder,loading,error,onOlder,onRetry,composer,busy,onCollapse,restoring=false,showHeading=true}:{messages:ConversationMessage[];hasOlder:boolean;loading:boolean;error:string;onOlder:()=>Promise<void>;onRetry:()=>void;composer:ReactNode;busy:boolean;onCollapse?:()=>void;restoring?:boolean;showHeading?:boolean}){
  const scroller=useRef<HTMLDivElement>(null),bottom=useRef(true),initial=useRef(true),anchor=useRef<{height:number;top:number;first:string|undefined}|null>(null);
  const [unseen,setUnseen]=useState(false);const last=messages.at(-1)?.id;
  const first=messages[0]?.id;
  useLayoutEffect(()=>{const node=scroller.current;if(!node)return;if(anchor.current){if(first!==anchor.current.first){node.scrollTop=anchor.current.top+node.scrollHeight-anchor.current.height;anchor.current=null;}}else if(initial.current||bottom.current){node.scrollTop=node.scrollHeight;if(messages.length)initial.current=false;}else if(last)queueMicrotask(()=>setUnseen(true));},[last,first,messages.length]);
  async function older(){const node=scroller.current;if(node)anchor.current={height:node.scrollHeight,top:node.scrollTop,first};await onOlder();requestAnimationFrame(()=>{anchor.current=null;});}
  return <div className="research-chat-content">{showHeading&&<div className="conversation-heading"><h2>Чат</h2>{onCollapse&&<ChatPanelToggle expanded onClick={onCollapse}/>}</div>}<div className="research-messages native-scroll" tabIndex={0} aria-label="История переписки" ref={scroller} onScroll={()=>{const node=scroller.current;if(node){bottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<70;if(bottom.current)setUnseen(false);}}}>
    {restoring&&<p role="status">Восстанавливаю переписку…</p>}
    {hasOlder&&<button className="studio-button conversation-older" disabled={loading} onClick={()=>void older()}>{loading?'Загружаю…':'Ранние сообщения'}</button>}
    {messages.filter(m=>m.role!=='system'&&m.origin!=='CATALOG_STATUS').map(m=><div key={m.id} data-message-id={m.id} className={`research-message ${m.role}`}><small>{m.role==='user'?'Вы':'Souvenir Studio'}</small><p>{m.content}</p>{m.role==='user'&&m.status==='ACCEPTED'&&<small role="status">Сообщение сохранено · обрабатывается</small>}{m.status==='LOCAL_PENDING'&&<small role="status">Отправляю · сохранение ещё не подтверждено</small>}{m.status==='ACKNOWLEDGED'&&<small role="status">Сообщение сохранено · загружаю ответ</small>}{m.status==='LOCAL_ERROR'&&<small role="status">Отправка не подтверждена. Текст остался в поле для повтора.</small>}</div>)}
    {busy&&<p role="status">Отправляю сообщение…</p>}
  </div>{error&&<p role="alert">Не удалось загрузить переписку. <button onClick={onRetry}>Повторить</button></p>}
    {unseen&&<button className="studio-button conversation-new" onClick={()=>{if(scroller.current)scroller.current.scrollTop=scroller.current.scrollHeight;bottom.current=true;setUnseen(false);}}>К новым сообщениям</button>}{composer}</div>;
}
