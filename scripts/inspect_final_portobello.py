import json
from pathlib import Path
from html.parser import HTMLParser


class Parser(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.data=''
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('id')=='ng-state':self.active=True
    def handle_data(self,value):
        if self.active:self.data+=value
    def handle_endtag(self,tag):
        if tag=='script':self.active=False


parser=Parser();parser.feed(Path('.local/research/six/portobello/bottles.html').read_text(encoding='utf-8'))
state=json.loads(parser.data)['apollo.state']
for k,v in state['ROOT_QUERY'].items():
    if k.startswith('Catalog_Products_Paginator('):
        print(k,json.dumps(v,ensure_ascii=False)[:2500])
        ref=v.get('items',[{}])[0].get('__ref')
        print(ref,json.dumps(state.get(ref,{}),ensure_ascii=False)[:2500])
