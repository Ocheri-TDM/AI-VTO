"""Main-API acceptance. Isolated exact-ID resources; cookies remain only in .local.

prepare -> run -> native Edge + inspect every page -> approve --reviewed -> cleanup
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import Text, cast, delete, func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'apps/api'))
from app.application.presentation_render import render_document
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
from app.database.models import Project, User
from app.domain.presentation import ACTIVE
from app.domain.presentation_materials import MaterialEdit

LEDGER = ROOT/'.local/title-acceptance.json'
SOURCES = ROOT/'apps/api/tests/fixtures/presentations/custom-materials/sources'


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare','run','concurrent','approve','verify','cleanup'])
    parser.add_argument('--reviewed', action='store_true')
    args=parser.parse_args()
    state=json.loads(LEDGER.read_text('utf-8')) if LEDGER.exists() else {'run':uuid4().hex,'users':[],'projects':[],'documents':[],'cases':{}}
    if args.phase=='prepare' and state['users']:
        raise SystemExit('Existing run must be cleaned first')
    output=ROOT/'.local/title-acceptance'/state['run']
    output.mkdir(parents=True,exist_ok=True)
    cfg=Settings()
    engine,sessions=create_database(cfg.database_url)
    def persist():
        LEDGER.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8')
    async def drafts(db):
        return dict((await db.execute(select(Document.id,func.md5(cast(Document.draft,Text))).where(Document.id.not_in(state['documents'])))).all())
    async def call(c,method,path,**kw):
        response=await c.request(method,path,**kw)
        response.raise_for_status()
        return None if response.status_code == 204 else response.json()
    async def get(c,id):
        return await call(c,'GET','/api/presentations/'+id)
    async def edit(c,doc,**settings):
        d=doc['draft']
        return await call(c,'PATCH','/api/presentations/'+doc['id'],json={'version':doc['version'],
            'settings':d['settings']|settings,'items':[{k:i[k] for k in ('offer_id','display_name','caption','image_url')} for i in d['items']],
            'category_order':d['category_order'],'original_fallback_ids':d['original_fallback_ids'],
            'materials':[{k:m[k] for k in MaterialEdit.model_fields} for m in d.get('materials',[])]})
    async def wait(c,id,revision,label):
        for _ in range(160):
            doc=await get(c,id)
            r=next(v for v in doc['revisions'] if v['id']==revision)
            if r['status'] not in ACTIVE:
                assert r['status'] in ('READY','NEEDS_REVIEW') and r['result'].get('pdf_url'),r
                break
            await asyncio.sleep(.3)
        else:
            raise TimeoutError(label)
        async with sessions() as db:
            row=await db.get(Revision,revision)
            item={'document':id,'revision':revision,'input':row.input,'result':row.result,'request_id':row.input.get('generation_request_id')}
        assert item['result']['design_plan']['pages'][0]['layout']=='title'
        assert item['result']['design_plan']['pages'][0]['product_refs']==[]
        assert [v['component'] for v in item['result']['component_trace'] if v['page']==1]==['title_logo']
        folder=output/label
        folder.mkdir(exist_ok=True)
        for stale in folder.glob('page-*.png'):
            stale.unlink()
        for page in r['result']['pages']:
            data=(await c.get(page['preview'])).content
            (folder/f"page-{page['page']}.png").write_bytes(data)
        state['cases'][label]=item
        persist()
        print(label,r['status'],item['result']['design_signature'],flush=True)
        return doc
    async def generate(c,id,label,duplicate=False):
        if label in state['cases'] and state['cases'][label]['result']['design_signature']['status'] == 'DISTINCT':
            return await get(c,id)
        doc=await get(c,id)
        body={'version':doc['version'],'request_id':str(uuid4())}
        if duplicate:
            values=await asyncio.gather(*(call(c,'POST','/api/presentations/'+id+'/generate',json=body) for _ in range(2)))
            assert values[0]['id']==values[1]['id']
            state['duplicate_http_same_revision']=True
            result=values[0]
        else:
            result=await call(c,'POST','/api/presentations/'+id+'/generate',json=body)
        return await wait(c,id,result['id'],label)
    try:
        clients=[httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=90,cookies=u['cookies']) for u in state['users']]
        if args.phase=='prepare':
            async with sessions() as db:
                state['other_drafts']=await drafts(db)
                state['catalog_before']=await db.scalar(text('SELECT count(*) FROM supplier_offers'))
            for n in range(2):
                c=httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=90)
                clients.append(c)
                u=await call(c,'POST','/api/auth/register',json={'name':f"title-{state['run']}-{n}",'password':secrets.token_urlsafe(24)})
                state['users'].append({'id':u['id'],'cookies':dict(c.cookies)})
                persist()
                chat=await call(c,'POST','/api/chats',json={'project_name':'title-'+state['run'][:8]})
                project=next(p for p in await call(c,'GET','/api/projects') if p['chat_id']==chat['id'])
                state['projects'].append(project['id'])
                persist()
                for offer in ['vto:1823109','vto:1823108','vto:1823107']:
                    col=await call(c,'POST',f"/api/projects/{project['id']}/collection/items",json={'offer_id':offer})
                doc=await call(c,'POST',f"/api/projects/{project['id']}/presentations",json={'collection_version':col['version']})
                state['documents'].append(doc['id'])
                persist()
                await edit(c,doc,style='premium-dark',auto_design=False,palette=['#163E52','#C9AB78'])
            print('Two isolated users/projects/documents prepared')
        elif args.phase=='run':
            c,foreign=clients
            id,second=state['documents']
            for n in range(5):
                await generate(c,id,'variant-'+str(n),duplicate=n==0)
            first=state['cases']['variant-0']
            signatures=[state['cases']['variant-'+str(n)]['result']['design_signature'] for n in range(5)]
            assert len({s['hash'] for s in signatures})==5
            assert all(s['minimum_feature_changes'] is None or s['minimum_feature_changes']>=2 for s in signatures)
            # A controlled technical failure of an already rendered disposable row.
            if 'retry' not in state['cases']:
                async with sessions.begin() as db:
                    row=await db.get(Revision,first['revision'])
                    row.status='FAILED'
                retry=await call(c,'POST',f'/api/presentations/{id}/generate',json={'version':999,'request_id':first['request_id']})
                assert retry['id']==first['revision']
                await wait(c,id,first['revision'],'retry')
            assert state['cases']['retry']['result']['sha256']==first['result']['sha256']
            assert state['cases']['retry']['input']==first['input']
            doc=await get(foreign,second)
            doc=await call(foreign,'POST',f"/api/presentations/{second}/logo?version={doc['version']}",content=(SOURCES/'logo.png').read_bytes())
            # Simultaneous isolated documents choose their own logo/seed/plan.
            await asyncio.gather(generate(c,id,'concurrent-default'),generate(foreign,second,'custom-active'))
            assert state['cases']['custom-active']['result']['resolved_logo']['source']=='custom'
            assert (await foreign.get(f'/api/presentations/{id}')).status_code==404
            doc=await get(foreign,second)
            await edit(foreign,doc,use_logo=False)
            await generate(foreign,second,'custom-disabled')
            assert state['cases']['custom-disabled']['result']['resolved_logo']['source']=='default'
            doc=await get(c,id)
            await edit(c,doc,client='Объединённая научно-производственная компания корпоративных решений и технологий',subtitle='Подборка для команды. Реальные варианты, фотографии и цены.')
            await generate(c,id,'long-company')
            for role,file in [('COVER_PHOTO','architecture.jpg'),('DECORATION','ornament.png')]:
                doc=await get(c,id)
                if any(m['role']==role and m['status']=='READY' for m in doc['draft'].get('materials',[])):
                    continue
                doc=await call(c,'POST',f"/api/presentations/{id}/materials?role={role}&version={doc['version']}",content=(SOURCES/file).read_bytes(),headers={'X-Filename':file})
                for _ in range(100):
                    doc=await get(c,id)
                    if not doc['materials_preparing']:
                        break
                    await asyncio.sleep(.15)
            d=doc['draft']
            for m in d['materials']:
                m.update(scope='COVER',enabled=True,crop=m['role']!='DECORATION')
            await edit(c,doc)
            await generate(c,id,'custom-art')
            # Broken active custom file is visibly rejected; frozen earlier logo survives.
            doc=await get(foreign,second)
            await edit(foreign,doc,use_logo=True)
            doc=await get(foreign,second)
            path=(cfg.presentation_storage/doc['draft']['settings']['logo']).resolve()
            assert path.is_relative_to((cfg.presentation_storage/'documents'/second).resolve())
            data=path.read_bytes()
            try:
                path.write_bytes(b'controlled broken disposable logo')
                response=await foreign.post(f'/api/presentations/{second}/generate',json={'version':doc['version'],'request_id':str(uuid4())})
                assert response.status_code==422
                state['broken_logo']={'status':422,'message':response.json()['detail'],'no_default_substitution':True}
            finally:
                path.write_bytes(data)
            await edit(foreign,doc,use_logo=False)
            # Replay frozen new input with actual prepared source assets.
            result=render_document(cfg.presentation_storage,'frozen-replay',first['input'],first['result']['assets'])
            assert result['sha256']==first['result']['sha256']
            state['frozen_replay_identical']=True
            persist()
        elif args.phase=='concurrent':
            c,foreign=clients
            first,second=state['documents']
            doc=await get(foreign,second)
            await edit(foreign,doc,use_logo=True)
            await asyncio.gather(generate(c,first,'parallel-default'),generate(foreign,second,'parallel-custom'))
            assert state['cases']['parallel-default']['result']['resolved_logo']['source']=='default'
            assert state['cases']['parallel-custom']['result']['resolved_logo']['source']=='custom'
            state['parallel_final_verified']=True
            persist()
        elif args.phase=='approve':
            if not args.reviewed:
                raise SystemExit('Inspect every final page before --reviewed')
            for label,item in state['cases'].items():
                c=clients[state['documents'].index(item['document'])]
                hashes=[v['hash'] for v in item['result']['assets'].values() if v['review_required']]
                await call(c,'POST',f"/api/presentations/{item['document']}/revisions/{item['revision']}/approve",json={'asset_hashes':hashes})
                pdf=await c.get(f"/api/presentations/{item['document']}/revisions/{item['revision']}/pdf?historical=true")
                pdf.raise_for_status()
                assert hashlib.sha256(pdf.content).hexdigest()==item['result']['sha256']
                (output/label/'presentation.pdf').write_bytes(pdf.content)
            state['reviewed']=True
            persist()
        elif args.phase=='verify':
            async with sessions() as db:
                assert await drafts(db)==state['other_drafts']
                state['catalog_after']=await db.scalar(text('SELECT count(*) FROM supplier_offers'))
            persist()
            print('Other drafts and catalog preserved')
        else:
            for c,project in zip(clients,state['projects'],strict=True):
                response=await c.delete('/api/projects/'+project)
                assert response.status_code in (204,404), response.status_code
            async with sessions.begin() as db:
                await db.execute(delete(User).where(User.id.in_([u['id'] for u in state['users']])))
            async with sessions() as db:
                assert await drafts(db)==state['other_drafts']
                for model,column,ids in [(User,User.id,[u['id'] for u in state['users']]),(Project,Project.id,state['projects']),(Document,Document.id,state['documents']),(Revision,Revision.document_id,state['documents'])]:
                    assert await db.scalar(select(func.count()).select_from(model).where(column.in_(ids)))==0
            for id in state['documents']:
                target=(cfg.presentation_storage/'documents'/id).resolve()
                assert target.parent==(cfg.presentation_storage/'documents').resolve()
                if target.exists():
                    shutil.rmtree(target)
            assert output.parent==(ROOT/'.local/title-acceptance').resolve()
            shutil.rmtree(output)
            LEDGER.unlink()
            print('Only exact-ID disposable users/projects/documents removed; other drafts preserved')
    finally:
        for c in clients:
            await c.aclose()
        await engine.dispose()


if __name__=='__main__':
    asyncio.run(main())
