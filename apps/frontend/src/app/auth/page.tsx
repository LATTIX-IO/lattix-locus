import type { Metadata } from "next";

import { LattixAuthCard } from "@/components/auth/lattix-auth-card";
import { getOperatorSession } from "@/lib/api";
import type { OperatorSession } from "@/types/frontier";

export const dynamic = "force-dynamic";

type AuthUiConfig = {
  authMode: "oidc" | "shared-token" | "unknown";
  provider: string;
  providerLabel: string;
  issuer: string;
  signinUrl: string;
  signupUrl: string;
  scopes: string[];
  audience: string;
  clientId: string;
  isConfigured: boolean;
  validationError: string;
  browserFlowConfigured: boolean;
  browserFlowError: string;
};

type SearchParamValue = string | string[] | undefined;

type AuthPageProps = {
  searchParams?: Promise<Record<string, SearchParamValue>> | Record<string, SearchParamValue>;
};

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1" || normalized.endsWith(".localhost");
}

function parseAbsoluteHttpUrl(value: string): URL | null {
  const candidate = value.trim();
  if (!candidate) {
    return null;
  }
  try {
    const parsed = new URL(candidate);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return null;
    }
    if (!parsed.hostname || parsed.username || parsed.password || parsed.hash) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function oidcRedirectMatchesIssuer(candidateUrl: string, issuer: string): boolean {
  const candidate = parseAbsoluteHttpUrl(candidateUrl);
  const parsedIssuer = parseAbsoluteHttpUrl(issuer);
  if (!candidate || !parsedIssuer) {
    return false;
  }
  if (candidate.origin !== parsedIssuer.origin) {
    return false;
  }
  if (parsedIssuer.protocol !== "https:" && !isLocalHostname(parsedIssuer.hostname)) {
    return false;
  }
  return true;
}

function firstSearchParamValue(value: SearchParamValue): string {
  if (Array.isArray(value)) {
    return String(value[0] ?? "").trim();
  }
  return String(value ?? "").trim();
}

function getAuthUiConfigFromSession(session: OperatorSession | null): AuthUiConfig {
  const authMode = String(session?.auth_mode ?? process.env.FRONTIER_AUTH_MODE ?? "").trim().toLowerCase();
  const provider = String(
    session?.oidc?.provider
      ?? session?.provider
      ?? process.env.FRONTIER_AUTH_OIDC_PROVIDER
      ?? "",
  ).trim().toLowerCase();
  const issuer = String(session?.oidc?.issuer ?? process.env.FRONTIER_AUTH_OIDC_ISSUER ?? "").trim();
  const authorizationUrl = (process.env.FRONTIER_AUTH_OIDC_AUTHORIZATION_URL ?? "").trim();
  const signinUrl = (process.env.FRONTIER_AUTH_OIDC_SIGNIN_URL ?? authorizationUrl).trim();
  const signupUrl = (process.env.FRONTIER_AUTH_OIDC_SIGNUP_URL ?? authorizationUrl).trim();
  const scopes = (process.env.FRONTIER_AUTH_OIDC_SCOPES ?? "")
    .split(/\s+/)
    .map((scope) => scope.trim())
    .filter(Boolean);
  const audience = String(session?.oidc?.audience ?? process.env.FRONTIER_AUTH_OIDC_AUDIENCE ?? "").trim();
  const clientId = (process.env.FRONTIER_AUTH_OIDC_CLIENT_ID ?? "").trim();
  const browserFlowConfigured = Boolean(session?.oidc?.browser_flow_configured);
  const browserFlowError = String(session?.oidc?.browser_flow_error ?? "").trim();
  const sessionConfigured = Boolean(session?.oidc?.configured);

  const normalizedMode: AuthUiConfig["authMode"] = authMode === "oidc"
    ? "oidc"
    : authMode === "shared-token"
      ? "shared-token"
      : "unknown";

  const providerLabel = provider === "casdoor"
    ? "Casdoor"
    : provider === "oidc"
      ? "OIDC Provider"
      : provider
        ? provider.replace(/[-_]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
        : "OIDC Provider";

  let validationError = "";
  const issuerUrl = parseAbsoluteHttpUrl(issuer);
  if (normalizedMode === "oidc") {
    const backendValidationError = session?.oidc?.validation_error?.trim() ?? "";
    if (backendValidationError) {
      validationError = backendValidationError;
    } else if (!browserFlowConfigured) {
      if (!issuerUrl) {
        validationError = "OIDC issuer must be a valid absolute http(s) URL.";
      } else if (!oidcRedirectMatchesIssuer(signinUrl, issuer)) {
        validationError = "Sign-in URL must belong to the configured OIDC issuer origin.";
      } else if (!oidcRedirectMatchesIssuer(signupUrl, issuer)) {
        validationError = "Sign-up URL must belong to the configured OIDC issuer origin.";
      }
    }
  }

  const redirectFlowConfigured = Boolean(signinUrl && signupUrl && issuer)
    && Boolean(sessionConfigured || Boolean(signinUrl && signupUrl && issuer))
    && !validationError;
  const isConfigured = normalizedMode === "oidc"
    && !validationError
    && (browserFlowConfigured || redirectFlowConfigured);

  return {
    authMode: normalizedMode,
    provider,
    providerLabel,
    issuer,
    signinUrl,
    signupUrl,
    scopes,
    audience,
    clientId,
    isConfigured,
    validationError,
    browserFlowConfigured,
    browserFlowError,
  };
}

export const metadata: Metadata = {
  title: "Sign in | Lattix xFrontier",
  description: "Secure access to the Lattix xFrontier console.",
};

async function loadOperatorSession(): Promise<OperatorSession | null> {
  try {
    return await getOperatorSession();
  } catch {
    // Fail closed: without a session/config snapshot, only native sign-in is offered.
    return null;
  }
}

/**
 * Native local-password sign-in/up (LattixAuthCard) is always available. When the
 * install is configured for an external OIDC provider with a complete browser
 * flow, a single sign-on panel is added; its redirects land back in xFrontier via
 * the backend callback exchange (`/auth/callback` -> `/api/auth/oidc/callback`).
 */
export default async function AuthPage({ searchParams }: AuthPageProps = {}) {
  const [operatorSession, resolvedSearchParams] = await Promise.all([
    loadOperatorSession(),
    Promise.resolve(searchParams ?? {}),
  ]);
  const config = getAuthUiConfigFromSession(operatorSession);
  const issuerUrl = parseAbsoluteHttpUrl(config.issuer);
  // A local Casdoor issuer is served through the native card (backend /auth/login).
  const usesLocalCasdoor = config.isConfigured
    && config.provider === "casdoor"
    && Boolean(issuerUrl?.hostname && isLocalHostname(issuerUrl.hostname));
  const externalOidcBrowserSupported = config.authMode === "oidc"
    && config.isConfigured
    && config.browserFlowConfigured
    && !usesLocalCasdoor;
  const authErrorCode = firstSearchParamValue(resolvedSearchParams.auth_error) || null;
  const callbackError = firstSearchParamValue(resolvedSearchParams.error);

  const ssoStatusMessage = externalOidcBrowserSupported || usesLocalCasdoor
    ? `Configured against ${config.issuer}. The backend validates issuer, audience, and JWKS before granting console access.`
    : config.isConfigured
      ? config.browserFlowError
        ? config.browserFlowError
        : `Configured against ${config.issuer}, but browser sign-in is still missing required callback or token-exchange settings.`
      : config.validationError
        ? config.validationError
        : "OIDC is selected, but the IAM endpoints are incomplete. Finish the issuer and sign-in/sign-up URLs in your install environment to activate single sign-on.";

  return (
    <section className="relative flex min-h-screen flex-col items-center justify-center gap-4 overflow-hidden px-4 py-10">
      {callbackError ? (
        <div
          role="status"
          className="relative z-20 w-full max-w-[420px] border border-[color-mix(in_srgb,var(--fx-danger)_50%,var(--ui-border)_50%)] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] px-4 py-3 text-sm leading-6 text-[hsl(var(--foreground))]"
        >
          {callbackError}
        </div>
      ) : null}

      <LattixAuthCard initialErrorCode={authErrorCode} />

      {config.authMode === "oidc" && !usesLocalCasdoor ? (
        <div className="relative z-20 w-full max-w-[420px] border border-[var(--ui-border)] bg-[hsl(var(--card))] p-4" aria-labelledby="auth-sso-heading">
          <h2 id="auth-sso-heading" className="font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-[hsl(var(--foreground))]">
            Single sign-on · {config.providerLabel}
          </h2>
          <p className="mt-2 text-[12px] leading-5 text-[var(--fx-muted)]">{ssoStatusMessage}</p>
          {externalOidcBrowserSupported ? (
            <div className="mt-3 grid gap-2">
              <a
                href="/api/auth/oidc/start?intent=signin"
                className="fx-btn-primary inline-flex h-10 items-center justify-center px-4 font-mono text-[11px] font-bold uppercase tracking-[0.12em] no-underline"
              >
                Sign in with {config.providerLabel}
              </a>
              <a
                href="/api/auth/oidc/start?intent=signup"
                className="fx-btn-secondary inline-flex h-10 items-center justify-center px-4 font-mono text-[11px] font-bold uppercase tracking-[0.12em] no-underline"
              >
                Create account with {config.providerLabel}
              </a>
            </div>
          ) : (
            <div className="mt-3 grid gap-2">
              <button
                type="button"
                disabled
                className="fx-btn-secondary inline-flex h-10 items-center justify-center px-4 font-mono text-[11px] font-bold uppercase tracking-[0.12em] opacity-60"
              >
                Sign in with {config.providerLabel}
              </button>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
