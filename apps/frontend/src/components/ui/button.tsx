import { Slot } from "@radix-ui/react-slot";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-[10px] text-[13px] font-medium no-underline transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0";

const VARIANTS = {
  default: "border border-primary bg-primary text-primary-foreground shadow-xs hover:bg-primary/90",
  secondary: "border border-border bg-card text-foreground shadow-xs hover:bg-muted",
  outline: "border border-border bg-transparent text-foreground hover:bg-muted",
  ghost: "border border-transparent bg-transparent text-foreground hover:bg-muted",
  destructive: "border border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90",
  link: "border border-transparent bg-transparent px-0 text-primary underline-offset-4 hover:underline",
} as const;

const SIZES = {
  default: "h-9 px-3.5",
  sm: "h-8 px-2.5 text-xs",
  lg: "h-10 px-5",
  icon: "size-8 p-0",
} as const;

export type ButtonVariant = keyof typeof VARIANTS;
export type ButtonSize = keyof typeof SIZES;

export function buttonVariants({
  variant = "default",
  size = "default",
  className,
}: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}): string {
  return cn(BASE, VARIANTS[variant], SIZES[size], className);
}

type ButtonProps = ComponentProps<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Render the child element (for example a Next `Link`) with button styling. */
  asChild?: boolean;
};

export function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  if (asChild) {
    return <Slot data-slot="button" className={buttonVariants({ variant, size, className })} {...props} />;
  }
  return (
    <button
      data-slot="button"
      className={buttonVariants({ variant, size, className })}
      type={type ?? "button"}
      {...props}
    />
  );
}
