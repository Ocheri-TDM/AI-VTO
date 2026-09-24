"""Public browser reconnaissance. No accounts, no CAPTCHA bypass."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

SITES={'oasis':'https://www.oasiscatalog.com/','ucontay':'https://ucontay.kz/', 'gifts':'https://gifts.ru/', 'portobello':'https://portobello.ru/', 'happygifts':'https://happygifts.ru/', 'artegifts':'https://kz.artegifts.by/'}

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--site');parser.add_argument('--url');parser.add_argument('--label',default='home');args=parser.parse_args()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch();semaphore=asyncio.Semaphore(2)
        async def inspect(name,url):
            async with semaphore:
                context=await browser.new_context(locale='ru-RU');page=await context.new_page();network=[]
                page.on('response',lambda r:network.append({'url':r.url,'status':r.status}) if r.request.resource_type in ('xhr','fetch') else None)
                folder=Path('.local/research/six')/name;folder.mkdir(parents=True,exist_ok=True)
                try:
                    response=await page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    if response:
                        try:(folder/f'{args.label}-response.html').write_text(await response.text(),encoding='utf-8')
                        except Exception:pass
                    await page.wait_for_timeout(1500)
                    data=await page.evaluate('''()=>({url:location.href,title:document.title,forms:[...document.forms].map(f=>f.outerHTML.slice(0,8000)),inputs:[...document.querySelectorAll('input')].map(x=>x.outerHTML),links:[...document.querySelectorAll('a[href]')].map(x=>({href:x.href,text:x.innerText,cls:x.className})),scripts:[...document.scripts].map(x=>({src:x.src,type:x.type,text:x.src?'':x.textContent})),body:document.body.innerText})''')
                    data['network']=network;data['status']=response.status if response else None
                    (folder/f'{args.label}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                    (folder/f'{args.label}.html').write_text(await page.content(),encoding='utf-8')
                    print(name,data['status'],data['url'],data['title'],flush=True)
                except Exception as error:
                    (folder/f'{args.label}-error.txt').write_text(str(error),encoding='utf-8');print(name,type(error).__name__,str(error)[:180],flush=True)
                finally:await context.close()
        await asyncio.gather(*(inspect(name,args.url or url) for name,url in SITES.items() if not args.site or name==args.site))
        await browser.close()

if __name__=='__main__':asyncio.run(main())
