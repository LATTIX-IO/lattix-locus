import { FxSectionHeader } from "@/components/fx-ui";
import { IntegrationsManager } from "@/components/integrations-manager";

export default function BuilderIntegrationsPage() {
  return (
    <section className="space-y-4">
      <FxSectionHeader
        label="Integrations"
        index="/09 — Configure"
        sub="External connectors with auth, capability scopes, and signed marketplace metadata."
      />
      <IntegrationsManager />
    </section>
  );
}
