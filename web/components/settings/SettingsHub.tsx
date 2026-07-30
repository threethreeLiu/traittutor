"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Brain, Palette, ShieldCheck, type LucideIcon } from "lucide-react";

type Item = {
  href: string;
  icon: LucideIcon;
  label: { zh: string; en: string };
  description: { zh: string; en: string };
};

const ITEMS: Item[] = [
  {
    href: "/settings/appearance",
    icon: Palette,
    label: { zh: "界面外观", en: "Appearance" },
    description: { zh: "选择阅读主题与代码显示方式，修改会立即生效。", en: "Choose your reading theme and code display preferences." },
  },
  {
    href: "/profile/learning-model",
    icon: Brain,
    label: { zh: "我的学习模型", en: "My learning model" },
    description: { zh: "查看、纠正或清除用于个性化学习支持的证据与偏好。", en: "Review and manage the evidence and preferences used for learning support." },
  },
  {
    href: "/settings/account",
    icon: ShieldCheck,
    label: { zh: "账户与数据", en: "Account & data" },
    description: { zh: "管理账户安全，以及你在 TraitTutor 中保留的数据。", en: "Manage account security and your retained TraitTutor data." },
  },
];

/** Consumer settings: personal controls only; runtime configuration stays out of the learner UI. */
export default function SettingsHub() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = (value: { zh: string; en: string }) => (zh ? value.zh : value.en);

  return (
    <div className="max-w-3xl">
      <header className="border-b border-[var(--border)] pb-6">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-[var(--foreground)]">
          {tr({ zh: "设置", en: "Settings" })}
        </h1>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">
          {tr({ zh: "管理你看到的界面、学习个性化和账户数据。模型与服务由 TraitTutor 自动维护。", en: "Manage your interface, learning personalization, and account data. TraitTutor manages models and services automatically." })}
        </p>
      </header>

      <section className="mt-5 divide-y divide-[var(--border)] border-y border-[var(--border)]" aria-label={tr({ zh: "个人设置", en: "Personal settings" })}>
        {ITEMS.map(({ href, icon: Icon, label, description }) => (
          <Link key={href} href={href} className="group flex min-w-0 items-start gap-3 px-1 py-4 transition-colors hover:bg-[var(--muted)]/35 sm:px-3">
            <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[var(--muted)] text-[var(--primary)]"><Icon className="h-4.5 w-4.5" aria-hidden="true" /></span>
            <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-[var(--foreground)]">{tr(label)}</span><span className="mt-1 block max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr(description)}</span></span>
            <ArrowUpRight className="mt-2 h-4 w-4 shrink-0 text-[var(--muted-foreground)]/50 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-[var(--foreground)]" aria-hidden="true" />
          </Link>
        ))}
      </section>
    </div>
  );
}
