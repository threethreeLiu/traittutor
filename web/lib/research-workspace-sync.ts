import type { ResearchWorkspaceSummary } from "@/lib/research-workspace-api";

const RESEARCH_WORKSPACES_UPDATED_EVENT = "traittutor:research-workspaces-updated";

export function publishResearchWorkspaces(workspaces: ResearchWorkspaceSummary[]): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<ResearchWorkspaceSummary[]>(RESEARCH_WORKSPACES_UPDATED_EVENT, {
      detail: workspaces,
    }),
  );
}

export function subscribeToResearchWorkspaces(
  listener: (workspaces: ResearchWorkspaceSummary[]) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;

  const handleUpdate = (event: Event) => {
    listener((event as CustomEvent<ResearchWorkspaceSummary[]>).detail);
  };
  window.addEventListener(RESEARCH_WORKSPACES_UPDATED_EVENT, handleUpdate);
  return () => window.removeEventListener(RESEARCH_WORKSPACES_UPDATED_EVENT, handleUpdate);
}
