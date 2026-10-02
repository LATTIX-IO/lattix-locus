import { PlaybooksScreen } from "@/components/playbooks-screen";
import { getPlaybooks } from "@/lib/api";

export default async function PlaybooksPage() {
  const playbooks = await getPlaybooks();
  return <PlaybooksScreen initialPlaybooks={playbooks} />;
}
