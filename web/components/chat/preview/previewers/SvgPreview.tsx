"use client";

/**
 * SVG preview rendered with a native <img> (matches ChatComposer's pending
 * SVG chip). Scripts inside an SVG do not execute under the <img> context,
 * so this is safe even though next/image rejects SVG by default.
 */
export default function SvgPreview({
  url,
  filename,
}: {
  url: string;
  filename: string;
}) {
  return (
    <div className="flex h-full w-full items-center justify-center bg-[var(--muted)]/30 p-6">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={filename}
        className="max-h-full max-w-full object-contain"
      />
    </div>
  );
}
