/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');
const {workspacePanels,visibleConversation,panelPreferenceKey,transferPanelChoice}=load('src/lib/workspace-panels.ts');
const restored={projectsReady:true,chat:'new',historyReady:true,researchReady:true,collectionReady:true,hasResearch:false,hasMessages:false,knownMessages:false,submission:null,selectedCount:0};
const submission={chat:'new',id:'request',text:'  Нужны ручки\nсиние  ',created_at:'2026-11-07T10:00:00Z',phase:'SENDING'};

test('empty and typed drafts use the central composer without chat or zero collection',()=>{
  assert.deepEqual(workspacePanels(restored),{started:false,restored:true,showInitialComposer:true,showCollectionShortcut:false});
  for(const missing of ['projectsReady','historyReady','researchReady','collectionReady']){
    const value=workspacePanels({...restored,[missing]:false,selectedCount:null});
    assert.equal(value.restored,false);assert.equal(value.showInitialComposer,false);assert.equal(value.showCollectionShortcut,false);
  }
});

test('submission, zero-result research and history each keep the conversation available',()=>{
  for(const change of [{submission},{hasResearch:true},{hasMessages:true},{knownMessages:true}]){
    const value=workspacePanels({...restored,...change});
    assert.equal(value.started,true);assert.equal(value.showInitialComposer,false);
  }
  assert.equal(workspacePanels({...restored,submission:{...submission,phase:'ERROR'}}).started,true);
  assert.equal(workspacePanels({...restored,chat:'another',submission}).started,false);
});

test('persisted selection without research gets compact access; unknown is never a confirmed zero',()=>{
  assert.equal(workspacePanels({...restored,selectedCount:3}).showCollectionShortcut,true);
  assert.equal(workspacePanels({...restored,selectedCount:3,hasResearch:true}).showCollectionShortcut,false);
  const {CollectionSummary}=load('src/components/research/research-hud.tsx');
  const html=renderToStaticMarkup(React.createElement(CollectionSummary,{count:0,loading:true,onOpen(){}}));
  assert.match(html,/Загрузка…/);assert.match(html,/disabled/);assert.doesNotMatch(html,/>0</);
});

test('local pending message retains exact text and only the matching server request replaces it',()=>{
  const pending=visibleConversation([],submission,'new');
  assert.equal(pending.length,1);assert.equal(pending[0].content,submission.text);assert.equal(pending[0].status,'LOCAL_PENDING');
  const other={...pending[0],id:'old',request_id:'previous'};
  assert.equal(visibleConversation([other],submission,'new').length,2);
  const confirmed={...pending[0],id:'server-id',status:'ACCEPTED'};
  assert.deepEqual(visibleConversation([confirmed],submission,'new'),[confirmed]);
  assert.deepEqual(visibleConversation([],submission,'foreign'),[]);
  assert.equal(visibleConversation([],{...submission,phase:'ERROR'},'new')[0].status,'LOCAL_ERROR');
});

test('pending and failed messages explain unconfirmed persistence; collapse is a real action',()=>{
  const {Conversation}=load('src/components/research/conversation.tsx');
  for(const phase of ['SENDING','ERROR']){
    const html=renderToStaticMarkup(React.createElement(Conversation,{messages:visibleConversation([],{...submission,phase},'new'),hasOlder:false,loading:false,error:'',onOlder:async()=>{},onRetry(){},onCollapse(){},composer:null,busy:phase==='SENDING'}));
    assert.match(html,/Свернуть чат/);assert.match(html,/tabindex="0"/);assert.match(html,/не подтвержден/);assert.doesNotMatch(html,/Сообщение сохранено/);
  }
});

test('composer keeps a single controlled draft and distinguishes disabled from sending',()=>{
  const {ConversationComposer}=load('src/components/research/conversation.tsx');
  const html=renderToStaticMarkup(React.createElement(ConversationComposer,{value:'черновик',onChange(){},onSubmit(){},busy:false,disabled:true,started:true}));
  assert.equal((html.match(/<textarea/g)||[]).length,1);assert.match(html,/черновик/);assert.match(html,/disabled/);assert.doesNotMatch(html,/Отправляю/);
  assert.notEqual(panelPreferenceKey('one','chat'),panelPreferenceKey('two','chat'));
  assert.notEqual(panelPreferenceKey('one','chat'),panelPreferenceKey('one','other'));
});

test('a late automatic chat creation carries the latest manual panel decision to its new scope',()=>{
  assert.deepEqual(transferPanelChoice({key:'initial',open:false},'initial','created'),{key:'created',open:false});
  assert.deepEqual(transferPanelChoice({key:'initial',open:true},'initial','created'),{key:'created',open:true});
  assert.deepEqual(transferPanelChoice({key:'foreign',open:false},'initial','created'),{key:'created',open:true});
});
