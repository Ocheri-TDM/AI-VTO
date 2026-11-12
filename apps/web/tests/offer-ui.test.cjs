/* eslint-disable @typescript-eslint/no-require-imports */
const test=require('node:test');
const assert=require('node:assert/strict');
const {load}=require('./helpers/load.cjs');
const {copyOfferLink}=load('src/lib/commercial-offers.ts');

test('client link copy waits for Clipboard completion and never mutates publication',async()=>{
  const previous=global.navigator;let resolve,calls=[];
  Object.defineProperty(global,'navigator',{configurable:true,value:{clipboard:{writeText:value=>{calls.push(value);return new Promise(r=>resolve=r);}}}});
  try{
    const share=Object.freeze({active:true,public_url:'https://example.com/offers/test-capability'});
    let success=false;const pending=copyOfferLink(share).then(()=>success=true);
    await Promise.resolve();assert.equal(success,false);assert.deepEqual(calls,[share.public_url]);
    resolve();await pending;assert.equal(success,true);
    navigator.clipboard.writeText=async()=>{throw new DOMException('private details','NotAllowedError');};
    await assert.rejects(copyOfferLink(share),{message:'Не удалось скопировать ссылку. Повторите попытку.'});
    assert.equal(share.active,true);
  }finally{Object.defineProperty(global,'navigator',{configurable:true,value:previous});}
});

test('copy refuses inactive, local, editor and API addresses before Clipboard access',async()=>{
  for(const public_url of [null,'http://example.com/offers/key','https://localhost/offers/key','https://127.0.0.1/offers/key','https://[::1]/offers/key','https://example.com/offers/key/edit','https://example.com/api/public/offers/key','https://example.com/offers/key?other=true','https://user:pass@example.com/offers/key']){
    await assert.rejects(copyOfferLink({active:true,public_url}),/ссылка недоступна/);
  }
  await assert.rejects(copyOfferLink({active:false,public_url:'https://example.com/offers/key'}),/ссылка недоступна/);
});

test('logout sends valid JSON through the shared validation layer and accepts 204',async()=>{
  const original=global.fetch;
  global.fetch=async(url,options)=>{
    assert.equal(url,'/api/auth/logout');assert.equal(options.method,'POST');
    assert.equal(options.headers['Content-Type'],'application/json');
    assert.deepEqual(JSON.parse(options.body),{});assert.equal(options.credentials,'include');
    return new Response(null,{status:204});
  };
  try{const {api}=load('src/lib/api.ts');assert.equal(await api.logout(),undefined);}
  finally{global.fetch=original;}
});
