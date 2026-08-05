"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft, Check, ChevronRight, Circle,
  CircleHelp, Loader2, PanelRightOpen, Play, RefreshCcw, Route,
  ShieldCheck, SkipForward, Sparkles, Volume2, X,
} from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";
import { useAppShell } from "@/context/AppShellContext";
import { readStoredSidebarCollapsed } from "@/context/app-shell-storage";
import {
  createLearningComponentPlan,
  createTraitTutorGenerationTask,
  generationErrorMessage,
  getLearningPack,
  getTraitTutorGenerationTask,
  recordLearningComponentEvent,
  updateLearningPack,
  type GenerateKind,
  type GenerateSuiteResult,
  type LearningComponent,
  type LearningComponentPlan,
  type LearningPack,
} from "@/lib/traittutor-api";

type Locale = "zh" | "en";
type ComponentOutput = GenerateSuiteResult | { audioUrl: string; transcript: string };

export default function LearningCanvas({ packId, locale }: { packId: string; locale: Locale }) {
  const zh = locale === "zh";
  const { setSidebarCollapsed } = useAppShell();
  const [pack, setPack] = useState<LearningPack | null>(null);
  const [plan, setPlan] = useState<LearningComponentPlan | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [outputs, setOutputs] = useState<Record<string, ComponentOutput>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [whyOpen, setWhyOpen] = useState(false);
  const [adjusted, setAdjusted] = useState(false);

  useEffect(() => {
    const wasCollapsed = readStoredSidebarCollapsed();
    setSidebarCollapsed(true);
    return () => {
      // Restore the entry state only when the sidebar is still collapsed.
      // If the learner expanded it manually, that newer preference wins.
      if (readStoredSidebarCollapsed()) setSidebarCollapsed(wasCollapsed);
    };
  }, [setSidebarCollapsed]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const loaded = await getLearningPack(packId);
        let current = loaded.component_plans?.find((item) => item.plan_id === loaded.active_plan_id) ?? null;
        if (!current) current = await createLearningComponentPlan(packId, { instruction: loaded.goal?.text ?? loaded.title });
        if (!active) return;
        setPack(loaded);
        setPlan(current);
        // Component output is durable through its generation id. Rehydrate it
        // before rendering so a refresh never turns completed work into a new
        // billable generation request.
        const restored = await Promise.all(current.components.map(async (component) => {
          if (!component.output_ref) return null;
          try {
            const output = await getTraitTutorGenerationTask(component.output_ref);
            return "result" in output ? [component.component_id, output] as const : null;
          } catch {
            return null;
          }
        }));
        if (!active) return;
        setOutputs(Object.fromEntries(restored.filter((entry): entry is readonly [string, GenerateSuiteResult] => entry !== null)));
        setSelectedId(current.components.find((item) => !["completed", "skipped"].includes(item.status))?.component_id ?? current.components[0]?.component_id ?? null);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [packId]);

  const selected = useMemo(
    () => plan?.components.find((item) => item.component_id === selectedId) ?? plan?.components[0] ?? null,
    [plan, selectedId],
  );
  const completed = plan?.components.filter((item) => item.status === "completed").length ?? 0;

  const applyComponentEvent = useCallback(async (
    component: LearningComponent,
    event: Parameters<typeof recordLearningComponentEvent>[3],
  ) => {
    if (!plan) return undefined;
    const result = await recordLearningComponentEvent(packId, plan.plan_id, component.component_id, event);
    if (result.replanned_plan) {
      setPlan(result.replanned_plan);
      setAdjusted(true);
      setSelectedId(result.replanned_plan.components.find((item) => !["completed", "skipped"].includes(item.status))?.component_id ?? null);
      return result;
    }
    setPlan((current) => current ? {
      ...current,
      components: current.components.map((item) => item.component_id === component.component_id ? result.component : item),
    } : current);
    if (event.action === "complete" || event.action === "skip") {
      const index = plan.components.findIndex((item) => item.component_id === component.component_id);
      setSelectedId(plan.components[index + 1]?.component_id ?? component.component_id);
    }
    return result;
  }, [packId, plan]);

  const generate = useCallback(async (component: LearningComponent) => {
    if (!pack) return;
    setBusy(component.component_id);
    setError(null);
    try {
      await applyComponentEvent(component, { action: "start", replan: false });
      const generationType = executorKind(component.executor);
      const material = pack.material as {
        source_type?: "knowledge" | "notebook" | "upload" | "paste";
        title?: string; text?: string; source_id?: string | null; metadata?: Record<string, unknown>;
      };
      const accepted = await createTraitTutorGenerationTask({
        generation_type: generationType,
        material: {
          source_type: material.source_type ?? "paste",
          title: material.title ?? pack.title,
          text: material.text ?? plan?.goal ?? pack.title,
          source_id: material.source_id,
          metadata: material.metadata,
        },
        options: {
          learning_component: {
            component_id: component.component_id,
            component_type: component.component_type,
            reason: component.reason,
            concept_refs: component.concept_refs,
          },
        },
      });
      const result = await waitForGeneration(accepted.generation_id);
      await updateLearningPack(packId, { generation_id: result.generation_id });
      let output: ComponentOutput = result;
      let mediaDegraded = component.executor === "image" && result.result.image_generation?.status !== "completed";
      if (component.executor === "audio") {
        const transcript = outputText(result) || plan?.goal || pack.title;
        try {
          const response = await apiFetch(apiUrl("/api/v1/voice/tts"), {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: transcript.slice(0, 4000) }),
          });
          if (!response.ok) throw new Error("tts unavailable");
          output = { audioUrl: URL.createObjectURL(await response.blob()), transcript };
        } catch {
          mediaDegraded = true;
          output = result;
        }
      }
      setOutputs((current) => ({ ...current, [component.component_id]: output }));
      if (component.executor === "lesson" || component.executor === "image" || component.executor === "audio") {
        await applyComponentEvent(component, { action: mediaDegraded ? "degrade" : "complete", output_ref: result.generation_id, replan: false });
      }
    } catch (reason) {
      setError(generationErrorMessage(reason, zh));
      await applyComponentEvent(component, { action: "degrade", replan: false }).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }, [applyComponentEvent, pack, packId, plan?.goal, zh]);

  if (loading) return <FullState icon={<Loader2 className="animate-spin" />} title={zh ? "正在恢复学习路径" : "Restoring your learning path"} />;
  if (!pack || !plan || !selected) return <FullState icon={<CircleHelp />} title={error ?? (zh ? "学习路径暂不可用" : "Learning path unavailable")} />;

  return (
    <main className="learning-canvas">
      <header className="learning-canvas__header">
        <div className="learning-canvas__toolbar">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/space/learning" className="learning-icon-button"><ArrowLeft size={16} /></Link>
            <div className="min-w-0">
              <p className="learning-eyebrow">Learning path · v{plan.version}</p>
              <h1 className="truncate font-serif text-lg font-semibold md:text-xl">{plan.goal}</h1>
            </div>
          </div>
          <button onClick={() => setWhyOpen(true)} className="learning-button learning-button--secondary shrink-0 px-3 py-2 xl:hidden">
            <PanelRightOpen size={15} />{zh ? "为什么这一步" : "Why this step"}
          </button>
        </div>
      </header>

      {adjusted ? (
        <div className="learning-notice">
          {zh ? "根据刚才的作答，尚未开始的下一步已经调整。" : "Based on your answer, the unstarted next steps were adjusted."}
        </div>
      ) : null}

      <div className="learning-canvas__layout">
        <aside className="learning-canvas__path-panel">
          <div className="mb-5 flex items-end justify-between">
            <div><p className="learning-meta">{zh ? "学习路径" : "Learning path"}</p><p className="learning-copy-muted mt-1 text-xs">{completed} / {plan.components.length} {zh ? "已完成" : "completed"}</p></div>
            <Route size={18} className="learning-accent" />
          </div>
          <ol className="flex gap-2 overflow-x-auto pb-2 lg:block lg:space-y-1 lg:overflow-visible">
            {plan.components.map((component, index) => (
              <li key={component.component_id} className="min-w-[210px] lg:min-w-0">
                <button onClick={() => setSelectedId(component.component_id)} className={`learning-step ${selected.component_id === component.component_id ? "learning-step--active" : ""}`}>
                  <StatusIcon status={component.status} active={selected.component_id === component.component_id} />
                  <span className="min-w-0 flex-1"><span className="learning-meta block text-[8px]">{String(index + 1).padStart(2, "0")}</span><span className="block truncate text-xs font-medium">{zh ? component.label_zh : component.label_en}</span></span>
                  <ChevronRight size={13} className="opacity-40" />
                </button>
              </li>
            ))}
          </ol>
        </aside>

        <section className="learning-canvas__content">
          <div className="flex min-h-0 w-full flex-1 flex-col">
            <div className="learning-canvas__section-heading mb-5 flex items-start justify-between gap-4 border-b pb-5">
              <div><p className="learning-eyebrow">{stageLabel(selected.bkt_stage, zh)} · {modalityLabel(selected.modality, zh)}</p><h2 className="mt-2 font-serif text-2xl font-semibold md:text-3xl">{zh ? selected.label_zh : selected.label_en}</h2><p className="learning-copy-muted mt-2 max-w-2xl text-sm leading-6">{componentReason(selected, zh)}</p></div>
              <span className="learning-status-pill">{statusLabel(selected.status, zh)}</span>
            </div>

            <ComponentBody
              component={selected}
              output={outputs[selected.component_id]}
              goal={plan.goal}
              zh={zh}
              busy={busy === selected.component_id}
              onGenerate={() => void generate(selected)}
              onEvent={(event) => applyComponentEvent(selected, event)}
            />
            {error ? <p role="alert" className="learning-alert--error">{error}</p> : null}
          </div>
        </section>

        <aside className="learning-canvas__rationale">
          <WhyPanel component={selected} plan={plan} zh={zh} />
        </aside>
      </div>

      {whyOpen ? <div className="learning-drawer-backdrop" onClick={() => setWhyOpen(false)}><aside className="learning-drawer" onClick={(event) => event.stopPropagation()}><button onClick={() => setWhyOpen(false)} className="learning-icon-button float-right"><X size={16} /></button><WhyPanel component={selected} plan={plan} zh={zh} /></aside></div> : null}
    </main>
  );
}

function ComponentBody({ component, output, goal, zh, busy, onGenerate, onEvent }: {
  component: LearningComponent; output?: ComponentOutput; goal: string; zh: boolean; busy: boolean;
  onGenerate: () => void;
  onEvent: (event: Parameters<typeof recordLearningComponentEvent>[3]) => Promise<unknown>;
}) {
  if (component.executor === "deterministic") {
    return <div className="learning-card learning-card--large"><Sparkles className="learning-accent" /><h3 className="mt-6 font-serif text-xl">{goal}</h3><p className="learning-copy-muted mt-3 text-sm leading-7">{zh ? "完成标准：能够解释核心概念、在练习中使用它，并通过一次主动回忆或迁移检查。" : "Completion means explaining the core idea, using it in practice, and passing an active-recall or transfer check."}</p><ActionBar component={component} zh={zh} onEvent={onEvent} /></div>;
  }
  if (!output) return <div className="learning-component-stage"><div className="learning-component-stage__intro"><span className="learning-icon-badge"><Play size={20} /></span><p className="learning-meta mt-7">{zh ? "当前步骤已就绪" : "Ready for this step"}</p><h3 className="mt-3 max-w-xl font-serif text-2xl font-semibold md:text-3xl">{zh ? "生成当前学习组件" : "Generate this learning component"}</h3><p className="learning-copy-muted mt-3 max-w-xl text-sm leading-7">{zh ? "系统会复用当前材料、学科知识状态和支持动作，只生成这一阶段需要的内容。" : "TraitTutor reuses the current source, subject knowledge state, and support actions to generate only what this step needs."}</p></div><div className="learning-component-stage__action"><div><p className="text-sm font-medium">{zh ? "准备好后开始" : "Start when ready"}</p><p className="learning-copy-muted mt-1 text-xs">{zh ? "生成失败只会降级当前组件，不影响整条路径。" : "A failure degrades only this component, not the learning path."}</p></div><button disabled={busy} onClick={onGenerate} className="learning-button learning-button--primary px-5 py-3 text-sm">{busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}{busy ? (zh ? "正在生成" : "Generating") : (zh ? "开始学习" : "Start learning")}</button></div></div>;
  if ("audioUrl" in output) return <div className="learning-card learning-card--large"><Volume2 className="learning-accent" /><audio controls src={output.audioUrl} className="mt-5 w-full" /><p className="mt-5 whitespace-pre-wrap text-sm leading-7">{output.transcript}</p><ActionBar component={component} zh={zh} onEvent={onEvent} /></div>;
  if (component.executor === "assessment") return <AssessmentView component={component} result={output} zh={zh} onEvent={onEvent} />;
  if (component.executor === "retrieval") return <RetrievalView component={component} result={output} zh={zh} onEvent={onEvent} />;
  return <LessonView result={output} component={component} zh={zh} onEvent={onEvent} />;
}

function LessonView({ result, component, zh, onEvent }: { result: GenerateSuiteResult; component: LearningComponent; zh: boolean; onEvent: (event: Parameters<typeof recordLearningComponentEvent>[3]) => Promise<unknown> }) {
  const sections = result.result.sections ?? [];
  const images = [...(result.result.images ?? []), ...sections.flatMap((section) => section.images ?? [])];
  return <div className="space-y-5">{images.slice(0, 2).map((image) => <figure key={image.url} className="learning-card overflow-hidden p-0"><img src={image.url} alt={image.alt} className="h-auto w-full object-contain" /><figcaption className="learning-copy-muted px-4 py-2 text-xs">{image.alt}</figcaption></figure>)}{sections.length ? sections.map((section, index) => <article key={index} className="learning-card"><h3 className="font-serif text-lg">{section.title ?? section.section_title}</h3><div className="mt-3 space-y-3 text-sm leading-7">{(section.content ?? [section.core_content]).filter(Boolean).map((paragraph, item) => <p key={item}>{paragraph}</p>)}</div></article>) : <article className="learning-card whitespace-pre-wrap text-sm leading-7">{result.result.markdown ?? result.result.title}</article>}<ActionBar component={component} zh={zh} onEvent={onEvent} /></div>;
}

function AssessmentView({ component, result, zh, onEvent }: { component: LearningComponent; result: GenerateSuiteResult; zh: boolean; onEvent: (event: Parameters<typeof recordLearningComponentEvent>[3]) => Promise<unknown> }) {
  const items = (result.result.items ?? []).slice(0, 5);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [submitted, setSubmitted] = useState(false);
  const submit = async () => {
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      const questionId = String(item.question_id ?? "");
      await onEvent({
        event_id: `${result.generation_id}:${questionId || index}`.slice(0, 128),
        action: index === items.length - 1 ? "complete" : "feedback",
        answer: answers[index] ?? "",
        question_id: questionId,
        output_ref: result.generation_id,
        concept_id: String(item.node_id ?? component.concept_refs[0] ?? component.component_id),
        concept_label: String(item.node_name ?? item.question ?? "Concept"),
        replan: index === items.length - 1,
      });
    }
    setSubmitted(true);
  };
  return <div className="space-y-4">{items.map((item, index) => { const options = Array.isArray(item.options) ? item.options as Array<Record<string, unknown>> : []; return <fieldset key={String(item.question_id ?? index)} className="learning-card"><legend className="px-2 font-serif text-lg">{index + 1}. {String(item.question ?? "")}</legend><div className="mt-3 grid gap-2">{options.map((option, optionIndex) => { const value = String(option.key ?? option.id ?? option.text ?? optionIndex); return <label key={value} className={`learning-choice ${answers[index] === value ? "learning-choice--selected" : ""}`}><input type="radio" name={`q-${index}`} value={value} checked={answers[index] === value} onChange={() => setAnswers((current) => ({ ...current, [index]: value }))} />{String(option.text ?? value)}</label>; })}</div>{submitted ? <p className="learning-success mt-3 text-xs">{zh ? "已记录到当前学科的学习证据。" : "Recorded as subject-scoped learning evidence."}</p> : null}</fieldset>; })}<button disabled={!items.length || Object.keys(answers).length < items.length || submitted} onClick={() => void submit()} className="learning-button learning-button--primary px-5 py-3 text-sm">{zh ? "提交并调整下一步" : "Submit and adapt next steps"}</button></div>;
}

function RetrievalView({ component, result, zh, onEvent }: { component: LearningComponent; result: GenerateSuiteResult; zh: boolean; onEvent: (event: Parameters<typeof recordLearningComponentEvent>[3]) => Promise<unknown> }) {
  const item = result.result.items?.[0] ?? {};
  const [flipped, setFlipped] = useState(false);
  return <div className="learning-card learning-card--large text-center"><p className="learning-eyebrow">{zh ? "主动回忆" : "Active recall"}</p><h3 className="mx-auto mt-12 max-w-2xl font-serif text-2xl leading-10">{String(item.front ?? item.question ?? result.result.title)}</h3>{flipped ? <p className="learning-action-bar mx-auto mt-8 max-w-2xl border-t pt-6 text-sm leading-7">{String(item.back ?? item.answer ?? "")}</p> : <button onClick={() => setFlipped(true)} className="learning-button learning-button--secondary mt-10 px-4 py-2 text-sm">{zh ? "翻面核对" : "Reveal answer"}</button>}{flipped ? <div className="mt-10 grid gap-2 sm:grid-cols-3">{(["unknown", "uncertain", "known"] as const).map((state) => <button key={state} onClick={() => onEvent({ action: "complete", observation: state, concept_id: String(item.node_id ?? component.concept_refs[0] ?? component.component_id), concept_label: String(item.node_name ?? item.front ?? "Concept"), output_ref: result.generation_id, replan: true })} className="learning-button learning-button--secondary px-3 py-3 text-sm">{{ unknown: zh ? "还不熟" : "Not yet", uncertain: zh ? "有点模糊" : "Uncertain", known: zh ? "掌握了" : "Known" }[state]}</button>)}</div> : null}</div>;
}

function ActionBar({ component, zh, onEvent }: { component: LearningComponent; zh: boolean; onEvent: (event: Parameters<typeof recordLearningComponentEvent>[3]) => Promise<unknown> }) {
  return <div className="learning-action-bar mt-6 flex flex-wrap gap-2 border-t pt-4"><button onClick={() => onEvent({ action: "complete", replan: false })} className="learning-button learning-button--primary"><Check size={14} />{zh ? "完成并继续" : "Complete and continue"}</button>{!component.required ? <button onClick={() => onEvent({ action: "skip", replan: false })} className="learning-button learning-button--secondary"><SkipForward size={14} />{zh ? "跳过" : "Skip"}</button> : null}<button onClick={() => onEvent({ action: "retry", feedback: "request_alternative_explanation", replan: false })} className="learning-button learning-button--secondary"><RefreshCcw size={14} />{zh ? "换一种解释" : "Explain differently"}</button></div>;
}

function WhyPanel({ component, plan, zh }: { component: LearningComponent; plan: LearningComponentPlan; zh: boolean }) {
  const dimensions = component.support_dimensions.map((key) => plan.support_state_snapshot.dimensions[key]).filter(Boolean);
  return <div className="pt-12 xl:pt-0"><p className="learning-eyebrow">{zh ? "为什么是这一步" : "Why this step"}</p><h2 className="mt-2 font-serif text-xl">{zh ? "学习依据" : "Learning rationale"}</h2><dl className="mt-6 space-y-5 text-xs"><WhyRow label={zh ? "当前目标" : "Current goal"} value={plan.goal} /><WhyRow label={zh ? "知识阶段" : "Knowledge stage"} value={stageLabel(component.bkt_stage, zh)} /><WhyRow label={zh ? "教学动作" : "Teaching action"} value={componentReason(component, zh)} /><WhyRow label={zh ? "材料证据" : "Source evidence"} value={component.evidence_refs.length ? (zh ? `${component.evidence_refs.length} 条来源` : `${component.evidence_refs.length} refs`) : (zh ? "当前材料与目标" : "Current source and goal")} /><WhyRow label={zh ? "支持信号" : "Support signal"} value={dimensions.length ? component.support_dimensions.map((item) => supportLabel(item, zh)).join(" · ") : (zh ? "标准结构" : "Standard structure")} /></dl><p className="learning-action-bar learning-copy-muted mt-7 flex gap-2 border-t pt-5 text-[10px] leading-5"><ShieldCheck size={14} className="mt-0.5 shrink-0" />{zh ? "支持状态只用于选择临时教学动作，不用于诊断能力、人格、情绪或固定学习风格。" : plan.support_state_snapshot.boundary}</p></div>;
}
function WhyRow({ label, value }: { label: string; value: string }) { return <div><dt className="learning-meta text-[8px]">{label}</dt><dd className="mt-1.5 leading-5">{value}</dd></div>; }
function StatusIcon({ status, active }: { status: LearningComponent["status"]; active: boolean }) { if (status === "completed") return <Check size={15} className="learning-accent" />; if (active) return <span className="learning-status-dot" />; return <Circle size={14} className={status === "degraded" ? "text-[var(--destructive)]" : "text-[var(--border)]"} />; }
function stageLabel(stage: string, zh: boolean): string { if (!zh) return stage.replaceAll("_", " "); return ({ unobserved: "尚未观察", emerging: "正在形成", developing: "逐步理解", proficient: "基本掌握", mastered: "稳定掌握", needs_support: "需要支持" } as Record<string, string>)[stage] ?? stage.replaceAll("_", " "); }
function modalityLabel(modality: string, zh: boolean): string { if (!zh) return modality.replaceAll("_", " "); return ({ interactive: "互动", visual: "图解", audio: "语音", text: "阅读", assessment: "诊断", retrieval: "回忆" } as Record<string, string>)[modality] ?? modality.replaceAll("_", " "); }
function statusLabel(status: string, zh: boolean): string { if (!zh) return status; return ({ pending: "待开始", active: "进行中", completed: "已完成", skipped: "已跳过", degraded: "已降级" } as Record<string, string>)[status] ?? status; }
function supportLabel(key: string, zh: boolean): string { if (!zh) return key.replaceAll("_", " "); return ({ monitoring_regulation: "监控与调节", cognitive_scaffolding: "认知支架", motivation_engagement: "动机参与", social_affective: "互动支持" } as Record<string, string>)[key] ?? key.replaceAll("_", " "); }
function componentReason(component: LearningComponent, zh: boolean): string { if (!zh) return component.reason; return ({ goal_map: "先明确目标、阶段与完成标准，让后续学习有清晰方向。", diagnostic_check: "当前学科还没有可判分证据，先用短诊断确认真实起点。", concept_explanation: "结合当前知识状态补足核心概念，再进入练习。", worked_example: "通过分步例题把概念连接到可执行的方法。", visual_map: "用关系图呈现重点概念和它们之间的联系。", audio_explanation: "用语音和文字稿提供另一种理解入口。", guided_practice: "在提示和即时反馈下完成练习，形成可判分证据。", retrieval_card: "用主动回忆检验保持程度，并安排后续复习。", progress_checkpoint: "回看当前证据，确认下一阶段最值得投入的内容。", reflection_prompt: "用简短反思整理本轮学习策略，不把自评当作掌握证据。", transfer_challenge: "把已学知识迁移到新情境，检验能否灵活运用。", review_queue: "优先复习已到期或仍需支持的概念。" } as Record<string, string>)[component.component_type] ?? component.reason; }
function FullState({ icon, title }: { icon: React.ReactNode; title: string }) { return <main className="learning-full-state"><div className="learning-accent text-center">{icon}<p className="mt-4 font-serif text-xl text-[var(--foreground)]">{title}</p></div></main>; }
function executorKind(executor: LearningComponent["executor"]): GenerateKind { return executor === "assessment" ? "quiz" : executor === "retrieval" ? "flashcards" : "courseware"; }
function outputText(result: GenerateSuiteResult): string { return result.result.markdown ?? result.result.sections?.flatMap((section) => section.content ?? [section.core_content ?? ""]).filter(Boolean).join("\n") ?? result.result.title; }
async function waitForGeneration(generationId: string): Promise<GenerateSuiteResult> { for (let attempt = 0; attempt < 180; attempt += 1) { const result = await getTraitTutorGenerationTask(generationId); if ("result" in result) return result; if (["failed", "cancelled", "interrupted"].includes(result.status)) throw new Error(result.error_code ?? result.error ?? "generation_failed"); await new Promise((resolve) => window.setTimeout(resolve, 650)); } throw new Error("generation_interrupted"); }
