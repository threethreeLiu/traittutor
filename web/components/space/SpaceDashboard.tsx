"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";

import { listNotebookEntries } from "@/lib/notebook-api";
import { fetchAllProgress } from "@/lib/learning-api";
import { listLearningPacks, listTraitProfiles } from "@/lib/traittutor-api";

/**
 * Learning Space dashboard — the hub of `/space`.
 *
 * Rendered as a table-of-contents index rather than a card grid: numbered
 * rows separated by hairlines, one description line each, live count on the
 * right. Empty sections show an em dash plus a quiet action invitation
 * instead of reporting "0" — a zero is not information, an invitation is.
 */

type Lang = { zh: string; en: string };

type DashKey =
  | "question_bank"
  | "traittutor"
  | "mastery_path"
  | "courseware"
  | "flashcards"
  | "quiz";

interface DashboardItem {
  key: DashKey;
  href: string;
  icon: TraitTutorIconName;
  title: Lang;
  blurb: Lang;
  /** Unit shown after the live count, e.g. "168 conversations". */
  unit: Lang;
  /** Optional invitation shown next to an empty (zero) count. */
  invite?: Lang;
  load: () => Promise<number>;
}

interface DashboardGroup {
  label: Lang;
  items: DashboardItem[];
}

const GROUPS: DashboardGroup[] = [
  {
    label: { zh: "学习工具", en: "Study Tools" },
    items: [
      {
        key: "courseware",
        href: "/space/courseware",
        icon: "courseware",
        title: { zh: "课件", en: "Courseware" },
        blurb: { zh: "把材料变成可逐节学习的课件", en: "Turn material into a guided lesson" },
        unit: { zh: "个学习包", en: "learning packs" },
        invite: { zh: "生成第一份 →", en: "Create the first →" },
        load: async () => (await listLearningPacks()).length,
      },
      {
        key: "flashcards",
        href: "/space/flashcards",
        icon: "standard",
        title: { zh: "Flashcard 学习", en: "Flashcard Study" },
        blurb: { zh: "用主动回忆巩固关键概念", en: "Use active recall for key concepts" },
        unit: { zh: "个学习包", en: "learning packs" },
        load: async () => (await listLearningPacks()).length,
      },
      {
        key: "quiz",
        href: "/space/quiz",
        icon: "measurement",
        title: { zh: "Quiz 测验", en: "Quiz" },
        blurb: { zh: "生成练习、作答并查看解析", en: "Generate practice, answer, and review" },
        unit: { zh: "个学习包", en: "learning packs" },
        load: async () => (await listLearningPacks()).length,
      },
    ],
  },
  {
    label: { zh: "学习记录", en: "Learning Records" },
    items: [
      {
        key: "question_bank",
        href: "/space/questions",
        icon: "measurement",
        title: { zh: "错题与练习记录", en: "Practice History" },
        blurb: {
          zh: "回顾 Quiz 作答、错题与解析",
          en: "Review quiz attempts, incorrect answers, and explanations",
        },
        unit: { zh: "道题", en: "questions" },
        load: async () => (await listNotebookEntries({ limit: 1 })).total,
      },
    ],
  },
  {
    label: { zh: "个性化", en: "Personalization" },
    items: [
      {
        key: "traittutor",
        href: "/space/traittutor",
        icon: "personality",
        title: { zh: "学习画像", en: "Learning Profile" },
        blurb: {
          zh: "大五画像与学习支持策略",
          en: "Your Big Five profile and learning support strategy",
        },
        unit: { zh: "份画像", en: "profiles" },
        load: async () => (await listTraitProfiles()).length,
      },
      {
        key: "mastery_path",
        href: "/space/learning",
        icon: "motivation",
        title: { zh: "精通之路", en: "Mastery Path" },
        blurb: {
          zh: "掌握式学习：硬门槛与间隔复习",
          en: "Mastery-based learning: hard gate and spaced review",
        },
        unit: { zh: "条路径", en: "paths" },
        invite: { zh: "开始一条 →", en: "Start one →" },
        load: async () =>
          (await fetchAllProgress()).summaries.filter((s) => s.kp_count > 0)
            .length,
      },
    ],
  },
];

const ALL_ITEMS = GROUPS.flatMap((g) => g.items);

export default function SpaceDashboard() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((l: Lang) => (zh ? l.zh : l.en), [zh]);

  const [counts, setCounts] = useState<Partial<Record<DashKey, number>>>({});

  useEffect(() => {
    let cancelled = false;
    // Each row loads independently so one slow/failed endpoint never blanks
    // the whole dashboard.
    for (const item of ALL_ITEMS) {
      item
        .load()
        .then((n) => {
          if (!cancelled) setCounts((prev) => ({ ...prev, [item.key]: n }));
        })
        .catch(() => {
          /* leave undefined → row just omits the count */
        });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  // Cumulative row offsets so each group continues the numbering without
  // mutating a variable during render.
  const groupOffsets = useMemo(() => {
    const offsets: number[] = [];
    let acc = 0;
    for (const group of GROUPS) {
      offsets.push(acc);
      acc += group.items.length;
    }
    return offsets;
  }, []);

  return (
    <div>
      <header className="mb-10">
        <h1 className="font-serif text-[24px] font-semibold leading-tight tracking-tight text-[var(--foreground)]">
          {tr({ zh: "学习空间", en: "Learning Space" })}
        </h1>
        <p className="mt-2 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
          {tr({
            zh: "你的对话、学习材料与练习记录，集中在一处。",
            en: "Your conversations, learning materials, and practice in one place.",
          })}
        </p>
      </header>

      <div className="space-y-11">
        {GROUPS.map((group, groupIndex) => (
          <section key={group.label.en}>
            <h2 className="pb-2.5 text-[11.5px] font-medium uppercase tracking-[0.2em] text-[var(--muted-foreground)]">
              {tr(group.label)}
            </h2>
            <div>
              {group.items.map((item, itemIndex) => (
                <TocRow
                  key={item.key}
                  item={item}
                  index={groupOffsets[groupIndex] + itemIndex + 1}
                  count={counts[item.key]}
                  tr={tr}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function TocRow({
  item,
  index,
  count,
  tr,
}: {
  item: DashboardItem;
  index: number;
  count: number | undefined;
  tr: (l: Lang) => string;
}) {
  const loaded = count !== undefined;
  const empty = loaded && count === 0;
  const formatted = useMemo(
    () => (loaded && !empty ? count.toLocaleString() : ""),
    [loaded, empty, count],
  );

  return (
    <Link
      href={item.href}
      className="group flex items-baseline gap-5 border-t border-[var(--border)] px-0.5 py-3.5 transition-colors last:border-b hover:bg-[var(--primary)]/[0.05]"
    >
      <span className="w-6 shrink-0 font-serif text-[12px] tabular-nums text-[var(--primary)]">
        {String(index).padStart(2, "0")}
      </span>
      <TraitTutorIcon name={item.icon} size={19} strokeWidth={1.65} className="mt-0.5 shrink-0" />
      <span className="w-28 shrink-0 text-[14.5px] font-medium leading-snug tracking-tight text-[var(--foreground)]">
        {tr(item.title)}
      </span>
      <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--muted-foreground)]">
        {tr(item.blurb)}
      </span>
      <span className="flex shrink-0 items-baseline gap-3">
        {!loaded ? (
          <span className="my-[3px] h-3 w-10 animate-pulse rounded bg-[var(--muted)]" />
        ) : empty ? (
          <>
            <span className="text-[13px] text-[var(--border)]">——</span>
            {item.invite && (
              <span className="text-[12.5px] text-[var(--primary)] opacity-0 transition-opacity group-hover:opacity-100">
                {tr(item.invite)}
              </span>
            )}
          </>
        ) : (
          <span className="text-[13px] tabular-nums text-[var(--muted-foreground)]">
            {formatted} {tr(item.unit)}
          </span>
        )}
      </span>
    </Link>
  );
}
