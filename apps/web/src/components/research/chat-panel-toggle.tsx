'use client';
import type {Ref} from 'react';
import {MessageSquare,PanelRightClose} from 'lucide-react';

export function ChatPanelToggle({expanded,onClick,buttonRef,mobile=false}:{expanded:boolean;onClick:()=>void;buttonRef?:Ref<HTMLButtonElement>;mobile?:boolean}){
  const Icon=expanded?PanelRightClose:MessageSquare;
  const label=expanded?'Свернуть чат':'Открыть чат';
  return <button type="button" ref={buttonRef} className={`studio-button workspace-chat-toggle${expanded?' chat-panel-collapse':' research-chat-reopen'}`}
    aria-label={label} title={label} aria-expanded={expanded} aria-controls="workspace-chat" aria-haspopup={!expanded&&mobile?'dialog':undefined}
    data-chat-collapse={expanded?'true':undefined} onClick={onClick}>
    <Icon size={20} aria-hidden="true"/><span>{expanded?'Свернуть':'Чат'}</span>
  </button>;
}
