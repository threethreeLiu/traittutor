"use client";

import dynamic from "next/dynamic";
import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams, useRouter } from "next/navigation";

import {
  Clapperboard,
  Code2,
  Compass,
  Database,
  FileSearch,
  Globe,
  Image as ImageIcon,
  Lightbulb,
  GraduationCap,
  PenLine,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SelectedRecord } from "@/lib/notebook-selection-types";
import type { SelectedHistorySession } from "@/components/chat/HistorySessionPicker";
import type { SelectedQuestionEntry } from "@/components/chat/QuestionBankPicker";
import ChatComposer from "@/components/chat/home/ChatComposer";
import { ChatMessageList } from "@/components/chat/home/ChatMessages";
import SessionLoadingView from "@/components/chat/home/SessionLoadingView";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
// Imported eagerly so the drawer shell is always mounted off-screen —
// clicking a chip becomes a single CSS class flip, no chunk fetch + double
// render. The heavy renderers inside still load lazily.
import FilePreviewDrawer from "@/components/chat/preview/FilePreviewDrawer";
import { buildSessionActivity } from "@/components/chat/home/SessionActivityPanel";
import Tooltip from "@/components/common/Tooltip";
import SessionViewerPanel, {
  type SessionViewerPanelHandle,
} from "@/components/chat/home/SessionViewerPanel";
import {
  QuizFollowupProvider,
  useQuizFollowupController,
} from "@/context/QuizFollowupContext";
import {
  GeogebraTabProvider,
  useGeogebraTabOpener,
} from "@/context/GeogebraTabContext";
import { Download, PanelRight } from "lucide-react";
import {
  useUnifiedChat,
  type MessageAttachment,
} from "@/context/UnifiedChatContext";
import { useAppShell } from "@/context/AppShellContext";
import type { FilePreviewSource } from "@/components/chat/preview/previewerFor";
import type { LLMSelection, StreamEvent } from "@/lib/unified-ws";
import {
  extractBase64FromDataUrl,
  readFileAsDataUrl,
} from "@/lib/file-attachments";
import { classifyFile, isSvgFilename } from "@/lib/doc-attachments";
import { useAttachmentLimits } from "@/lib/attachment-limits";
import { useChatAutoScroll } from "@/hooks/useChatAutoScroll";
import { useMeasuredHeight } from "@/hooks/useMeasuredHeight";
import {
  DEFAULT_QUIZ_CONFIG,
  buildQuizWSConfig,
} from "@/lib/quiz-types";
import {
  DEFAULT_VISUALIZE_CONFIG,
  buildVisualizeWSConfig,
} from "@/lib/visualize-types";
import {
  buildResearchWSConfig,
} from "@/lib/research-types";
import { listKnowledgeBases } from "@/lib/knowledge-api";
import { getSubagentSettings } from "@/lib/subagents-api";
import { listLLMOptions, type LLMOption } from "@/lib/llm-options";
import {
  getEnabledOptionalTools,
  invalidateEnabledOptionalToolsCache,
} from "@/lib/tools-settings";
import { downloadChatMarkdown } from "@/lib/chat-export";
import type { SpaceMemoryFile } from "@/lib/space-items";
import {
  selectedBooksToPayload,
  type SelectedBookReference,
} from "@/lib/book-references";

const NotebookRecordPicker = dynamic(
  () => import("@/components/notebook/NotebookRecordPicker"),
  {
    ssr: false,
  },
);
const HistorySessionPicker = dynamic(
  () => import("@/components/chat/HistorySessionPicker"),
  {
    ssr: false,
  },
);
const MyAgentsPicker = dynamic(
  () => import("@/components/chat/MyAgentsPicker"),
  {
    ssr: false,
  },
);
const QuestionBankPicker = dynamic(
  () => import("@/components/chat/QuestionBankPicker"),
  {
    ssr: false,
  },
);
const MemoryPicker = dynamic(() => import("@/components/chat/MemoryPicker"), {
  ssr: false,
});
const BookReferencePicker = dynamic(
  () => import("@/components/chat/BookReferencePicker"),
  {
    ssr: false,
  },
);
/* ------------------------------------------------------------------ */
/*  Type & data definitions                                           */
/* ------------------------------------------------------------------ */

type ToolName =
  | "brainstorm"
  | "geogebra_analysis"
  | "web_search"
  | "code_execution"
  | "reason"
  | "paper_search"
  | "imagegen"
  | "videogen";

interface ToolDef {
  name: ToolName;
  label: string;
  icon: LucideIcon;
}

const ALL_TOOLS: ToolDef[] = [
  { name: "brainstorm", label: "Brainstorm", icon: Lightbulb },
  { name: "geogebra_analysis", label: "GeoGebra", icon: Compass },
  { name: "web_search", label: "Web Search", icon: Globe },
  { name: "code_execution", label: "Code", icon: Code2 },
  { name: "reason", label: "Reason", icon: Sparkles },
  { name: "paper_search", label: "Arxiv Search", icon: FileSearch },
  { name: "imagegen", label: "Image Gen", icon: ImageIcon },
  { name: "videogen", label: "Video Gen", icon: Clapperboard },
];

interface CapabilityDef {
  value: string;
  label: string;
  description: string;
  icon: TraitTutorIconName;
  allowedTools: ToolName[];
  defaultTools: ToolName[];
  // Loop-engine capabilities run on the chat agent loop (solve / mastery) rather
  // than a bespoke pipeline. They are collapsed into the "More" flyout in the
  // capability picker instead of listed directly. Driven by the loop-capability
  // registry on the backend; mirrored here as a static flag.
  loopEngine?: boolean;
}

const CAPABILITIES: CapabilityDef[] = [
  {
    value: "",
    label: "Chat",
    description: "Flexible conversation with any tool",
    icon: "chat",
    allowedTools: [
      "brainstorm",
      "geogebra_analysis",
      "web_search",
      "code_execution",
      "reason",
      "paper_search",
      "imagegen",
      "videogen",
    ],
    defaultTools: [],
  },
  {
    value: "deep_solve",
    label: "Guided Solve",
    description: "Work through a problem with guided reasoning",
    icon: "solve",
    allowedTools: ["web_search", "code_execution", "reason"],
    defaultTools: ["web_search", "code_execution", "reason"],
    loopEngine: true,
  },
  {
    value: "deep_question",
    label: "Generate Quiz",
    description: "Create a practice quiz with review",
    icon: "quiz",
    allowedTools: ["web_search"],
    defaultTools: ["web_search"],
  },
  {
    value: "deep_research",
    label: "Deep Research",
    description: "Comprehensive multi-agent research",
    icon: "research",
    allowedTools: ["web_search", "paper_search"],
    defaultTools: ["web_search", "paper_search"],
  },
  {
    value: "visualize",
    label: "Knowledge Map",
    description: "Turn ideas into diagrams, charts, and interactive views",
    icon: "visualize",
    allowedTools: [],
    defaultTools: [],
  },
  {
    value: "mastery_path",
    label: "Learning Path",
    description: "Practice, feedback, and review at your pace",
    icon: "mastery",
    // The mastery tools (status/quiz/grade/assess/build) auto-mount server-side
    // when this capability is active; rag auto-mounts when a KB is attached.
    // These are only the extra optional tools the tutor may also reach for.
    allowedTools: ["web_search", "code_execution"],
    defaultTools: [],
    loopEngine: true,
  },
];

interface KnowledgeBase {
  name: string;
  is_default?: boolean;
  metadata?: {
    /** Connected-source kind, e.g. "obsidian" | "subagent". */
    type?: string;
    /** Backend of a connected subagent: "claude_code" | "codex" | "partner". */
    agent_kind?: string;
  };
}

interface PendingAttachment {
  type: string;
  filename: string;
  base64?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function getCapability(value: string | null): CapabilityDef {
  return CAPABILITIES.find((c) => c.value === (value || "")) ?? CAPABILITIES[0];
}

/* ------------------------------------------------------------------ */
/*  Chat page                                                         */
/* ------------------------------------------------------------------ */

export default function ChatPage() {
  const router = useRouter();
  const params = useParams<{ sessionId?: string[] }>();
  const { t } = useTranslation();
  const sessionIdParam = params.sessionId?.[0] ?? null;
  const { setActiveSessionId, language: appLanguage } = useAppShell();

  const {
    state,
    setTools,
    setCapability,
    setKBs,
    setLLMSelection,
    setPersonaSelection,
    sendMessage,
    cancelStreamingTurn,
    submitUserReply,
    regenerateLastMessage,
    deleteTurn,
    editMessage,
    switchBranch,
    newSession,
    loadSession,
    renameSessionTitle,
  } = useUnifiedChat();

  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  // A connected agent to preselect once it loads, from `?agent=<name>` on the
  // URL (the partner list page links here to drop straight into a chat with a
  // partner). Captured once at first client render — the URL is rewritten to
  // `/home/<sessionId>` as soon as the new session is created, dropping the
  // query — so we can't read it later from the live search params.
  const pendingAgentRef = useRef<string | null | undefined>(undefined);
  if (pendingAgentRef.current === undefined) {
    pendingAgentRef.current =
      typeof window === "undefined"
        ? null
        : new URLSearchParams(window.location.search).get("agent");
  }
  const agentPreselectDoneRef = useRef(false);
  const [llmOptions, setLLMOptions] = useState<LLMOption[]>([]);
  const [activeLLMDefault, setActiveLLMDefault] = useState<LLMSelection | null>(
    null,
  );
  const [llmOptionsLoading, setLLMOptionsLoading] = useState(true);
  const [llmOptionsError, setLLMOptionsError] = useState(false);
  // User-toggleable tools the user has enabled in /settings/tools. This is
  // the single source of truth for which optional tools the chat agent may
  // use; the chat composer no longer exposes a picker.
  const [userEnabledTools, setUserEnabledTools] = useState<string[] | null>(
    null,
  );
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [chatGenerationKind, setChatGenerationKind] = useState<
    "courseware" | "flashcards" | "humanizer" | null
  >(null);
  const attachmentLimits = useAttachmentLimits();
  const [dragging, setDragging] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [previewSource, setPreviewSource] = useState<FilePreviewSource | null>(
    null,
  );
  // Right-side panels — Activity (floating cards) and Viewer (full sidebar
  // with tabs for file previews + web pages). Each independently togglable
  // and persisted across reloads.
  //
  // We initialise both to `false` so the SSR-rendered HTML matches the
  // first client render exactly (no hydration mismatch). The persisted
  // preference is then applied in a post-mount effect below.
  // Single right-side panel: the Activity/Viewer. Its home view is the
  // session activity; files and web pages open as tabs alongside it.
  const [viewerPanelOpen, setViewerPanelOpen] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem("dt:chat:viewer-panel") === "1") {
      setViewerPanelOpen(true);
    }
  }, []);
  const setViewerOpen = useCallback((next: boolean) => {
    setViewerPanelOpen(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("dt:chat:viewer-panel", next ? "1" : "0");
    }
  }, []);
  const toggleViewerPanel = useCallback(() => {
    setViewerPanelOpen((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem("dt:chat:viewer-panel", next ? "1" : "0");
      }
      return next;
    });
  }, []);
  const viewerPanelRef = useRef<SessionViewerPanelHandle | null>(null);
  const attachmentErrorTimer = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const [capMenuOpen, setCapMenuOpen] = useState(false);
  const [showNotebookPicker, setShowNotebookPicker] = useState(false);
  const [showBookPicker, setShowBookPicker] = useState(false);
  const [showHistoryPicker, setShowHistoryPicker] = useState(false);
  const [showAgentsPicker, setShowAgentsPicker] = useState(false);
  const [showQuestionBankPicker, setShowQuestionBankPicker] = useState(false);
  // Session persona selector (toolbar chip / `/persona` / @space entry all
  // open the same dropdown). The selection itself lives in the unified chat
  // context (state.personaSelection) so it follows the session.
  const [personaSelectorOpen, setPersonaSelectorOpen] = useState(false);
  const [showMemoryPicker, setShowMemoryPicker] = useState(false);
  // Product-level intent: this is deliberately separate from the legacy
  // capability picker. The backend routes it through TraitTutor's internal
  // LangGraph agents instead of external CLI/Partner connections.
  const [productMode, setProductMode] = useState<"learn" | "assist">("assist");
  const [spaceMenuOpen, setSpaceMenuOpen] = useState(false);
  const [selectedNotebookRecords, setSelectedNotebookRecords] = useState<
    SelectedRecord[]
  >([]);
  const [selectedBookReferences, setSelectedBookReferences] = useState<
    SelectedBookReference[]
  >([]);
  const [selectedHistorySessions, setSelectedHistorySessions] = useState<
    SelectedHistorySession[]
  >([]);
  // Imported-agent conversation references. Same shape as history sessions —
  // they fold into the same history_references payload (see below), so the
  // backend treats them identically; the separate state only keeps the
  // composer's "My Agents" group distinct from "Chat History".
  const [selectedAgentSessions, setSelectedAgentSessions] = useState<
    SelectedHistorySession[]
  >([]);
  const [selectedQuestionEntries, setSelectedQuestionEntries] = useState<
    SelectedQuestionEntry[]
  >([]);
  const [selectedMemoryFiles, setSelectedMemoryFiles] = useState<
    SpaceMemoryFile[]
  >([]);
  const dragCounter = useRef(0);
  const capMenuRef = useRef<HTMLDivElement>(null);
  const capBtnRef = useRef<HTMLButtonElement>(null);
  const spaceMenuRef = useRef<HTMLDivElement>(null);
  const spaceBtnRef = useRef<HTMLButtonElement>(null);
  const initialLoadRef = useRef(false);
  // Session-loading overlay: shown while navigating from chat-history →
  // session detail. Holds an AbortController so the user can cancel.
  const [sessionLoading, setSessionLoading] = useState(false);
  const loadAbortRef = useRef<AbortController | null>(null);
  // Bridge ref: ``ChatComposer`` writes a prefill function into this on
  // mount; ``ChatMessageList`` reads it via ``handlePrefillComposer`` so an
  // ``AskUserOptions`` chip click can drop text into the composer textarea.
  const prefillInputRef = useRef<((text: string) => void) | null>(null);
  const handlePrefillComposer = useCallback((text: string) => {
    prefillInputRef.current?.(text);
  }, []);

  // A clickable node inside an inlined visualization SVG (data-prompt) — and the
  // html widget's sendPrompt bridge — dispatch this window event; mirror it into
  // the composer as a prefilled follow-up (user confirms before sending).
  useEffect(() => {
    const onVizPrompt = (e: Event) => {
      const text = (e as CustomEvent<string>).detail;
      if (typeof text === "string" && text) handlePrefillComposer(text);
    };
    window.addEventListener("dt:visualize-prompt", onVizPrompt);
    return () => window.removeEventListener("dt:visualize-prompt", onVizPrompt);
  }, [handlePrefillComposer]);

  const activeCap = useMemo(
    () => getCapability(state.activeCapability),
    [state.activeCapability],
  );
  const isQuizMode = activeCap.value === "deep_question";
  const isVisualizeMode = activeCap.value === "visualize";
  const isResearchMode = activeCap.value === "deep_research";

  const hasMessages = state.messages.length > 0;
  // Time-of-day greeting: seeded once on mount from the user's local clock so
  // the heading stays stable while they're on the page. State (not useMemo)
  // because the random pick would otherwise mismatch SSR ↔ client hydration.
  const [welcomeGreeting, setWelcomeGreeting] = useState<string>(
    "What would you like to learn?",
  );
  useEffect(() => {
    const hour = new Date().getHours();
    let bucket: string[];
    if (hour >= 5 && hour < 12) {
      bucket = [
        "Good morning.",
        "Morning — let's learn something.",
        "What would you like to learn?",
      ];
    } else if (hour >= 12 && hour < 17) {
      bucket = [
        "Good afternoon.",
        "Afternoon — what's on your mind?",
        "What would you like to learn?",
      ];
    } else if (hour >= 17 && hour < 22) {
      bucket = [
        "Good evening.",
        "Evening — what shall we explore?",
        "What would you like to learn?",
      ];
    } else {
      bucket = [
        "It's late today.",
        "Burning the midnight oil?",
        "What would you like to learn?",
      ];
    }
    setWelcomeGreeting(bucket[Math.floor(Math.random() * bucket.length)]);
  }, []);
  const firstUserTitle = useMemo(
    () =>
      state.messages
        .find((msg) => msg.role === "user")
        ?.content.trim()
        .replace(/\s+/g, " ")
        .slice(0, 80) || "",
    [state.messages],
  );
  const persistedSessionTitle = state.sessionTitle.trim();
  // The backend's empty-session sentinel is English. Never surface it as a
  // user-facing title; use the locale label until a real title is available.
  const hasPlaceholderSessionTitle =
    persistedSessionTitle === "New conversation" ||
    persistedSessionTitle === "New chat";
  const displaySessionTitle =
    (hasPlaceholderSessionTitle ? "" : persistedSessionTitle) ||
    firstUserTitle ||
    t("New chat");
  const canRenameSession = Boolean(state.sessionId);
  const titleInputRef = useRef<HTMLInputElement | null>(null);
  const skipTitleCommitRef = useRef(false);
  const [sessionTitleDraft, setSessionTitleDraft] =
    useState(displaySessionTitle);
  const [sessionTitleEditing, setSessionTitleEditing] = useState(false);
  const [sessionTitleSaving, setSessionTitleSaving] = useState(false);
  const [sessionTitleError, setSessionTitleError] = useState<string | null>(
    null,
  );
  useEffect(() => {
    if (sessionTitleEditing) return;
    setSessionTitleDraft(displaySessionTitle);
  }, [displaySessionTitle, sessionTitleEditing]);
  useEffect(() => {
    if (!sessionTitleEditing) return;
    window.requestAnimationFrame(() => {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    });
  }, [sessionTitleEditing]);
  const startSessionTitleEdit = useCallback(() => {
    if (!canRenameSession) return;
    skipTitleCommitRef.current = false;
    setSessionTitleError(null);
    setSessionTitleDraft(displaySessionTitle);
    setSessionTitleEditing(true);
  }, [canRenameSession, displaySessionTitle]);
  const cancelSessionTitleEdit = useCallback(() => {
    skipTitleCommitRef.current = true;
    setSessionTitleDraft(displaySessionTitle);
    setSessionTitleError(null);
    setSessionTitleEditing(false);
  }, [displaySessionTitle]);
  const commitSessionTitleEdit = useCallback(async () => {
    if (skipTitleCommitRef.current) {
      skipTitleCommitRef.current = false;
      return;
    }
    const next = sessionTitleDraft.trim();
    if (!next) {
      setSessionTitleDraft(displaySessionTitle);
      setSessionTitleEditing(false);
      return;
    }
    if (!canRenameSession || next === persistedSessionTitle) {
      setSessionTitleDraft(next || displaySessionTitle);
      setSessionTitleEditing(false);
      return;
    }
    setSessionTitleSaving(true);
    setSessionTitleError(null);
    try {
      await renameSessionTitle(next);
      setSessionTitleEditing(false);
    } catch (error) {
      console.error("Failed to rename session:", error);
      setSessionTitleError(t("Rename failed"));
      titleInputRef.current?.focus();
    } finally {
      setSessionTitleSaving(false);
    }
  }, [
    canRenameSession,
    displaySessionTitle,
    persistedSessionTitle,
    renameSessionTitle,
    sessionTitleDraft,
    t,
  ]);
  const handleSessionTitleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void commitSessionTitleEdit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancelSessionTitleEdit();
      }
    },
    [cancelSessionTitleEdit, commitSessionTitleEdit],
  );
  const { ref: composerRef, height: composerHeight } =
    useMeasuredHeight<HTMLDivElement>();
  const notebookReferenceGroups = useMemo(() => {
    const groups = new Map<string, { notebookName: string; count: number }>();
    selectedNotebookRecords.forEach((record) => {
      const existing = groups.get(record.notebookId);
      if (existing) {
        existing.count += 1;
      } else {
        groups.set(record.notebookId, {
          notebookName: record.notebookName,
          count: 1,
        });
      }
    });
    return Array.from(groups.entries()).map(([notebookId, value]) => ({
      notebookId,
      ...value,
    }));
  }, [selectedNotebookRecords]);
  const notebookReferencesPayload = useMemo(() => {
    const grouped = new Map<string, string[]>();
    selectedNotebookRecords.forEach((record) => {
      const current = grouped.get(record.notebookId) || [];
      current.push(record.id);
      grouped.set(record.notebookId, current);
    });
    return Array.from(grouped.entries()).map(([notebook_id, record_ids]) => ({
      notebook_id,
      record_ids,
    }));
  }, [selectedNotebookRecords]);
  const bookReferencesPayload = useMemo(
    () => selectedBooksToPayload(selectedBookReferences),
    [selectedBookReferences],
  );
  // Chat-history and imported-agent references are both just session ids and
  // share one backend field. Merge + de-dupe them here.
  const historyReferencesPayload = useMemo(
    () =>
      Array.from(
        new Set([
          ...selectedHistorySessions.map((session) => session.sessionId),
          ...selectedAgentSessions.map((session) => session.sessionId),
        ]),
      ),
    [selectedHistorySessions, selectedAgentSessions],
  );
  const questionNotebookReferencesPayload = useMemo(
    () => selectedQuestionEntries.map((entry) => entry.id),
    [selectedQuestionEntries],
  );
  const memoryReferencesPayload = useMemo(
    () => [...selectedMemoryFiles],
    [selectedMemoryFiles],
  );
  const lastMessage = state.messages[state.messages.length - 1];
  const {
    containerRef: messagesContainerRef,
    endRef: messagesEndRef,
    shouldAutoScrollRef,
    handleScroll: handleMessagesScroll,
  } = useChatAutoScroll({
    hasMessages,
    isStreaming: state.isStreaming,
    composerHeight,
    messageCount: state.messages.length,
    lastMessageContent: lastMessage?.content,
    lastEventCount: lastMessage?.events?.length,
  });
  const copyAssistantMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;
    try {
      await navigator.clipboard.writeText(content);
    } catch (error) {
      console.error("Failed to copy assistant message:", error);
    }
  }, []);
  /* ---- URL-driven session loading ---- */

  const navigateToHome = useCallback(() => {
    router.replace("/home", { scroll: false });
  }, [router]);

  /** Abort in-flight load + navigate home. */
  const cancelSessionLoad = useCallback(() => {
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
    setSessionLoading(false);
    navigateToHome();
  }, [navigateToHome]);

  /**
   * Shared helper: kick off a load. The user can cancel via the ✕ button;
   * otherwise the loading overlay stays until the API responds (no timeout).
   */
  const startSessionLoad = useCallback(
    (sid: string) => {
      loadAbortRef.current?.abort();
      const ctrl = new AbortController();
      loadAbortRef.current = ctrl;
      setSessionLoading(true);

      void loadSession(sid, ctrl.signal)
        .then(() => {
          if (!ctrl.signal.aborted) {
            loadAbortRef.current = null;
            setSessionLoading(false);
          }
        })
        .catch(() => {
          if (!ctrl.signal.aborted) {
            loadAbortRef.current = null;
            setSessionLoading(false);
            navigateToHome();
          }
        });
    },
    [loadSession, navigateToHome],
  );

  // Initial mount — load the session from the URL.
  // Uses a ref-based flag so Strict Mode double-mount doesn't break the flow:
  // when React tears down + re-mounts in dev, we reset initialLoadRef in
  // cleanup so the second mount restarts the load cleanly. The abort is
  // deliberately OMITTED from cleanup — cancelSessionLoad handles
  // user-initiated cancellation.
  useEffect(() => {
    if (initialLoadRef.current) return;
    initialLoadRef.current = true;
    if (sessionIdParam) {
      startSessionLoad(sessionIdParam);
    } else {
      newSession();
    }
    return () => {
      initialLoadRef.current = false;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // When URL param changes (sidebar navigation), load the corresponding session
  const prevSessionIdParam = useRef(sessionIdParam);
  useEffect(() => {
    if (sessionIdParam === prevSessionIdParam.current) return;
    prevSessionIdParam.current = sessionIdParam;
    // Abort any in-flight session load from the previous param
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
    if (sessionIdParam) {
      if (sessionIdParam === state.sessionId) {
        setSessionLoading(false);
        return;
      }
      startSessionLoad(sessionIdParam);
    } else {
      newSession();
      setSessionLoading(false);
    }
  }, [sessionIdParam, startSessionLoad, newSession, state.sessionId]);

  // When a new session_id is assigned by the server, update the URL
  useEffect(() => {
    if (state.sessionId && !sessionIdParam) {
      router.replace(`/home/${state.sessionId}`, { scroll: false });
    }
  }, [state.sessionId, sessionIdParam, router]);

  useEffect(() => {
    setActiveSessionId(state.sessionId || sessionIdParam || null);
  }, [state.sessionId, sessionIdParam, setActiveSessionId]);

  const refreshKnowledgeBases = useCallback(
    async (options?: { force?: boolean }) => {
      try {
        const list = await listKnowledgeBases({ force: options?.force });
        setKnowledgeBases(list);
      } catch {
        setKnowledgeBases([]);
      }
    },
    [],
  );

  /* Load KBs */
  useEffect(() => {
    void refreshKnowledgeBases({ force: true });
  }, [refreshKnowledgeBases]);

  const refreshUserEnabledTools = useCallback(
    async (options?: { force?: boolean }) => {
      try {
        const list = await getEnabledOptionalTools({ force: options?.force });
        setUserEnabledTools(list);
      } catch {
        setUserEnabledTools([]);
      }
    },
    [],
  );

  /* Load user tool prefs */
  useEffect(() => {
    void refreshUserEnabledTools({ force: true });
  }, [refreshUserEnabledTools]);

  const refreshLLMOptions = useCallback(async () => {
    setLLMOptionsLoading(true);
    try {
      const payload = await listLLMOptions();
      setLLMOptions(payload.options);
      setActiveLLMDefault(payload.active);
      setLLMOptionsError(false);
    } catch {
      setLLMOptionsError(true);
      setLLMOptions([]);
      setActiveLLMDefault(null);
    } finally {
      setLLMOptionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshLLMOptions();
  }, [refreshLLMOptions]);

  useEffect(() => {
    if (state.llmSelection || !activeLLMDefault) return;
    setLLMSelection(activeLLMDefault);
  }, [activeLLMDefault, setLLMSelection, state.llmSelection]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const refresh = () => {
      void refreshKnowledgeBases({ force: true });
      void refreshLLMOptions();
      // Picks up toggles the user changed in another tab (/settings/tools).
      invalidateEnabledOptionalToolsCache();
      void refreshUserEnabledTools({ force: true });
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", refresh);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", refresh);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [refreshKnowledgeBases, refreshLLMOptions, refreshUserEnabledTools]);

  /* URL query params (capability, tool) */
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    const qc = p.get("capability");
    const qt = p.getAll("tool");
    if (qc !== null) handleSelectCapability(qc || "");
    else if (qt.length) {
      const valid = qt.filter((t): t is ToolName =>
        ALL_TOOLS.some((d) => d.name === t),
      );
      if (valid.length) setTools(Array.from(new Set(valid)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (
        capMenuRef.current &&
        !capMenuRef.current.contains(t) &&
        capBtnRef.current &&
        !capBtnRef.current.contains(t)
      )
        setCapMenuOpen(false);
      if (
        spaceMenuRef.current &&
        !spaceMenuRef.current.contains(t) &&
        spaceBtnRef.current &&
        !spaceBtnRef.current.contains(t)
      )
        setSpaceMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Keep state.enabledTools = (user's toggleable set) ∩ (capability's allowed
  // set). Re-runs when the user flips a toggle in /settings/tools or when
  // the active capability changes. The composer no longer owns this — the
  // /settings/tools page is the single switchboard.
  useEffect(() => {
    if (userEnabledTools === null) return;
    const allowed = new Set(activeCap.allowedTools);
    const next = userEnabledTools.filter((tool) =>
      allowed.has(tool as ToolName),
    );
    const current = state.enabledTools;
    const same =
      current.length === next.length &&
      current.every((tool, idx) => tool === next[idx]);
    if (!same) setTools(next);
  }, [activeCap.allowedTools, setTools, state.enabledTools, userEnabledTools]);

  /* ---- handlers ---- */

  const handleSelectCapability = useCallback(
    (value: string) => {
      const cap =
        CAPABILITIES.find((c) => c.value === value) ?? CAPABILITIES[0];
      setCapability(cap.value || null);
      // Per-capability tool selection now derives from the user's saved
      // settings (/settings/tools) intersected with the capability's
      // allow-list. Playground-saved configs still override when the user
      // explicitly pinned tools in the playground for this capability.
      const baseline =
        userEnabledTools === null ? cap.allowedTools : userEnabledTools;
      const enabledToolsForCap = baseline.filter((tool) =>
        cap.allowedTools.includes(tool as ToolName),
      );
      setTools(enabledToolsForCap);
      setCapMenuOpen(false);
    },
    [setCapability, setTools, userEnabledTools],
  );

  const handleSelectGenerationShortcut = useCallback(
    (kind: "courseware" | "flashcards" | "research" | "humanizer") => {
      setCapMenuOpen(false);
      if (kind === "research") {
        setChatGenerationKind(null);
        handleSelectCapability("deep_research");
        return;
      }
      setCapability(null);
      setChatGenerationKind(kind);
    },
    [handleSelectCapability, setCapability],
  );

  const fileToAttachment = useCallback(
    (f: File): Promise<PendingAttachment> =>
      new Promise((resolve, reject) => {
        readFileAsDataUrl(f)
          .then((raw) => {
            // SVG: treat as file (text extraction on server, vision models
            // reject SVG) but keep the data URL so the chip can render a
            // thumbnail via a raw <img> tag.
            const svg = isSvgFilename(f.name) || f.type === "image/svg+xml";
            const isImage = !svg && f.type.startsWith("image/");
            const b64 = extractBase64FromDataUrl(raw);
            resolve({
              type: isImage ? "image" : "file",
              filename: f.name,
              base64: b64,
              previewUrl: isImage || svg ? raw : undefined,
              size: f.size,
              mimeType: f.type || undefined,
            });
          })
          .catch(reject);
      }),
    [],
  );

  const showAttachmentError = useCallback((message: string) => {
    setAttachmentError(message);
    if (attachmentErrorTimer.current) {
      clearTimeout(attachmentErrorTimer.current);
    }
    attachmentErrorTimer.current = setTimeout(() => {
      setAttachmentError(null);
      attachmentErrorTimer.current = null;
    }, 4000);
  }, []);

  const filterAndReportFiles = useCallback(
    (files: File[]): File[] => {
      let runningTotal = attachments.reduce((s, a) => s + (a.size ?? 0), 0);
      const accepted: File[] = [];
      const rejected: {
        name: string;
        reason: "unsupported" | "too_large" | "quota";
      }[] = [];
      for (const f of files) {
        const kind = classifyFile(f);
        if (!kind) {
          rejected.push({ name: f.name, reason: "unsupported" });
          continue;
        }
        if (f.size > attachmentLimits.maxFileBytes) {
          rejected.push({ name: f.name, reason: "too_large" });
          continue;
        }
        if (runningTotal + f.size > attachmentLimits.maxTotalBytes) {
          rejected.push({ name: f.name, reason: "quota" });
          break;
        }
        runningTotal += f.size;
        accepted.push(f);
      }
      if (rejected.length) {
        const first = rejected[0];
        let msg: string;
        if (first.reason === "too_large") {
          msg = t("File too large: {{name}}", { name: first.name });
        } else if (first.reason === "quota") {
          msg = t("Too many files, skipped some");
        } else {
          msg = t("Unsupported file type: {{name}}", { name: first.name });
        }
        showAttachmentError(msg);
      }
      return accepted;
    },
    [attachments, attachmentLimits, showAttachmentError, t],
  );

  const handlePaste = useCallback(
    async (event: React.ClipboardEvent) => {
      const items = Array.from(event.clipboardData.items);
      const files = items
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null);
      const accepted = filterAndReportFiles(files);
      if (!accepted.length) return;
      event.preventDefault();
      const next = await Promise.all(accepted.map(fileToAttachment));
      setAttachments((prev) => [...prev, ...next]);
    },
    [fileToAttachment, filterAndReportFiles],
  );

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handlePreviewPendingAttachment = useCallback(
    (index: number) => {
      const a = attachments[index];
      if (!a) return;
      setPreviewSource({
        filename: a.filename,
        mimeType: a.mimeType,
        type: a.type,
        base64: a.base64,
        size: a.size,
      });
    },
    [attachments],
  );

  // Fold all messages once per state.messages change to power the
  // SessionActivityPanel on the right (tools, KBs, space refs, attachments).
  const sessionActivity = useMemo(
    () => buildSessionActivity(state.messages),
    [state.messages],
  );

  // Clicking an attachment (from the Activity home or from a chat message)
  // routes into the panel as a new file tab. It auto-opens and the
  // preference is persisted so a follow-up click feels instant.
  const handlePreviewMessageAttachment = useCallback((a: MessageAttachment) => {
    viewerPanelRef.current?.openFileTab(a);
  }, []);

  // Event-delegated link interception inside the messages container. When
  // the user clicks an http(s) link in an assistant message, we open it as
  // a Viewer tab instead of letting the browser navigate / open a new tab.
  // Cmd/ctrl/shift + click keep their standard meaning (open in browser).
  const handleMessagesClick = useCallback((event: React.MouseEvent) => {
    if (event.defaultPrevented) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    if (event.button !== 0) return;
    const target = event.target as HTMLElement | null;
    if (!target) return;
    const anchor = target.closest<HTMLAnchorElement>("a[href]");
    if (!anchor) return;
    const href = anchor.getAttribute("href");
    if (!href) return;
    if (!/^https?:\/\//i.test(href)) return;
    event.preventDefault();
    viewerPanelRef.current?.openWebTab(href);
  }, []);

  const handleClosePreview = useCallback(() => {
    setPreviewSource(null);
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes("Files")) setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) setDragging(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(false);
      dragCounter.current = 0;
      const accepted = filterAndReportFiles(Array.from(e.dataTransfer.files));
      if (!accepted.length) return;
      const next = await Promise.all(accepted.map(fileToAttachment));
      setAttachments((prev) => [...prev, ...next]);
    },
    [fileToAttachment, filterAndReportFiles],
  );

  const handleAddFiles = useCallback(
    async (files: File[]) => {
      const accepted = filterAndReportFiles(files);
      if (!accepted.length) return;
      const next = await Promise.all(accepted.map(fileToAttachment));
      setAttachments((prev) => [...prev, ...next]);
    },
    [fileToAttachment, filterAndReportFiles],
  );

  // Connected subagents are stored as ``type: subagent`` KBs. Derive the
  // selected one before the send callback so the callback can depend on the
  // current selection instead of capturing an undeclared-later value.
  const agentNameSet = useMemo(
    () =>
      new Set(
        knowledgeBases
          .filter((kb) => kb.metadata?.type === "subagent")
          .map((kb) => kb.name),
      ),
    [knowledgeBases],
  );
  const selectedAgent = useMemo(
    () => state.knowledgeBases.find((name) => agentNameSet.has(name)) ?? null,
    [state.knowledgeBases, agentNameSet],
  );
  // How many times TraitTutor may consult the selected agent this turn. Seeded
  // from the configured default; the composer's stepper overrides it per turn.
  const [subagentBudget, setSubagentBudget] = useState<number | null>(null);
  useEffect(() => {
    void getSubagentSettings()
      .then((settings) => setSubagentBudget(settings.consult_budget))
      .catch(() => undefined);
  }, []);

  const handleSend = useCallback(
    async (content: string) => {
      if (
        (!content &&
          !attachments.length &&
          !selectedBookReferences.length &&
          !selectedNotebookRecords.length &&
          !selectedHistorySessions.length &&
          !selectedQuestionEntries.length &&
          !selectedMemoryFiles.length) ||
        state.isStreaming
      )
        return;

      let extraAttachments = attachments.map((a) => ({
        type: a.type,
        filename: a.filename,
        base64: a.base64,
        mime_type: a.mimeType,
      }));
      let config: Record<string, unknown> | undefined;

      if (isQuizMode) {
        config = buildQuizWSConfig(DEFAULT_QUIZ_CONFIG);
      }
      if (isVisualizeMode)
        config = buildVisualizeWSConfig(DEFAULT_VISUALIZE_CONFIG);
      if (isResearchMode) {
        config = buildResearchWSConfig({ mode: "notes", depth: "standard" });
      }
      // When a connected agent is selected, carry the per-turn consult budget
      // (how many times TraitTutor may ask it) so the subagent capability uses it.
      if (selectedAgent && subagentBudget) {
        config = { ...(config ?? {}), subagent_consult_budget: subagentBudget };
      }
      config = { ...(config ?? {}), product_mode: productMode };

      const memoryPayload = [...memoryReferencesPayload];
      const generationInstruction = chatGenerationKind
        ? chatGenerationKind === "humanizer"
          ? "[TRAITTUTOR_HUMANIZER]"
          : `${t("Create a source-grounded learning artifact in this conversation.")} ${t(
              chatGenerationKind === "courseware"
                ? "Rewrite Courseware"
                : chatGenerationKind === "flashcards"
                  ? "Generate Flashcards"
                  : "Humanizer",
            )}`
        : "";
      const messageContent =
        content ||
        (selectedNotebookRecords.length ||
        selectedBookReferences.length ||
        selectedHistorySessions.length ||
        selectedAgentSessions.length ||
        selectedQuestionEntries.length ||
        memoryPayload.length
          ? t("Please use the selected context to help with this request.")
          : "") ||
        (attachments.some((a) => a.type === "image")
          ? t("Please analyze the attached image(s).")
          : "") || generationInstruction;
      // Persona is NOT passed per-call here: it is a session-level
      // preference (state.personaSelection) that sendMessage resolves and
      // sends with every turn.
      sendMessage(
        generationInstruction && messageContent !== generationInstruction ? `${generationInstruction}\n\n${messageContent}` : messageContent,
        extraAttachments,
        config,
        notebookReferencesPayload,
        historyReferencesPayload,
        { bookReferences: bookReferencesPayload },
        questionNotebookReferencesPayload,
        undefined,
        memoryPayload,
      );
      shouldAutoScrollRef.current = true;
      setAttachments([]);
      setSelectedBookReferences([]);
      setSelectedNotebookRecords([]);
      setSelectedHistorySessions([]);
      setSelectedAgentSessions([]);
      setSelectedQuestionEntries([]);
      setSelectedMemoryFiles([]);
      setChatGenerationKind(null);
    },
    [
      attachments,
      bookReferencesPayload,
      historyReferencesPayload,
      isQuizMode,
      isResearchMode,
      isVisualizeMode,
      memoryReferencesPayload,
      notebookReferencesPayload,
      productMode,
      questionNotebookReferencesPayload,
      selectedAgent,
      selectedHistorySessions.length,
      selectedAgentSessions.length,
      selectedMemoryFiles.length,
      selectedBookReferences.length,
      selectedNotebookRecords.length,
      selectedQuestionEntries.length,
      sendMessage,
      shouldAutoScrollRef,
      state.isStreaming,
      subagentBudget,
      t,
      chatGenerationKind,
    ],
  );

  const handleRegenerateMessage = useCallback(() => {
    regenerateLastMessage();
  }, [regenerateLastMessage]);

  const handleToggleKB = useCallback(
    (name: string) => {
      const current = state.knowledgeBases;
      setKBs(
        current.includes(name)
          ? current.filter((kb) => kb !== name)
          : [...current, name],
      );
    },
    [setKBs, state.knowledgeBases],
  );

  // Real knowledge bases and connected subagents render as separate composer
  // controls even though both travel through the knowledge_bases request path.
  const kbOptions = useMemo(
    () => knowledgeBases.filter((kb) => kb.metadata?.type !== "subagent"),
    [knowledgeBases],
  );
  const agentOptions = useMemo(
    () =>
      knowledgeBases
        .filter((kb) => kb.metadata?.type === "subagent")
        .map((kb) => ({ name: kb.name, kind: kb.metadata?.agent_kind })),
    [knowledgeBases],
  );
  const selectedKbOnly = useMemo(
    () => state.knowledgeBases.filter((n) => !agentNameSet.has(n)),
    [state.knowledgeBases, agentNameSet],
  );
  const handleSelectAgent = useCallback(
    (name: string | null) => {
      // Single-select: clear any selected agent, then set the new one (if any).
      const withoutAgents = state.knowledgeBases.filter(
        (n) => !agentNameSet.has(n),
      );
      setKBs(name ? [...withoutAgents, name] : withoutAgents);
    },
    [setKBs, state.knowledgeBases, agentNameSet],
  );
  // Honor `?agent=<name>` once its connection KB has loaded: preselect it so a
  // partner opened from the partner list starts the chat already targeting it.
  useEffect(() => {
    if (agentPreselectDoneRef.current) return;
    const name = pendingAgentRef.current;
    if (!name || !agentNameSet.has(name)) return;
    agentPreselectDoneRef.current = true;
    handleSelectAgent(name);
  }, [agentNameSet, handleSelectAgent]);
  const handleSelectNotebookPicker = useCallback(() => {
    setShowNotebookPicker(true);
  }, []);
  const handleSelectBookPicker = useCallback(() => {
    setShowBookPicker(true);
  }, []);
  const handleSelectHistoryPicker = useCallback(() => {
    setShowHistoryPicker(true);
  }, []);
  const handleSelectAgentsPicker = useCallback(() => {
    setShowAgentsPicker(true);
  }, []);
  const handleSelectQuestionBankPicker = useCallback(() => {
    setShowQuestionBankPicker(true);
  }, []);
  const handleSelectPersonaPicker = useCallback(() => {
    // The @space "Persona" entry now opens the session persona selector.
    setPersonaSelectorOpen(true);
  }, []);
  const handleSelectMemoryPicker = useCallback(() => {
    setShowMemoryPicker(true);
  }, []);
  const handleRemoveHistory = useCallback((sessionId: string) => {
    setSelectedHistorySessions((prev) =>
      prev.filter((item) => item.sessionId !== sessionId),
    );
  }, []);
  const handleRemoveAgent = useCallback((sessionId: string) => {
    setSelectedAgentSessions((prev) =>
      prev.filter((item) => item.sessionId !== sessionId),
    );
  }, []);
  const handleRemoveNotebook = useCallback((notebookId: string) => {
    setSelectedNotebookRecords((prev) =>
      prev.filter((record) => record.notebookId !== notebookId),
    );
  }, []);
  const handleRemoveBookReference = useCallback((bookId: string) => {
    setSelectedBookReferences((prev) =>
      prev.filter((record) => record.bookId !== bookId),
    );
  }, []);
  const handleRemoveQuestion = useCallback((entryId: number) => {
    setSelectedQuestionEntries((prev) =>
      prev.filter((entry) => entry.id !== entryId),
    );
  }, []);
  const handleClearPersona = useCallback(() => {
    setPersonaSelection("");
  }, [setPersonaSelection]);

  const handleToggleMemoryFile = useCallback((file: SpaceMemoryFile) => {
    setSelectedMemoryFiles((prev) =>
      prev.includes(file)
        ? prev.filter((item) => item !== file)
        : [...prev, file],
    );
  }, []);

  const handleCloseNotebookPicker = useCallback(() => {
    setShowNotebookPicker(false);
  }, []);
  const handleCloseBookPicker = useCallback(() => {
    setShowBookPicker(false);
  }, []);
  const handleApplyBookReferences = useCallback(
    (references: SelectedBookReference[]) => {
      setSelectedBookReferences(references);
    },
    [],
  );
  const handleApplyNotebookRecords = useCallback(
    (records: SelectedRecord[]) => {
      setSelectedNotebookRecords(records);
    },
    [],
  );
  const handleCloseHistoryPicker = useCallback(() => {
    setShowHistoryPicker(false);
  }, []);
  const handleApplyHistorySessions = useCallback(
    (sessions: SelectedHistorySession[]) => {
      setSelectedHistorySessions(sessions);
    },
    [],
  );
  const handleCloseAgentsPicker = useCallback(() => {
    setShowAgentsPicker(false);
  }, []);
  const handleApplyAgentSessions = useCallback(
    (sessions: SelectedHistorySession[]) => {
      setSelectedAgentSessions(sessions);
    },
    [],
  );
  const handleCloseQuestionBankPicker = useCallback(() => {
    setShowQuestionBankPicker(false);
  }, []);
  const handleApplyQuestionEntries = useCallback(
    (entries: SelectedQuestionEntry[]) => {
      setSelectedQuestionEntries(entries);
    },
    [],
  );
  const handleCloseMemoryPicker = useCallback(() => {
    setShowMemoryPicker(false);
  }, []);
  const handleApplyMemoryFiles = useCallback((files: SpaceMemoryFile[]) => {
    setSelectedMemoryFiles(files);
  }, []);

  const handleDownloadMarkdown = useCallback(() => {
    if (!state.messages.length) return;
    const title =
      state.messages
        .find((msg) => msg.role === "user")
        ?.content.trim()
        .slice(0, 80) || "Chat Session";
    downloadChatMarkdown(state.messages, { title });
  }, [state.messages]);

  return (
    <QuizFollowupProvider>
      <GeogebraTabProvider>
        <QuizFollowupBridge viewerPanelRef={viewerPanelRef} />
        <GeogebraTabBridge viewerPanelRef={viewerPanelRef} />
        <SubagentTabWatcher
          messages={state.messages}
          viewerPanelRef={viewerPanelRef}
        />
        <div
          // When the preview drawer is open AND the viewport is wide enough,
          // push the chat content to the left by the drawer's width so the two
          // panels live side-by-side (matches Claude desktop). On smaller
          // screens the drawer overlays — squeezing a phone-width chat into
          // the remaining ~30 px would be useless. The actual padding +
          // transition lives in `chat-preview-shell` (globals.css) so we can
          // hand-tune it without fighting Tailwind's arbitrary-value parser.
          data-preview-open={previewSource ? "true" : "false"}
          data-viewer-open={viewerPanelOpen ? "true" : "false"}
          className="chat-preview-shell flex h-full flex-col overflow-hidden bg-[var(--background)]"
        >
          <div className="mx-auto flex w-full max-w-[960px] flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-6 pt-3 pb-0">
            <div className="group/title min-w-0 flex flex-1 items-center gap-2">
              {sessionTitleEditing ? (
                <input
                  ref={titleInputRef}
                  value={sessionTitleDraft}
                  onChange={(event) => setSessionTitleDraft(event.target.value)}
                  onBlur={() => void commitSessionTitleEdit()}
                  onKeyDown={handleSessionTitleKeyDown}
                  disabled={sessionTitleSaving}
                  aria-label={t("Session title")}
                  className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] shadow-sm outline-none transition focus:border-[var(--ring)] focus:ring-2 focus:ring-[var(--ring)]/20 disabled:opacity-60"
                  maxLength={100}
                />
              ) : (
                <button
                  type="button"
                  onClick={startSessionTitleEdit}
                  disabled={!canRenameSession}
                  title={
                    canRenameSession
                      ? t("Click to rename session")
                      : t("Start a conversation to rename")
                  }
                  className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl px-2 py-1 text-left font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] transition hover:bg-[var(--muted)]/55 disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <span className="truncate">{displaySessionTitle}</span>
                  {canRenameSession ? (
                    <PenLine className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover/title:opacity-100" />
                  ) : null}
                </button>
              )}
              {sessionTitleSaving ? (
                <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                  {t("Saving...")}
                </span>
              ) : null}
              {sessionTitleError ? (
                <span className="shrink-0 text-xs text-[var(--destructive)]">
                  {sessionTitleError}
                </span>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-0.5">
              <HeaderActionButton
                onClick={handleDownloadMarkdown}
                disabled={!state.messages.length}
                icon={Download}
                label={t("Download Markdown")}
                title={t("Download chat history as Markdown")}
              />
              <HeaderActionButton
                onClick={toggleViewerPanel}
                active={viewerPanelOpen}
                icon={PanelRight}
                label={t("Activity")}
                title={t("Session activity, attachments & previews")}
              />
            </div>
          </div>
          <div className="flex w-full flex-1 min-h-0 flex-col">
            {sessionLoading ? (
              <div className="flex w-full flex-1 min-h-0 justify-center px-6">
                <div className="h-full w-full max-w-[960px]">
                  <SessionLoadingView onCancel={cancelSessionLoad} />
                </div>
              </div>
            ) : !hasMessages ? (
              <div className="flex w-full flex-1 min-h-0 items-end justify-center pb-14 animate-fade-in px-6">
                <div className="w-full max-w-[960px] flex items-center justify-center gap-4">
                  <TraitTutorMark className="h-10 w-10 shrink-0 select-none" />
                  <h1 className="font-serif text-[40px] font-medium leading-[1.1] tracking-[-0.015em] text-[var(--foreground)]">
                    {t(welcomeGreeting)}
                  </h1>
                </div>
              </div>
            ) : (
              <div
                ref={messagesContainerRef}
                data-chat-scroll-root="true"
                onScroll={handleMessagesScroll}
                onClick={handleMessagesClick}
                // `both-edges` reserves the scrollbar gutter on both sides so
                // the inner mx-auto column centers on the same axis as the
                // header and composer (siblings outside this scrollport) on
                // classic-scrollbar platforms; plain `stable` would shift it
                // ~half a scrollbar-width left of them.
                className={`w-full flex-1 min-h-0 overflow-y-auto [scrollbar-gutter:stable_both-edges] ${hasMessages ? "pt-6" : "pt-2 pb-6"}`}
                style={
                  hasMessages
                    ? (() => {
                        // The bottom 40 px of the messages area fades to
                        // transparent so content "dissolves" into the composer
                        // gutter. Without enough bottom padding, the fade
                        // overlaps the last assistant paragraph and looks like
                        // a stuck scroll — the user reaches scrollHeight but
                        // can still see only a faded sliver of text. paddingBottom
                        // is sized so the fade falls over empty space.
                        const maskImage =
                          "linear-gradient(to bottom, transparent 0px, #000 32px, #000 calc(100% - 40px), transparent 100%)";
                        return {
                          paddingBottom: "48px",
                          WebkitMaskImage: maskImage,
                          maskImage,
                        };
                      })()
                    : undefined
                }
              >
                <div className="mx-auto w-full max-w-[960px] space-y-9 px-6">
                  <ChatMessageList
                    messages={state.messages}
                    isStreaming={state.isStreaming}
                    sessionId={state.sessionId}
                    language={state.language}
                    onCopyAssistantMessage={copyAssistantMessage}
                    onRegenerateMessage={handleRegenerateMessage}
                    onPreviewAttachment={handlePreviewMessageAttachment}
                    onDeleteTurn={deleteTurn}
                    selectedBranches={state.selectedBranches}
                    onEditMessage={editMessage}
                    onSwitchBranch={switchBranch}
                    onSubmitUserReply={submitUserReply}
                  />
                  <div ref={messagesEndRef} className="h-px w-full shrink-0" />
                </div>
              </div>
            )}

            <ChatComposer
              composerRef={composerRef}
              capMenuRef={capMenuRef}
              capBtnRef={capBtnRef}
              spaceMenuRef={spaceMenuRef}
              spaceBtnRef={spaceBtnRef}
              dragCounter={dragCounter}
              dragging={dragging}
              capMenuOpen={capMenuOpen}
              spaceMenuOpen={spaceMenuOpen}
              hasMessages={hasMessages}
              attachments={attachments}
              attachmentError={attachmentError}
              activeCap={activeCap}
              knowledgeBases={kbOptions}
              connectedAgents={[]}
              selectedAgent={selectedAgent}
              onSelectAgent={handleSelectAgent}
              agentsAvailable={false}
              subagentBudget={subagentBudget}
              onSubagentBudgetChange={setSubagentBudget}
              llmOptions={llmOptions}
              activeLLMDefault={activeLLMDefault}
              llmSelection={state.llmSelection}
              llmOptionsLoading={llmOptionsLoading}
              llmOptionsError={llmOptionsError}
              selectedBookReferences={selectedBookReferences}
              selectedNotebookRecords={selectedNotebookRecords}
              selectedHistorySessions={selectedHistorySessions}
              selectedAgentSessions={selectedAgentSessions}
              selectedQuestionEntries={selectedQuestionEntries}
              notebookReferenceGroups={notebookReferenceGroups}
              selectedPersona={null}
              selectedMemoryFiles={selectedMemoryFiles}
              selectedKnowledgeBases={selectedKbOnly}
              isStreaming={state.isStreaming}
              isVisualizeMode={isVisualizeMode}
              capabilities={CAPABILITIES}
              onSetCapMenuOpen={setCapMenuOpen}
              onSetSpaceMenuOpen={setSpaceMenuOpen}
              onToggleKB={handleToggleKB}
              onSelectLLM={setLLMSelection}
              onSelectNotebookPicker={handleSelectNotebookPicker}
              onSelectBookPicker={handleSelectBookPicker}
              onSelectHistoryPicker={handleSelectHistoryPicker}
              onSelectAgentsPicker={handleSelectAgentsPicker}
              onSelectQuestionBankPicker={handleSelectQuestionBankPicker}
              onSelectPersonaPicker={handleSelectPersonaPicker}
              onSelectMemoryPicker={handleSelectMemoryPicker}
              onClearPersona={handleClearPersona}
              personaSelection={state.personaSelection}
              onPersonaSelectionChange={setPersonaSelection}
              personaSelectorOpen={personaSelectorOpen}
              onPersonaSelectorOpenChange={setPersonaSelectorOpen}
              onToggleMemoryFile={handleToggleMemoryFile}
              onSend={handleSend}
              onRemoveAttachment={removeAttachment}
              onPreviewAttachment={handlePreviewPendingAttachment}
              onRemoveHistory={handleRemoveHistory}
              onRemoveAgent={handleRemoveAgent}
              onRemoveBookReference={handleRemoveBookReference}
              onRemoveNotebook={handleRemoveNotebook}
              onRemoveQuestion={handleRemoveQuestion}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              onPaste={handlePaste}
              onAddFiles={handleAddFiles}
              onSelectCapability={handleSelectCapability}
              onSelectGenerationShortcut={handleSelectGenerationShortcut}
              generationShortcut={chatGenerationKind ?? (isResearchMode ? "research" : null)}
              onClearGenerationShortcut={() => setChatGenerationKind(null)}
              inputPlaceholder={
                isResearchMode
                  ? t("Describe the research topic...")
                  : chatGenerationKind === "humanizer"
                  ? t("Paste text to humanize. Prefix with 检测： for review only.")
                  : undefined
              }
              onCancelStreaming={cancelStreamingTurn}
              prefillInputRef={prefillInputRef}
            />
            {!hasMessages && (
              <div className="mx-auto mt-3 flex w-full max-w-[960px] items-center justify-center gap-1" role="group" aria-label="TraitTutor mode">
                <button
                  type="button"
                  onClick={() => setProductMode("learn")}
                  className={`inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[12.5px] transition-colors ${productMode === "learn" ? "bg-teal-500/15 text-teal-700 dark:text-teal-300" : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}
                >
                  <GraduationCap size={14} />
                  {t("学习")}
                </button>
                <button
                  type="button"
                  onClick={() => setProductMode("assist")}
                  className={`inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[12.5px] transition-colors ${productMode === "assist" ? "bg-[var(--primary)]/10 text-[var(--primary)]" : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}
                >
                  <Sparkles size={14} />
                  {t("让 TraitTutor 帮我做事")}
                </button>
              </div>
            )}
            <div
              aria-hidden="true"
              className="shrink-0"
              style={{
                flexGrow: hasMessages ? 0 : 1.4,
                transition: "flex-grow 650ms cubic-bezier(0.16, 1, 0.3, 1)",
              }}
            />
          </div>
          <NotebookRecordPicker
            open={showNotebookPicker}
            onClose={handleCloseNotebookPicker}
            onApply={handleApplyNotebookRecords}
          />
          <BookReferencePicker
            open={showBookPicker}
            initialReferences={selectedBookReferences}
            onClose={handleCloseBookPicker}
            onApply={handleApplyBookReferences}
          />
          <HistorySessionPicker
            open={showHistoryPicker}
            onClose={handleCloseHistoryPicker}
            onApply={handleApplyHistorySessions}
          />
          <MyAgentsPicker
            open={showAgentsPicker}
            onClose={handleCloseAgentsPicker}
            onApply={handleApplyAgentSessions}
          />
          <QuestionBankPicker
            open={showQuestionBankPicker}
            onClose={handleCloseQuestionBankPicker}
            onApply={handleApplyQuestionEntries}
          />
          <MemoryPicker
            open={showMemoryPicker}
            initialFiles={selectedMemoryFiles}
            onClose={handleCloseMemoryPicker}
            onApply={handleApplyMemoryFiles}
          />
          <FilePreviewDrawer
            open={previewSource !== null}
            source={previewSource}
            onClose={handleClosePreview}
          />
          <SessionViewerPanel
            ref={viewerPanelRef}
            open={viewerPanelOpen && previewSource === null}
            sessionId={state.sessionId}
            activity={sessionActivity}
            onClose={() => setViewerOpen(false)}
            onAutoOpen={() => setViewerOpen(true)}
          />
        </div>
      </GeogebraTabProvider>
    </QuizFollowupProvider>
  );
}

/**
 * Bridges the SessionViewerPanel's imperative ``openQuizFollowupTab`` into
 * the QuizFollowupController so descendants (QuizViewer) can call
 * ``controller.openFollowupTab(...)`` without prop-drilling the panel ref
 * through several layers of components.
 */
function QuizFollowupBridge({
  viewerPanelRef,
}: {
  viewerPanelRef: React.MutableRefObject<SessionViewerPanelHandle | null>;
}) {
  const controller = useQuizFollowupController();
  useEffect(() => {
    controller.setOpenTabHandler((ctx) => {
      viewerPanelRef.current?.openQuizFollowupTab(ctx);
    });
    return () => controller.setOpenTabHandler(null);
  }, [controller, viewerPanelRef]);
  return null;
}

/**
 * Same shape as QuizFollowupBridge, for the GeoGebra-tab opener exposed
 * to in-message CTAs (the ``ggbscript`` markdown fence becomes a card
 * that calls ``controller.openTab(...)`` here).
 */
function GeogebraTabBridge({
  viewerPanelRef,
}: {
  viewerPanelRef: React.MutableRefObject<SessionViewerPanelHandle | null>;
}) {
  const controller = useGeogebraTabOpener();
  useEffect(() => {
    if (!controller) return;
    controller.setOpenHandler((payload) => {
      viewerPanelRef.current?.openGeogebraTab(payload);
    });
    return () => controller.setOpenHandler(null);
  }, [controller, viewerPanelRef]);
  return null;
}

/**
 * Watches the turn's messages for connected-subagent runs and mirrors each
 * (grouped by the consult's call id) into its own side-viewer tab — opening +
 * focusing the panel when a consult starts, then live-refreshing as the
 * agent's native events stream in. Keeps the chat trace compact while the full
 * run shows in the sidebar.
 */
function SubagentTabWatcher({
  messages,
  viewerPanelRef,
}: {
  messages: { events?: StreamEvent[] }[];
  viewerPanelRef: React.MutableRefObject<SessionViewerPanelHandle | null>;
}) {
  useEffect(() => {
    // Group by turn so all of one turn's consults (TraitTutor may ask the agent
    // several questions in a row, each its own tool call) land in one tab as a
    // single running dialogue; fall back to the call id when no turn is set.
    const groups = new Map<string, { label: string; events: StreamEvent[] }>();
    for (const msg of messages) {
      for (const ev of msg.events ?? []) {
        const meta = (ev.metadata ?? {}) as Record<string, unknown>;
        if (meta.trace_kind !== "subagent_event") continue;
        const key = String(meta.turn_id || meta.call_id || meta.trace_id || "");
        if (!key) continue;
        const existing = groups.get(key);
        const label = String(
          meta.subagent_name || existing?.label || "Subagent",
        );
        if (existing) {
          existing.label = label;
          existing.events.push(ev);
        } else {
          groups.set(key, { label, events: [ev] });
        }
      }
    }
    for (const [key, group] of groups) {
      viewerPanelRef.current?.openSubagentTab(key, group.label, group.events);
    }
  }, [messages, viewerPanelRef]);
  return null;
}

/**
 * Header action button that auto-collapses to icon-only when the chat
 * column gets squeezed (Viewer panel open, narrow viewport, etc.). The
 * label stays as the button's `title` so hovering an icon still reveals
 * what it does. Optional `active` flag paints the button with a primary
 * tint, used by the panel-toggle buttons to surface their on/off state.
 */
// Claude-style icon-only header action: bare 16px glyph, function revealed
// by an instant tooltip; active state gets a primary tint.
function HeaderActionButton({
  onClick,
  disabled,
  active,
  icon: Icon,
  label,
  title,
}: {
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  icon: LucideIcon;
  label: string;
  title?: string;
}) {
  return (
    <Tooltip label={title ?? label} side="bottom">
      <button
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={active}
        className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90 disabled:cursor-not-allowed disabled:opacity-40 ${
          active
            ? "bg-[var(--primary)]/10 text-[var(--primary)]"
            : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] disabled:hover:bg-transparent disabled:hover:text-[var(--muted-foreground)]"
        }`}
      >
        <Icon size={16} strokeWidth={1.7} className="shrink-0" />
      </button>
    </Tooltip>
  );
}
