import { redirect } from "next/navigation";

type SearchParamValue = string | string[] | undefined;

type AuthCallbackPageProps = {
  searchParams?: Promise<Record<string, SearchParamValue>> | Record<string, SearchParamValue>;
};

export default async function AuthCallbackPage({ searchParams }: AuthCallbackPageProps = {}) {
  const resolvedSearchParams = await Promise.resolve(searchParams ?? {});
  const params = new URLSearchParams();

  for (const [key, value] of Object.entries(resolvedSearchParams)) {
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item != null) {
          params.append(key, String(item));
        }
      });
      continue;
    }
    if (value != null) {
      params.set(key, String(value));
    }
  }

  const query = params.toString();
  redirect(query ? `/api/auth/oidc/callback?${query}` : "/api/auth/oidc/callback");
}