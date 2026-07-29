"use client";

import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import type { Block } from "@/lib/book-types";

export interface TextBlockProps {
  block: Block;
}

export default function TextBlock({ block }: TextBlockProps) {
  const body = String(block.payload?.body ?? "");

  return (
    <div className="text-[var(--foreground)]">
      <MarkdownRenderer content={body} variant="prose" />
    </div>
  );
}
