/* eslint-disable @typescript-eslint/no-require-imports -- Use real components with the existing Node/TSX harness. */
const test = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {load} = require('./helpers/load.cjs');
const {ProjectList} = load('src/components/projects/project-list.tsx');
const {Sheet} = load('src/components/common/sheet.tsx');
const {ProductCard} = load('src/components/product-card.tsx');
const names = ['Короткий', 'Очень длинное название проекта для большой команды', 'БезПробелов'.repeat(20)];
const projects = names.map((name,i)=>({id:String(i),chat_id:'chat-'+i,name,updated_at:'2026-11-05T12:00:00Z',summary:i?'Подборка':null}));
const product = {id:'vto:1823107',name:'Рюкзак-мешок Melango — чёрный',category:'backpack',primary_image:null,colors:[{original_color:'чёрный',normalized_color:'BLACK'}],price_kzt:2084,freshness:'STALE',availability:{availableNow:123456,current:[{observedAt:'2026-10-10T12:00:00Z'}],incoming:[],incomingConfirmed:0}};

test('project opener and settings stay separate buttons with a full-title tooltip',()=>{
  const html=renderToStaticMarkup(React.createElement(ProjectList,{projects,active:'0',disabled:false,onOpen(){},onNew(){},onEdit(){}}));
  assert.equal((html.match(/class="project-open"/g)||[]).length,3);
  assert.equal((html.match(/class="icon-button project-settings"/g)||[]).length,3);
  assert.match(html,/aria-current="page"/); assert.match(html,/Есть переписка/); assert.match(html,/Пустой чат/); assert.match(html,/Новый чат/);
  for(const name of names) {assert.ok(html.includes(`title="${name}"`));assert.ok(html.includes(`Настройки проекта ${name}`));}
  assert.doesNotMatch(html,/<button[^>]*>(?:(?!<\/button>)[\s\S])*<button/);
});
test('disabled project list keeps open, settings and create actions disabled',()=>{
  const html=renderToStaticMarkup(React.createElement(ProjectList,{projects,disabled:true,onOpen(){},onNew(){},onEdit(){}}));
  assert.equal((html.match(/disabled=""/g)||[]).length,7);
});
test('large sheet sizing is opt-in and retains the accessible name and close action',()=>{
  const html=renderToStaticMarkup(React.createElement(Sheet,{title:'Варианты модели',variant:'modelVariants',onClose(){}},'items'));
  assert.match(html,/model-variants-sheet/); assert.match(html,/aria-label="Варианты модели"/); assert.match(html,/aria-label="Закрыть панель"/);
  const normal=renderToStaticMarkup(React.createElement(Sheet,{title:'Фильтры',onClose(){}},'filters'));
  assert.doesNotMatch(normal,/model-variants-sheet/);
});
test('variant uses one shared card, one checkbox and image/info/actions in normal order',()=>{
  const html=renderToStaticMarkup(React.createElement(ProductCard,{product,selected:true,context:'modelVariants',onSelect(){},onDetails(){}}));
  assert.equal((html.match(/type="checkbox"/g)||[]).length,1); assert.match(html,/checked=""/); assert.match(html,/data-offer-id="vto:1823107"/);
  assert.ok(html.indexOf('product-image-wrap')<html.indexOf('product-info')); assert.ok(html.indexOf('product-info')<html.indexOf('model-variant-actions'));
  assert.ok(html.indexOf('model-variant-actions')<html.indexOf('type="checkbox"')); assert.match(html,/В подборке/);
  assert.match(html,/Последний известный остаток/); assert.match(html,/требуется уточнение/); assert.doesNotMatch(html,/href=/);
  const normal=renderToStaticMarkup(React.createElement(ProductCard,{product,selected:false,onSelect(){},onDetails(){}}));
  assert.doesNotMatch(normal,/model-variant-actions|model-variant-card/);assert.ok(normal.indexOf('type="checkbox"')<normal.indexOf('product-info'));
});
test('one, two, four and many variants retain exact offer IDs, source order and missing images',()=>{
  for(const count of [1,2,4,24]){
    const items=Array.from({length:count},(_,i)=>({...product,id:'vto:variant-'+i,name:'Длинное название варианта '+i}));
    const html=renderToStaticMarkup(React.createElement('div',null,items.map((p,i)=>React.createElement(ProductCard,{key:p.id,product:p,selected:i===0,context:'modelVariants',onSelect(){},onDetails(){}}))));
    const ids=[...html.matchAll(/data-offer-id="([^"]+)"/g)].map(m=>m[1]);assert.deepEqual(ids,items.map(p=>p.id));
    assert.equal((html.match(/Нет изображения/g)||[]).length,count);assert.equal((html.match(/checked=""/g)||[]).length,1);
  }
});
