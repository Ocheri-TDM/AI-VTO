'use client';

import Link from 'next/link';
import {ThemeSwitch} from './theme-switch';

export function AppNavbar({name,logout,active='projects',onProjects}:{name:string;logout:()=>void;active?:'projects'|'offers';onProjects?:()=>void}){
  return <header className="research-top app-navbar" data-testid="app-navbar">
    <strong className="app-navbar-brand" data-testid="header-brand">Souvenir Studio</strong>
    <nav className="app-navbar-links" aria-label="Основная навигация">
      {onProjects?<button className="studio-button" aria-current={active==='projects'?'page':undefined} onClick={onProjects}>Проекты</button>:<Link className="studio-button" href="/" aria-current={active==='projects'?'page':undefined}>Проекты</Link>}
      <Link className="studio-button" href="/offers" aria-current={active==='offers'?'page':undefined}>Предложения</Link>
    </nav>
    <div className="app-navbar-account" data-testid="header-account"><ThemeSwitch/><span className="app-navbar-name" title={name}>{name}</span><button className="studio-button" data-testid="header-logout" onClick={logout}>Выйти</button></div>
  </header>;
}
