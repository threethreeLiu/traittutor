"use client";

import type { CSSProperties } from "react";

type ThinkingOrbProps = {
  size?: "sm" | "md" | "lg";
  className?: string;
};

/** A compact dot-field indicator for reasoning and generation states. */
export function ThinkingOrb({ size = "md", className = "" }: ThinkingOrbProps) {
  return (
    <span
      aria-hidden="true"
      className={`traittutor-thinking-orb traittutor-thinking-orb--${size} ${className}`}
    >
      {Array.from({ length: 13 }, (_, index) => (
        <i key={index} style={{ "--orb-index": index } as CSSProperties} />
      ))}
    </span>
  );
}
