import { ResearchWorkspace } from "@/components/research/research-workspace";
import { AuthGate } from "@/components/auth-gate";

export default function Home() {
  return <AuthGate><ResearchWorkspace /></AuthGate>;
}
