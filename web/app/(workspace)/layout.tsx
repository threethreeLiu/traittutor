import WorkspaceSidebar from "@/components/sidebar/WorkspaceSidebar";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import OnboardingProvider from "@/components/onboarding/OnboardingProvider";
import { UnifiedChatProvider } from "@/context/UnifiedChatContext";
import { MobileNavigation } from "@/components/sidebar/MobileNavigation";

export default function WorkspaceLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CapabilityAccessProvider>
      <UnifiedChatProvider>
        <div className="flex h-[100dvh] min-h-0 overflow-hidden">
          <WorkspaceSidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <MobileNavigation />
            <main className="min-h-0 min-w-0 flex-1 overflow-hidden bg-[var(--background)]">
              <OnboardingProvider>
                <CapabilityGate>{children}</CapabilityGate>
              </OnboardingProvider>
            </main>
          </div>
        </div>
      </UnifiedChatProvider>
    </CapabilityAccessProvider>
  );
}
