'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {ClientSelectionWriter,clientSelectionApi,type SelectionView} from '@/lib/client-selection';

const empty:SelectionView={state:null,pending_ids:[],status:'loading',error:'',blocked:false};
export function useClientSelection(token:string,revision:string|null){
  const key=token+':'+revision;
  const [value,setValue]=useState<{key:string;view:SelectionView}|null>(null);
  const writer=useRef<ClientSelectionWriter|null>(null);
  const channel=useRef<BroadcastChannel|null>(null);
  useEffect(()=>{
    if(!revision)return;
    const instance=new ClientSelectionWriter(token,revision,clientSelectionApi,view=>setValue({key,view}),state=>{
      if(channel.current)channel.current.postMessage({version:state.version});
      else try{localStorage.setItem('offer-choice-updated:'+state.scope,crypto.randomUUID());}catch{}
    });
    writer.current=instance;void instance.load();
    const focus=()=>{if(document.visibilityState==='visible')void instance.load();};
    window.addEventListener('focus',focus);document.addEventListener('visibilitychange',focus);
    return()=>{instance.dispose();if(writer.current===instance)writer.current=null;window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',focus);};
  },[token,revision,key]);
  const view=value?.key===key?value.view:empty;
  const scope=view.state?.scope;
  useEffect(()=>{
    if(!scope)return;
    const bus=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('offer-choice:'+scope);
    channel.current=bus;
    const reload=()=>void writer.current?.load();
    if(bus)bus.onmessage=reload;
    const storage=(event:StorageEvent)=>{if(event.key==='offer-choice-updated:'+scope)reload();};
    window.addEventListener('storage',storage);
    return()=>{bus?.close();if(channel.current===bus)channel.current=null;window.removeEventListener('storage',storage);};
  },[scope]);
  return {...view,set:useCallback((id:string,selected:boolean)=>writer.current?.set(id,selected),[]),retry:useCallback(()=>writer.current?.retry(),[])};
}
