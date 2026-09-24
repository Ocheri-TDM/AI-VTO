"use client";

import { createContext, FormEvent, useContext, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

type AuthSession = {
  user: { id: string; name: string };
  logout: () => Promise<void>;
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
  useEffect(() => {
    api.me().then(setUser).catch((cause: unknown) => {
      setUser(null);
      if (cause instanceof ApiError && cause.status === 503) setError(cause.message);
    });
  }, []);
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
      setError(cause instanceof ApiError && cause.status === 503
        ? cause.message
        : register ? "Не удалось создать аккаунт" : "Неверное имя или пароль");
    }
    finally { setBusy(false); }
  }
  if (user === undefined) return <main className="auth-screen"><p>Загрузка…</p></main>;
  if (!user) return <main className="auth-screen"><form className="auth-card" onSubmit={submit}>
    <small>Souvenir Studio · Research</small><h1>{register ? "Создать аккаунт" : "Вход"}</h1>
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
    logout: async () => { await api.logout(); setUser(null); },
  }}>{children}</AuthSessionContext.Provider>;
}
