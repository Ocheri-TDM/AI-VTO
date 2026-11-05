'use client';

import {useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode} from 'react';
import {Check, ChevronDown, Search} from 'lucide-react';
import {dropdownGeometry, filterOptions, type DropdownOption} from '@/lib/dropdown';

const OPEN_EVENT = 'souvenir:dropdown-open';
type CommonProps = {
  label: string;
  options: DropdownOption[];
  disabled?: boolean;
  hideLabel?: boolean;
  compact?: boolean;
  className?: string;
  name?: string;
};
type SelectProps = CommonProps & {value: string; onChange: (value: string) => void};

/** The popover stays beside its trigger in the DOM, including inside a modal.
 * Native top-layer placement escapes clipping without a portal or a z-index stack. */
function useDropdown(disabled: boolean, preferredWidth: number) {
  const id = useId();
  const wrapper = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({preventScroll: true});
  }
  function show() {
    if (disabled) return;
    document.dispatchEvent(new CustomEvent(OPEN_EVENT, {detail: id}));
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    if (disabled) {queueMicrotask(() => setOpen(false)); return;}
    const node = popup.current!, button = trigger.current!;
    node.showPopover?.();
    function position() {
      const r = button.getBoundingClientRect(), v = window.visualViewport;
      const viewport = {width: v?.width ?? innerWidth, height: v?.height ?? innerHeight, left: v?.offsetLeft ?? 0, top: v?.offsetTop ?? 0};
      if (r.bottom < viewport.top || r.top > viewport.top + viewport.height) {setOpen(false); return;}
      // Reset the previous cap before measuring a resized/search-filtered list.
      node.style.maxHeight = '360px';
      const g = dropdownGeometry(r, viewport, preferredWidth, Math.min(360, node.scrollHeight + 2));
      Object.assign(node.style, {width: g.width + 'px', maxHeight: g.maxHeight + 'px', left: g.left + 'px', top: g.top + 'px'});
      node.dataset.placement = g.placement;
    }
    function outside(event: PointerEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    function sibling(event: Event) {
      if ((event as CustomEvent<string>).detail !== id) setOpen(false);
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        setOpen(false); button.focus({preventScroll: true});
      }
    }
    position();
    const observer = new ResizeObserver(position);
    observer.observe(button);
    // List content changes (search/results) can change the preferred direction.
    if (node.firstElementChild) observer.observe(node.firstElementChild);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    document.addEventListener('pointerdown', outside);
    document.addEventListener(OPEN_EVENT, sibling);
    wrapper.current?.addEventListener('keydown', escape);
    const owner = wrapper.current;
    return () => {
      if (typeof node.hidePopover === 'function' && node.matches(':popover-open')) node.hidePopover();
      observer.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener(OPEN_EVENT, sibling);
      owner?.removeEventListener('keydown', escape);
    };
  }, [open, disabled, preferredWidth, id]);
  return {id, wrapper, trigger, popup, open: open && !disabled, show, close,
    blur: (next: EventTarget | null) => {
      if (wrapper.current?.contains(next as Node)) return;
      // A label first blurs the search input, then focuses its checkbox as the
      // click's default action. Let that action finish before dismissing.
      requestAnimationFrame(() => {
        if (wrapper.current && !wrapper.current.contains(document.activeElement)) close();
      });
    }};
}

function SelectControl({searchable = false, searchLabel, label, value, options, onChange, disabled = false, hideLabel = false, compact = false, className = '', name}: SelectProps & {searchable?: boolean; searchLabel?: string}) {
  const {id, wrapper, trigger, popup, open, show: openPopup, close, blur} = useDropdown(disabled, compact ? 160 : 280);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(''), [active, setActive] = useState<string | null>(null);
  const typeahead = useRef({text: '', time: 0});
  const visible = searchable ? filterOptions(options, query) : options;
  const enabled = visible.filter(o => !o.disabled);
  const current = enabled.find(o => o.value === active) ?? enabled.find(o => o.value === value) ?? enabled[0];
  const selected = options.find(o => o.value === value);
  const optionId = (option: DropdownOption) => `${id}-option-${options.indexOf(option)}`;
  function show(last = false) {
    setQuery('');
    setActive((last ? options.filter(o => !o.disabled).at(-1) : options.find(o => o.value === value && !o.disabled) ?? options.find(o => !o.disabled))?.value ?? null);
    openPopup();
  }
  function choose(option?: DropdownOption) {
    if (!option || option.disabled) return;
    onChange(option.value); close(true);
  }
  useEffect(() => {
    if (open && searchable) input.current?.focus({preventScroll: true});
  }, [open, searchable]);
  useEffect(() => {
    if (!open || !current) return;
    const option = document.getElementById(optionId(current)), list = option?.parentElement;
    if (option && list) {
      const r = option.getBoundingClientRect(), bounds = list.getBoundingClientRect();
      if (r.top < bounds.top) list.scrollTop -= bounds.top - r.top;
      else if (r.bottom > bounds.bottom) list.scrollTop += r.bottom - bounds.bottom;
    }
  });
  function key(event: KeyboardEvent) {
    if (event.key === 'Escape') return; // Shared dismissal consumes Escape before dialog cancellation.
    if (event.key === 'Tab') {if (open && !searchable) {choose(current);} return;}
    if (event.key === 'Enter' || (event.key === ' ' && event.target !== input.current)) {
      event.preventDefault(); if (open) choose(current); else show(); return;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(event.key)) {
      // Home/End remain native editing keys in the search box.
      if (event.target === input.current && ['Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (!open) {show(event.key === 'ArrowUp' || event.key === 'End'); return;}
      const step = event.key === 'PageDown' ? 10 : event.key === 'PageUp' ? -10 : event.key === 'ArrowUp' ? -1 : 1;
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1 : Math.max(0, enabled.indexOf(current)) + step;
      setActive(enabled[Math.max(0, Math.min(enabled.length - 1, index))]?.value ?? null); return;
    }
    if (!searchable && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      const now = Date.now();
      let term = now - typeahead.current.time < 700 ? typeahead.current.text + event.key : event.key;
      if (new Set(term.toLocaleLowerCase()).size === 1) term = event.key;
      typeahead.current = {text: term, time: now};
      const start = open ? enabled.indexOf(current) + 1 : 0;
      const match = [...enabled.slice(start), ...enabled.slice(0, start)].find(o => o.label.toLocaleLowerCase().startsWith(term.toLocaleLowerCase()));
      if (match) {if (!open) openPopup(); setActive(match.value);}
    }
  }
  return <div className={`ui-select ${compact ? 'is-compact' : ''} ${className}`} ref={wrapper} onBlur={e => blur(e.relatedTarget)}>
    <label id={`${id}-label`} htmlFor={`${id}-trigger`} className={hideLabel ? 'sr-only' : 'ui-select-label'}>{label}</label>
    <button id={`${id}-trigger`} ref={trigger} type="button" className="ui-select-trigger" role={searchable ? undefined : 'combobox'} aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="listbox" aria-controls={`${id}-list`} aria-expanded={open} aria-activedescendant={!searchable && open && current ? optionId(current) : undefined} disabled={disabled} onKeyDown={key} onClick={() => {trigger.current?.focus({preventScroll:true}); if (open) close(); else show();}}>
      <span id={`${id}-value`} className="ui-select-value">{selected?.label ?? 'Нет вариантов'}</span><ChevronDown size={15} aria-hidden="true"/>
    </button>
    {name && <input type="hidden" name={name} value={value}/>}
    <div ref={popup} className="ui-select-popup" popover="manual" hidden={!open} onKeyDown={searchable ? key : undefined}>
      <div className="ui-select-content">
        {searchable && <div className="ui-select-search"><Search size={15} aria-hidden="true"/><input ref={input} role="combobox" aria-label={searchLabel ?? `Поиск: ${label}`} aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-activedescendant={open && current ? optionId(current) : undefined} value={query} placeholder="Найти…" onChange={e => {setQuery(e.target.value); setActive(null);}}/></div>}
        <div id={`${id}-list`} className="ui-select-options" role="listbox" aria-labelledby={`${id}-label`}>
          {visible.map(option => <div key={option.value} id={optionId(option)} role="option" aria-selected={option.value === value} aria-disabled={!!option.disabled} data-active={current?.value === option.value} className="ui-select-option" onPointerDown={e => e.preventDefault()} onPointerMove={() => {if (!option.disabled) setActive(option.value);}} onClick={() => choose(option)}>
            <span className="ui-select-option-text">{option.label}</span>{option.count !== undefined && <span className="ui-select-count">{option.count}</span>}<span className="ui-select-check" aria-hidden="true">{option.value === value && <Check size={16}/>}</span>
          </div>)}
          {!visible.length && <p className="ui-select-empty" role="status">Ничего не найдено</p>}
        </div>
      </div>
    </div>
  </div>;
}

export function Select(props: SelectProps) {return <SelectControl {...props}/>;}
export function SearchableSelect(props: SelectProps & {searchLabel?: string}) {return <SelectControl {...props} searchable/>;}

export function MultiSelect({label, value, options, onChange, disabled = false, hideLabel = false, className = '', summary, help, name, searchLabel = `Поиск: ${label}`}:
  CommonProps & {value: string[]; onChange: (value: string[]) => void; summary?: string; help?: ReactNode; searchLabel?: string}) {
  const {id, wrapper, trigger, popup, open, show: openPopup, close, blur} = useDropdown(disabled, 340);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const visible = filterOptions(options, query);
  useEffect(() => {if (open) input.current?.focus({preventScroll: true});}, [open]);
  return <div className={`ui-select ui-multiselect ${className}`} ref={wrapper} onBlur={e => blur(e.relatedTarget)}>
    <label id={`${id}-label`} htmlFor={`${id}-trigger`} className={hideLabel ? 'sr-only' : 'ui-select-label'}>{label}</label>
    <button id={`${id}-trigger`} ref={trigger} type="button" className="ui-select-trigger" aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="dialog" aria-expanded={open} aria-controls={`${id}-popup`} disabled={disabled} onClick={() => {if (open) close(); else {setQuery(''); openPopup();}}} onKeyDown={e => {if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {e.preventDefault(); setQuery(''); openPopup();}}}>
      <span id={`${id}-value`} className="ui-select-value">{summary ?? (value.length ? `Выбрано ${value.length}` : 'Все бренды')}</span><ChevronDown size={15} aria-hidden="true"/>
    </button>
    {name && value.map(v => <input key={v} type="hidden" name={name} value={v}/>)}
    <div ref={popup} id={`${id}-popup`} className="ui-select-popup" role="dialog" aria-labelledby={`${id}-label`} popover="manual" hidden={!open}>
      <div className="ui-select-content">
        <div className="ui-select-search"><Search size={15} aria-hidden="true"/><input ref={input} aria-label={searchLabel} placeholder="Найти бренд" value={query} onChange={e => setQuery(e.target.value)}/></div>
        <div className="ui-select-options ui-checkbox-options" role="group" aria-label={label}>
          {visible.map(option => <label className="ui-select-option ui-checkbox-option" key={option.value} onPointerDown={e => {
            // Keep focus until the label's click activates its associated input.
            if (!(e.target instanceof HTMLInputElement)) e.preventDefault();
          }}>
            <input type="checkbox" checked={value.includes(option.value)} disabled={disabled || option.disabled} onChange={e => onChange(e.target.checked ? [...value, option.value] : value.filter(v => v !== option.value))}/>
            <span className="ui-select-option-text">{option.label}{option.note && <small>{option.note}</small>}</span>{option.count !== undefined && <span className="ui-select-count">{option.count}</span>}
          </label>)}
          {!visible.length && <p className="ui-select-empty" role="status">Ничего не найдено</p>}
        </div>
        {help && <div className="ui-select-help">{help}</div>}
      </div>
    </div>
  </div>;
}
