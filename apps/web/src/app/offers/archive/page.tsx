import {AuthGate} from '@/components/auth-gate';
import {OfferDocuments} from '@/components/offers/offer-documents';
export const dynamic='force-dynamic';
export default function Page(){return <AuthGate><OfferDocuments archive/></AuthGate>;}
