"""Installed Edge acceptance of open listboxes, keyboard levels and saved settings."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationRevision
from app.domain.presentation_palette import contrast
from check_palette_browser import Browser


async def main():
    ledger=ROOT/'.local/title-acceptance.json'
    state=json.loads(ledger.read_text('utf-8'))
    output=ROOT/'.local/title-acceptance'/state['run']
    b=Browser()
    await b.connect(state['users'][0]['cookies'])
    try:
        await b.call('Page.navigate',{'url':'about:blank'})
        await b.call('Page.navigate',{'url':'http://127.0.0.1:3000/#presentation='+state['documents'][0]})
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        await b.click('Подобрать сочетание')
        await b.wait("document.querySelectorAll('dialog[open] .palette-card').length>0")
        await b.js("document.querySelector('dialog details').open=true")
        evidence={}
        for label in ['Ведущее направление','Характер','Применение']:
            expression="Array.from(document.querySelectorAll('[role=combobox]')).find(b=>document.getElementById(b.getAttribute('aria-labelledby').split(' ')[0]).textContent==="+json.dumps(label)+')'
            await b.js(expression+'.focus();'+expression+'.click()')
            await b.wait("!!document.querySelector('[role=combobox][aria-expanded=true]')&&!!document.querySelector('.palette-explorer .ui-select-popup:popover-open')")
            data=await b.js("""(()=>{const list=document.querySelector('.palette-explorer .ui-select-popup:popover-open'),r=list.getBoundingClientRect();return {rect:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{w:innerWidth,h:innerHeight},options:[...list.querySelectorAll('[role=option]')].map(o=>{const s=getComputedStyle(o);return {text:o.textContent,color:s.color,background:s.backgroundColor==='rgba(0, 0, 0, 0)'?getComputedStyle(list).backgroundColor:s.backgroundColor,opacity:s.opacity,disabled:o.getAttribute('aria-disabled'),selected:o.getAttribute('aria-selected')}})}})()""")
            def hexcolor(rgb):
                return '#'+''.join(f'{int(n):02X}' for n in rgb.removeprefix('rgb(').removesuffix(')').split(','))
            scores=[contrast(hexcolor(o['color']),hexcolor(o['background'])) for o in data['options'] if o['disabled']!='true']
            assert min(scores)>=4.5
            assert data['rect']['y']>=0 and data['rect']['y']+data['rect']['height']<=data['viewport']['h']
            data['minimum_contrast']=min(scores)
            await b.shot(output/('dropdown-'+str(len(evidence))+'.png'))
            await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'ArrowDown','code':'ArrowDown','windowsVirtualKeyCode':40})
            await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
            await b.wait("!!document.querySelector('dialog[open]')&&!document.querySelector('.palette-explorer .ui-select-popup:popover-open')")
            assert await b.js("document.activeElement.getAttribute('role')==='combobox'")
            await b.js(expression+'.focus();'+expression+'.click()')
            await b.wait("!!document.querySelector('[role=combobox][aria-expanded=true]')")
            await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'End','code':'End','windowsVirtualKeyCode':35})
            await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Enter','code':'Enter','windowsVirtualKeyCode':13})
            await b.wait("!document.querySelector('.palette-explorer .ui-select-popup:popover-open')")
            evidence[label]=data
        # Explicit apply and F5, with every filter still using the original API semantics.
        await b.click('Отмена',"document.querySelector('dialog')")
        await b.click('Подобрать сочетание')
        await b.wait("document.querySelectorAll('dialog[open] .palette-card').length>0")
        await b.js("document.querySelector('.palette-card').click()")
        await b.click('Применить палитру')
        await b.wait("fetch('/api/presentations/"+state['documents'][0]+"').then(r=>r.json()).then(d=>!!d.draft.settings.palette_plan)")
        await asyncio.sleep(.9)
        before=await b.js("fetch('/api/presentations/"+state['documents'][0]+"').then(r=>r.json()).then(d=>d.draft.settings.palette_plan)")
        await b.call('Page.reload')
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        after=await b.js("fetch('/api/presentations/"+state['documents'][0]+"').then(r=>r.json()).then(d=>d.draft.settings.palette_plan)")
        assert after==before
        await b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
        await b.click('Подобрать сочетание')
        await b.wait("document.querySelectorAll('dialog[open] .palette-card').length>0")
        await b.js("document.querySelector('dialog details').open=true;document.querySelectorAll('.palette-explorer-filters [role=combobox]')[2].scrollIntoView({block:'center'});document.querySelectorAll('.palette-explorer-filters [role=combobox]')[2].click()")
        await b.wait("!!document.querySelector('.palette-explorer .ui-select-popup:popover-open')")
        assert await b.js("(()=>{let r=document.querySelector('.palette-explorer .ui-select-popup:popover-open').getBoundingClientRect();return r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight})()")
        await b.shot(output/'dropdown-mobile.png')
        await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
        await b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
        assert await b.js("document.querySelector('dialog').contains(document.activeElement)")
        await b.click('Отмена',"document.querySelector('dialog')")
        result={'desktop_lists':evidence,'mobile_popup_in_viewport':True,'keyboard_arrow_enter_escape_tab':True,'escape_preserves_dialog':True,'focus_returns_to_trigger':True,'palette_apply_f5':True}
        # The second user's current document has the active custom logo.
        await b.ws.close()
        b=Browser()
        await b.connect(state['users'][1]['cookies'])
        identifier=state['documents'][1]
        await b.call('Page.navigate',{'url':'about:blank'})
        await b.call('Page.navigate',{'url':'http://127.0.0.1:3000/#presentation='+identifier})
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',cookies=state['users'][1]['cookies']) as client:
            doc=(await client.get('/api/presentations/'+identifier)).json()
        assert doc['draft']['settings']['use_logo']
        logo=(Settings().presentation_storage/doc['draft']['settings']['logo']).resolve()
        assert logo.is_relative_to((Settings().presentation_storage/'documents'/identifier).resolve())
        original=logo.read_bytes()
        try:
            logo.write_bytes(b'controlled broken test logo')
            await b.click('Сгенерировать PDF')
            await b.wait("[...document.querySelectorAll('.presentation-error')].some(p=>p.textContent.includes('клиентский логотип'))")
            await b.shot(output/'broken-logo-visible.png')
        finally:
            logo.write_bytes(original)
        downloads=output/'browser-download'
        downloads.mkdir(exist_ok=True)
        for old in downloads.glob('*.pdf'):
            old.unlink()
        await b.call('Browser.setDownloadBehavior',{'behavior':'allow','downloadPath':str(downloads)})
        await b.js("document.querySelector('.presentation-preview a[download]').click()")
        for _ in range(100):
            files=list(downloads.glob('*.pdf'))
            if files:
                break
            await asyncio.sleep(.1)
        assert len(files)==1
        digest=hashlib.sha256(files[0].read_bytes()).hexdigest()
        assert digest==state['cases']['parallel-custom']['result']['sha256']
        result['native_download_sha256']=digest
        result['broken_active_logo_visible_error']=True
        oldsrc=await b.js("document.querySelector('.presentation-page').getAttribute('src')")
        count=len(doc['revisions'])
        await b.click('Сгенерировать PDF')
        assert await b.js("document.querySelector('.presentation-page').getAttribute('src')") == oldsrc
        await b.wait("fetch('/api/presentations/"+identifier+"').then(r=>r.json()).then(d=>d.revisions.length>"+str(count)+"&&d.revisions[0].status==='READY')",90)
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',cookies=state['users'][1]['cookies']) as client:
            fresh=(await client.get('/api/presentations/'+identifier)).json()
            revision=fresh['revisions'][0]
            folder=output/'browser-generated'
            folder.mkdir(exist_ok=True)
            for page in revision['result']['pages']:
                (folder/f"page-{page['page']}.png").write_bytes((await client.get(page['preview'])).content)
        engine,sessions=create_database(Settings().database_url)
        async with sessions() as db:
            row=await db.get(PresentationRevision,revision['id'])
            state['cases']['browser-generated']={'document':identifier,'revision':row.id,'input':row.input,'result':row.result,'request_id':row.input.get('generation_request_id')}
        await engine.dispose()
        result['previous_preview_retained']=True
        # Restore a saved older revision, then verify F5 keeps that exact choice.
        older=state['cases']['custom-active']['revision']
        await b.select_revision(identifier,older)
        await b.call('Page.reload')
        await b.wait("!!document.querySelector('.presentation-page') && sessionStorage.getItem('presentation-revision:'+"+json.dumps(identifier)+")==="+json.dumps(older))
        result['history_revision_f5']=True
        ledger.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8')
        (output/'browser-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print('Native Edge: all three open dropdowns, contrast, keyboard, mobile, apply and F5 PASS')
    finally:
        await b.ws.close()


if __name__=='__main__':
    asyncio.run(main())
