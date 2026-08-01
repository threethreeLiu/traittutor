"use client";

import {
  AudioLines,
  Boxes,
  Brain,
  Clapperboard,
  Database,
  FileScan,
  Image as ImageIcon,
  Library,
  MessagesSquare,
  Mic,
  Network,
  Palette,
  Paperclip,
  Plug,
  Search,
  SlidersHorizontal,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { ServiceName } from "@/components/settings/SettingsContext";

/**
 * Settings information architecture.
 *
 * Two levels, deliberately unlike the flat Learning Space dashboard:
 *   • The hub (`/settings`) shows six category blocks + a resident Status
 *     module — nothing else.
 *   • Categories with several settings (Models, Chat) open a sub-hub page
 *     that lists their leaves as tiles; single-setting categories link
 *     straight to their leaf page.
 *
 * This module is the single source for the blocks, the sub-hub tiles, and the
 * breadcrumb trail rendered top-left on every page.
 */

export type Lang = { zh: string; en: string };

export interface SettingsLeaf {
  key: string;
  href: string;
  label: Lang;
  blurb: Lang;
  icon: LucideIcon;
  /** Colored icon-tile accent for the sub-hub grid (full class strings). */
  tile: string;
  /** Model-service leaves carry a configured/not chip from the catalog. */
  service?: ServiceName;
  /** Hidden from non-admin users (the backend rejects them anyway). */
  adminOnly?: boolean;
}

export interface SettingsCategory {
  key: string;
  label: Lang;
  /** One-line descriptor shown on the hub block. */
  blurb: Lang;
  icon: LucideIcon;
  /** Where clicking the block lands — a sub-hub or a leaf page. */
  href: string;
  /** Leaves listed on the sub-hub page (omitted for direct-leaf categories). */
  children?: SettingsLeaf[];
}

const MODEL_CHILDREN: SettingsLeaf[] = [
  {
    key: "llm",
    href: "/settings/llm",
    label: { zh: "LLM", en: "LLM" },
    blurb: {
      zh: "语言模型供应商与当前档位。",
      en: "Language model providers and active profile.",
    },
    icon: Brain,
    tile: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    service: "llm",
  },
  {
    key: "embedding",
    href: "/settings/embedding",
    label: { zh: "嵌入模型", en: "Embedding" },
    blurb: {
      zh: "向量模型供应商与维度。",
      en: "Embedding model providers and dimensions.",
    },
    icon: Database,
    tile: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    service: "embedding",
  },
  {
    key: "search",
    href: "/settings/search",
    label: { zh: "搜索", en: "Search" },
    blurb: { zh: "联网搜索供应商。", en: "Web search providers." },
    icon: Search,
    tile: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    service: "search",
  },
  {
    key: "tts",
    href: "/settings/tts",
    label: { zh: "语音合成", en: "Text-to-Speech" },
    blurb: {
      zh: "朗读助手回复的 TTS 供应商。",
      en: "Text-to-speech for reading replies aloud.",
    },
    icon: AudioLines,
    tile: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    service: "tts",
  },
  {
    key: "stt",
    href: "/settings/stt",
    label: { zh: "语音识别", en: "Speech-to-Text" },
    blurb: {
      zh: "转写麦克风录音的 STT 供应商。",
      en: "Speech-to-text for the composer microphone.",
    },
    icon: Mic,
    tile: "bg-pink-500/10 text-pink-600 dark:text-pink-400",
    service: "stt",
  },
  {
    key: "imagegen",
    href: "/settings/image",
    label: { zh: "文生图", en: "Image Generation" },
    blurb: {
      zh: "chat imagegen 工具使用的文生图模型。",
      en: "Text-to-image model for the chat imagegen tool.",
    },
    icon: ImageIcon,
    tile: "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
    service: "imagegen",
  },
  {
    key: "videogen",
    href: "/settings/video",
    label: { zh: "文生视频", en: "Video Generation" },
    blurb: {
      zh: "chat videogen 工具使用的文生视频模型。",
      en: "Text-to-video model for the chat videogen tool.",
    },
    icon: Clapperboard,
    tile: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    service: "videogen",
  },
];

const CHAT_CHILDREN: SettingsLeaf[] = [
  {
    key: "tools",
    href: "/settings/tools",
    label: { zh: "工具", en: "Tools" },
    blurb: {
      zh: "对话智能体可调用的内置工具。",
      en: "Built-in tools the chat agent can invoke.",
    },
    icon: Wrench,
    tile: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
  },
  {
    key: "mcp",
    href: "/settings/mcp",
    label: { zh: "MCP 服务器", en: "MCP servers" },
    blurb: {
      zh: "部署共享的外部 MCP 服务器。",
      en: "External MCP servers shared by the deployment.",
    },
    icon: Plug,
    tile: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    adminOnly: true,
  },
  {
    key: "capabilities",
    href: "/settings/capabilities",
    label: { zh: "能力", en: "Capabilities" },
    blurb: {
      zh: "各能力的 LLM 参数与运行时旋钮。",
      en: "Per-capability LLM parameters and runtime knobs.",
    },
    icon: SlidersHorizontal,
    tile: "bg-lime-500/10 text-lime-600 dark:text-lime-400",
  },
  {
    key: "attachments",
    href: "/settings/attachments",
    label: { zh: "附件", en: "Attachments" },
    blurb: {
      zh: "聊天附件的大小上限与文本提取预算。",
      en: "Upload caps and extraction budgets for chat attachments.",
    },
    icon: Paperclip,
    tile: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
    adminOnly: true,
  },
];

export const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    key: "appearance",
    label: { zh: "外观", en: "Appearance" },
    blurb: { zh: "视觉主题与界面语言", en: "Theme and interface language" },
    icon: Palette,
    href: "/settings/appearance",
  },
  {
    key: "network",
    label: { zh: "网络", en: "Network" },
    blurb: {
      zh: "端口、浏览器 API 地址与 CORS",
      en: "Ports, browser API base, and CORS",
    },
    icon: Network,
    href: "/settings/network",
  },
  {
    key: "models",
    label: { zh: "模型", en: "Models" },
    blurb: {
      zh: "语言、向量、搜索、语音与生成模型",
      en: "Language, embedding, search, voice, and generation models",
    },
    icon: Boxes,
    href: "/settings/models",
    children: MODEL_CHILDREN,
  },
  {
    key: "knowledge",
    label: { zh: "知识库", en: "Knowledge Base" },
    blurb: { zh: "文档解析引擎", en: "Document parsing engine" },
    icon: Library,
    href: "/settings/document-parsing",
  },
  {
    key: "chat",
    label: { zh: "聊天", en: "Chat" },
    blurb: {
      zh: "工具、MCP 服务器、能力与附件",
      en: "Tools, MCP servers, capabilities, and attachments",
    },
    icon: MessagesSquare,
    href: "/settings/chat",
    children: CHAT_CHILDREN,
  },
];

export const SETTINGS_HUB_HREF = "/settings";
const HUB_LABEL: Lang = { zh: "设置", en: "Settings" };

/** Routes that are pure navigation (hub + sub-hubs) — no Save/Apply toolbar. */
const NAV_ONLY_ROUTES = new Set<string>([
  SETTINGS_HUB_HREF,
  ...SETTINGS_CATEGORIES.filter((c) => c.children).map((c) => c.href),
]);

export function isNavOnlyRoute(pathname: string): boolean {
  return NAV_ONLY_ROUTES.has(pathname);
}

// The on-disk file (under data/user/settings/) each leaf module persists to.
// Surfaced in the toolbar status line so every page says where its parameters
// live, without duplicating the string on each page.
const STORAGE_PATHS: Record<string, string> = {
  "/settings/appearance": "data/user/settings/interface.json",
  "/settings/network": "data/user/settings/system.json",
  "/settings/llm": "data/user/settings/model_catalog.json",
  "/settings/embedding": "data/user/settings/model_catalog.json",
  "/settings/search": "data/user/settings/model_catalog.json",
  "/settings/tts": "data/user/settings/model_catalog.json",
  "/settings/stt": "data/user/settings/model_catalog.json",
  "/settings/image": "data/user/settings/model_catalog.json",
  "/settings/video": "data/user/settings/model_catalog.json",
  "/settings/document-parsing": "data/user/settings/document_parsing.json",
  "/settings/tools": "data/user/settings/interface.json",
  "/settings/attachments": "data/user/settings/system.json",
  "/settings/mcp": "data/user/settings/mcp.json",
  "/settings/capabilities": "data/user/settings/main.yaml · agents.yaml",
};

export function storagePathFor(pathname: string): string | null {
  return STORAGE_PATHS[pathname] ?? null;
}

export interface Crumb {
  label: Lang;
  /** Omitted on the current (last) crumb. */
  href?: string;
}

/**
 * The breadcrumb trail for a settings route, e.g.
 *   /settings/llm  →  设置 / 模型 / LLM
 *   /settings/network  →  设置 / 网络
 * Returns just [设置] for the hub itself.
 */
export function breadcrumbFor(pathname: string): Crumb[] {
  const root: Crumb = { label: HUB_LABEL, href: SETTINGS_HUB_HREF };
  if (pathname === SETTINGS_HUB_HREF) return [{ label: HUB_LABEL }];

  // Direct-leaf or sub-hub category landed on its own href.
  const category = SETTINGS_CATEGORIES.find((c) => c.href === pathname);
  if (category) return [root, { label: category.label }];

  // A leaf inside a sub-hub category.
  for (const c of SETTINGS_CATEGORIES) {
    const leaf = c.children?.find((l) => l.href === pathname);
    if (leaf) {
      return [root, { label: c.label, href: c.href }, { label: leaf.label }];
    }
  }

  // Unknown sub-route (e.g. a legacy redirect target rendered directly).
  return [root];
}
