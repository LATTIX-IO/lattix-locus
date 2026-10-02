import { redirect } from "next/navigation";

type Props = {
  params: Promise<{ id: string }>;
};

export default async function RunConversationPage({ params }: Props) {
  const { id } = await params;
  redirect(`/inbox?session=${encodeURIComponent(id)}&details=1`);
}
