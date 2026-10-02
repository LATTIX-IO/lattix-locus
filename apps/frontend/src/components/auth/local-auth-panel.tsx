"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { getOperatorSession, loginWithLocalPassword, registerWithLocalPassword } from "@/lib/api";

type LocalAuthPanelProps = {
  providerLabel: string;
};

type AuthMode = "signin" | "signup";

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message.replace(/^Request failed \(\d+\):?\s*/i, "").trim();
    return message || "Authentication failed. Please try again.";
  }
  return "Authentication failed. Please try again.";
}

export function LocalAuthPanel({ providerLabel }: LocalAuthPanelProps) {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const primaryCta = useMemo(() => {
    return mode === "signin" ? "Sign in securely" : "Create account";
  }, [mode]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setErrorMessage("");

    if (mode === "signup" && password !== confirmPassword) {
      setErrorMessage("Passwords do not match yet. A dramatic reunion is required.");
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "signin") {
        await loginWithLocalPassword({ username, password });
      } else {
        await registerWithLocalPassword({
          username,
          email,
          display_name: displayName,
          password,
        });
      }
      const session = await getOperatorSession();
      if (!session.authenticated) {
        throw new Error("Authenticated session was not established.");
      }
      router.replace("/inbox");
      router.refresh();
    } catch (error) {
      setErrorMessage(normalizeError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="inline-flex rounded-[14px] border border-[var(--ui-border)] bg-[hsl(var(--card))] p-1 shadow-[var(--fx-shadow-soft)]">
        {([
          ["signin", "Sign in"],
          ["signup", "Create account"],
        ] as const).map(([value, label]) => {
          const active = mode === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => {
                setMode(value);
                setErrorMessage("");
              }}
              className={`min-w-32 rounded-[10px] border px-3 py-2 text-[12px] font-medium transition ${active
                ? "border-[var(--fx-primary)] bg-[var(--fx-primary)] text-[var(--fx-primary-text)]"
                : "border-transparent text-[var(--fx-muted)] hover:border-[var(--ui-border)] hover:bg-[var(--fx-nav-hover)] hover:text-[hsl(var(--foreground))]"
                }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2 sm:col-span-2">
          <label className="block text-[12px] font-medium tracking-[0.03em] text-[var(--fx-muted)]" htmlFor="auth-username">
            Username
          </label>
          <input
            id="auth-username"
            autoComplete={mode === "signin" ? "username" : "new-username"}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className="w-full rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--background))] px-4 py-3 text-sm outline-none transition focus:border-[var(--fx-primary)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--fx-primary)_18%,transparent)]"
            placeholder="username"
            required
          />
          </div>

          {mode === "signup" ? (
            <>
              <div className="space-y-2 sm:col-span-2">
              <label className="block text-[12px] font-medium tracking-[0.03em] text-[var(--fx-muted)]" htmlFor="auth-display-name">
                Display name
              </label>
              <input
                id="auth-display-name"
                autoComplete="name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                className="w-full rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--background))] px-4 py-3 text-sm outline-none transition focus:border-[var(--fx-primary)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--fx-primary)_18%,transparent)]"
                placeholder="James Booth"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="block text-[12px] font-medium tracking-[0.03em] text-[var(--fx-muted)]" htmlFor="auth-email">
                Email
              </label>
              <input
                id="auth-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--background))] px-4 py-3 text-sm outline-none transition focus:border-[var(--fx-primary)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--fx-primary)_18%,transparent)]"
                placeholder="james@xfrontier.localhost"
                required
              />
            </div>
          </>
        ) : null}

          <div className="space-y-2">
          <label className="block text-[12px] font-medium tracking-[0.03em] text-[var(--fx-muted)]" htmlFor="auth-password">
            Password
          </label>
          <input
            id="auth-password"
            type="password"
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--background))] px-4 py-3 text-sm outline-none transition focus:border-[var(--fx-primary)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--fx-primary)_18%,transparent)]"
            placeholder="••••••••"
            required
          />
          </div>

          {mode === "signup" ? (
            <div className="space-y-2">
            <label className="block text-[12px] font-medium tracking-[0.03em] text-[var(--fx-muted)]" htmlFor="auth-password-confirm">
              Confirm password
            </label>
            <input
              id="auth-password-confirm"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="w-full rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--background))] px-4 py-3 text-sm outline-none transition focus:border-[var(--fx-primary)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--fx-primary)_18%,transparent)]"
              placeholder="Repeat the password"
              required
            />
          </div>
        ) : null}
        </div>

        {errorMessage ? (
          <div className="rounded-[14px] border border-[color-mix(in_srgb,var(--fx-danger)_45%,var(--ui-border)_55%)] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] px-4 py-3 text-sm text-[hsl(var(--foreground))]">
            {errorMessage}
          </div>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="fx-btn-primary inline-flex min-h-12 w-full items-center justify-center px-4 py-3 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-70"
        >
          {submitting ? "Working on it…" : primaryCta}
        </button>
      </form>

      <div className="rounded-[16px] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--muted))_42%,transparent)] p-4 shadow-[var(--fx-shadow-soft)]">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--ui-border)] pb-3">
          <h3 className="text-[0.95rem] font-semibold tracking-[-0.01em] text-[hsl(var(--foreground))]">Local sign-in</h3>
          <span className="rounded-full border border-[var(--ui-border)] px-2.5 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">Local first</span>
        </div>
        <p className="mt-3 text-sm leading-6 text-[var(--fx-muted)]">
          {providerLabel} manages the identity records, and xFrontier handles the sign-in form and session setup here.
        </p>
      </div>
    </div>
  );
}
