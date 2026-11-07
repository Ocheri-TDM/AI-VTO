'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {conversationApi,mergeMessages,type ConversationMessage} from '@/lib/conversation';

export function useConversation(chat:string|null){
  const [value,setValue]=useState<{chat:string;messages:ConversationMessage[];before:string|null;after:string|null}|null>(null);
  const [error,setError]=useState(''),[loading,setLoading]=useState(false);
  const current=useRef(value);
  const activeChat=useRef(chat);
  useEffect(()=>{current.current=value;},[value]);
  const generation=useRef(0),pending=useRef<Promise<void>|null>(null);
  const refresh=useCallback(async()=>{
    const chat=activeChat.current;if(!chat)return;if(pending.current)return pending.current;
    const epoch=generation.current;
    const base=current.current;const after=base?.chat===chat?base.after:null;
    const promise=conversationApi.page(chat,after?{after}:{}).then(page=>{if(epoch!==generation.current)return;setError('');setValue(old=>({chat,messages:mergeMessages(old?.chat===chat?old.messages:[],page.messages),before:old?.chat===chat?old.before:page.next_before,after:page.next_after}));}).catch(e=>{if(epoch===generation.current)setError(e.message);}).finally(()=>{if(pending.current===promise)pending.current=null;});
    pending.current=promise;return promise;
  },[]);
  const invalidate=useCallback(()=>{generation.current++;},[]);
  useEffect(()=>{invalidate();activeChat.current=chat;current.current=null;pending.current=null;queueMicrotask(()=>{setError('');setValue(null);});void refresh();const timer=setInterval(()=>void refresh(),10000);const focus=()=>void refresh();window.addEventListener('focus',focus);return()=>{invalidate();clearInterval(timer);window.removeEventListener('focus',focus);};},[chat,refresh,invalidate]);
  async function older(){const base=current.current;if(!chat||base?.chat!==chat||!base.before||loading)return;const epoch=generation.current;setLoading(true);try{const page=await conversationApi.page(chat,{before:base.before});if(epoch===generation.current){setValue(old=>old?.chat===chat?{...old,messages:mergeMessages(old.messages,page.messages),before:page.next_before}:old);setError('');}}catch(e){if(epoch===generation.current)setError((e as Error).message);}finally{if(epoch===generation.current)setLoading(false);}}
  return {messages:value?.chat===chat?value.messages:[],ready:!!(value?.chat===chat),hasOlder:!!(value?.chat===chat&&value.before),loading,error,older,refresh};
}
