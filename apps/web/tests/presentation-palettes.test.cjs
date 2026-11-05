/* eslint-disable @typescript-eslint/no-require-imports -- Isolated component/state harness. */
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/load.cjs');

const {manualPalette,appendPalettes}=load('src/lib/presentation-palettes.ts');
const {PaletteCard,PaletteExplorer}=load('src/components/presentations/palette-explorer.tsx');
const {PaletteSelect}=load('src/components/presentations/palette-select.tsx');

test('shared palette select exposes named combobox/listbox, selected and real disabled options',()=>{
  const html=renderToStaticMarkup(React.createElement(PaletteSelect,{label:'Направление',value:'red',options:[{value:'red',label:'Красное'},{value:'locked',label:'Недоступное',disabled:true}],onChange(){throw Error('No implicit selection');}}));
  assert.match(html,/role="combobox"/);assert.match(html,/role="listbox"/);assert.match(html,/aria-selected="true"/);assert.match(html,/aria-disabled="true"/);assert.ok(html.includes('lucide-check'));assert.ok(!html.includes('<select'));
  const empty=renderToStaticMarkup(React.createElement(PaletteSelect,{label:'Пусто',value:'',options:[],onChange(){}}));assert.ok(empty.includes('Ничего не найдено'));
});
test('shared published fixture is consumed as authoritative roles without a second color algorithm',()=>{
  const fixture=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../api/tests/fixtures/presentations/palette-explorer/library-v1.json'),'utf8'));
  assert.ok(fixture.palettes.length>=300);
  for(const palette of [fixture.palettes[0],fixture.palettes[100],fixture.palettes.at(-1)]){
    const html=renderToStaticMarkup(React.createElement(PaletteCard,{palette,dark:true,selected:false,onSelect(){}}));
    assert.ok(html.includes(palette.roles.dark_background));assert.ok(html.includes(palette.roles.dark_text));assert.ok(html.includes(palette.primary));
  }
});
const palette={preset_id:'red-1',origin_preset_id:null,name:'Бархат',primary:'#AE3044',colors:['#AE3044','#33141B'],manual:false,locked:true,character:'calm',direction:'red',roles:{dark_background:'#33141B',dark_text:'#FAF5F2',dark_muted:'#E4D9D4',dark_accent:'#D9B195',dark_peak:'#51232D',light_background:'#FAF4F5',light_text:'#27171B',light_muted:'#624B51',light_accent:'#703543',light_surface:'#EBD7DB'}};
test('manual editing retains preset provenance and marks the exact primary without mutating a saved plan',()=>{
  const theme={palette:palette.colors,palette_plan:palette},result=manualPalette(theme,['#17A06A','#283F35']);
  assert.equal(result.palette_plan.name,'Своя палитра');assert.equal(result.palette_plan.origin_preset_id,'red-1');assert.equal(result.palette_plan.primary,'#17A06A');assert.equal(result.palette_plan.preset_id,null);
  assert.equal(theme.palette_plan.preset_id,'red-1');assert.equal(theme.palette_plan.primary,'#AE3044');
});
test('more combinations appends unique entries without replacing the current selected object',()=>{
  const selected=palette,old=[palette],next={...palette,preset_id:'red-2'};
  const result=appendPalettes(old,[palette,next]);assert.equal(result.length,2);assert.equal(result[0],selected);assert.equal(old.length,1);
});
test('schematic card uses server roles directly for both styles and never calls it PDF preview',()=>{
  for(const dark of [true,false]){
    const html=renderToStaticMarkup(React.createElement(PaletteCard,{palette,dark,selected:true,onSelect(){}}));
    assert.ok(html.includes(palette.roles[dark?'dark_background':'light_background']));assert.ok(html.includes(palette.roles[dark?'dark_text':'light_text']));assert.ok(html.includes('aria-pressed="true"'));assert.ok(!html.includes('<img'));
  }
});
test('library opens as a cancelable dialog with an explicit disabled apply action and no render request',()=>{
  const html=renderToStaticMarkup(React.createElement(PaletteExplorer,{current:null,dark:true,onApply(){throw Error('No implicit apply');},onClose(){}}));
  assert.ok(html.includes('<dialog'));assert.ok(html.includes('Отмена'));assert.ok(html.includes('Схема')||html.includes('схема оформления, не PDF-preview'));assert.match(html,/disabled=""[^>]*>Применить палитру/);
});
