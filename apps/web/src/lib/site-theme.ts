export const THEME_STORAGE_KEY = 'souvenir:site-theme';
export type SiteTheme = 'current' | 'pulse';
const CHANGE_EVENT = 'souvenir:theme-change';

export function normalizeSiteTheme(value: unknown): SiteTheme {
  return value === 'pulse' ? 'pulse' : 'current';
}

// Static, bounded code: no stored value is interpolated into HTML or CSS.
// Runs in <head> before the interface paints, including on a hard reload.
export const THEME_INIT_SCRIPT = `(function(){var t='current';try{if(localStorage.getItem('${THEME_STORAGE_KEY}')==='pulse')t='pulse'}catch(e){}document.documentElement.dataset.siteTheme=t})()`;

export function getSiteTheme(): SiteTheme {
  return normalizeSiteTheme(document.documentElement.dataset.siteTheme);
}

export function getServerSiteTheme(): SiteTheme {
  return 'current';
}

export function setSiteTheme(value: SiteTheme) {
  const theme = normalizeSiteTheme(value);
  document.documentElement.dataset.siteTheme = theme;
  try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch { /* Works for this page when storage is unavailable. */ }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeSiteTheme(listener: () => void) {
  const storage = (event: StorageEvent) => {
    if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
    let value: string | null = null;
    try { value = localStorage.getItem(THEME_STORAGE_KEY); } catch { return; }
    document.documentElement.dataset.siteTheme = normalizeSiteTheme(value);
    listener();
  };
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener('storage', storage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener('storage', storage);
  };
}
