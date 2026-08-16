"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

export default function ResearchError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");

  useEffect(() => {
    console.error("Research route failed", error);
  }, [error]);

  return (
    <main className="h-full overflow-y-auto px-4 py-8 sm:px-6 lg:px-10">
      <div role="alert" className="mx-auto max-w-2xl rounded-xl border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-5">
        <h1 className="text-lg font-semibold">{zh ? "研究页面暂时无法显示" : "The research page is temporarily unavailable"}</h1>
        <p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">{zh ? "当前工作区数据没有被修改。你可以重试，或返回工作区列表。" : "No workspace data was changed. Try again or return to the workspace list."}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={reset} className={buttonClass}>{zh ? "重试" : "Try again"}</button>
          <Link href="/research" className={buttonClass}>{zh ? "返回工作区列表" : "Back to workspaces"}</Link>
        </div>
      </div>
    </main>
  );
}

const buttonClass = "inline-flex min-h-10 items-center justify-center rounded-md border border-[var(--border)] px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]";
