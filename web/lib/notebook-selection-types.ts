// Shared types used by Notebook reference pickers.

export interface Notebook {
  id: string;
  name: string;
  description: string;
  record_count: number;
  color: string;
}

export interface NotebookRecord {
  id: string;
  title: string;
  summary?: string;
  user_query: string;
  output: string;
  type: string;
}

export interface SelectedRecord extends NotebookRecord {
  notebookId: string;
  notebookName: string;
}

export function getTypeColor(type: string): string {
  switch (type) {
    case "solve":
      return "bg-blue-100 text-blue-700 border-blue-200";
    case "question":
      return "bg-purple-100 text-purple-700 border-purple-200";
    case "research":
      return "bg-emerald-100 text-emerald-700 border-emerald-200";
    case "chat":
      return "bg-cyan-100 text-cyan-700 border-cyan-200";
    case "co_writer":
      return "bg-amber-100 text-amber-700 border-amber-200";
    default:
      return "bg-slate-100 text-slate-700 border-slate-200";
  }
}
