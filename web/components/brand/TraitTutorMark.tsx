interface TraitTutorMarkProps {
  className?: string;
  title?: string;
}

/**
 * Theme-aware TraitTutor PNG mark. Every theme uses the same generated
 * geometry and canvas so switching palettes never shifts surrounding layout.
 */
export function TraitTutorMark({
  className,
  title = "TraitTutor",
}: TraitTutorMarkProps) {
  return (
    <span
      role="img"
      aria-label={title}
      title={title}
      data-testid="traittutor-mark"
      className={`traittutor-mark block shrink-0 ${className ?? ""}`}
    />
  );
}
