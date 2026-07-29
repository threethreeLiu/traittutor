import SpaceMain from "@/components/space/SpaceMain";
import OnboardingProvider from "@/components/onboarding/OnboardingProvider";

export default function SpaceLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <OnboardingProvider><SpaceMain>{children}</SpaceMain></OnboardingProvider>;
}
