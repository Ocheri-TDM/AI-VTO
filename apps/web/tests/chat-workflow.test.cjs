/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');

test('conversation merge deduplicates server IDs while retaining repeated text as separate messages',()=>{
  const {mergeMessages}=load('src/lib/conversation.ts');
  const a={id:'1',content:'Ещё ручки',created_at:'2026-11-07T10:00:00Z',role:'user',operation_id:'a',status:'ACCEPTED'};
  const b={...a,id:'2',created_at:'2026-11-07T10:00:01Z',operation_id:'b'};
  const answer={...a,id:'3',created_at:'2026-11-07T10:00:02Z',role:'assistant',status:'COMPLETED'};
  const result=mergeMessages([a,b],[a,answer]);
  assert.deepEqual(result.map(m=>m.id),['1','2','3']);
  assert.equal(result[0].status,'COMPLETED');assert.equal(result[1].status,'ACCEPTED');
});
test('history keeps user text resembling a former technical hint and stable message IDs',()=>{
  const {Conversation}=load('src/components/research/conversation.tsx');
  const html=renderToStaticMarkup(React.createElement(Conversation,{messages:[{id:'message-id',role:'user',content:'Каталог обновляется автоматически',origin:'LEGACY_CONVERSATION',status:'COMPLETED'}],hasOlder:true,loading:false,error:'',onOlder:async()=>{},onRetry(){},composer:null,busy:false}));
  assert.match(html,/Каталог обновляется автоматически/);assert.match(html,/data-message-id="message-id"/);assert.match(html,/Ранние сообщения/);
  assert.doesNotMatch(html,/Более премиальные|Выбери 5 лучших/);
});
test('shared composer uses one controlled input and shows sending instead of saved',()=>{
  const {ConversationComposer}=load('src/components/research/conversation.tsx');
  const html=renderToStaticMarkup(React.createElement(ConversationComposer,{value:'Исходный текст',onChange(){},onSubmit(){},busy:true,started:true}));
  assert.equal((html.match(/<textarea/g)||[]).length,1);assert.match(html,/Исходный текст/);assert.match(html,/Отправляю/);assert.doesNotMatch(html,/Сообщение сохранено/);
});
test('owner inventory is read-only and preserves zero, unknown, dates and separate incoming',()=>{
  const {OwnerStockNotice}=load('src/components/offers/owner-stock.tsx');
  const value={source:'LOCAL_INDEX',label:'Остаток по данным VTO',quantity:0,observed_at:'2026-10-01T10:00:00Z',incoming_quantity:17,snapshot_quantity:123,snapshot_observed_at:'2026-09-01T10:00:00Z'};
  const html=renderToStaticMarkup(React.createElement(OwnerStockNotice,{value,quantity:200}));
  assert.match(html,/0 шт/);assert.match(html,/Ожидается: 17 шт/);assert.match(html,/Тираж выше/);assert.doesNotMatch(html,/<input/);
  const unknown=renderToStaticMarkup(React.createElement(OwnerStockNotice,{value:{...value,quantity:null,source:'SNAPSHOT_ONLY'},quantity:200}));
  assert.match(unknown,/Количество не указано/);assert.match(unknown,/Только снимок/);
});
