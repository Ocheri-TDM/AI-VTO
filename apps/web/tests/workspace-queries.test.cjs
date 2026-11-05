/* eslint-disable @typescript-eslint/no-require-imports -- Compile the real component and exercise its event handlers without a browser dependency. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const ts=require('typescript');

for(const oldFails of [false,true])test(`history ignores an older ${oldFails?'error':'response'} after another activation`,async()=>{
  const filename=path.join(__dirname,'../src/components/research/workspace-queries.tsx');
  const mod=new Module(filename,module);mod.filename=filename;mod.paths=module.paths;
  const pending=[],published=[],errors=[];let state=0;
  const hookStates=[true,[{id:'old',query_text:'old'},{id:'new',query_text:'new'}]];
  const realRequire=mod.require.bind(mod);
  mod.require=id=>id==='react'?{useRef:()=>({current:0}),useState:()=>[hookStates[state++],()=>{}],useEffect:fn=>fn()}:
    id==='@/lib/project-queries'?{workspaceApi:{activate:(_,query)=>new Promise((resolve,reject)=>pending.push({query,resolve,reject}))}}:
    id==='../common/sheet'?{Sheet:()=>null}:realRequire(id);
  mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
  const tree=mod.exports.WorkspaceQueries({projectId:'project',onResearch:r=>published.push(r),onError:e=>errors.push(e)});
  const buttons=tree.props.children[1].props.children.props.children;
  const first=buttons[0].props.onClick();const second=buttons[1].props.onClick();
  pending[1].resolve({research:{id:'new'}});await second;
  if(oldFails)pending[0].reject(new Error('stale conflict'));else pending[0].resolve({research:{id:'old'}});
  await first;
  assert.deepEqual(published,[{id:'new'}]);assert.deepEqual(errors,[]);
});
