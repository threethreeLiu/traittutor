import UtilitySidebar from "@/components/sidebar/UtilitySidebar";
import { CapabilityAccessProvider } from "@/components/access/CapabilityAccessContext";
import CapabilityGate from "@/components/access/CapabilityGate";
import { MobileNavigation } from "@/components/sidebar/MobileNavigation";

export default function UtilityLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <CapabilityAccessProvider>
      <div className="flex h-[100dvh] min-h-0 overflow-hidden">
        <UtilitySidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNavigation />
          <main className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
            <CapabilityGate>{children}</CapabilityGate>
          </main>
        </div>
      </div>
    </CapabilityAccessProvider>
  );
}
