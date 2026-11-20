export interface DropdownOption {
  value: string;
  label: string;
  count?: number;
  disabled?: boolean;
  note?: string;
}

/** Compare labels only; selected IDs and supplier values always remain unchanged. */
export function filterOptions(options: DropdownOption[], query: string) {
  const term = query.trim().toLocaleLowerCase();
  return options.filter(option => option.label.toLocaleLowerCase().includes(term));
}

/** Render a bounded page while keeping active choices removable; no hidden top-N. */
export function dropdownPage(options:DropdownOption[], selected:string[], limit=60){
  const items=options.slice(0,limit),included=new Set(items.map(v=>v.value));
  for(const option of options)if(selected.includes(option.value)&&!included.has(option.value)){items.push(option);included.add(option.value);}
  return {items,remaining:options.length-items.length};
}

export function dropdownGeometry(
  anchor: {left: number; top: number; bottom: number; width: number},
  viewport: {width: number; height: number; left?: number; top?: number},
  preferredWidth: number,
  contentHeight: number,
) {
  const margin = 12, gap = 6, leftEdge = viewport.left ?? 0, topEdge = viewport.top ?? 0;
  const width = Math.min(Math.max(anchor.width, preferredWidth), Math.max(0, viewport.width - 2 * margin));
  const below = Math.max(0, topEdge + viewport.height - anchor.bottom - margin - gap);
  const above = Math.max(0, anchor.top - topEdge - margin - gap);
  const desired = Math.min(360, contentHeight);
  const down = below >= desired || below >= above;
  const maxHeight = Math.min(360, down ? below : above);
  const height = Math.min(contentHeight, maxHeight);
  return {
    width, maxHeight,
    left: Math.max(leftEdge + margin, Math.min(anchor.left, leftEdge + viewport.width - margin - width)),
    top: down ? anchor.bottom + gap : anchor.top - gap - height,
    placement: down ? 'bottom' : 'top',
  };
}
