import type {Metadata} from 'next';
import {AuthGate} from '@/components/auth-gate';
import {OfferEditor} from '@/components/offers/offer-editor';

export const metadata:Metadata={title:'Редактор предложения',robots:{index:false,follow:false},referrer:'no-referrer'};
export const dynamic='force-dynamic';
export default async function Page({params}:{params:Promise<{key:string}>}){const {key}=await params;return <AuthGate><OfferEditor key={key} id={key}/></AuthGate>;}
