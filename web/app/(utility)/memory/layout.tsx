// Compatibility shell for old memory URLs. Routes redirect to the learner model.
export default function MemoryLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <main className="flex h-full min-h-0 flex-col bg-[var(--background)]">
      {children}
    </main>
  );
}
