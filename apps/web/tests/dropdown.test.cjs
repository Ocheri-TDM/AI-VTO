/* eslint-disable @typescript-eslint/no-require-imports -- Node component tests use the actual shared controls. */
const test = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {load} = require('./helpers/load.cjs');
const {Select, SearchableSelect, MultiSelect} = load('src/components/common/select.tsx');
const {dropdownGeometry, filterOptions} = load('src/lib/dropdown.ts');
const options = [{value:'raw:first',label:'Длинное название',count:12},{value:'',label:'Бренд не указан',count:3},{value:'locked',label:'Molti',disabled:true}];
const props = {label:'Выбор',value:'raw:first',options,onChange(){throw Error('SSR cannot change a form');}};

test('single select preserves the exact value, form name, accessible label and selected mark', () => {
  const html = renderToStaticMarkup(React.createElement(Select,{...props,name:'material'}));
  assert.match(html,/role="combobox"/); assert.match(html,/aria-controls=/); assert.match(html,/for="[^"]+-trigger"/);
  assert.match(html,/name="material" value="raw:first"/); assert.match(html,/aria-selected="true"/);
  assert.match(html,/aria-disabled="true"/); assert.match(html,/popover="manual" hidden=""/);
  assert.match(html,/lucide-check/); assert.doesNotMatch(html,/<select/);
});
test('searchable select has a labeled input controlling its listbox without changing IDs', () => {
  const html = renderToStaticMarkup(React.createElement(SearchableSelect,props));
  assert.match(html,/aria-autocomplete="list"/); assert.match(html,/Поиск: Выбор/);
  assert.match(html,/role="listbox"/); assert.match(html,/Длинное название/);
});
test('brand multiselect uses labeled real checkboxes, separate count cells and no nested options', () => {
  const html = renderToStaticMarkup(React.createElement(MultiSelect,{...props,value:['raw:first',''],name:'brands'}));
  assert.match(html,/aria-haspopup="dialog"/); assert.match(html,/role="group"/);
  assert.equal((html.match(/checked=""/g)||[]).length,2);
  assert.match(html,/Бренд не указан/); assert.match(html,/class="ui-select-count">12/);
  assert.equal((html.match(/name="brands"/g)||[]).length,2);
  assert.doesNotMatch(html,/role="option"|role="listbox"/);
});
test('empty and disabled controls remain explicit and all instances have distinct label IDs', () => {
  const html = renderToStaticMarkup(React.createElement('div',null,React.createElement(Select,{...props,disabled:true,options:[]}),React.createElement(Select,props)));
  assert.match(html,/disabled=""/); assert.match(html,/Ничего не найдено/);
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map(m=>m[1]); assert.equal(new Set(ids).size,ids.length);
});
test('searching labels never loses the unknown brand value or rewrites supplier IDs', () => {
  assert.equal(filterOptions(options,' НАЗВАНИЕ ')[0],options[0]);
  assert.equal(filterOptions(options,'не указан')[0].value,'');
  assert.deepEqual(filterOptions(options,'absent'),[]); assert.equal(options.length,3);
});
test('popup flips upward near the bottom and caps long material widths to the mobile viewport', () => {
  const g=dropdownGeometry({left:310,top:700,bottom:740,width:150},{width:390,height:844},340,360);
  assert.equal(g.placement,'top'); assert.ok(g.left>=12); assert.ok(g.left+g.width<=378);
  assert.ok(g.top>=12); assert.ok(g.top+g.maxHeight<=700);
});
test('short menus prefer below and zoomed viewports keep popup edges inside visible bounds', () => {
  const short=dropdownGeometry({left:20,top:100,bottom:140,width:120},{width:1440,height:900},160,100);
  assert.equal(short.placement,'bottom'); assert.equal(short.width,160); assert.equal(short.top,146);
  const zoom=dropdownGeometry({left:500,top:200,bottom:240,width:140},{width:350,height:400,left:150,top:100},280,360);
  assert.ok(zoom.left>=162); assert.ok(zoom.left+zoom.width<=488); assert.ok(zoom.top>=112);
});
