'use client';
import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {useRouter} from 'next/navigation';
import {mergeNotificationPage,notificationApi,type NotificationPage} from '@/lib/client-notifications';
import {OfferError} from '@/lib/commercial-offers';

export type ConnectionState='connecting'|'live'|'polling'|'offline'|'idle';
interface NotificationContextValue {page:NotificationPage|null;connection:ConnectionState;error:string;cursor:number;refresh:()=>Promise<void>;more:()=>Promise<void>;read:(id:string,sequence:number)=>Promise<void>;navigate:(id:string)=>Promise<boolean>;guard:(id:string,dirty:boolean)=>()=>void}
const NotificationContext=createContext<NotificationContextValue|null>(null);
export function useClientNotifications(){return useContext(NotificationContext);}

export function NotificationProvider({userId,onSessionExpired,children}:{userId:string;onSessionExpired:()=>void;children:ReactNode}){
  const router=useRouter();
  const [page,setPage]=useState<NotificationPage|null>(null),[connection,setConnection]=useState<ConnectionState>('connecting'),[error,setError]=useState('');
  const active=useRef(true),epoch=useRef(0),cursor=useRef(0),pending=useRef<Promise<void>|null>(null),again=useRef(false);
  const blocker=useRef<{id:string;dirty:boolean}|null>(null);
  const invalidate=useCallback(()=>{epoch.current++;},[]);
  const accept=useCallback((value:NotificationPage,append=false)=>{if(!active.current)return;cursor.current=Math.max(cursor.current,value.cursor);setPage(old=>mergeNotificationPage(old,value,append));setError('');},[]);
  const refresh=useCallback(async function refreshState(){
    if(pending.current){again.current=true;return pending.current;}
    const generation=epoch.current;
    const task=notificationApi.get().then(value=>{if(active.current&&generation===epoch.current){if(value.cursor<cursor.current){again.current=true;return;}accept(value);}}).catch(e=>{if(active.current&&generation===epoch.current){setError((e as Error).message);if(e instanceof OfferError&&e.status===401){setPage(null);onSessionExpired();}}}).finally(()=>{
      if(pending.current===task)pending.current=null;
      if(active.current&&again.current){again.current=false;void refreshState();}
    });
    pending.current=task;return task;
  },[accept,onSessionExpired]);
  useEffect(()=>{
    active.current=true;let stream:EventSource|null=null,retry:ReturnType<typeof setTimeout>|null=null,backoff=1000,mode:ConnectionState='connecting',heartbeat=Date.now();
    const status=(value:ConnectionState)=>{mode=value;if(active.current)setConnection(value);};
    const close=()=>{stream?.close();stream=null;if(retry){clearTimeout(retry);retry=null;}};
    const schedule=(delay:number)=>{if(retry||!active.current)return;retry=setTimeout(()=>{retry=null;connect();},delay);};
    function connect(){
      if(!active.current||document.visibilityState!=='visible')return;
      if(!navigator.onLine){status('offline');return;}
      if(stream)return;status('connecting');heartbeat=Date.now();
      const source=new EventSource('/api/notifications/events?cursor='+cursor.current,{withCredentials:true});stream=source;
      const current=()=>active.current&&stream===source;
      const healthy=()=>{if(current()){heartbeat=Date.now();backoff=1000;status('live');}};
      source.addEventListener('stream.ready',healthy);source.addEventListener('stream.heartbeat',healthy);
      const changed=(event:Event)=>{if(!current())return;healthy();const message=event as MessageEvent;const sequence=Number(message.lastEventId);if(Number.isSafeInteger(sequence))cursor.current=Math.max(cursor.current,sequence);void refresh();};
      source.addEventListener('account.changed',changed);
      source.addEventListener('state.sync',event=>{if(!current())return;const value=Number((event as MessageEvent).lastEventId);if(Number.isSafeInteger(value)&&value<cursor.current){epoch.current++;cursor.current=value;setPage(null);}changed(event);});
      source.addEventListener('stream.renew',()=>{if(!current())return;close();status('connecting');schedule(300);});
      const failed=()=>{if(!current())return;close();status(navigator.onLine?'polling':'offline');schedule(backoff);backoff=Math.min(30000,backoff*2);};
      source.addEventListener('stream.unavailable',failed);source.onerror=failed;
      source.addEventListener('auth.required',()=>{if(!current())return;close();setPage(null);onSessionExpired();});
    }
    const visibility=()=>{if(document.visibilityState==='visible'){void refresh();connect();}else{close();status('idle');}};
    const offline=()=>{close();status('offline');};const online=()=>{void refresh();connect();};
    const fallback=setInterval(()=>{
      if(!active.current||document.visibilityState!=='visible'||!navigator.onLine)return;
      if(mode==='live'&&Date.now()-heartbeat>25000){close();status('polling');}
      if(mode!=='live'){void refresh();connect();}
    },15000);
    void refresh();connect();
    document.addEventListener('visibilitychange',visibility);window.addEventListener('offline',offline);window.addEventListener('online',online);
    return()=>{active.current=false;invalidate();again.current=false;close();clearInterval(fallback);document.removeEventListener('visibilitychange',visibility);window.removeEventListener('offline',offline);window.removeEventListener('online',online);blocker.current=null;};
  },[userId,onSessionExpired,refresh,invalidate]);
  const more=useCallback(async()=>{if(!page?.next_before)return;try{accept(await notificationApi.get(page.next_before),true);}catch(e){setError((e as Error).message);}},[page,accept]);
  const read=useCallback(async(id:string,sequence:number)=>{try{accept(await notificationApi.read(id,sequence));}catch(e){setError((e as Error).message);}},[accept]);
  const guard=useCallback((id:string,dirty:boolean)=>{const value={id,dirty};blocker.current=value;return()=>{if(blocker.current===value)blocker.current=null;};},[]);
  const navigate=useCallback(async(id:string)=>{
    if(blocker.current?.dirty&&blocker.current.id!==id&&!window.confirm('В открытом предложении есть несохранённые изменения. Перейти к другому предложению без их сохранения?'))return false;
    router.push('/offers/'+id+'/edit#client-selection');return true;
  },[router]);
  const value=useMemo(()=>({page,connection,error,cursor:page?.cursor??0,refresh,more,read,navigate,guard}),[page,connection,error,refresh,more,read,navigate,guard]);
  return <NotificationContext.Provider value={value}>{children}</NotificationContext.Provider>;
}
