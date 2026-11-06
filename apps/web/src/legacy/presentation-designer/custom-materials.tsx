'use client';
/* eslint-disable @next/next/no-img-element -- Thumbnails are private authenticated document files. */
import {useState} from 'react';
import {Select} from '@/components/common/select';
import type {MaterialRole,MaterialUsage,PresentationMaterial} from '@/lib/presentations';
import {materialUsageLabel} from '@/lib/presentation-materials';

const roles:{role:MaterialRole;name:string;help:string}[]=[
  {role:'COVER_PHOTO',name:'Фотография обложки',help:'Фото или иллюстрация в фоновой области обложки или товарных фото. Не заменяет товары.'},
  {role:'BACKGROUND',name:'Фон / текстура',help:'Под содержимым. Читаемость текста и фотографий сохраняется.'},
  {role:'DECORATION',name:'Декоративные элементы',help:'Прозрачные детали на полях и в свободных зонах.'},
];

export function CustomMaterials({materials,usage,previous,disabled,maxBytes,maxDecorations,onChange,onUpload}:{
  materials:PresentationMaterial[];usage:MaterialUsage[];previous:boolean;disabled:boolean;maxBytes:number;maxDecorations:number;
  onChange:(next:PresentationMaterial[])=>void;onUpload:(files:File[],role:MaterialRole,replace?:string)=>Promise<void>;
}){
  const [error,setError]=useState('');
  async function upload(files:FileList|null,role:MaterialRole,replace?:string){
    const values=Array.from(files??[]);if(!values.length)return;
    if(values.some(f=>f.size>maxBytes)){setError(`Максимальный размер файла — ${Math.round(maxBytes/1048576)} МБ`);return;}
    setError('');try{await onUpload(values,role,replace);}catch(e){setError((e as Error).message);}
  }
  function patch(id:string,value:Partial<PresentationMaterial>){onChange(materials.map(m=>m.id===id?{...m,...value}:m));}
  return <details className="presentation-materials"><summary>Свои материалы <span>{materials.length||'необязательно'}</span></summary>
    <p className="presentation-help">PNG, JPEG, WebP · до {Math.round(maxBytes/1048576)} МБ. Загрузка подготавливает файл. Чтобы применить изменения, сгенерируйте PDF.</p>
    {error&&<p role="alert" className="presentation-error">{error}</p>}
    {roles.map(({role,name,help})=>{
      const values=materials.filter(m=>m.role===role),limit=role==='DECORATION'?maxDecorations:1;
      return <section className="material-zone" key={role} aria-label={name}><h3>{name}</h3><p>{help}</p>
        {values.map(m=><article className="material-card" key={m.id}>
          <div className="material-meta">{m.thumbnail_url?<img src={m.thumbnail_url} alt={m.filename}/>:<div className="material-placeholder" aria-hidden="true">{m.status==='FAILED'?'!':'…'}</div>}<div><strong>{m.filename}</strong><small>{m.width&&m.height?`${m.width} × ${m.height}`:'Подготовка файла'}</small></div></div>
          <p role="status" className={m.status==='FAILED'?'presentation-warning':'material-use'}>{materialUsageLabel(m,usage.find(u=>u.asset_ref===m.id),previous)}</p>
          <label className="presentation-check"><input type="checkbox" checked={m.enabled} disabled={disabled||m.status!=='READY'} onChange={e=>patch(m.id,{enabled:e.target.checked})}/>Использовать материал</label>
          {m.status==='READY'&&m.enabled&&<div className="material-options">
            <Select label="Где использовать" value={m.scope} disabled={disabled} onChange={value=>patch(m.id,{scope:value as PresentationMaterial['scope']})} options={[{value:'COVER',label:'Только обложка'},{value:'PRODUCTS',label:'Товарные страницы'},{value:'DOCUMENT',label:'Весь документ'}]}/>
            <Select label="Выразительность" value={m.expression} disabled={disabled} onChange={value=>patch(m.id,{expression:value as PresentationMaterial['expression']})} options={[{value:'SUBTLE',label:'Деликатно'},{value:'NORMAL',label:'Обычно'},{value:'BOLD',label:'Выразительно'}]}/>
            <Select label="Размещение" value={m.position} disabled={disabled} onChange={value=>patch(m.id,{position:value as PresentationMaterial['position']})} options={[{value:'AUTO',label:'Автоматически'},{value:'TOP',label:'Вверху'},{value:'BOTTOM',label:'Внизу'},{value:'LEFT',label:'Слева'},{value:'RIGHT',label:'Справа'}]}/>
            <details><summary>Дополнительные настройки</summary>
              {role!=='DECORATION'&&<><label className="presentation-check"><input type="checkbox" checked={m.crop} disabled={disabled} onChange={e=>patch(m.id,{crop:e.target.checked})}/>Разрешить обрезку</label>
                {m.crop&&<div className="material-focal">{(['focal_x','focal_y'] as const).map(k=><label key={k}>Точка внимания · {k==='focal_x'?'по горизонтали':'по вертикали'}<input type="range" min="0" max="100" value={m[k]*100} disabled={disabled} onChange={e=>patch(m.id,{[k]:Number(e.target.value)/100})}/></label>)}</div>}</>}
              <label>Интенсивность · {Math.round(m.opacity*100)}%<input type="range" min="0" max="100" value={m.opacity*100} disabled={disabled} onChange={e=>patch(m.id,{opacity:Number(e.target.value)/100})}/></label>
              {role==='DECORATION'&&<label className="presentation-check"><input type="checkbox" checked={m.harmonize} disabled={disabled} onChange={e=>patch(m.id,{harmonize:e.target.checked})}/>Согласовать декор с палитрой</label>}
              <label className="presentation-check"><input type="checkbox" checked={m.required} disabled={disabled} onChange={e=>patch(m.id,{required:e.target.checked})}/>Обязательно использовать</label>
              <label>Описание для оформления (необязательно)<input maxLength={160} value={m.description} disabled={disabled} onChange={e=>patch(m.id,{description:e.target.value})}/></label>
            </details>
          </div>}
          <div className="material-actions"><label className="material-file">Заменить<input aria-label={`Заменить ${m.filename}`} type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled||m.status==='QUEUED'} onChange={e=>{void upload(e.target.files,role,m.id);e.target.value='';}}/></label><button className="studio-button" disabled={disabled} onClick={()=>onChange(materials.filter(v=>v.id!==m.id))}>Удалить</button></div>
        </article>)}
        {values.filter(m=>m.status!=='FAILED').length<limit&&<label className="material-file material-upload">Загрузить {role==='DECORATION'?`декор · до ${maxDecorations} файлов`:'изображение'}<input type="file" accept="image/png,image/jpeg,image/webp" aria-label={`Загрузить: ${name}`} multiple={role==='DECORATION'} disabled={disabled} onChange={e=>{void upload(e.target.files,role);e.target.value='';}}/></label>}
      </section>;
    })}
  </details>;
}
