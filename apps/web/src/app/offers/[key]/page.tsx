import type {Metadata} from 'next';
import {PublicOffer} from '@/components/offers/public-offer';

export const metadata:Metadata={title:'Коммерческое предложение',robots:{index:false,follow:false,noarchive:true},referrer:'no-referrer'};
export const dynamic='force-dynamic';
export default async function Page({params}:{params:Promise<{key:string}>}){const {key}=await params;return <PublicOffer token={key}/>;}
