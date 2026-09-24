import type { Research } from '@/lib/research';

export function ResearchProgress({research}:{research:Research}) {
  if(research.source_mode==='index') return <div className="research-progress" aria-live="polite">
    <span>{['QUEUED','RUNNING'].includes(research.status)?'Обновление каталога выполняется в фоне. Текущие результаты доступны.':
      'Каталог обновляется автоматически.'}</span>
  </div>;
  const suppliers=[...new Set(research.coverage.map(b=>b.supplier))];
  return <div className="research-progress" aria-live="polite">{suppliers.map(s=>{
    const branches=research.coverage.filter(b=>b.supplier===s);
    const complete=branches.every(b=>b.status==='COMPLETE');
    const issue=branches.some(b=>['FAILED','CAPTCHA','TIMEOUT'].includes(b.status));
    return <div key={s}><strong>{s==='artegifts'?'ArteGifts':s==='happygifts'?'HappyGifts':s}</strong><span>
      {complete?'Завершено':issue?'Часть данных недоступна':'Исследование не завершено'} · {branches.reduce((n,b)=>n+b.products_seen,0)} просмотрено
    </span></div>;
  })}</div>;
}
