import type {PalettePlan,PresentationTheme} from './presentations';

// Colors/roles are calculated only on the server. Manual edits mark provenance;
// the existing save endpoint resolves their authoritative roles before a job.
export function manualPalette(theme:PresentationTheme,colors:string[]):PresentationTheme{
  const prior=theme.palette_plan;
  const changed=colors.map((c,n)=>c===theme.palette[n]?-1:n).filter(n=>n>=0);
  const edited_slots=changed.includes(0)?[0]:Array.from(new Set([...(prior?.edited_slots??[]),...changed])).filter(n=>n<colors.length);
  return {...theme,palette:colors,palette_plan:prior?{...prior,manual:true,preset_id:null,
    origin_preset_id:prior.origin_preset_id??prior.preset_id,name:'Своя палитра',locked:false,
    primary:colors[0],colors,edited_slots}:null};
}
export function appendPalettes(old:PalettePlan[],next:PalettePlan[]){
  const seen=new Set(old.map(p=>p.preset_id));
  return [...old,...next.filter(p=>!seen.has(p.preset_id))];
}
export const paletteCharacter={calm:'Спокойный',soft:'Мягкий',saturated:'Насыщенный',contrast:'Контрастный'};
