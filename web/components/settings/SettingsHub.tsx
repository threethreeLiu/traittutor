'use client'

import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpRight,
  BrainCircuit,
  Palette,
  ShieldCheck,
  Sparkles,
  UserRound,
  type LucideIcon,
} from 'lucide-react'

type Item = {
  href: string
  icon: LucideIcon
  label: { zh: string; en: string }
  description: { zh: string; en: string }
}

const ITEMS: Item[] = [
  {
    href: '/settings/appearance',
    icon: Palette,
    label: { zh: '界面外观', en: 'Appearance' },
    description: {
      zh: '选择阅读主题与代码显示方式，修改会立即生效。',
      en: 'Choose your reading theme and code display preferences.',
    },
  },
  {
    href: '/settings/account',
    icon: ShieldCheck,
    label: { zh: '账户与数据', en: 'Account & data' },
    description: {
      zh: '管理账户安全，以及你在 TraitTutor 中保留的数据。',
      en: 'Manage account security and your retained TraitTutor data.',
    },
  },
  {
    href: '/settings/tutor',
    icon: Sparkles,
    label: { zh: '导师设置', en: 'Tutor settings' },
    description: {
      zh: '调整导师的称呼、语气、反馈方式和语音表达。',
      en: 'Adjust your tutor’s address, tone, feedback, and voice.',
    },
  },
  {
    href: '/settings/personality',
    icon: UserRound,
    label: { zh: '性格设置', en: 'Personality settings' },
    description: {
      zh: '查看和管理用于教学支持的性格画像。',
      en: 'Review and manage the personality profile used for teaching support.',
    },
  },
  {
    href: '/settings/learning-model',
    icon: BrainCircuit,
    label: { zh: '学习画像设置', en: 'Learning profile settings' },
    description: {
      zh: '查看学习证据、学科状态和复习治理。',
      en: 'Review learning evidence, subject state, and review governance.',
    },
  },
  {
    href: '/settings/memory',
    icon: BrainCircuit,
    label: { zh: '记忆设置', en: 'Memory settings' },
    description: {
      zh: '管理 TraitTutor 保存的记忆、候选和来源。',
      en: 'Manage retained memories, candidates, and provenance.',
    },
  },
]

/** Consumer settings: personal controls only; runtime configuration stays out of the learner UI. */
export default function SettingsHub() {
  const { i18n } = useTranslation()
  const zh = i18n.language?.toLowerCase().startsWith('zh')
  const tr = (value: { zh: string; en: string }) => (zh ? value.zh : value.en)

  return (
    <div className="w-full">
      <header className="border-b border-[var(--border)] pb-6">
        <h1 className="font-serif text-2xl font-semibold tracking-tight text-[var(--foreground)]">
          {tr({ zh: '设置', en: 'Settings' })}
        </h1>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">
          {tr({
            zh: '管理你看到的界面、学习个性化和账户数据。模型与服务由 TraitTutor 自动维护。',
            en: 'Manage your interface, learning personalization, and account data. TraitTutor manages models and services automatically.',
          })}
        </p>
      </header>

      <section
        className="mt-5 grid border-y border-[var(--border)] md:grid-cols-2 md:[&>*:nth-child(odd)]:border-r md:[&>*:nth-child(n+3)]:border-t"
        aria-label={tr({ zh: '个人设置', en: 'Personal settings' })}
      >
        {ITEMS.map(({ href, icon: Icon, label, description }) => (
          <Link
            key={href}
            href={href}
            className="group flex min-w-0 items-start gap-3 border-t border-[var(--border)] px-1 py-4 transition-colors first:border-t-0 hover:bg-[var(--muted)]/35 sm:px-3 md:border-t-0"
          >
            <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[var(--muted)] text-[var(--primary)]">
              <Icon className="h-4.5 w-4.5" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-[var(--foreground)]">
                {tr(label)}
              </span>
              <span className="mt-1 block max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">
                {tr(description)}
              </span>
            </span>
            <ArrowUpRight
              className="mt-2 h-4 w-4 shrink-0 text-[var(--muted-foreground)]/50 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-[var(--foreground)]"
              aria-hidden="true"
            />
          </Link>
        ))}
      </section>
    </div>
  )
}
