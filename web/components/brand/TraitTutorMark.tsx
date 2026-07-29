interface TraitTutorMarkProps {
  className?: string;
  title?: string;
}

/** The approved TraitTutor graphic mark on a transparent canvas. */
export function TraitTutorMark({
  className,
  title = "TraitTutor",
}: TraitTutorMarkProps) {
  return (
    <svg
      viewBox="0 0 560 560"
      role="img"
      aria-label={title}
      className={`block overflow-hidden rounded-[22%] ${className ?? ""}`}
    >
      <title>{title}</title>
      <image
        href="/brand/traittutor-mark.png"
        width="560"
        height="560"
      />
    </svg>
  );
}
