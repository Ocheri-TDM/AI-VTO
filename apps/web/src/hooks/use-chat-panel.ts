'use client';

import {useCallback,useEffect,useRef,useState} from 'react';
import {panelPreferenceKey,transferPanelChoice,type PanelChoice} from '@/lib/workspace-panels';

/** UI state only. Request/history/collection hooks stay above the visual panel. */
export function useChatPanel(user:string,chat:string|null,available:boolean){
  const key=panelPreferenceKey(user,chat);
  const [choice,setChoice]=useState<PanelChoice|null>(null);
  const currentChoice=useRef<PanelChoice|null>(null);
  const [exiting,setExiting]=useState<string|null>(null);
  const opener=useRef<HTMLButtonElement>(null);
  const bindOpener=useCallback((node:HTMLButtonElement|null)=>{opener.current=node;},[]);
  useEffect(()=>{let saved:boolean=true;try{saved=localStorage.getItem(key)!=='false';}catch{}
    const next=currentChoice.current?.key===key?currentChoice.current:{key,open:saved};currentChoice.current=next;
    queueMicrotask(()=>setChoice(next));
  },[key]);
  useEffect(()=>{if(!exiting)return;const timer=setTimeout(()=>setExiting(null),matchMedia('(prefers-reduced-motion: reduce)').matches?0:210);return()=>clearTimeout(timer);},[exiting]);
  const expanded=available&&choice?.key===key&&choice.open;
  function store(next:PanelChoice){currentChoice.current=next;setChoice(next);try{localStorage.setItem(next.key,String(next.open));}catch{}}
  function select(open:boolean){store({key,open});}
  function transfer(target:string){store(transferPanelChoice(currentChoice.current,key,panelPreferenceKey(user,target)));}
  function show(){setExiting(null);select(true);requestAnimationFrame(()=>{
    const target=matchMedia('(max-width:1100px)').matches?document.querySelector<HTMLElement>('#workspace-chat [data-chat-collapse]'):document.getElementById('research-message');
    target?.focus({preventScroll:true});
  });}
  function hide(){setExiting(key);select(false);requestAnimationFrame(()=>opener.current?.focus({preventScroll:true}));}
  return {expanded,mounted:!!expanded||(available&&exiting===key),animate:choice?.key===key,show,hide,transfer,bindOpener};
}
