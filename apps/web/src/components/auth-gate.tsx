"use client";

import { createContext, FormEvent, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ThemeSwitch } from "./theme-switch";
import {NotificationProvider} from './notifications/notification-provider';

type AuthSession = {
  user: { id: string; name: string };
  logout: () => Promise<void>;
  expire: () => void;
};

const AuthSessionContext = createContext<AuthSession | null>(null);

export function useAuthSession() {
  const session = useContext(AuthSessionContext);
  if (!session) throw new Error("useAuthSession must be used inside AuthGate");
  return session;
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<{ id: string; name: string } | null>();
  const [register, setRegister] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionExpired=useCallback(()=>{setError("Сессия завершилась. Войдите снова.");setUser(null);},[]);
  const checkSession=useCallback(async()=>{
    try{setUser(await api.me());setError("");}
    catch(cause){
      if(cause instanceof ApiError&&cause.status===401)setUser(null);
      else setError(cause instanceof Error?cause.message:"Не удалось проверить сессию.");
    }
  },[]);
  useEffect(() => {queueMicrotask(()=>void checkSession());},[checkSession]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true);
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") ?? "");
    const password = String(data.get("password") ?? "");
    if (register && password !== String(data.get("repeat") ?? "")) {
      setError("Пароли не совпадают"); setBusy(false); return;
    }
    try { setUser(await (register ? api.register(name, password) : api.login(name, password))); }
    catch (cause) {
      setError(cause instanceof ApiError && cause.status === 401
        ? "Неверное имя или пароль"
        : cause instanceof Error ? cause.message : register ? "Не удалось создать аккаунт" : "Не удалось войти");
    }
    finally { setBusy(false); }
  }
  if (user === undefined) return <main className="auth-screen"><div className="auth-theme"><ThemeSwitch /></div>
    {error?<div className="auth-card"><p role="alert">{error}</p><button className="studio-button primary" onClick={()=>{setError('');void checkSession();}}>Повторить подключение</button></div>:<p>Загрузка…</p>}
  </main>;
  if (!user) return <main className="auth-screen"><form className="auth-card" onSubmit={submit}>
    <div className="auth-card-top"><small>Souvenir Studio · Research</small><ThemeSwitch /></div><h1>{register ? "Создать аккаунт" : "Вход"}</h1>
    <label>Имя<input name="name" required maxLength={200} autoComplete="username" /></label>
    <label>Пароль<input name="password" type="password" required minLength={8} autoComplete={register ? "new-password" : "current-password"} /></label>
    {register && <label>Повторите пароль<input name="repeat" type="password" required minLength={8} autoComplete="new-password" /></label>}
    {error && <p className="field-error" role="alert">{error}</p>}
    <button className="studio-button primary" disabled={busy}>{busy ? "Подождите…" : register ? "Создать аккаунт" : "Войти"}</button>
    <button className="auth-switch" type="button" onClick={() => { setRegister(!register); setError(""); }}>
      {register ? "Уже есть аккаунт? Войти" : "Нет аккаунта? Создать"}
    </button>
  </form></main>;
  return <AuthSessionContext.Provider value={{
    user,
    logout: async () => { try{await api.logout();}finally{setError("");setUser(null);} },
    expire: sessionExpired,
  }}><NotificationProvider key={user.id} userId={user.id} onSessionExpired={sessionExpired}>{children}</NotificationProvider></AuthSessionContext.Provider>;
}
