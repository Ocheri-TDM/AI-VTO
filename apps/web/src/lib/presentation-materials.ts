import type {PresentationDraft,PresentationMaterial,MaterialUsage} from './presentations';

const fields=['enabled','scope','expression','position','crop','focal_x','focal_y','opacity','harmonize','required','description'] as const;

export function normalizePresentationDraft(draft:PresentationDraft):PresentationDraft{
  return {...draft,materials:draft.materials??[],settings:{...draft.settings,style:draft.settings.style??'editorial',
    style_version:draft.settings.style_version??2,design_seed:draft.settings.design_seed??0,auto_design:draft.settings.auto_design??false}};
}

// Preparation publications may arrive while the user edits a name or material
// setting. Merge technical metadata without dropping those edits or reviving a
// locally removed file. A server-side replacement may remove its old asset ID.
export function mergePreparedMaterials(local:PresentationDraft,server:PresentationDraft,confirmed:PresentationDraft):PresentationDraft{
  const previous=new Set((confirmed.materials??[]).map(m=>m.id));
  const current=new Map((local.materials??[]).map(m=>[m.id,m]));
  const materials=(server.materials??[]).filter(m=>!previous.has(m.id)||current.has(m.id)).map(m=>{
    const edit=current.get(m.id);if(!edit)return m;
    const merged={...m};for(const key of fields)Object.assign(merged,{[key]:edit[key]});
    if(m.status==='FAILED')merged.enabled=false;
    return merged;
  });
  return {...local,materials};
}

export function materialUsageLabel(material:PresentationMaterial,usage?:MaterialUsage,previous=false){
  if(material.status==='FAILED')return material.error??'Не удалось подготовить файл';
  if(!material.enabled)return 'Выключен пользователем';
  if(material.status!=='READY')return 'Подготовка в очереди…';
  if(!usage)return 'Готов к применению. Обновите PDF.';
  const prefix=previous?'Предыдущая PDF: ':'';
  if(usage.status==='USED')return prefix+(usage.pages.length===1&&usage.pages[0]===1&&material.scope==='COVER'
    ?'Использован на обложке':`Использован на страницах: ${usage.pages.join(', ')}`);
  return prefix+`Не использован: ${usage.reason??'нет подходящей области'}`;
}
