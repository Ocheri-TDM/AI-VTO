'use client';
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {useAuthSession} from '../auth-gate';
import {AppNavbar} from '../app-navbar';
import {Sheet} from '../common/sheet';
import {copyOfferLink,offerApi,offerExpiry,OfferError,type OfferSummary} from '@/lib/commercial-offers';
import {notificationApi,type ClientInterest} from '@/lib/client-notifications';
import {useClientNotifications} from '../notifications/notification-provider';
import {OfferClientInterest} from './client-feedback';

const states:Record<string,string>={SAVED:'Черновик сохранён',READY:'Файлы готовы',QUEUED:'В очереди',PREPARING_ASSETS:'Подготовка',RENDERING:'Подготовка',VALIDATING:'Проверка',FAILED:'Ошибка подготовки',ACTIVE:'Опубликовано',EXPIRED:'Ссылка истекла',UNPUBLISHED:'Не опубликовано',PENDING:'Публикация готовится'};
export function OfferDocuments({archive=false}:{archive?:boolean}){
  const {user,logout}=useAuthSession();
  const notifications=useClientNotifications(),signal=notifications?.cursor??0;
  const [interest,setInterest]=useState<Record<string,ClientInterest>>({});
  const [items,setItems]=useState<OfferSummary[]>([]),[cursor,setCursor]=useState<string|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [removing,setRemoving]=useState<OfferSummary|null>(null),[deleting,setDeleting]=useState(false),[deleteError,setDeleteError]=useState('');
  const interestIds=items.map(i=>i.id).join(',');
  useEffect(()=>{if(archive||!interestIds)return;let live=true;notificationApi.interest(interestIds.split(',')).then(value=>{if(live){setInterest(value.offers);if(value.unavailable_ids.length)setItems(old=>old.filter(i=>!value.unavailable_ids.includes(i.id)));}}).catch(async e=>{if(!live)return;if(e instanceof OfferError&&e.status===404){try{const page=await offerApi.all();if(live){setItems(page.items);setCursor(page.next_cursor);}}catch(failure){if(live)setError((failure as Error).message);}}else setError((e as Error).message);});return()=>{live=false;};},[interestIds,archive,signal]);
  useEffect(()=>{let live=true;offerApi.all(undefined,archive).then(page=>{if(live){setItems(page.items);setCursor(page.next_cursor);}}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setBusy(false);});return()=>{live=false;};},[archive]);
  async function more(){if(!cursor||busy)return;setBusy(true);try{const page=await offerApi.all(cursor,archive);setItems(old=>[...old,...page.items.filter(i=>!old.some(p=>p.id===i.id))]);setCursor(page.next_cursor);setError('');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function copy(id:string){setNotice('');setError('');try{await copyOfferLink(await offerApi.link(id));setNotice('Ссылка скопирована');}catch(e){setError((e as Error).message);}}
  async function remove(){if(!removing||deleting)return;setDeleting(true);setDeleteError('');try{await offerApi.delete(removing.id,removing.version);setItems(old=>old.filter(i=>i.id!==removing.id));setRemoving(null);setNotice('Предложение удалено. Чат и выбранные товары сохранены.');}catch(e){setDeleteError(e instanceof OfferError?e.message:'Не удалось удалить предложение. Проверьте соединение и повторите.');}finally{setDeleting(false);}}
  return <div className="offer-library"><AppNavbar name={user.name} logout={logout} active="offers"/>
    <main className="offer-library-main"><div className="offer-library-heading"><h1>{archive?'Архив дизайнерских документов':'Предложения'}</h1><Link href={archive?'/offers':'/offers/archive'}>{archive?'К коммерческим предложениям':'Архив дизайнерских документов'}</Link></div>
      <p>Все ваши {archive?'архивные документы':'коммерческие предложения'} независимо от открытого чата.</p>
      {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      {!busy&&!items.length&&!error&&<div className="research-empty"><h2>{archive?'Архив пуст':'Предложений пока нет'}</h2><p>Подберите товары в чате и создайте предложение из выбранных.</p><Link className="studio-button primary" href="/">К подбору товаров</Link></div>}
      <div className="offer-library-list">{items.map(item=><article key={item.id} data-document-id={item.id} className="offer-library-row"><div><h2>{item.title}</h2><p>{item.project_name} · {item.item_count} позиций</p><small>Изменено {new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Almaty',dateStyle:'medium',timeStyle:'short'}).format(new Date(item.updated_at))}</small></div><div><p>{!archive&&!item.is_primary?'Предыдущее предложение · ':''}{states[item.draft_state]??item.draft_state}</p>{!archive&&<p>{states[item.publication_state]??item.publication_state}</p>}{!archive&&item.expires_at&&<small>Доступ до {offerExpiry(item.expires_at)}</small>}{!archive&&interest[item.id]&&(interest[item.id].mark_count>0||interest[item.id].historical_mark_count>0)&&<OfferClientInterest identifier={item.id} value={interest[item.id]}/>}</div><div className="offer-library-actions"><Link className="studio-button primary" href={archive?`/offers/archive/${item.id}`:`/offers/${item.id}/edit`}>Открыть</Link>{item.can_copy&&!archive&&<button className="studio-button" onClick={()=>void copy(item.id)}>Копировать ссылку</button>}{!archive&&item.pdf_url&&<a className="studio-button" href={item.pdf_url}>PDF</a>}{!archive&&item.xlsx_url&&<a className="studio-button" href={item.xlsx_url}>Excel</a>}{!archive&&<button className="offer-delete-action" onClick={()=>{setDeleteError('');setRemoving(item);}}>Удалить</button>}</div></article>)}</div>
      {busy&&<p role="status">Загружаю…</p>}{cursor&&<button disabled={busy} className="studio-button" onClick={()=>void more()}>Показать ещё</button>}
    </main>{removing&&<Sheet title="Удалить предложение?" className="offer-delete-dialog" onClose={()=>{if(!deleting)setRemoving(null);}}><strong>{removing.title}</strong><p>Оно исчезнет из списка. Все его клиентские ссылки перестанут работать. Чат и выбранные товары сохранятся.</p><p>Уже скачанные клиентом копии останутся на его устройстве.</p>{deleteError&&<p role="alert">{deleteError}</p>}<div className="offer-delete-controls"><button className="studio-button" disabled={deleting} onClick={()=>setRemoving(null)}>Отмена</button><button className="studio-button" disabled={deleting} onClick={()=>void remove()}>{deleting?'Удаляю…':'Удалить предложение'}</button></div></Sheet>}</div>;
}
