import OnboardingProvider from '@/components/onboarding/OnboardingProvider'
import PersonalityProfilePage from '@/components/personalization/PersonalityProfilePage'

export default function PersonalitySettingsPage() {
  return (
    <OnboardingProvider>
      <PersonalityProfilePage />
    </OnboardingProvider>
  )
}
