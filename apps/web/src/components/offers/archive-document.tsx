'use client';
import {useRouter} from 'next/navigation';
import {PresentationWorkspace} from '@/components/presentations/presentation-workspace';
export function ArchiveDocument({id}:{id:string}){const router=useRouter();return <PresentationWorkspace id={id} onBack={()=>router.push('/offers/archive')}/>;}
