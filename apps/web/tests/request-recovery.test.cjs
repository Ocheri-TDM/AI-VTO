/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const {load}=require('./helpers/load.cjs');
const {request,ApiError}=load('src/lib/api.ts');
const {draftKey,saveDraft,readDraft,restoredSubmission,visibleConversation}=load('src/lib/workspace-panels.ts');

test('transport, outage, missing backend configuration and expired session are distinct',async()=>{
  const original=global.fetch;
  try{
    for(const [status,body,pattern,code] of [
      [503,{detail:'Service unavailable'},/временно недоступен/,undefined],
      [503,{detail:{code:'BACKEND_NOT_CONFIGURED',message:'not configured'}},/API_BASE_URL/,'BACKEND_NOT_CONFIGURED'],
      [401,{detail:'Unauthorized'},/Сессия завершилась/,undefined],
      [422,{detail:{code:'PRICE_RANGE_CONFLICT',message:'Минимальная цена выше максимальной. Какой диапазон использовать?'}},/Какой диапазон использовать/,'PRICE_RANGE_CONFLICT'],
    ]){
      global.fetch=async()=>Response.json(body,{status});
      await assert.rejects(()=>request('/probe',{},true),e=>e instanceof ApiError&&e.status===status&&e.code===code&&pattern.test(e.message));
    }
    global.fetch=async()=>{throw new TypeError('Failed to fetch');};
    await assert.rejects(()=>request('/probe'),e=>e.status===0&&e.code==='NETWORK_UNAVAILABLE'&&!e.message.includes('API_BASE_URL'));
    global.fetch=async()=>new Response('not JSON',{status:200});
    await assert.rejects(()=>request('/probe'),e=>e.code==='INVALID_RESPONSE');
  }finally{global.fetch=original;}
});

test('F5 restores exact request ID, creation key and text only for its user and chat',()=>{
  const data=new Map();const prior=global.sessionStorage;
  global.sessionStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  try{
    const draft={text:'  powerbank  ',id:'a',created_at:'2026-11-25T12:00:00Z',creation_id:'c'};
    saveDraft('owner','initial',draft);
    assert.deepEqual(readDraft('owner','initial'),draft);
    assert.equal(readDraft('another','initial'),null);
    assert.equal(readDraft('owner','chat'),null);
    const s=restoredSubmission(null,readDraft('owner','initial'));
    assert.equal(s.id,'a');assert.equal(s.phase,'ERROR');assert.equal(s.text,draft.text);
    saveDraft('owner','chat',draft);saveDraft('owner','initial',null);
    assert.ok(!data.has(draftKey('owner','initial')));
    const stored=restoredSubmission('chat',readDraft('owner','chat'));
    assert.equal(visibleConversation([],stored,'chat')[0].status,'LOCAL_ERROR');
    const server={role:'user',request_id:'a',status:'COMPLETED'};
    assert.deepEqual(visibleConversation([server],stored,'chat'),[server]);
  }finally{if(prior===undefined)delete global.sessionStorage;else global.sessionStorage=prior;}
});
