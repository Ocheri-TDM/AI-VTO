'use client';
import type {Ref} from 'react';
import {ListChecks,PanelRightClose} from 'lucide-react';

export function CollectionPanelToggle({expanded,onClick,buttonRef,mobile=false,collapse=false,count=null}:{
  expanded:boolean;onClick:()=>void;buttonRef?:Ref<HTMLButtonElement>;mobile?:boolean;collapse?:boolean;count?:number|null;
}){
  const Icon=collapse?PanelRightClose:ListChecks;
  const label=expanded?'Свернуть подборку':'Открыть подборку';
  return <button type="button" ref={buttonRef} className={`studio-button workspace-collection-toggle${collapse?' collection-panel-collapse':''}`}
    aria-label={label} title={label} aria-expanded={expanded} aria-controls="workspace-collection" aria-haspopup={!expanded&&mobile?'dialog':undefined}
    data-collection-collapse={collapse?'true':undefined} onClick={onClick}>
    <Icon size={20} aria-hidden="true"/><span>{collapse?'Свернуть':'Подборка'}</span>
    {!collapse&&<span className="workspace-collection-count" aria-live="polite" aria-label={count===null?'Подборка загружается':`Выбрано товаров: ${count}`}>{count??'…'}</span>}
  </button>;
}
