/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');
const {ChatPanelToggle}=load('src/components/research/chat-panel-toggle.tsx');

test('right opener has a visible label, outline message icon and panel relationship',()=>{
  const html=renderToStaticMarkup(React.createElement(ChatPanelToggle,{expanded:false,onClick(){}}));
  assert.match(html,/<button[^>]*type="button"/);assert.match(html,/>Чат<\/span>/);
  assert.match(html,/lucide-message-square/);assert.match(html,/aria-hidden="true"/);
  assert.match(html,/aria-label="Открыть чат"/);assert.match(html,/aria-expanded="false"/);assert.match(html,/aria-controls="workspace-chat"/);
  assert.doesNotMatch(html,/aria-haspopup/);
});

test('collapse uses a panel icon and short visible text with the full accessible name',()=>{
  const html=renderToStaticMarkup(React.createElement(ChatPanelToggle,{expanded:true,onClick(){}}));
  assert.match(html,/lucide-panel-right-close/);assert.match(html,/>Свернуть<\/span>/);
  assert.match(html,/aria-label="Свернуть чат"/);assert.match(html,/aria-expanded="true"/);
  assert.doesNotMatch(html,/research-chat-reopen/);
});

test('mobile opener identifies the existing dialog; mobile composer does not request keyboard focus',()=>{
  const html=renderToStaticMarkup(React.createElement(ChatPanelToggle,{expanded:false,mobile:true,onClick(){}}));
  assert.match(html,/aria-haspopup="dialog"/);assert.match(html,/>Чат<\/span>/);
  const {ConversationComposer}=load('src/components/research/conversation.tsx');
  const composer=renderToStaticMarkup(React.createElement(ConversationComposer,{value:'draft',onChange(){},onSubmit(){},busy:false,started:true,focusOnMount:false}));
  assert.doesNotMatch(composer,/autofocus/);assert.match(composer,/draft/);
});

test('chat sheet has one custom collapse control while other sheets keep their original close button',()=>{
  const {Sheet}=load('src/components/common/sheet.tsx');
  const chat=renderToStaticMarkup(React.createElement(Sheet,{id:'workspace-chat',title:'Чат',onClose(){},closeControl:React.createElement(ChatPanelToggle,{expanded:true,onClick(){}})},'history'));
  assert.match(chat,/id="workspace-chat"/);assert.equal((chat.match(/<button/g)||[]).length,1);assert.match(chat,/Свернуть чат/);assert.doesNotMatch(chat,/Закрыть панель/);
  const other=renderToStaticMarkup(React.createElement(Sheet,{title:'Выбранные товары',onClose(){}},'items'));
  assert.match(other,/Закрыть панель/);assert.doesNotMatch(other,/workspace-chat-toggle/);
});
