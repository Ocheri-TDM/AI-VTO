/* eslint-disable @typescript-eslint/no-require-imports -- Node CommonJS harness compiles TSX in memory. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');

function load(relative) {
  const filename=path.join(__dirname,'..',relative);
  const code=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const mod=new Module(filename,module);
  mod.filename=filename;mod.paths=module.paths;
  const original=mod.require.bind(mod);
  mod.require=id=>{
    if(id==='next/image')return {default:props=>React.createElement('img',{src:props.src,alt:props.alt})};
    const local=id.startsWith('@/')?path.join(__dirname,'../src',id.slice(2)):id.startsWith('.')?path.resolve(path.dirname(filename),id):null;
    if(local){for(const suffix of ['.ts','.tsx',''])if(fs.existsSync(local+suffix))return load(path.relative(path.join(__dirname,'..'),local+suffix));}
    return original(id);
  };
  mod._compile(code,filename);
  return mod.exports;
}

const {ResearchFilterPanel,ResearchConditions}=load('src/components/research/research-filters.tsx');
const research={intent:{raw_query:'ручки',brands:[],branches:[{label:'ручки',category:'pen',colors:['BLACK'],materials:[],quantity:300,min_price:null,max_price:null}]},
  id:'test-research',products:[],selected_products:[],groups:[],view:{version:2,selected_ids:[],filters:{brands:['molti'],colors:[],materials:[],sizes:[],min_price:null,max_price:5000,minimum_quantity:null,excluded_materials:[],required_terms:[],origins:{brands:'chat'}}},
  facets:{brand:[{value:'molti',label:'Molti',count:12},{value:'other',label:'Other',count:4}]}};

test('brand component renders real counts and selected checkbox in the existing panel',()=>{
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research,disabled:false,onChange(){}}));
  assert.match(html,/Molti/);assert.match(html,/12/);assert.match(html,/checked=""/);
  assert.match(html,/Поиск бренда/);assert.doesNotMatch(html,/hard-request|Case View|Пересобрать/);
});

test('HUD has inline controls and a distinct selection region without removed actions',()=>{
  const {ResearchHud,hasAdditionalFilters}=load('src/components/research/research-hud.tsx');
  const html=renderToStaticMarkup(React.createElement(ResearchHud,{research,grouped:false,pending:false,disabled:false,
    onGrouped(){},onFilters(){},onReset(){},onSelected(){},onMobileFilters(){}}));
  assert.match(html,/research-inline-filters/);assert.match(html,/data-testid="selected-products"/);
  assert.ok(html.indexOf('data-testid="result-filters"')<html.indexOf('data-testid="selected-products"'));
  assert.match(html,/Открыть подборку/);
  assert.doesNotMatch(html,/По соответствию|Отменить|Продолжить исследование|Источников|Выбрано 0/);
  assert.equal(hasAdditionalFilters(research.view.filters),true);
  assert.equal(hasAdditionalFilters({...research.view.filters,brands:[],max_price:null,origins:{}}),false);
});

test('card keeps detail buttons and selection but has no external anchors or icons',()=>{
  const {ProductCard}=load('src/components/product-card.tsx');
  const html=renderToStaticMarkup(React.createElement(ProductCard,{product:{id:'vto:1',name:'Ручка',category:'pen',primary_image:null,
    source_url:'https://vto.kz/product/pen/',colors:[],price_kzt:500},selected:false,onSelect(){},onDetails(){}}));
  assert.match(html,/image-details-button/);assert.match(html,/product-name/);assert.match(html,/type="checkbox"/);
  assert.doesNotMatch(html,/<a\b|Открыть у поставщика|lucide-external-link|lucide-move-up-right|product-source-link/);
});

test('related matches never inherit exact label from the color match',()=>{
  const {matchCaption}=load('src/lib/product-match.ts');
  assert.equal(matchCaption({category_match:'RELATED',match:'exact'}),'Связанное предложение');
});

test('view writes serialize versions and coalesce fast filters with selection',async()=>{
  const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
  const calls=[],published=[];
  const writer=new ResearchViewWriter((id,body)=>new Promise(resolve=>calls.push({id,body,resolve})),async()=>research,
    (value,pending)=>{if(!pending?.filters)published.push(value);},()=>{},error=>{throw error;});
  const base={...research,view:{...research.view,version:0}};writer.reset(base);
  writer.enqueue({filters:{...base.view.filters,brands:['a']}});
  writer.enqueue({filters:{...base.view.filters,brands:['b']}});writer.enqueue({selected_ids:['one']});
  calls[0].resolve({...base,view:{...base.view,version:1}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,2);assert.equal(calls[1].body.version,1);assert.deepEqual(calls[1].body.filters.brands,['b']);
  assert.deepEqual(calls[1].body.selected_ids,['one']);assert.equal(published.length,0);
  calls[1].resolve({...base,view:{...base.view,version:2,filters:calls[1].body.filters,selected_ids:['one']}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(published.at(-1).view.version,2);assert.equal(writer.saving,false);
});

test('late write from a previous project cannot publish into the next workspace',async()=>{
  const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
  const calls=[],published=[];
  const writer=new ResearchViewWriter((id,body)=>new Promise(resolve=>calls.push({id,body,resolve})),async()=>research,
    value=>published.push(value.id),()=>{},()=>{});
  writer.reset(research);writer.enqueue({selected_ids:['old']});
  const next={...research,id:'next',view:{...research.view,version:0}};writer.reset(next);writer.enqueue({selected_ids:['new']});
  calls[0].resolve(research);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[1].id,'next');assert.equal(calls[1].body.version,0);assert.deepEqual(published,[]);
  calls[1].resolve(next);await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(published,['next']);
});

test('view conflict reloads once and preserves the most recent filter and selection',async()=>{
  const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
  const calls=[],published=[];let reloads=0;
  const fresh={...research,view:{...research.view,version:9}};
  const writer=new ResearchViewWriter((id,body)=>new Promise((resolve,reject)=>calls.push({id,body,resolve,reject})),
    async()=>{reloads++;return fresh;},value=>published.push(value),()=>{},error=>{throw error;});
  writer.reset(research);writer.enqueue({filters:{...research.view.filters,brands:['old']}});
  writer.enqueue({filters:{...research.view.filters,brands:['new']},selected_ids:['one']});
  calls[0].reject({status:409});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(reloads,1);assert.equal(calls[1].body.version,9);
  assert.deepEqual(calls[1].body.filters.brands,['new']);assert.deepEqual(calls[1].body.selected_ids,['one']);
  calls[1].resolve({...fresh,view:{...fresh.view,version:10}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(published.at(-1).view.version,10);
});
test('closed-panel HUD retains source and additional constraints',()=>{
  const html=renderToStaticMarkup(React.createElement(ResearchConditions,{research}));
  assert.match(html,/300 шт/);assert.match(html,/5000/);assert.match(html,/molti/);
  assert.match(html,/Уточнение в чате/);assert.match(html,/Требование запроса/);
});

test('selected panel preserves two selected color variants with compact cards and document action',()=>{
  const {SelectedProducts}=load('src/components/research/selected-products.tsx');
  const products=['BLUE','GREY'].map((color,n)=>({id:`vto:${n}`,name:'Рюкзак одного семейства',brand:'Molti',price_kzt:2084,
    primary_image:null,colors:[{normalized_color:color,original_color:color}],availability:{availableNow:1739,incoming:[]},freshness:'STALE'}));
  const html=renderToStaticMarkup(React.createElement(SelectedProducts,{products,count:2,busy:false,onRemove(){},onDetails(){},onClear(){},onCreate(){}}));
  assert.equal((html.match(/class="selected-product"/g)||[]).length,2);
  assert.match(html,/selected-products-actions/);assert.match(html,/Создать презентацию/);assert.match(html,/Синий|синий/);
  assert.doesNotMatch(html,/Точное совпадение|Варианты модели|Последний известный остаток|research-match/);
});

test('empty selection cannot create a document',()=>{
  const {SelectedProducts}=load('src/components/research/selected-products.tsx');
  const html=renderToStaticMarkup(React.createElement(SelectedProducts,{products:[],count:0,busy:false,onRemove(){},onDetails(){},onClear(){},onCreate(){}}));
  assert.match(html,/disabled="">Создать презентацию/);
});
