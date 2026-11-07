/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');

test('shared navbar retains visible navigation, full username tooltip and account actions',()=>{
  const {AppNavbar}=load('src/components/app-navbar.tsx');
  const name='Очень длинное имя пользователя '.repeat(8);
  for(const active of ['projects','offers']){
    const html=renderToStaticMarkup(React.createElement(AppNavbar,{name,logout(){},active,context:'Ручки и рюкзаки'}));
    assert.match(html,/app-navbar/);assert.match(html,/Основная навигация/);
    assert.match(html,/Проекты/);assert.match(html,/Предложения/);assert.match(html,/Выйти/);
    assert.ok(html.includes('title="'+name+'"'));assert.match(html,/aria-current="page"/);
    assert.doesNotMatch(html,/Исследование каталогов|Research<\/span>/);
    assert.doesNotMatch(html,/header-project-title|app-navbar-context|research-top-project|Ручки и рюкзаки/);
  }
});

test('delete submits confirmation and version; explicit replacement includes creation generation',async()=>{
  const {offerApi}=load('src/lib/commercial-offers.ts');
  const original=global.fetch,calls=[];
  global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({})};};
  try{
    await offerApi.delete('doc',3);
    await offerApi.create('project',7,2);
    assert.equal(calls[0].options.method,'DELETE');assert.deepEqual(JSON.parse(calls[0].options.body),{version:3,confirmed:true});
    assert.deepEqual(JSON.parse(calls[1].options.body),{collection_version:7,offer_generation:2});
    assert.equal(calls[0].options.credentials,'include');
  }finally{global.fetch=original;}
});
