/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');
const modules=new Map();
const {ClientSelectionWriter}=load('src/lib/client-selection.ts',modules);
const {OfferError}=load('src/lib/commercial-offers.ts',modules);
const wait=()=>new Promise(resolve=>setImmediate(resolve));
const lock=(_scope,call)=>call();
const initial={scope:'opaque-scope',initialized:true,version:0,published_revision_id:'revision',csrf_token:'private-csrf',selected_items:[],previous_item_count:0};

test('rapid choose/remove/choose sends serialized desired states and retains the latest projection',async()=>{
  const calls=[],views=[];let server={...initial};
  const api={get:async()=>server,start:async()=>server,mutate:(_token,body)=>new Promise(resolve=>calls.push({body,resolve}))};
  const writer=new ClientSelectionWriter('private-link','revision',api,v=>views.push(v),()=>{},lock);
  await writer.load();writer.set('item',true);writer.set('item',false);writer.set('item',true);await wait();
  assert.equal(calls.length,1);assert.deepEqual(views.at(-1).state.selected_items.map(i=>i.item_id),['item']);assert.equal(views.at(-1).status,'saving');
  for(let n=0;n<3;n++){
    const call=calls[n];assert.equal(call.body.selected,[true,false,true][n]);assert.equal(call.body.version,n);assert.equal(call.body.published_revision_id,'revision');assert.equal('selected_ids' in call.body,false);
    server={...server,version:n+1,selected_items:call.body.selected?[{item_id:'item',published_revision_id:'revision'}]:[]};call.resolve(server);await wait();
  }
  assert.equal(calls.length,3);assert.equal(new Set(calls.map(c=>c.body.mutation_id)).size,3);assert.equal(views.at(-1).status,'saved');assert.equal(views.at(-1).state.version,3);
  writer.dispose();
});

test('failed save retains the intent and retries the same idempotent operation',async()=>{
  const views=[],ids=[];let failed=true;
  const api={get:async()=>initial,start:async()=>initial,mutate:async(_token,body)=>{ids.push(body.mutation_id);if(failed)throw Error('network offline');return {...initial,version:1,selected_items:[{item_id:body.item_id,published_revision_id:'revision'}]};}};
  const writer=new ClientSelectionWriter('link','revision',api,v=>views.push(v),()=>{},lock);await writer.load();writer.set('item',true);await wait();
  assert.equal(views.at(-1).status,'error');assert.deepEqual(views.at(-1).pending_ids,['item']);assert.equal(views.at(-1).state.selected_items.length,1);
  await writer.load();assert.equal(views.at(-1).status,'error');assert.equal(views.at(-1).pending_ids.length,1);
  failed=false;writer.retry();await wait();assert.equal(ids.length,2);assert.equal(ids[0],ids[1]);assert.equal(views.at(-1).status,'saved');writer.dispose();
});

test('a conflict reloads server state, keeps unrelated items and reapplies only latest pending line intent',async()=>{
  let server={...initial},release;const views=[],calls=[];
  const api={get:async()=>server,start:async()=>server,mutate:async(_token,body)=>{calls.push(body);if(calls.length===1){await new Promise(resolve=>release=resolve);throw new OfferError(409,'SELECTION_CONFLICT','changed');}server={...server,version:server.version+1,selected_items:[...server.selected_items,{item_id:body.item_id,published_revision_id:'revision'}]};return server;}};
  const writer=new ClientSelectionWriter('link','revision',api,v=>views.push(v),()=>{},lock);await writer.load();writer.set('own',true);await wait();writer.set('own',false);writer.set('own',true);
  server={...initial,version:1,selected_items:[{item_id:'other-tab',published_revision_id:'revision'}]};release();await wait();await wait();
  assert.deepEqual(views.at(-1).state.selected_items.map(i=>i.item_id),['other-tab','own']);assert.equal(calls.length,2);assert.equal(calls[1].selected,true);assert.equal(calls[1].version,1);writer.dispose();
});

test('outdated reads cannot overwrite a newer confirmed state and revision conflicts stay blocked',async()=>{
  const views=[];let get=initial;
  const api={get:async()=>get,start:async()=>initial,mutate:async()=>({...initial,version:2,selected_items:[{item_id:'item',published_revision_id:'revision'}]})};
  const writer=new ClientSelectionWriter('link','revision',api,v=>views.push(v),()=>{},lock);await writer.load();writer.set('item',true);await wait();await writer.load();
  assert.equal(views.at(-1).state.version,2);get={...initial,published_revision_id:'another-revision'};await writer.load();assert.equal(views.at(-1).blocked,true);assert.equal(views.at(-1).status,'error');writer.dispose();
});

test('retry after a cleared visitor cookie initializes a new session and keeps only pending intent',async()=>{
  const views=[];let server={...initial,selected_items:[{item_id:'old-session-only',published_revision_id:'revision'}]},calls=0,starts=0;
  const api={get:async()=>server,start:async()=>{starts++;server={...initial,csrf_token:'new-private-session'};return server;},mutate:async(_token,body)=>{
    if(++calls===1){server={...initial,initialized:false,csrf_token:null};throw new OfferError(401,'VISITOR_SESSION_REQUIRED','Restore visitor session');}
    assert.equal(body.version,0);return {...server,version:1,selected_items:[{item_id:body.item_id,published_revision_id:'revision'}]};
  }};
  const writer=new ClientSelectionWriter('link','revision',api,v=>views.push(v),()=>{},lock);await writer.load();writer.set('pending',true);await wait();
  assert.equal(views.at(-1).status,'error');writer.retry();await wait();await wait();
  assert.equal(starts,1);assert.equal(views.at(-1).status,'saved');assert.deepEqual(views.at(-1).state.selected_items.map(i=>i.item_id),['pending']);writer.dispose();
});

test('fresh session initialization is serialized and rechecks the shared HttpOnly session after locking',async()=>{
  const views=[];let starts=0,gets=0,locked=0;
  const api={get:async()=>{gets++;return gets===1?{...initial,initialized:false,csrf_token:null}:initial;},start:async()=>{starts++;return initial;},mutate:async(_t,body)=>({...initial,version:1,selected_items:[{item_id:body.item_id,published_revision_id:'revision'}]})};
  const writer=new ClientSelectionWriter('link','revision',api,v=>views.push(v),()=>{},async(scope,call)=>{assert.equal(scope,'opaque-scope');locked++;return call();});
  await writer.load();writer.set('item',true);await wait();assert.equal(locked,1);assert.equal(starts,0);assert.equal(views.at(-1).status,'saved');writer.dispose();
});

test('notification snapshots deduplicate groups and preserve newer unread versions',()=>{
  const {mergeNotificationPage,notificationText}=load('src/lib/client-notifications.ts');
  const old={cursor:3,unread_count:1,items:[{id:'one',sequence:3,selected_count:3,unread:true,visitor_label:'Посетитель 1'}],next_before:null};
  assert.equal(mergeNotificationPage(old,{...old,cursor:2}),old);
  const current=mergeNotificationPage(old,{...old,cursor:4,items:[{...old.items[0],sequence:4,selected_count:0}]},true);
  assert.equal(current.items.length,1);assert.equal(notificationText(current.items[0]),'Клиент снял все отметки');assert.equal(current.unread_count,1);
});

test('owner interest distinguishes visitors, positions and marks without commercial totals',()=>{
  const {OfferClientInterest}=load('src/components/offers/client-feedback.tsx');
  const html=renderToStaticMarkup(React.createElement(OfferClientInterest,{identifier:'document',value:{visitor_count:2,item_count:1,mark_count:2,historical_mark_count:1}}));
  assert.match(html,/Посетителей с выбором: 2/);assert.match(html,/Позиций: 1/);assert.match(html,/Отметок: 2/);assert.match(html,/Посмотреть выбор/);assert.doesNotMatch(html,/Итого|₸|Общая стоимость/);
});
