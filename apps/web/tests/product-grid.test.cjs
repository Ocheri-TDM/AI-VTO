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

test('project collection operations retain prior searches and reject delayed older reads',async()=>{
  const {CollectionWriter}=load('src/lib/project-collection.ts');
  const base={project_id:'A',version:2,selected_ids:['pen-a','pen-b'],products:[{id:'pen-a'},{id:'pen-b'}],unavailable_ids:[]};
  const calls=[],published=[];
  const writer=new CollectionWriter({get:async()=>base,mutate:(project,op)=>new Promise(resolve=>calls.push({project,op,resolve}))},v=>{if(v)published.push(v);},e=>{throw e;});
  writer.reset('A');writer.adopt(base);
  writer.enqueue({type:'add',offer:'bag',source:'new-search'},{id:'bag'});
  writer.enqueue({type:'add',offer:'bottle',source:'another-search'},{id:'bottle'});
  assert.deepEqual(published.at(-1).selected_ids,['pen-a','pen-b','bag','bottle']);
  const flushed=writer.flush();
  calls[0].resolve({...base,version:3,selected_ids:['pen-a','pen-b','bag'],products:[...base.products,{id:'bag'}]});
  await new Promise(resolve=>setImmediate(resolve));
  calls[1].resolve({...base,version:4,selected_ids:['pen-a','pen-b','bag','bottle'],products:[...base.products,{id:'bag'},{id:'bottle'}]});
  assert.equal((await flushed).version,4);
  writer.adopt(base);assert.deepEqual(published.at(-1).selected_ids,['pen-a','pen-b','bag','bottle']);
});

test('project collection ignores late writes after switching projects',async()=>{
  const {CollectionWriter}=load('src/lib/project-collection.ts');
  const calls=[],published=[];
  const writer=new CollectionWriter({get:async()=>null,mutate:(project,op)=>new Promise(resolve=>calls.push({project,op,resolve}))},v=>{if(v)published.push(v);},()=>{});
  const state=id=>({project_id:id,version:0,selected_ids:[],products:[],unavailable_ids:[]});
  writer.reset('A');writer.adopt(state('A'));writer.enqueue({type:'add',offer:'old'},{id:'old'});
  writer.reset('B');writer.adopt(state('B'));writer.enqueue({type:'add',offer:'new'},{id:'new'});
  calls[0].resolve({...state('A'),version:1,selected_ids:['old'],products:[{id:'old'}]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(published.at(-1).project_id,'B');assert.deepEqual(published.at(-1).selected_ids,['new']);
  calls[1].resolve({...state('B'),version:1,selected_ids:['new'],products:[{id:'new'}]});await writer.flush();
});

test('failed collection save rolls back its optimistic item and blocks a silently incomplete snapshot',async()=>{
  const {CollectionWriter}=load('src/lib/project-collection.ts');
  const state={project_id:'A',version:1,selected_ids:['saved'],products:[{id:'saved'}],unavailable_ids:[]};
  const published=[],errors=[];
  const writer=new CollectionWriter({get:async()=>state,mutate:async()=>{throw new Error('offline');}},v=>{if(v)published.push(v);},e=>errors.push(e));
  writer.reset('A');writer.adopt(state);writer.enqueue({type:'add',offer:'unsaved'},{id:'unsaved'});
  await assert.rejects(writer.flush(),/offline/);
  assert.deepEqual(published.at(-1).selected_ids,['saved']);assert.equal(errors.length,1);
});

test('focus and interval collection reloads share one in-flight read',async()=>{
  const {CollectionWriter}=load('src/lib/project-collection.ts');
  let count=0,resolve;const state={project_id:'A',version:1,selected_ids:[],products:[],unavailable_ids:[]};
  const writer=new CollectionWriter({get:()=>{count++;return new Promise(r=>resolve=r);},mutate:async()=>state},()=>{},()=>{});
  writer.reset('A');const a=writer.reload(),b=writer.reload();assert.equal(a,b);assert.equal(count,1);resolve(state);await a;
});

test('idempotent collection save respects Retry-After once and preserves subsequent remove',async()=>{
  const {CollectionWriter}=load('src/lib/project-collection.ts');
  const state={project_id:'A',version:1,selected_ids:[],products:[],unavailable_ids:[]};let attempts=0;const ops=[];
  const writer=new CollectionWriter({get:async()=>state,mutate:async(project,op)=>{
    attempts++;ops.push(op.type);if(attempts===1)throw {status:429,retryAfter:.001};
    return {...state,version:attempts,selected_ids:op.type==='add'?['x']:[]};
  }},()=>{},e=>{throw e;});
  writer.reset('A');writer.adopt(state);writer.enqueue({type:'add',offer:'x'},{id:'x'});writer.enqueue({type:'remove',offer:'x'});
  const saved=await writer.flush();assert.deepEqual(ops,['add','add','remove']);assert.deepEqual(saved.selected_ids,[]);
});
const research={intent:{raw_query:'ручки',brands:[],branches:[{label:'ручки',category:'pen',colors:['BLACK'],materials:[],quantity:300,min_price:null,max_price:null}]},
  id:'test-research',products:[],selected_products:[],groups:[],view:{version:2,selected_ids:[],filters:{brands:['molti'],colors:[],materials:[],sizes:[],min_price:null,max_price:5000,minimum_quantity:null,excluded_materials:[],required_terms:[],origins:{brands:'chat'}}},
  facets:{brand:[{value:'molti',label:'Molti',count:12},{value:'other',label:'Other',count:4}]}};

const noEffective={mode:'none',values:[],number:null,exclusive:false};
const effectiveCommon=(overrides={})=>Object.fromEntries(['brands','colors','materials','sizes','min_price','max_price','minimum_quantity'].map(f=>[f,overrides[f]??noEffective]));
const commonFields=effectiveCommon({colors:{...noEffective,mode:'common',values:['DARK_BLUE']},minimum_quantity:{...noEffective,mode:'common',number:250}});
const effectiveResearch={...research,facets:{},intent:{...research.intent,models:[],branches:[{...research.intent.branches[0],colors:['DARK_BLUE'],quantity:250}]},
  view:{...research.view,filters:{...research.view.filters,brands:[],max_price:null,origins:{}}},effective_filters:{fields:commonFields,branches:[{id:'pen',label:'Ручки',category:'pen',fields:commonFields}]}};

test('effective controls hold real common values at zero facets without duplicate condition pills',()=>{
  let writes=0;
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:effectiveResearch,disabled:false,onChange(){writes++;},onEffectiveChange(){writes++;}}));
  assert.match(html,/class="ui-select-value">Тёмно-синий<\/span>/);
  assert.match(html,/aria-selected="true"[^>]*>[\s\S]*?Тёмно-синий/);
  assert.match(html,/Тираж от, шт\.<input[^>]*type="number"[^>]*value="250"/);
  assert.doesNotMatch(html,/Условия по категориям/);
  assert.equal(renderToStaticMarkup(React.createElement(ResearchConditions,{research:effectiveResearch})), '');
  assert.equal(writes,0);assert.equal(effectiveResearch.view.filters.minimum_quantity,null);
});

test('mixed projection leaves numeric input empty and exposes editable per-branch values inside filters',()=>{
  const state={...noEffective,mode:'mixed'};
  const local=(color,number)=>effectiveCommon({colors:{...noEffective,mode:'common',values:[color]},minimum_quantity:{...noEffective,mode:'common',number}});
  const value={...effectiveResearch,effective_filters:{fields:{...commonFields,colors:state,minimum_quantity:state},branches:[{id:'red',label:'Ручки',category:'pen',fields:local('RED',500)},{id:'blue',label:'Бутылки',category:'bottle',fields:local('BLUE',100)}]}};
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:value,disabled:false,onChange(){},onEffectiveChange(){}}));
  assert.match(html,/class="ui-select-value">Разные по категориям/);
  assert.match(html,/Тираж от, шт\.<input[^>]*value=""/);assert.match(html,/Разный по категориям/);
  assert.match(html,/<details[^>]*><summary>Условия по категориям/);
  assert.match(html,/Тираж от, шт\. · Ручки<input[^>]*value="500"/);
  assert.match(html,/Тираж от, шт\. · Бутылки<input[^>]*value="100"/);
  assert.match(html,/data-filter-branch="red"/);assert.match(html,/data-filter-branch="blue"/);
  assert.doesNotMatch(html, /value="Разный по категориям"/);
});

test('replacement options remain selectable outside the current constrained facets',()=>{
  const value={...effectiveResearch,effective_filters:{...effectiveResearch.effective_filters,options:{colors:['DARK_BLUE','RED'],brands:['alpha','beta'],materials:['сталь','дерево'],sizes:['A5','A6']}}};
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:value,disabled:false,onChange(){},onEffectiveChange(){}}));
  assert.match(html,/aria-selected="false" aria-disabled="false"[^>]*><span class="ui-select-option-text">Красный/);
  assert.match(html,/class="ui-select-option-text">Красный<\/span><span class="ui-select-check"/);
  for(const label of ['alpha','beta','сталь','дерево','A5','A6'])assert.ok(html.includes(label));
  assert.match(html,/aria-selected="true"[^>]*><span class="ui-select-option-text">Тёмно-синий/);
});

test('effective controls preserve nontrivial requirements, models and exclusions without repeating common fields',()=>{
  const value={...effectiveResearch,intent:{...effectiveResearch.intent,models:['Tippo'],excluded_materials:['PLASTIC'],excluded_colors:['WHITE']},query_categories:[{id:'pen',label:'Ручки',resolved:true,constraints:{constraints:{capacity_mah:10000,excluded_colors:['WHITE'],availability_only:true,features:['ETERNAL']}}}],
    effective_filters:{...effectiveResearch.effective_filters,fields:{...commonFields,materials:{...noEffective,mode:'complex',values:['METAL','WOOD'],requirements:[['METAL'],['WOOD']]}}}};
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:value,disabled:false,onChange(){}}));
  assert.match(html,/Несколько условий/);assert.match(html,/Одновременно: \(Металл\) и \(Дерево\)/);
  const conditions=renderToStaticMarkup(React.createElement(ResearchConditions,{research:value}));
  assert.match(conditions,/Модель: Tippo/);assert.match(conditions,/Без: PLASTIC/);assert.match(conditions,/10000 мАч/);assert.match(conditions,/Без цвета: Белый/);
  assert.match(conditions,/Только в наличии/);assert.match(conditions,/Вечный пишущий элемент/);assert.equal((conditions.match(/Без цвета: Белый/g)??[]).length,1);
  assert.doesNotMatch(conditions,/Тёмно-синий|250 шт/);
  const {hasAdditionalFilters}=load('src/components/research/research-hud.tsx');
  assert.equal(hasAdditionalFilters(effectiveResearch.view.filters,[{field:'colors',action:'clear'}]),true);
});

test('effective edits serialize in order and a delayed prior workspace cannot replace fields',async()=>{
  const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
  const calls=[],published=[];
  const writer=new ResearchViewWriter((id,body)=>new Promise(resolve=>calls.push({id,body,resolve})),async()=>effectiveResearch,
    (value,pending)=>{if(!pending?.effective_changes?.length)published.push(value);},()=>{},e=>{throw e;});
  writer.reset(effectiveResearch);
  writer.enqueue({effective_changes:[{field:'colors',action:'set',values:['RED']}]});
  writer.enqueue({effective_changes:[{field:'minimum_quantity',action:'set',number:500}]});
  writer.enqueue({effective_changes:[{field:'colors',action:'clear'}]});
  calls[0].resolve({...effectiveResearch,view:{...research.view,version:3}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(published.length,0);assert.equal(calls[1].body.version,3);
  assert.deepEqual(calls[1].body.effective_changes,[{field:'minimum_quantity',action:'set',number:500},{field:'colors',action:'clear'}]);
  const next={...effectiveResearch,id:'new-workspace',effective_filters:{fields:effectiveCommon(),branches:[]}};
  writer.reset(next);writer.enqueue({effective_changes:[{field:'colors',action:'set',values:['BLUE']}]});
  calls[1].resolve(effectiveResearch);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(published.length,0);assert.equal(calls[2].id,'new-workspace');
  calls[2].resolve(next);await new Promise(resolve=>setImmediate(resolve));assert.equal(published.at(-1).id,'new-workspace');
});

test('source color remains selected with zero facets without writing an additional filter',()=>{
  const value={...research,color_state:{mode:'uniform',values:['DARK_BLUE'],source_mode:'uniform',source_values:['DARK_BLUE'],additional_values:[],conflicting_branch_ids:[]},facets:{},view:{...research.view,filters:{...research.view.filters,colors:[]}}};
  let writes=0;
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:value,disabled:false,onChange(){writes++;}}));
  assert.match(html,/class="ui-select-value">Тёмно-синий<\/span>/);
  assert.match(html,/aria-selected="true"[^>]*>[\s\S]*?Тёмно-синий/);
  assert.match(html,/Из запроса/);assert.match(html,/цвет не важен/);
  assert.match(html,/ui-select-count">0<\/span>/);
  assert.equal((html.match(/class="ui-select-option-text">Тёмно-синий/g)??[]).length,1);
  assert.equal(writes,0);assert.deepEqual(value.view.filters.colors,[]);
});

test('mixed source colors and conflicting manual narrowing are explicit in the HUD',()=>{
  const state={mode:'mixed',values:['RED','BLUE'],source_mode:'mixed',source_values:['RED','BLUE'],additional_values:[],conflicting_branch_ids:[]};
  let html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:{...research,color_state:state},disabled:false,onChange(){}}));
  assert.match(html,/class="ui-select-value">Разные по категориям<\/span>/);
  html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:{...research,color_state:{...state,mode:'conflict',conflicting_branch_ids:['pen']},view:{...research.view,filters:{...research.view.filters,colors:['GREEN']}}},disabled:false,onChange(){}}));
  assert.match(html,/Дополнительный цвет конфликтует с запросом/);
  assert.match(html,/class="ui-select-value">Зелёный<\/span>/);
});

test('condition labels use the same branch identity as query categories',()=>{
  const value={...research,intent:{...research.intent,branches:[{...research.intent.branches[0],id:'shopper',category:'shopperbag',label:'supplier-merch'}]},query_categories:[{id:'shopper',label:'Сумки-шопперы'}]};
  const html=renderToStaticMarkup(React.createElement(ResearchConditions,{research:value}));
  assert.match(html,/Сумки-шопперы: Чёрный/);assert.doesNotMatch(html,/Мерч|supplier-merch/);
});

test('history activation sends a JSON object accepted by the request validator',async()=>{
  const {workspaceApi}=load('src/lib/project-queries.ts');
  const previous=global.fetch;
  let body;
  global.fetch=async(url,options)=>{body=JSON.parse(options.body);return {ok:true,status:200,json:async()=>({research})};};
  try {await workspaceApi.activate('project','query');assert.deepEqual(body,{});}
  finally {global.fetch=previous;}
});

test('brand component renders real counts and selected checkbox in the existing panel',()=>{
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research,disabled:false,onChange(){}}));
  assert.match(html,/Molti/);assert.match(html,/12/);assert.match(html,/checked=""/);
  assert.match(html,/Поиск бренда/);assert.doesNotMatch(html,/hard-request|Case View|Пересобрать/);
});

test('source brand and model stay visible while scoped filters remain additional',()=>{
  const {ResearchConditions}=load('src/components/research/research-filters.tsx');
  const {hasAdditionalFilters}=load('src/components/research/research-hud.tsx');
  const value={...research,brand_labels:{molti:'Molti'},intent:{...research.intent,brands:['Molti'],models:['Tippo']}};
  const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research:value,disabled:false,onChange(){}}));
  assert.match(html,/disabled=""[^>]*checked=""/);
  assert.match(html,/из запроса/);
  const pills=renderToStaticMarkup(React.createElement(ResearchConditions,{research:value}));
  assert.match(pills,/Бренд: Molti/);assert.match(pills,/Модель: Tippo/);
  assert.equal(hasAdditionalFilters({...research.view.filters,brands:[],max_price:null,branch_models:{pen:['Tippo']}}),true);
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
  assert.match(html,/selected-products-actions/);assert.match(html,/Создать предложение/);assert.match(html,/Синий|синий/);
  assert.doesNotMatch(html,/Точное совпадение|Варианты модели|Последний известный остаток|research-match/);
});

test('empty selection cannot create a document',()=>{
  const {SelectedProducts}=load('src/components/research/selected-products.tsx');
  const html=renderToStaticMarkup(React.createElement(SelectedProducts,{products:[],count:0,busy:false,onRemove(){},onDetails(){},onClear(){},onCreate(){}}));
  assert.match(html,/disabled="">Создать предложение/);
});

const categoryResearch={...research,query_categories:[
  {id:'pen-branch',concept:'pen',label:'Ручки',source_text:'красные ручки Linear',order:0,hidden:false,count:126,resolved:true,constraints:{colors:['RED'],models:['Linear']}},
  {id:'bottle-branch',concept:'bottle',label:'Бутылки',source_text:'бутылки',order:1,hidden:true,count:25,resolved:true,constraints:{}},
  {id:'empty-branch',concept:'socks',label:'Носки',source_text:'носки',order:2,hidden:false,count:0,resolved:true,constraints:{}}
],view:{...research.view,hidden_branch_ids:['bottle-branch']}};

test('query chips contain only names and accessible actions, retaining branch identity and order',()=>{
  const {QueryCategories}=load('src/components/research/query-categories.tsx');
  const html=renderToStaticMarkup(React.createElement(QueryCategories,{research:categoryResearch,disabled:false,pending:false,onAction(){}}));
  assert.match(html,/Категории запроса/);assert.match(html,/data-branch-id="pen-branch"/);
  assert.match(html,/Носки/);assert.match(html,/Показать категорию «Бутылки»/);
  assert.match(html,/Убрать из запроса «Бутылки»/);assert.match(html,/Показать все/);
  assert.doesNotMatch(html,/disabled=""|126|25|<small|Красный|Linear|title="красные ручки|Скрыта/);
  assert.ok(html.indexOf('pen-branch')<html.indexOf('bottle-branch')&&html.indexOf('bottle-branch')<html.indexOf('empty-branch'));
});

test('brand-only query and contextual restore have explicit distinct states',()=>{
  const {QueryCategories}=load('src/components/research/query-categories.tsx');
  const brand=renderToStaticMarkup(React.createElement(QueryCategories,{research:{...categoryResearch,query_categories:[],categories_unrestricted:true},disabled:false,pending:false,onAction(){}}));
  assert.match(brand,/Категории не ограничены/);assert.doesNotMatch(brand,/data-branch-id/);
  const removed=renderToStaticMarkup(React.createElement(QueryCategories,{research:{...categoryResearch,query_categories:[],category_notice:{id:'removal',label:'Рюкзаки'}},disabled:false,pending:false,onAction(){}}));
  assert.match(removed,/В запросе не осталось категорий/);assert.match(removed,/Рюкзаки/);assert.match(removed,/Вернуть/);assert.doesNotMatch(removed,/Отменить/);
});

test('rapid branch actions retain operation order while coalescing newer filters',async()=>{
  const {ResearchViewWriter,draftResearch}=load('src/lib/research-view-writer.ts');
  const calls=[],published=[];
  const writer=new ResearchViewWriter((id,body)=>new Promise(resolve=>calls.push({id,body,resolve})),async()=>categoryResearch,
    (value,pending)=>{if(!pending?.branch_actions?.length&&!pending?.filters)published.push(value);},()=>{},e=>{throw e;});
  writer.reset(categoryResearch);
  writer.enqueue({branch_actions:[{action:'hide',branch_id:'pen-branch'}]});
  writer.enqueue({branch_actions:[{action:'show',branch_id:'pen-branch'}]});
  writer.enqueue({branch_actions:[{action:'remove',branch_id:'empty-branch'}]});
  writer.enqueue({filters:{...research.view.filters,brands:['alpha']}});
  calls[0].resolve({...categoryResearch,view:{...categoryResearch.view,version:3}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls[1].body.branch_actions,[{action:'show',branch_id:'pen-branch'},{action:'remove',branch_id:'empty-branch'}]);
  assert.deepEqual(calls[1].body.filters.brands,['alpha']);assert.equal(published.length,0);
  calls[1].resolve({...categoryResearch,view:{...categoryResearch.view,version:4}});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(published.at(-1).view.version,4);
  const hidden=draftResearch(categoryResearch,{branch_actions:[{action:'hide',branch_id:'pen-branch'}]});
  assert.equal(hidden.query_categories[0].hidden,true);
  const shown=draftResearch(hidden,{branch_actions:[{action:'show',branch_id:'pen-branch'}]});
  assert.equal(shown.query_categories[0].hidden,false);assert.equal(shown.query_categories[1].hidden,true);
});

test('failed branch save reloads server state and rolls back the local hidden projection',async()=>{
  const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
  const published=[],errors=[];
  const writer=new ResearchViewWriter(async()=>{throw new Error('offline');},async()=>categoryResearch,v=>published.push(v),()=>{},e=>errors.push(e));
  writer.reset(categoryResearch);writer.enqueue({branch_actions:[{action:'hide',branch_id:'pen-branch'}]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(errors.length,1);assert.equal(published.at(-1).query_categories[0].hidden,false);
});

test('multifunction card uses its first remaining query branch as category caption',()=>{
  const {ProductCard}=load('src/components/product-card.tsx');
  const html=renderToStaticMarkup(React.createElement(ProductCard,{product:{id:'vto:hybrid',name:'Ручка и карандаш 2 в 1',category:'pen',branch_category:'pencil',primary_image:null,colors:[],price_kzt:500},selected:false,onSelect(){},onDetails(){}}));
  assert.match(html,/<span class="product-category">Карандаши<\/span>/);
});
