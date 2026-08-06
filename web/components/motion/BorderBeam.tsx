"use client";

import type { CSSProperties } from "react";

type BorderBeamProps = {
  /** Show the beam only while an asynchronous activity is genuinely active. */
  active?: boolean;
  className?: string;
};

/**
 * A low-cost CSS border beam for active learning surfaces. It deliberately
 * has no timers or canvas: the effect pauses with reduced-motion and does not
 * compete with streamed content for the main thread.
 */
export function BorderBeam({ active = true, className = "" }: BorderBeamProps) {
  if (!active) return null;

  return (
    <span
      aria-hidden="true"
      className={`traittutor-border-beam ${className}`}
      style={{ "--beam-angle": "0deg" } as CSSProperties}
    />
  );
}
