import OnboardingProvider from '@/components/onboarding/OnboardingProvider'
import SectionShell from '@/components/layout/SectionShell'
import { UnifiedChatProvider } from '@/context/UnifiedChatContext'

export default function LearningLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <UnifiedChatProvider>
      <OnboardingProvider>
        <SectionShell>{children}</SectionShell>
      </OnboardingProvider>
    </UnifiedChatProvider>
  )
}
