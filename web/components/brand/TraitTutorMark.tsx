interface TraitTutorMarkProps {
  className?: string;
  title?: string;
}

/**
 * Theme-aware TraitTutor mark. The shape stays constant, while CSS palette
 * tokens supply a glass, warm, or clean-blue treatment at every display size.
 */
export function TraitTutorMark({
  className,
  title = "TraitTutor",
}: TraitTutorMarkProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      role="img"
      aria-label={title}
      className={`traittutor-mark block ${className ?? ""}`}
    >
      <title>{title}</title>
      <path
        className="traittutor-mark__glow"
        d="M13 17h38a6 6 0 0 1 0 12H39a7 7 0 0 0-7 7v15a6 6 0 0 1-12 0V39a10 10 0 0 0-10-10h3a6 6 0 0 1 0-12Z"
      />
      <path
        className="traittutor-mark__line"
        d="M13 17h38a6 6 0 0 1 0 12H39a7 7 0 0 0-7 7v15a6 6 0 0 1-12 0V39a10 10 0 0 0-10-10"
      />
      <circle className="traittutor-mark__node" cx="20" cy="39" r="4.5" />
    </svg>
  );
}
