import Link from "next/link";
import { notFound } from "next/navigation";
import { GuardrailEditor } from "@/components/guardrail-editor";
import { getGuardrailRulesets } from "@/lib/api";

type Props = {
  params: Promise<{ id: string }>;
};

export default async function GuardrailDetailsPage({ params }: Props) {
  const { id } = await params;
  const rulesets = await getGuardrailRulesets();
  const ruleset = rulesets.find((item) => item.id === id);

  if (!ruleset) {
    notFound();
  }

  return (
    <div className="space-y-4">
      <GuardrailEditor mode="edit" ruleset={ruleset} />
      <div>
        <Link href="/library/guardrails" className="fx-btn-secondary inline-flex px-3 py-2 text-sm">
          Back to guardrails
        </Link>
      </div>
    </div>
  );
}
