'use client';
import {useState} from 'react';
import {Bell} from 'lucide-react';
import {Sheet} from '@/components/common/sheet';
import {notificationText} from '@/lib/client-notifications';
import {useClientNotifications} from './notification-provider';

const connectionText={connecting:'Подключаю обновления…',live:'Обновляется в реальном времени',polling:'Обновления с задержкой · периодическая проверка',offline:'Нет соединения с сетью',idle:'Обновления при возврате во вкладку'};
export function NotificationBell(){
  const notifications=useClientNotifications();const [open,setOpen]=useState(false);
  if(!notifications)return null;
  const {page,connection,error}=notifications;
  return <><button className="notification-bell" type="button" aria-label={'Уведомления'+(page?`: ${page.unread_count} непрочитанных`:' · загрузка')} title="Уведомления" onClick={()=>{setOpen(true);void notifications.refresh();}}>
    <Bell size={20} aria-hidden="true"/>{!!page?.unread_count&&<span className="notification-count" aria-hidden="true">{page.unread_count>99?'99+':page.unread_count}</span>}
  </button>{open&&<Sheet className="notification-sheet" title="Уведомления" onClose={()=>setOpen(false)}>
    <p className="notification-connection" role="status" data-connection={connection}>{connectionText[connection]}</p>
    {error&&<p role="alert">{error} <button onClick={()=>void notifications.refresh()}>Повторить</button></p>}
    {!page?<p role="status">Загружаю уведомления…</p>:!page.items.length?<p>Пока нет уведомлений о выборе клиента.</p>:<div className="notification-list">{page.items.map(value=><article key={value.id} className={value.unread?'is-unread':''} data-notification-id={value.id} data-notification-sequence={value.sequence}>
      <strong>{value.title}</strong><p>{notificationText(value)}</p><time dateTime={value.updated_at}>{new Date(value.updated_at).toLocaleString('ru-RU')}</time>
      <div>{value.available?<button className="studio-button" onClick={async()=>{if(await notifications.navigate(value.document_id)){await notifications.read(value.id,value.sequence);setOpen(false);}}}>Посмотреть выбор</button>:<span>Предложение недоступно</span>}
      {value.unread&&<button className="studio-button" onClick={()=>void notifications.read(value.id,value.sequence)}>Прочитано</button>}</div>
    </article>)}</div>}
    {page?.next_before&&<button className="studio-button" onClick={()=>void notifications.more()}>Ранние уведомления</button>}
  </Sheet>}</>;
}
