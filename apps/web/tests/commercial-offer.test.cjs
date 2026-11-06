/* eslint-disable @typescript-eslint/no-require-imports -- Existing in-memory TSX harness. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');

function load(relative){
  const filename=path.join(__dirname,'..',relative),mod=new Module(filename,module);
  mod.filename=filename;mod.paths=module.paths;
  const original=mod.require.bind(mod);
  mod.require=id=>{
    const local=id.startsWith('@/')?path.join(__dirname,'../src',id.slice(2)):id.startsWith('.')?path.resolve(path.dirname(filename),id):null;
    if(local){for(const suffix of ['.ts','.tsx',''])if(fs.existsSync(local+suffix))return load(path.relative(path.join(__dirname,'..'),local+suffix));}
    return original(id);
  };
  mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
  return mod.exports;
}

const {OfferView}=load('src/components/offers/offer-view.tsx');
const {offerMoney,offerApi}=load('src/lib/commercial-offers.ts');
const {offerExpiry}=load('src/lib/commercial-offers.ts');
const value={schema_version:1,title:'Предложение для клиента',company:'Компания',logo_url:'/api/public/offers/redacted/revisions/r/assets/logo',banner_url:null,
  items:[{id:'blue-line',title:'Рюкзак синий',sku:'00001-A.01',brand:'Molti',category:'Рюкзаки',description:'Полное описание\n\nВторой абзац.',characteristics:[{name:'Цвет',value:'синий'}],unit_price:'35224.51',quantity:100,line_amount:'3522451.00',availability:'В наличии',main_image:'blue-photo',images:[{id:'blue-photo',url:'/api/public/offers/redacted/revisions/r/assets/blue-photo'},{id:'blue-back',url:'/api/public/offers/redacted/revisions/r/assets/blue-back'}]}]};

test('money formatting preserves server decimals without Number arithmetic',()=>{
  assert.equal(offerMoney('35224.51'),'35 224,51 ₸');
  assert.equal(offerMoney('3522451.00'),'3 522 451,00 ₸');
  assert.equal(offerMoney('999999999999.99'),'999 999 999 999,99 ₸');
  assert.equal(offerMoney(null),'Цена не указана');
});

test('read-only offer renders SKU, independent line price, quantity, description and scoped photographs',()=>{
  const html=renderToStaticMarkup(React.createElement(OfferView,{value}));
  for(const text of ['00001-A.01','35 224,51','3 522 451,00','100 шт.','Стоимость позиции','Полное описание','Второй абзац.'])assert.ok(html.includes(text),text);
  assert.match(html,/blue-photo/);assert.match(html,/blue-back/);assert.match(html,/aria-pressed="true"/);
  assert.doesNotMatch(html,/<input|<textarea|AI Research|grand_total|Итого|Вход|Проекты|source_url/);
  const cover=html.slice(html.indexOf('offer-cover'),html.indexOf('offer-products'));
  assert.doesNotMatch(cover,/Рюкзак синий|35 224/);
});

test('variant rows remain separate and sensitive editor fields are never rendered in the shared view',()=>{
  const red={...value.items[0],id:'red-line',sku:'00002-A.02',title:'Рюкзак красный',main_image:'red-photo',images:[{id:'red-photo',url:'/api/public/offers/redacted/revisions/r/assets/red-photo'}],catalog_price:'PRIVATE_PURCHASE_PRICE',source_url:'PRIVATE_SUPPLIER_URL'};
  const html=renderToStaticMarkup(React.createElement(OfferView,{value:{...value,items:[...value.items,red]}}));
  assert.equal((html.match(/class="offer-product"/g)??[]).length,2);
  assert.match(html,/00001-A.01/);assert.match(html,/00002-A.02/);
  assert.doesNotMatch(html,/PRIVATE_PURCHASE_PRICE|PRIVATE_SUPPLIER_URL|Итого/);
});

test('untrusted visible strings are escaped; a missing primary is not replaced by another photograph',()=>{
  const html=renderToStaticMarkup(React.createElement(OfferView,{value:{...value,items:[{...value.items[0],title:'<img src=x onerror=evil()>',description:'=HYPERLINK("evil")',main_image:'unavailable'}]}}));
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src="x"/);
  assert.match(html,/Фотография недоступна/);
  assert.match(html,/HYPERLINK/);
});

test('public reads omit credentials and shared caches and do not change the supplied capability',async()=>{
  const previous=global.fetch;const calls=[];
  global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>value};};
  try{await offerApi.published('test-token');assert.equal(calls[0].url,'/api/public/offers/test-token');assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.referrerPolicy,'no-referrer');}
  finally{global.fetch=previous;}
});

test('each product section owns one separate VTO footer, including with a custom header logo',()=>{
  const item={...value.items[0],characteristics:[...value.items[0].characteristics,{name:'Бренд',value:'Molti'}]};
  const html=renderToStaticMarkup(React.createElement(OfferView,{value:{...value,logo_url:'/custom-client.png',items:[item,{...item,id:'second'}]}}));
  assert.equal((html.match(/class="offer-product-brand"/g)??[]).length,2);
  assert.equal((html.match(/alt="VTO"/g)??[]).length,2);
  assert.match(html,/custom-client.png/);
  assert.match(html,/Molti/);
});

test('expiry uses the explicit interface timezone and readonly absence never creates an offer',async()=>{
  assert.match(offerExpiry('2026-11-13T12:43:44Z'),/13\.11\.2026.*17:43.*UTC\+5/);
  const calls=[],previous=global.fetch;
  global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>null};};
  try{assert.equal(await offerApi.primary('project'),null);assert.equal(calls.length,1);assert.equal(calls[0].options.method,'GET');assert.equal(calls[0].url,'/api/projects/project/offer');}finally{global.fetch=previous;}
});
