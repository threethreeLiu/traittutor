'use client'

import { Sparkles } from 'lucide-react'
import TutorPersonaEditor from '@/components/personalization/TutorPersonaEditor'
import { useAppShell } from '@/context/AppShellContext'

export default function TutorPersonaPage() {
  const { language } = useAppShell()
  const zh = language === 'zh'

  return (
    <main className="w-full">
      <header className="border-b border-[var(--border)] pb-7 pt-2">
        <div className="min-w-0">
          <p className="mb-3 text-[10px] font-medium uppercase tracking-[0.24em] text-[var(--primary)]">
            {zh ? '导师个性 · TUTOR PERSONA' : 'Tutor persona'}
          </p>
          <div className="flex items-center gap-2.5">
            <Sparkles className="h-[25px] w-[25px]" strokeWidth={1.65} aria-hidden="true" />
            <h1 className="font-serif text-[26px] font-semibold tracking-tight text-[var(--foreground)]">
              {zh ? '导师设置' : 'Tutor settings'}
            </h1>
          </div>
          <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {zh
              ? '调整导师的称呼、语气、反馈结构、语音与无障碍呈现；教学事实、判分和安全边界保持不变。'
              : 'Adjust the tutor’s address, tone, feedback structure, voice, and accessible presentation. Teaching facts, grading, and safety boundaries remain unchanged.'}
          </p>
        </div>
      </header>

      <div className="mt-6">
        <TutorPersonaEditor />
      </div>
    </main>
  )
}
