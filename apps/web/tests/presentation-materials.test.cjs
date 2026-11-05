/* eslint-disable @typescript-eslint/no-require-imports -- Isolated Node harness compiles the project TS/TSX modules. */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');

const {load}=require('./helpers/load.cjs');

const {mergePreparedMaterials,materialUsageLabel,normalizePresentationDraft}=load('src/lib/presentation-materials.ts');
const {CustomMaterials}=load('src/components/presentations/custom-materials.tsx');
const asset={id:'a',role:'DECORATION',filename:'Орнамент.png',status:'READY',enabled:true,scope:'DOCUMENT',expression:'NORMAL',position:'AUTO',crop:false,focal_x:.5,focal_y:.5,opacity:1,harmonize:false,required:false,description:'',thumbnail_url:'/api/presentations/doc/materials/a?thumbnail=true'};
const props={materials:[],usage:[],previous:false,disabled:false,maxBytes:12582912,maxDecorations:6,onChange:()=>{},onUpload:async()=>{}};

test('optional compact materials panel has three distinct upload roles without duplicating logo/title',()=>{
  const html=renderToStaticMarkup(React.createElement(CustomMaterials,props));
  for(const name of ['Свои материалы','Фотография обложки','Фон / текстура','Декоративные элементы'])assert.ok(html.includes(name));
  assert.equal((html.match(/type="file"/g)||[]).length,3);
  assert.ok(!html.includes('Логотип PNG')&&!html.includes('Название презентации'));
});
test('material card exposes scope/intensity/preferences and reports actual revision usage',()=>{
  const html=renderToStaticMarkup(React.createElement(CustomMaterials,{...props,materials:[asset],usage:[{asset_ref:'a',status:'USED',pages:[1,3],reason:null}]}));
  assert.ok(html.includes('Использован на страницах: 1, 3'));
  assert.ok(html.includes('Согласовать декор с палитрой'));
  assert.ok(html.includes('Обязательно использовать'));
  assert.ok(html.includes('Товарные страницы'));
  assert.ok(html.includes('Орнамент.png'));
});
test('disabled, preparing, failed and stale usages are not called current successful placement',()=>{
  const use={asset_ref:'a',status:'USED',pages:[1],reason:null};
  assert.equal(materialUsageLabel({...asset,enabled:false},use),'Выключен пользователем');
  assert.match(materialUsageLabel({...asset,status:'QUEUED'},use),/Подготовка/);
  assert.equal(materialUsageLabel({...asset,status:'FAILED',error:'Повреждён PNG'},use),'Повреждён PNG');
  assert.match(materialUsageLabel(asset,use,true),/^Предыдущая PDF:/);
  assert.match(materialUsageLabel(asset,{...use,status:'SKIPPED',reason:'недостаточно свободного места'}),/Не использован: недостаточно/);
});
test('preparation publication preserves dirty names/settings and does not resurrect locally removed material',()=>{
  const original={items:[{display_name:'Source'}],materials:[asset,{...asset,id:'removed'}]};
  const local={...original,items:[{display_name:'Edited'}],materials:[{...asset,opacity:.4}]};
  const server={...original,materials:[{...asset,width:1200,status:'READY'},{...asset,id:'removed'},{...asset,id:'new'}]};
  const merged=mergePreparedMaterials(local,server,original);
  assert.equal(merged.items[0].display_name,'Edited');
  assert.deepEqual(merged.materials.map(m=>m.id),['a','new']);
  assert.equal(merged.materials[0].width,1200);
  assert.equal(merged.materials[0].opacity,.4);
  const failed=mergePreparedMaterials(local,{...server,materials:[{...asset,status:'FAILED',error:'bad'}]},original);
  assert.equal(failed.materials[0].enabled,false);
});
test('polling and initial opening share legacy settings defaults without mutating saved data',()=>{
  const original={items:[],settings:{title:'Legacy'},category_order:[],original_fallback_ids:[]};
  const normalized=normalizePresentationDraft(original);
  assert.equal(normalized.settings.style,'editorial');
  assert.equal(normalized.settings.style_version,2);
  assert.equal(normalized.settings.auto_design,false);
  assert.deepEqual(normalized.materials,[]);
  assert.deepEqual(original.settings,{title:'Legacy'});
});
