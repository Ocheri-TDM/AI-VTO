/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');
const {CategoryCharacteristics,ResearchFilterPanel}=load('src/components/research/research-filters.tsx');
const {ResearchViewWriter}=load('src/lib/research-view-writer.ts');
const {dropdownPage,filterOptions}=load('src/lib/dropdown.ts');
const {hasAdditionalFilters}=load('src/components/research/research-hud.tsx');
const filters={brands:[],colors:[],materials:[],sizes:[],categories:[],attributes:{},excluded_terms:[],required_terms:[],excluded_materials:[],origins:{},selected_only:false,minimum_quantity:null,min_price:null,max_price:null,branch_attributes:{}};
const empty={mode:'none',values:[],number:null,exclusive:false};
const fieldState=Object.fromEntries(['brands','colors','materials','sizes','minimum_quantity','min_price','max_price'].map(k=>[k,empty]));
const attribute=(id,label,values)=>({id,label,type:'number',unit:'мл',source_value:null,selected_values:[],has_more:false,total_values:values.length,values:values.map(([value,count])=>({value,label:value+' мл',number:Number(value),count,selected:false,disabled:false}))});
const research={id:'search-a',chat_id:'chat',view:{filters,version:0,selected_ids:[]},intent:{brands:[]},facets:{brand:[],color:[],material:[],size:[]},effective_filters:{fields:fieldState,branches:[]},query_categories:[{id:'b',hidden:false},{id:'m',hidden:false},{id:'u',hidden:false}],category_facets:[
  {branch_id:'b',category:'bottle',label:'Бутылки',attributes:[attribute('capacity_ml','Объём, мл',[['500',103],['750',2]])]},
  {branch_id:'m',category:'mug',label:'Кружки',attributes:[attribute('capacity_ml','Объём, мл',[['350',1]])]},
],products:[],selected_products:[]};

test('multi-category characteristics identify each branch and never render the untyped size',()=>{
 const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research,onChange(){},onEffectiveChange(){},disabled:false}));
 assert.match(html,/Характеристики категорий/);assert.match(html,/Бутылки/);assert.match(html,/Кружки/);
 assert.match(html,/500 мл/);assert.match(html,/>103</);assert.doesNotMatch(html,/Размер|A5|JINGLE|V2/);
 assert.match(html,/data-filter-branch="b"/);assert.match(html,/data-filter-branch="m"/);
});

test('a single known group remains labeled when other categories lack published attributes',()=>{
 const html=renderToStaticMarkup(React.createElement(CategoryCharacteristics,{research:{...research,category_facets:[research.category_facets[0]]},onChange(){},disabled:false}));
 assert.match(html,/Характеристики категорий/);assert.match(html,/Бутылки/);
});

test('a pending search hides old facet controls instead of offering stale options',()=>{
 const html=renderToStaticMarkup(React.createElement(ResearchFilterPanel,{research,onChange(){},disabled:true,loading:true}));
 assert.match(html,/role="status"/);assert.match(html,/Загружаю фильтры/);
 assert.doesNotMatch(html,/role="option"|500 мл|Объём|Характеристики/);
});

test('changing one characteristic preserves global filters and the other branch',()=>{
 let sent;
 const value={...research,view:{...research.view,filters:{...filters,colors:['DARK_BLUE'],minimum_quantity:200,branch_attributes:{m:{capacity_ml:['350']}}}}};
 const tree=CategoryCharacteristics({research:value,onChange:next=>sent=next,disabled:false});
 const branch=tree.props.children[1][0];
 const control=branch.props.children[1].props.children[0].props.children[0];
 control.props.onChange('500');
 assert.deepEqual(sent.colors,['DARK_BLUE']);assert.equal(sent.minimum_quantity,200);
 assert.deepEqual(sent.branch_attributes,{m:{capacity_ml:['350']},b:{capacity_ml:['500']}});
 assert.deepEqual(value.view.filters.branch_attributes,{m:{capacity_ml:['350']}});
});

test('selected zero stays visible and removable without offering arbitrary supplier text',()=>{
 const value=structuredClone(research);const a=value.category_facets[0].attributes[0];
 a.selected_values=['9999'];a.values.push({value:'9999',label:'9999 мл',number:9999,count:0,selected:true,disabled:false});
 const html=renderToStaticMarkup(React.createElement(CategoryCharacteristics,{research:value,onChange(){},disabled:false}));
 assert.match(html,/9999 мл/);assert.match(html,/Снять дополнительное условие/);
 assert.match(html,/aria-selected="true" aria-disabled="false"/);
});

test('a late facet-bearing write from the previous search cannot replace a new search',async()=>{
 let finish;const published=[];
 const writer=new ResearchViewWriter(()=>new Promise(resolve=>finish=resolve),async()=>research,v=>published.push(v),()=>{},()=>{});
 writer.reset(research);writer.enqueue({filters:{...filters,branch_attributes:{b:{capacity_ml:['500']}}}});
 writer.reset({...research,id:'search-b',category_facets:[],facet_context:{version:'new-context'}});
 finish({...research,facet_context:{version:'old-context'}});await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(published,[]);
});

test('large contextual option lists expose continuation, search all values, and pin selected zero',()=>{
 const options=Array.from({length:130},(_,i)=>({value:String(i),label:'Объём '+i,count:i}));
 assert.equal(dropdownPage(options,[]).items.length,60);assert.equal(dropdownPage(options,[]).remaining,70);
 assert.equal(dropdownPage(options,[],120).remaining,10);assert.equal(dropdownPage(options,[],180).remaining,0);
 const pinned=dropdownPage(options,['129']);assert.equal(pinned.items.at(-1).value,'129');assert.equal(pinned.remaining,69);
 assert.equal(dropdownPage(filterOptions(options,'129'),[]).items[0].value,'129');
});

test('nested category conditions enable reset; empty retained scopes do not',()=>{
 assert.equal(hasAdditionalFilters({...filters,branch_attributes:{b:{capacity_ml:['500']}}}),true);
 assert.equal(hasAdditionalFilters({...filters,branch_attributes:{b:{capacity_ml:[]}}}),false);
});
