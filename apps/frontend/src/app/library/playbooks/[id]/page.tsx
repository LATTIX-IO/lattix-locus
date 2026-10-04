import { getPlaybook, getPlaybooks } from "@/lib/api";
import { PlaybookStudioClient } from "./page.client";

export default async function PlaybookStudioPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  if (id === "new") {
    return <PlaybookStudioClient playbookId={id} isNew initialPlaybook={null} />;
  }

  const playbooks = await getPlaybooks();
  const listedPlaybook = playbooks.find((playbook) => playbook.id === id) ?? null;
  const selectedPlaybook = await getPlaybook(id);

  return <PlaybookStudioClient playbookId={id} initialPlaybook={selectedPlaybook ?? listedPlaybook} isNew={false} />;
}