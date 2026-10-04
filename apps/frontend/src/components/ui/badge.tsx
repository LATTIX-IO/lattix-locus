import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const VARIANTS = {
  default: "border-transparent bg-primary text-primary-foreground",
  secondary: "border-border bg-muted text-foreground",
  outline: "border-border bg-transparent text-foreground",
  success: "border-success/40 bg-success/10 text-foreground",
  warning: "border-warning/50 bg-warning/10 text-foreground",
  destructive: "border-destructive/50 bg-destructive/10 text-foreground",
} as const;

export type BadgeVariant = keyof typeof VARIANTS;

/** Status is a glyph plus a word (P21): never let the colour carry the meaning alone. */
export function Badge({ className, variant = "secondary", ...props }: ComponentProps<"span"> & { variant?: BadgeVariant }) {
  return (
    <span
      data-slot="badge"
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium [&_svg]:size-3",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
