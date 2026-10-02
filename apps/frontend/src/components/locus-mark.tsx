/* eslint-disable @next/next/no-img-element -- static brand assets, no optimization needed */

type Props = {
  className?: string;
  /** "icon" is the square mark; "logo" adds the wordmark. */
  variant?: "icon" | "logo";
};

/**
 * Locus brand mark. Both theme variants are rendered and CSS shows the one that
 * matches the `theme-dark` / `theme-light` class on <html>, so there is no flash
 * or hydration mismatch when the theme is read from storage.
 */
export function LocusMark({ className, variant = "icon" }: Props) {
  const base = variant === "logo" ? "/brand/locus-logo" : "/brand/locus-icon";
  return (
    <>
      <img src={`${base}-dark.svg`} alt="Locus" className={`locus-mark-on-dark ${className ?? ""}`} />
      <img src={`${base}-light.svg`} alt="" aria-hidden="true" className={`locus-mark-on-light ${className ?? ""}`} />
    </>
  );
}
