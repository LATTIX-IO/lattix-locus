/**
 * Minimal shadcn-style primitives on the Locus tokens (P31). Same markup in both variants, so any
 * styling difference comes from the chat library, not from these.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cn(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type ButtonVariant = "default" | "outline" | "destructive" | "ghost";

export function Button({
  variant = "default",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const styles: Record<ButtonVariant, string> = {
    default: "bg-primary text-primary-foreground hover:opacity-90",
    outline: "border border-border bg-card text-foreground hover:bg-muted",
    destructive: "bg-destructive text-destructive-foreground hover:opacity-90",
    ghost: "text-foreground hover:bg-muted",
  };
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "disabled:pointer-events-none disabled:opacity-50",
        styles[variant],
        className,
      )}
    />
  );
}

export type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "info";

export function Badge({ variant = "neutral", children }: { variant?: BadgeVariant; children?: ReactNode }) {
  const styles: Record<BadgeVariant, string> = {
    neutral: "bg-muted text-muted-foreground",
    success: "bg-success/15 text-success",
    warning: "bg-warning/15 text-warning",
    danger: "bg-destructive/15 text-destructive",
    info: "bg-info/15 text-info",
  };
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", styles[variant])}>{children}</span>;
}

export function Panel({ title, children, className, ...rest }: { title?: ReactNode; children?: ReactNode; className?: string; "aria-label"?: string; role?: string }) {
  return (
    <section {...rest} className={cn("rounded-[var(--radius)] border border-border bg-card p-3 text-card-foreground shadow-sm", className)}>
      {title ? <h3 className="mb-2 text-sm font-semibold">{title}</h3> : null}
      {children}
    </section>
  );
}
