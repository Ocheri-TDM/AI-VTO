'use client';

import { useSyncExternalStore } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Select } from './common/select';
import { getServerSiteTheme, getSiteTheme, setSiteTheme, subscribeSiteTheme, type SiteTheme } from '@/lib/site-theme';

export function ThemeSwitch() {
  const theme = useSyncExternalStore(subscribeSiteTheme, getSiteTheme, getServerSiteTheme);
  const Icon = theme === 'pulse' ? Sun : Moon;
  return <div className="theme-switch" title="Тема интерфейса">
    <Icon size={15} aria-hidden="true" />
    <Select label="Тема интерфейса" hideLabel compact value={theme} onChange={value => setSiteTheme(value as SiteTheme)} options={[{value:'current',label:'Текущая'},{value:'pulse',label:'Pulse'}]}/>
  </div>;
}
