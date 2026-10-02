"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import {
  getOperatorSession,
  loginWithLocalPassword,
  registerWithLocalPassword,
} from "@/lib/api";
import {
  SIGNIN_FIELDS,
  SIGNUP_FIELDS,
  resolveAuthErrorMessage,
  type AuthMode,
  type FormField,
} from "@/components/auth/presets";

const KEYFRAMES = `
@keyframes fx-fade-up-in {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes fx-glow-pulse {
  0%, 100% { box-shadow: 0 0 0 0 hsl(var(--primary) / 0.0); }
  50%      { box-shadow: 0 0 22px 0 hsl(var(--primary) / 0.35); }
}
`;

function CornerBracket({ position }: { position: string }) {
  return (
    <div className={`absolute ${position} h-3 w-3 pointer-events-none`} aria-hidden="true">
      <div className="absolute top-0 left-0 h-px w-3 bg-[var(--ui-border)]" />
      <div className="absolute top-0 left-0 h-3 w-px bg-[var(--ui-border)]" />
    </div>
  );
}

function IconShieldCheck(props: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={props.className}>
      <path d="M12 3l8 3v6c0 4.5-3 7.5-8 9-5-1.5-8-4.5-8-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

function IconEye(props: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={props.className}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IconEyeOff(props: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={props.className}>
      <path d="M3 3l18 18" />
      <path d="M10.58 10.58a2 2 0 002.83 2.83" />
      <path d="M9.88 4.24A9.94 9.94 0 0112 4c6 0 10 8 10 8a17.8 17.8 0 01-3.31 4.24M6.59 6.59A17.8 17.8 0 002 12s4 8 10 8c1.08 0 2.11-.17 3.06-.46" />
    </svg>
  );
}

function IconSun(props: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={props.className}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function IconMoon(props: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={props.className}>
      <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
    </svg>
  );
}

function BrandMark(props: { className?: string }) {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "dark" as const);
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand asset, no optimization needed
    <img
      src={theme === "dark" ? "/logo-mark-dark.svg" : "/logo-mark-light.svg"}
      alt="Lattix logo"
      className={props.className}
    />
  );
}

function readTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "dark";
  try {
    return window.localStorage.getItem("frontier-theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function subscribeTheme(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  window.addEventListener("frontier-theme-change", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("frontier-theme-change", callback);
  };
}

function ThemeToggle() {
  const theme = useSyncExternalStore(
    subscribeTheme,
    readTheme,
    () => "dark" as const,
  );

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem("frontier-theme", next);
    } catch {
      /* ignore */
    }
    const html = document.documentElement;
    html.classList.remove("theme-light", "theme-dark");
    html.classList.add(`theme-${next}`);
    window.dispatchEvent(new CustomEvent("frontier-theme-change"));
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className="flex h-8 w-8 items-center justify-center border border-[var(--ui-border)] text-[var(--fx-muted)] transition-colors hover:border-[hsl(var(--primary)/0.4)] hover:text-[hsl(var(--primary))]"
    >
      {theme === "dark" ? <IconSun className="h-[14px] w-[14px]" /> : <IconMoon className="h-[14px] w-[14px]" />}
    </button>
  );
}

function PasswordField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (name: string, value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input
        id={`auth-${field.name}`}
        name={field.name}
        type={visible ? "text" : "password"}
        placeholder={field.placeholder || field.label}
        autoComplete={field.autoComplete}
        required={field.required}
        aria-label={field.label}
        value={value}
        onChange={(event) => onChange(field.name, event.target.value)}
        className="h-10 w-full rounded-none border border-[var(--ui-border)] bg-[hsl(var(--background))] px-3 pr-10 font-mono text-[12px] text-[hsl(var(--foreground))] placeholder:text-[var(--fx-muted)] outline-none transition-colors focus:border-[hsl(var(--primary)/0.5)]"
      />
      <button
        type="button"
        onClick={() => setVisible((value) => !value)}
        tabIndex={-1}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--fx-muted)] transition-colors hover:text-[hsl(var(--primary))]"
      >
        {visible ? <IconEyeOff className="h-[14px] w-[14px]" /> : <IconEye className="h-[14px] w-[14px]" />}
      </button>
    </div>
  );
}

function TextField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (name: string, value: string) => void;
}) {
  return (
    <input
      id={`auth-${field.name}`}
      name={field.name}
      type={field.type}
      placeholder={field.placeholder}
      autoComplete={field.autoComplete}
      required={field.required}
      aria-label={field.label}
      value={value}
      onChange={(event) => onChange(field.name, event.target.value)}
      className="h-10 w-full rounded-none border border-[var(--ui-border)] bg-[hsl(var(--background))] px-3 font-mono text-[12px] text-[hsl(var(--foreground))] placeholder:text-[var(--fx-muted)] outline-none transition-colors focus:border-[hsl(var(--primary)/0.5)]"
    />
  );
}

function normalizeError(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = error.message.replace(/^Request failed \(\d+\):?\s*/i, "").trim();
    if (message) return message;
  }
  return fallback;
}

type LattixAuthCardProps = {
  initialErrorCode?: string | null;
};

export function LattixAuthCard({ initialErrorCode }: LattixAuthCardProps = {}) {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const initialError = resolveAuthErrorMessage(initialErrorCode ?? undefined);
  const [error, setError] = useState<string | null>(initialError);

  useEffect(() => {
    setError(initialError);
    // only when the server-provided error code changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialErrorCode]);

  const fields = mode === "signin" ? SIGNIN_FIELDS : SIGNUP_FIELDS;

  function switchMode(next: AuthMode) {
    setMode(next);
    setFormData({});
    setSubmitting(false);
    setError(next === "signin" ? resolveAuthErrorMessage(initialErrorCode ?? undefined) : null);
  }

  function handleFieldChange(name: string, value: string) {
    setFormData((prev) => ({ ...prev, [name]: value }));
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError(null);

    if (mode === "signup" && formData.password !== formData.confirmPassword) {
      setError(resolveAuthErrorMessage("passwords_mismatch"));
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "signin") {
        await loginWithLocalPassword({
          username: formData.email ?? "",
          password: formData.password ?? "",
        });
      } else {
        const firstName = (formData.firstName ?? "").trim();
        const lastName = (formData.lastName ?? "").trim();
        const displayName = `${firstName} ${lastName}`.trim() || formData.email || "Operator";
        await registerWithLocalPassword({
          username: formData.email ?? "",
          email: formData.email ?? "",
          display_name: displayName,
          password: formData.password ?? "",
        });
      }
      const session = await getOperatorSession();
      const destination = session.capabilities.can_builder && session.default_mode === "builder"
        ? "/builder/workflows"
        : "/inbox";
      router.replace(destination);
      router.refresh();
    } catch (err) {
      setError(
        normalizeError(
          err,
          mode === "signin"
            ? (resolveAuthErrorMessage("invalid_credentials") ?? "Sign-in failed.")
            : (resolveAuthErrorMessage("registration_failed") ?? "Registration failed."),
        ),
      );
    } finally {
      setSubmitting(false);
    }
  }

  const submitLabel = submitting
    ? mode === "signin"
      ? "Signing In…"
      : "Creating Account…"
    : mode === "signin"
      ? "Sign In"
      : "Create Account";

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: KEYFRAMES }} />

      {/* Grid background */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          backgroundImage:
            "linear-gradient(hsl(var(--border) / 0.15) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--border) / 0.15) 1px, transparent 1px)",
          backgroundSize: "60px 60px",
        }}
      />

      {/* Scanline overlay */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 z-[5]"
        style={{
          background:
            "repeating-linear-gradient(0deg, transparent, transparent 2px, hsl(var(--foreground) / 0.02) 2px, hsl(var(--foreground) / 0.02) 4px)",
        }}
      />

      {/* Corner brackets */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-6 z-10 hidden md:block">
        <CornerBracket position="top-0 left-0" />
        <CornerBracket position="top-0 right-0 rotate-90" />
        <CornerBracket position="bottom-0 right-0 rotate-180" />
        <CornerBracket position="bottom-0 left-0 -rotate-90" />
      </div>

      {/* Theme toggle */}
      <div className="fixed right-6 top-[44px] z-[9999]">
        <ThemeToggle />
      </div>

      {/* Login card */}
      <div
        className="relative z-20 mx-4 w-full max-w-[420px]"
        style={{ animation: "fx-fade-up-in 0.35s ease-out 0.05s both" }}
      >
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center" style={{ animation: "fx-fade-up-in 0.3s ease-out 0.075s both" }}>
          <BrandMark className="mb-4 h-14 w-14 rounded-[10px] border border-[var(--ui-border)] shadow-[0_8px_24px_rgba(0,0,0,0.25)]" />
          <span className="font-mono text-[13px] font-bold uppercase tracking-[0.3em] text-[hsl(var(--primary))]">
            Lattix
          </span>
          <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--fx-muted)]">
            Secure Access
          </span>
        </div>

        <div
          className="border border-[var(--ui-border)] border-t-2 border-t-[hsl(var(--primary))] bg-[hsl(var(--card)/0.6)] shadow-[0_24px_80px_rgba(0,0,0,0.15)] dark:shadow-[0_24px_80px_rgba(0,0,0,0.4)]"
          style={{ animation: "fx-fade-up-in 0.3s ease-out 0.125s both" }}
        >
          {/* Tabs */}
          <div role="tablist" aria-label="Authentication mode" className="flex border-b border-[var(--ui-border)]">
            {(["signin", "signup"] as const).map((value) => {
              const active = mode === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => switchMode(value)}
                  className={`relative flex-1 py-3 font-mono text-[10px] font-bold uppercase tracking-[0.14em] transition-colors ${
                    active
                      ? "bg-[hsl(var(--primary)/0.05)] text-[hsl(var(--primary))]"
                      : "text-[var(--fx-muted)] hover:bg-[hsl(var(--muted)/0.3)] hover:text-[hsl(var(--foreground))]"
                  }`}
                >
                  {value === "signin" ? "Sign In" : "Sign Up"}
                  {active ? (
                    <span aria-hidden="true" className="absolute inset-x-0 -bottom-px h-[2px] bg-[hsl(var(--primary))]" />
                  ) : null}
                </button>
              );
            })}
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-5 p-6 sm:p-8">
            <div className="mb-1 flex items-center gap-2">
              <IconShieldCheck className="h-[14px] w-[14px] text-[hsl(var(--primary))]" />
              <span className="font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
                {mode === "signin" ? "Sign in to your workspace" : "Create your workspace account"}
              </span>
            </div>

            <div className="space-y-4">
              {fields.map((field) => (
                <div key={field.name}>
                  <label
                    htmlFor={`auth-${field.name}`}
                    className="mb-1.5 block font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--fx-muted)]"
                  >
                    {field.label}
                  </label>
                  {field.type === "password" ? (
                    <PasswordField
                      field={field}
                      value={formData[field.name] ?? ""}
                      onChange={handleFieldChange}
                    />
                  ) : (
                    <TextField
                      field={field}
                      value={formData[field.name] ?? ""}
                      onChange={handleFieldChange}
                    />
                  )}
                </div>
              ))}
            </div>

            {error ? (
              <div
                role="alert"
                className="border border-[hsl(var(--state-critical)/0.3)] bg-[hsl(var(--state-critical)/0.05)] px-3 py-2 font-mono text-[10px] text-[hsl(var(--state-critical))]"
              >
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={submitting}
              className="h-11 w-full rounded-none bg-[hsl(var(--primary))] font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-[hsl(var(--foreground))] transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
              style={{
                color: "hsl(220 14% 14%)",
                animation: submitting ? "none" : "fx-glow-pulse 2.5s ease-in-out infinite",
              }}
            >
              {submitLabel}
            </button>
          </form>
        </div>

        {/* Footer */}
        <div
          className="mt-5 flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--fx-muted)]"
          style={{ animation: "fx-fade-up-in 0.3s ease-out 0.2s both" }}
        >
          <span>Lattix Technologies Corp</span>
          <span>Encrypted Channel</span>
        </div>
      </div>
    </>
  );
}
