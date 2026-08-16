import ResearchWorkspaceApp from "@/components/research/ResearchWorkspaceApp";

export default async function ResearchWorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return <ResearchWorkspaceApp workspaceId={workspaceId} />;
}
