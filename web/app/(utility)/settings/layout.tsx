import SettingsMain from "@/components/settings/SettingsMain";
import { SettingsProvider } from "@/components/settings/SettingsContext";

export default function SettingsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <SettingsProvider>
      <SettingsMain>{children}</SettingsMain>
    </SettingsProvider>
  );
}
