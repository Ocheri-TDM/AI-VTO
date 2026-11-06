'use client';
import {useEffect,useRef,useState} from 'react';
import {presentationApi,type PalettePlan,type PaletteCatalog} from '@/lib/presentations';
import {appendPalettes,paletteCharacter} from '@/lib/presentation-palettes';
import {PaletteSelect} from './palette-select';

export function PaletteCard({palette,dark,selected,onSelect}:{palette:PalettePlan;dark:boolean;selected:boolean;onSelect:()=>void}){
  const r=palette.roles,prefix=dark?'dark':'light';
  return <button type="button" className={`palette-card${selected?' is-selected':''}`} aria-pressed={selected} onClick={onSelect}>
    <span className="palette-scheme" style={{background:r[`${prefix}_background`],color:r[`${prefix}_text`]}}>
      <span className="palette-scheme-line" style={{background:r[`${prefix}_accent`]}}/>
      <strong>Предметы и идеи</strong><small style={{color:r[`${prefix}_muted`]}}>Ваша подборка</small>
      <span className="palette-scheme-surface" style={{background:r[dark?'dark_peak':'light_surface']}}/>
    </span>
    <span className="palette-swatches">{palette.colors.map((c,n)=><i key={n} style={{background:c}} title={c}/>)}</span>
    <strong>{palette.name}</strong><small>{palette.direction_label??'Свой цвет'} · {paletteCharacter[palette.character]} · {palette.primary}</small>
  </button>;
}

export function PaletteExplorer({current,dark,onApply,onClose}:{current:PalettePlan|null|undefined;dark:boolean;onApply:(palette:PalettePlan)=>void;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null),cache=useRef(new Map<string,PaletteCatalog>());
  const [direction,setDirection]=useState(current?.direction??'red'),[primary,setPrimary]=useState(current?.primary??'#B73143');
  const [character,setCharacter]=useState(''),[application,setApplication]=useState(''),[contrast,setContrast]=useState(false);
  const [page,setPage]=useState(1),[data,setData]=useState<PaletteCatalog|null>(null),[items,setItems]=useState<PalettePlan[]>([]);
  const [selection,setSelection]=useState<PalettePlan|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close();},[]);
  useEffect(()=>{
    const controller=new AbortController();
    const timer=setTimeout(()=>{
      if(direction==='custom'&&!/^#[0-9a-fA-F]{6}$/.test(primary)){setError('Введите цвет в формате #RRGGBB');return;}
      setBusy(true);setError('');
      const params=new URLSearchParams({direction,page:String(page),contrast_accent:String(contrast)});
      if(character)params.set('character',character);if(application)params.set('application',application);
      if(direction==='custom')params.set('primary',primary.toUpperCase());
      const key=params.toString(),saved=cache.current.get(key);
      (saved?Promise.resolve(saved):presentationApi.palettes(params,controller.signal)).then(value=>{
        if(controller.signal.aborted)return;
        if(cache.current.size>=24)cache.current.delete(cache.current.keys().next().value!);
        cache.current.set(key,value);setData(value);setItems(old=>page===1?value.items:appendPalettes(old,value.items));
      }).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setBusy(false);});
    },direction==='custom'?250:0);
    return()=>{clearTimeout(timer);controller.abort();};
  },[direction,primary,character,application,contrast,page]);
  function filter(update:()=>void){update();setPage(1);setItems([]);setSelection(null);}
  const directions=data?.directions??[{id:'red',label:'Красное',published:0}];
  return <dialog ref={dialog} className="palette-explorer" aria-labelledby="palette-explorer-title" onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===e.currentTarget)onClose();}}>
    <div className="palette-dialog-body"><header><div><h2 id="palette-explorer-title">Подобрать сочетание</h2><p>{data?`${data.published_total} проверенных сочетаний`:'Локальная библиотека палитр'} · схема оформления, не PDF-preview</p></div><button className="studio-button" onClick={onClose} aria-label="Закрыть библиотеку палитр">✕</button></header>
      <div className="palette-explorer-filters"><PaletteSelect label="Ведущее направление" value={direction} onChange={value=>filter(()=>setDirection(value))} options={[...directions.map(d=>({value:d.id,label:`${d.label} · ${d.published}`})),{value:'custom',label:'Свой основной цвет'}]}/>
        {direction==='custom'&&<div className="palette-custom"><label>Основной HEX<input aria-label="Основной HEX" value={primary} maxLength={7} onChange={e=>filter(()=>setPrimary(e.target.value))}/></label><input aria-label="Выбрать основной цвет" type="color" value={/^#[0-9a-fA-F]{6}$/.test(primary)?primary:'#B73143'} onChange={e=>filter(()=>setPrimary(e.target.value))}/><small>Точный основной цвет сохраняется. Фон и текст рассчитываются отдельно.</small></div>}
        <label className="presentation-check"><input type="checkbox" checked={contrast} onChange={e=>filter(()=>setContrast(e.target.checked))}/>С контрастным акцентом</label>
        <details><summary>Уточнить характер</summary><div><PaletteSelect label="Характер" value={character} onChange={value=>filter(()=>setCharacter(value))} options={[{value:'',label:'Все'},...Object.entries(paletteCharacter).map(([value,label])=>({value,label}))]}/><PaletteSelect label="Применение" value={application} onChange={value=>filter(()=>setApplication(value))} options={[{value:'',label:'Любое'},{value:'light',label:'Светлое'},{value:'dark',label:'Тёмное'},{value:'universal',label:'Универсальное'}]}/></div></details>
      </div>
      {error&&<p role="alert">{error}</p>}
      <div className="palette-results" aria-busy={busy}>{items.map(p=><PaletteCard key={p.preset_id} palette={p} dark={dark} selected={selection?.preset_id===p.preset_id} onSelect={()=>setSelection(p)}/>)}</div>
      {!busy&&!items.length&&!error&&<p>Нет сочетаний с такими уточнениями. Измените характер или акцент.</p>}
      {busy&&<p role="status">Подбираю сочетания…</p>}
      {data?.has_more&&<button className="studio-button palette-more" disabled={busy} onClick={()=>setPage(p=>p+1)}>Ещё сочетания</button>}
      <footer><span>{selection?`Выбрано: ${selection.name}`:'Выберите сочетание. Черновик изменится после применения.'}</span><button className="studio-button" onClick={onClose}>Отмена</button><button className="studio-button primary" disabled={!selection} onClick={()=>{if(selection)onApply(selection);}}>Применить палитру</button></footer>
    </div>
  </dialog>;
}
