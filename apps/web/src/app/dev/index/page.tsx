'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { request } from '@/lib/api';

interface SupplierQuality {
  supplier: string; offers_total: number; offers_active: number; fresh: number; stale: number;
  discovery_status: string; failed_branches: number; pending_branches: number;
  without_price: number; without_stock: number; without_category: number; without_image: number;
  last_complete_discovery: string | null; last_refresh: string | null;
  availability_observations: number; availability_fresh: number; availability_stale: number;
  availability_current_known: number; availability_current_unknown: number;
  availability_incoming_known: number; availability_remote_known: number;
  availability_preorder_known: number; availability_parser_versions: string[];
  last_incremental_sync: string | null; last_full_reconciliation: string | null;
  next_incremental: string | null; next_full_reconciliation: string | null;
  circuit_open_until: string | null; offer_temperature: Record<string, number>;
  availability_refreshing: number; current_job: unknown;
  products_per_minute: number | null; requests_per_minute: number | null;
  useful_change_rate: number | null; http_403: number; http_429: number;
  capabilities: Record<string, string>;
  roots: unknown[]; branches: unknown[]; jobs: unknown[]; anomalies: unknown[];
}

export default function IndexDiagnostics() {
  const [data, setData] = useState<SupplierQuality[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const load = () => request<{suppliers: SupplierQuality[]}>('/dev/index')
      .then(value => { if (active) setData(value.suppliers); })
      .catch(() => { if (active) setError('Диагностика недоступна. Включите DEBUG_AGENT на локальном сервере.'); });
    load(); const timer = setInterval(load, 15000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  return <main style={{padding: '2rem', maxWidth: 1200, margin: 'auto'}}>
    <Link href="/">← Рабочее пространство</Link><h1>Research Index · Developer</h1>
    <p>Покрытие каталогов и свежесть коммерческих наблюдений учитываются отдельно.</p>
    {error && <p role="alert">{error}</p>}
    {data.map(s => <section key={s.supplier} style={{border: '1px solid #444', padding: '1rem', marginBlock: '1rem', borderRadius: 12}}>
      <h2>{s.supplier} · {s.discovery_status}</h2>
      <p>Предложений: {s.offers_total} · Активных: {s.offers_active} · Свежих: {s.fresh} · Устаревших: {s.stale}</p>
      <p>Веток ожидает: {s.pending_branches} · Ошибок веток: {s.failed_branches}</p>
      <p>Без цены: {s.without_price} · Без остатка: {s.without_stock} · Без категории: {s.without_category} · Без фото: {s.without_image}</p>
      <p>Полный обход: {s.last_complete_discovery ?? 'не подтверждён'} · Обновление: {s.last_refresh ?? 'не завершено'}</p>
      <p>Availability records: {s.availability_observations} ? Fresh: {s.availability_fresh} ? Stale: {s.availability_stale}</p>
      <p>Incremental: {s.last_incremental_sync ?? 'never'} → {s.next_incremental ?? 'not scheduled'} · Full reconciliation: {s.last_full_reconciliation ?? 'never'} → {s.next_full_reconciliation ?? 'not scheduled'}</p>
      <p>Offers HOT/WARM/COLD/DORMANT: {s.offer_temperature.HOT ?? 0}/{s.offer_temperature.WARM ?? 0}/{s.offer_temperature.COLD ?? 0}/{s.offer_temperature.DORMANT ?? 0} · Refreshing: {s.availability_refreshing}</p>
      <p>Products/min: {s.products_per_minute ?? '—'} · Requests/min: {s.requests_per_minute ?? '—'} · Useful change rate: {s.useful_change_rate ?? '—'} · HTTP 403/429: {s.http_403}/{s.http_429}</p>
      {s.circuit_open_until && <p>Circuit open until: {s.circuit_open_until}</p>}
      <p>Current known: {s.availability_current_known} ? Current unknown: {s.availability_current_unknown} ? Incoming: {s.availability_incoming_known} ? Remote: {s.availability_remote_known} ? Preorder/production: {s.availability_preorder_known}</p>
      <p>Availability parsers: {s.availability_parser_versions.join(', ') || 'no normalized observations yet'}</p>
      <details><summary>Contract, корни, ветки, задания и аномалии</summary><pre style={{overflow: 'auto', maxHeight: 500}}>{JSON.stringify({capabilities: s.capabilities, roots: s.roots, branches: s.branches, jobs: s.jobs, anomalies: s.anomalies}, null, 2)}</pre></details>
    </section>)}
  </main>;
}
