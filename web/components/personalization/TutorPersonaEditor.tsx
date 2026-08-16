"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Bell, Check, RefreshCw, RotateCcw, Save, X } from "lucide-react";
import TutorPersonaPreview from "@/components/personalization/TutorPersonaPreview";
import { useAppShell } from "@/context/AppShellContext";
import {
  TutorPersonaApiError,
  acknowledgeTutorReminder,
  cancelTutorReminder,
  getTutorPersona,
  listTutorReminders,
  previewTutorPersona,
  replaceTutorPersona,
  resetTutorPersona,
  type AddressTerm,
  type AvatarRef,
  type EmojiPolicy,
  type FeedbackFormat,
  type PersonaIntensity,
  type PersonaProactivity,
  type TextScale,
  type TutorPersonaContract,
  type TutorPersonaProfile,
  type TutorReminder,
  type TutorPersonaSettings,
  type TutorTone,
  type VoiceId,
} from "@/lib/tutor-persona-api";

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

const ADDRESS_TERMS: AddressTerm[] = ["name", "you", "learner", "classmate"];
const AVATARS: AvatarRef[] = ["default", "mentor", "guide", "study_buddy"];
const VOICES: VoiceId[] = ["default", "calm", "bright", "steady"];
const TONES: TutorTone[] = ["warm", "neutral", "energetic", "calm"];
const INTENSITIES: PersonaIntensity[] = ["low", "medium", "high"];
const FEEDBACK_FORMATS: FeedbackFormat[] = ["concise", "balanced", "detailed", "socratic"];
const PROACTIVITY: PersonaProactivity[] = ["off", "reminders_only", "moderate"];
const EMOJI_POLICIES: EmojiPolicy[] = ["none", "minimal", "moderate"];
const TEXT_SCALES: TextScale[] = ["standard", "large", "extra_large"];
const COMMON_TIMEZONES = [
  "UTC",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Europe/London",
  "America/New_York",
  "America/Los_Angeles",
];
const NAME_PATTERN = /^[\p{L}\p{N}_ .·-]+$/u;

export default function TutorPersonaEditor() {
  const { language } = useAppShell();
  const zh = language === "zh";
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh]);
  const [profile, setProfile] = useState<TutorPersonaProfile | null>(null);
  const [settings, setSettings] = useState<TutorPersonaSettings | null>(null);
  const [preview, setPreview] = useState<TutorPersonaContract | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [addressError, setAddressError] = useState("");
  const [conflictVersion, setConflictVersion] = useState<number | null>(null);
  const alertRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError("");
    try {
      const next = await getTutorPersona(signal);
      if (signal?.aborted) return;
      setProfile(next);
      setSettings(next.settings);
      setConflictVersion(null);
      setMutationError("");
    } catch (cause) {
      if (signal?.aborted || isAbort(cause)) return;
      setLoadError(messageFor(cause, tr));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [tr]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // The backend preview is a deterministic compiler. Debouncing prevents a
  // request storm while retaining a live, non-persistent view of typed edits.
  useEffect(() => {
    if (!settings) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setPreviewing(true);
      setPreviewError("");
      try {
        const next = await previewTutorPersona(settings, controller.signal);
        if (!controller.signal.aborted) setPreview(next);
      } catch (cause) {
        if (!controller.signal.aborted && !isAbort(cause)) {
          setPreviewError(
            tr({
              zh: "当前草稿的表达预览暂时无法编译；草稿尚未保存。",
              en: "The expression preview could not be compiled. The draft is still unsaved.",
            }),
          );
        }
      } finally {
        if (!controller.signal.aborted) setPreviewing(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [settings, tr]);

  // A conflict alert is conditionally mounted. Focus it only after React has
  // committed the DOM node, rather than racing a requestAnimationFrame against
  // concurrent updates or Fast Refresh during an active save.
  useEffect(() => {
    if (mutationError) alertRef.current?.focus();
  }, [mutationError]);

  const validationError = useMemo(() => validate(settings, tr), [settings, tr]);
  const dirty = Boolean(profile && settings && JSON.stringify(profile.settings) !== JSON.stringify(settings));

  function update<K extends keyof TutorPersonaSettings>(key: K, value: TutorPersonaSettings[K]) {
    setSettings((current) => (current ? { ...current, [key]: value } : current));
    setStatusMessage("");
    setMutationError("");
  }

  function toggleAddress(term: AddressTerm) {
    if (!settings) return;
    const selected = settings.address_terms.includes(term);
    if (selected && settings.address_terms.length === 1) {
      setAddressError(
        tr({ zh: "至少保留一种称呼方式。", en: "Keep at least one form of address." }),
      );
      return;
    }
    setAddressError("");
    update(
      "address_terms",
      selected
        ? settings.address_terms.filter((item) => item !== term)
        : [...settings.address_terms, term],
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!profile || !settings || validationError) return;
    await mutate(async () => {
      const next = await replaceTutorPersona(
        settings,
        profile.version,
        `persona-save-${crypto.randomUUID()}`,
      );
      acceptProfile(next);
      setStatusMessage(
        tr({ zh: `Tutor Persona 已保存为版本 ${next.version}。`, en: `Tutor Persona saved as version ${next.version}.` }),
      );
    });
  }

  async function reset() {
    if (!profile) return;
    await mutate(async () => {
      const next = await resetTutorPersona(
        profile.version,
        `persona-reset-${crypto.randomUUID()}`,
      );
      acceptProfile(next);
      setStatusMessage(
        tr({ zh: `已恢复默认表达设置（版本 ${next.version}）。`, en: `Default expression settings restored (version ${next.version}).` }),
      );
    });
  }

  function acceptProfile(next: TutorPersonaProfile) {
    setProfile(next);
    setSettings(next.settings);
    setConflictVersion(null);
    setMutationError("");
  }

  async function mutate(work: () => Promise<void>) {
    setBusy(true);
    setMutationError("");
    setStatusMessage("");
    setConflictVersion(null);
    try {
      await work();
    } catch (cause) {
      if (
        cause instanceof TutorPersonaApiError &&
        cause.status === 409 &&
        cause.code === "version_conflict"
      ) {
        setConflictVersion(cause.actualVersion ?? null);
        setMutationError(
          tr({
            zh: "Tutor Persona 已在另一个窗口更新。当前草稿未覆盖新版本，请先加载最新版本。",
            en: "Tutor Persona changed in another window. This draft did not overwrite it; load the latest version first.",
          }),
        );
      } else {
        setMutationError(messageFor(cause, tr));
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading && !settings) return <EditorLoading tr={tr} />;

  if (!settings || !profile) {
    return (
      <div className="rounded-xl border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-5" role="alert">
        <h2 className="font-semibold">
          {tr({ zh: "Tutor Persona 无法读取", en: "Tutor Persona could not be loaded" })}
        </h2>
        <p className="mt-2 text-sm leading-relaxed">{loadError}</p>
        <button type="button" onClick={() => void load()} className={`${secondaryButtonClass} mt-4`}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          {tr({ zh: "重试", en: "Try again" })}
        </button>
      </div>
    );
  }

  const timezoneOptions = timezoneChoices(settings.quiet_hours.timezone);

  return (
    <div className="space-y-5">
      <TutorReminderInbox tr={tr} zh={zh} />

      {mutationError ? (
        <div
          ref={alertRef}
          tabIndex={-1}
          role="alert"
          className="rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-4 text-sm text-[var(--destructive)] focus:outline-none"
        >
          <p>{mutationError}</p>
          {conflictVersion !== null ? (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <span className="text-xs">
                {tr({
                  zh: `页面版本 ${profile.version} · 服务端版本 ${conflictVersion}`,
                  en: `Page version ${profile.version} · Server version ${conflictVersion}`,
                })}
              </span>
              <button type="button" disabled={busy} onClick={() => void load()} className={secondaryButtonClass}>
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                {tr({ zh: "加载最新版本", en: "Load latest version" })}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {statusMessage ? (
        <p role="status" className="rounded-lg border border-emerald-500/35 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
          {statusMessage}
        </p>
      ) : null}

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(20rem,0.8fr)]">
        <form onSubmit={(event) => void save(event)} className="space-y-5">
          <SettingsSection
            eyebrow="IDENTITY"
            title={tr({ zh: "称呼与呈现", en: "Address and presentation" })}
            description={tr({
              zh: "名称只是界面标签；所有称呼与头像来自封闭选项。",
              en: "The name is only a display label; address terms and avatars use closed options.",
            })}
          >
            <TextField
              id="persona-name"
              label={tr({ zh: "显示名称", en: "Display name" })}
              value={settings.name}
              onChange={(value) => update("name", value)}
              description={tr({
                zh: "1–40 个字符，只允许字母、数字、空格、点、连字符、中点或下划线。",
                en: "1–40 characters: letters, numbers, spaces, dots, hyphens, middle dots, or underscores.",
              })}
              maxLength={40}
            />
            <fieldset>
              <legend className="text-sm font-medium">{tr({ zh: "称呼方式", en: "Forms of address" })}</legend>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {tr({ zh: "至少选择一项。", en: "Choose at least one." })}
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {ADDRESS_TERMS.map((term) => (
                  <CheckField
                    key={term}
                    checked={settings.address_terms.includes(term)}
                    onChange={() => toggleAddress(term)}
                    label={optionLabel(term, zh)}
                  />
                ))}
              </div>
              {addressError ? <p className="mt-2 text-xs text-[var(--destructive)]">{addressError}</p> : null}
            </fieldset>
            <SelectField
              id="persona-avatar"
              label={tr({ zh: "头像类型", en: "Avatar type" })}
              value={settings.avatar_ref}
              options={AVATARS}
              zh={zh}
              onChange={(value) => update("avatar_ref", value)}
            />
          </SettingsSection>

          <SettingsSection
            eyebrow="EXPRESSION"
            title={tr({ zh: "表达风格", en: "Expression style" })}
            description={tr({
              zh: "这些选项只控制措辞和呈现，不参与答案与判分。",
              en: "These options control wording and presentation, not answers or grading.",
            })}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField id="persona-tone" label={tr({ zh: "语气", en: "Tone" })} value={settings.tone} options={TONES} zh={zh} onChange={(value) => update("tone", value)} />
              <SelectField id="persona-feedback" label={tr({ zh: "反馈结构", en: "Feedback structure" })} value={settings.feedback_format} options={FEEDBACK_FORMATS} zh={zh} onChange={(value) => update("feedback_format", value)} />
              <SelectField id="persona-directness" label={tr({ zh: "直接程度", en: "Directness" })} value={settings.directness} options={INTENSITIES} zh={zh} onChange={(value) => update("directness", value)} />
              <SelectField id="persona-encouragement" label={tr({ zh: "鼓励程度", en: "Encouragement" })} value={settings.encouragement_level} options={INTENSITIES} zh={zh} onChange={(value) => update("encouragement_level", value)} />
              <SelectField id="persona-humor" label={tr({ zh: "幽默程度", en: "Humor" })} value={settings.humor_level} options={INTENSITIES} zh={zh} onChange={(value) => update("humor_level", value)} />
              <SelectField id="persona-proactivity" label={tr({ zh: "主动提醒", en: "Proactivity" })} value={settings.proactivity} options={PROACTIVITY} zh={zh} onChange={(value) => update("proactivity", value)} />
              <SelectField id="persona-emoji" label={tr({ zh: "表情符号", en: "Emoji use" })} value={settings.emoji_policy} options={EMOJI_POLICIES} zh={zh} onChange={(value) => update("emoji_policy", value)} />
            </div>
            <CheckField
              checked={settings.reminder_consent}
              onChange={(reminder_consent) => update("reminder_consent", reminder_consent)}
              label={tr({ zh: "允许主动学习提醒", en: "Allow proactive learning reminders" })}
              description={tr({
                zh: "这是单独授权；关闭主动提醒、未授权或处于安静时段时均不会发送提醒。",
                en: "This is separate consent. No reminder is eligible while proactivity is off, consent is absent, or quiet hours apply.",
              })}
            />
          </SettingsSection>

          <SettingsSection
            eyebrow="VOICE & AVAILABILITY"
            title={tr({ zh: "语音与安静时段", en: "Voice and quiet hours" })}
            description={tr({
              zh: "语音设置仅影响呈现；安静时段用于限制主动提醒。",
              en: "Voice settings affect presentation only; quiet hours constrain proactive reminders.",
            })}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField id="persona-voice" label={tr({ zh: "语音类型", en: "Voice" })} value={settings.voice_id} options={VOICES} zh={zh} onChange={(value) => update("voice_id", value)} />
              <label htmlFor="persona-speech-rate" className="block text-sm font-medium">
                {tr({ zh: "语速", en: "Speech rate" })}
                <span className="ml-2 text-[var(--primary)]">{settings.speech_rate.toFixed(2)}×</span>
                <input
                  id="persona-speech-rate"
                  type="range"
                  min="0.75"
                  max="1.5"
                  step="0.05"
                  value={settings.speech_rate}
                  onChange={(event) => update("speech_rate", Number(event.target.value))}
                  className="mt-3 block w-full accent-[var(--primary)]"
                />
              </label>
            </div>
            <CheckField
              checked={settings.quiet_hours.enabled}
              onChange={(enabled) => update("quiet_hours", { ...settings.quiet_hours, enabled })}
              label={tr({ zh: "启用安静时段", en: "Enable quiet hours" })}
              description={tr({
                zh: "开启后，主动提醒会避开下列本地时间。",
                en: "When enabled, proactive reminders avoid the local window below.",
              })}
            />
            <div className="grid gap-4 sm:grid-cols-3">
              <TimeField id="quiet-start" label={tr({ zh: "开始", en: "Starts" })} value={settings.quiet_hours.start_local} disabled={!settings.quiet_hours.enabled} onChange={(value) => update("quiet_hours", { ...settings.quiet_hours, start_local: value })} />
              <TimeField id="quiet-end" label={tr({ zh: "结束", en: "Ends" })} value={settings.quiet_hours.end_local} disabled={!settings.quiet_hours.enabled} onChange={(value) => update("quiet_hours", { ...settings.quiet_hours, end_local: value })} />
              <SelectField id="quiet-timezone" label={tr({ zh: "时区", en: "Time zone" })} value={settings.quiet_hours.timezone} options={timezoneOptions} zh={false} disabled={!settings.quiet_hours.enabled} onChange={(value) => update("quiet_hours", { ...settings.quiet_hours, timezone: value })} />
            </div>
          </SettingsSection>

          <SettingsSection
            eyebrow="ACCESSIBILITY"
            title={tr({ zh: "无障碍呈现", en: "Accessible presentation" })}
            description={tr({
              zh: "这些偏好不会降低内容、安全或判分标准。",
              en: "These preferences never lower content, safety, or grading standards.",
            })}
          >
            <div className="grid gap-2 sm:grid-cols-2">
              <CheckField checked={settings.accessibility.captions} onChange={(captions) => update("accessibility", { ...settings.accessibility, captions })} label={tr({ zh: "显示字幕", en: "Show captions" })} />
              <CheckField checked={settings.accessibility.reduced_motion} onChange={(reduced_motion) => update("accessibility", { ...settings.accessibility, reduced_motion })} label={tr({ zh: "减少动态效果", en: "Reduce motion" })} />
              <CheckField checked={settings.accessibility.screen_reader_optimized} onChange={(screen_reader_optimized) => update("accessibility", { ...settings.accessibility, screen_reader_optimized })} label={tr({ zh: "优化屏幕阅读器结构", en: "Optimize screen-reader structure" })} />
            </div>
            <SelectField id="persona-text-scale" label={tr({ zh: "文字大小", en: "Text size" })} value={settings.accessibility.text_scale} options={TEXT_SCALES} zh={zh} onChange={(text_scale) => update("accessibility", { ...settings.accessibility, text_scale })} />
          </SettingsSection>

          {validationError ? (
            <p role="alert" className="rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]">
              {validationError}
            </p>
          ) : null}

          <div className="sticky bottom-3 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--background)]/95 p-3 shadow-lg backdrop-blur">
            <button type="submit" disabled={busy || !dirty || Boolean(validationError)} className={primaryButtonClass}>
              <Save className="h-4 w-4" aria-hidden="true" />
              {busy ? tr({ zh: "正在保存", en: "Saving" }) : tr({ zh: "保存 Tutor Persona", en: "Save Tutor Persona" })}
            </button>
            <button type="button" disabled={busy} onClick={() => void reset()} className={secondaryButtonClass}>
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {tr({ zh: "恢复默认设置", en: "Restore defaults" })}
            </button>
            {dirty ? (
              <span className="ml-auto text-xs text-[var(--muted-foreground)]">
                {tr({ zh: "有未保存更改", en: "Unsaved changes" })}
              </span>
            ) : null}
          </div>
        </form>

        <div className="lg:sticky lg:top-5">
          <TutorPersonaPreview preview={preview} loading={previewing} error={previewError} />
        </div>
      </div>
    </div>
  );
}

function TutorReminderInbox({ tr, zh }: { tr: Tr; zh: boolean }) {
  const [items, setItems] = useState<TutorReminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const loadReminders = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const reminders = await listTutorReminders("delivered", signal);
      if (!signal?.aborted) setItems(reminders);
    } catch (cause) {
      if (!signal?.aborted && !isAbort(cause)) setError(messageFor(cause, tr));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [tr]);

  useEffect(() => {
    const controller = new AbortController();
    void loadReminders(controller.signal);
    return () => controller.abort();
  }, [loadReminders]);

  async function mutateReminder(reminderId: string, action: "read" | "cancel") {
    setBusyId(reminderId);
    setError("");
    try {
      if (action === "read") await acknowledgeTutorReminder(reminderId);
      else await cancelTutorReminder(reminderId);
      setItems((current) => current.filter((item) => item.reminder_id !== reminderId));
    } catch (cause) {
      setError(messageFor(cause, tr));
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5" aria-labelledby="tutor-reminder-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-[11px] font-medium tracking-[0.16em] text-[var(--primary)]">
            <Bell className="h-4 w-4" aria-hidden="true" />
            {tr({ zh: "提醒", en: "REMINDERS" })}
          </p>
          <h2 id="tutor-reminder-heading" className="mt-1 font-semibold">
            {tr({ zh: "到期复习提醒", en: "Due review reminders" })}
          </h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {tr({ zh: "这里只显示你已授权且不在安静时段内投递的复习提醒。", en: "Only consented review reminders delivered outside quiet hours appear here." })}
          </p>
        </div>
        <button type="button" disabled={loading} onClick={() => void loadReminders()} className={secondaryButtonClass}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          {tr({ zh: "刷新", en: "Refresh" })}
        </button>
      </div>

      {error ? <p role="alert" className="mt-4 rounded-md border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]">{error}</p> : null}
      {loading ? <p role="status" className="mt-4 text-sm text-[var(--muted-foreground)]">{tr({ zh: "正在读取提醒…", en: "Loading reminders…" })}</p> : null}
      {!loading && !items.length ? <p className="mt-4 rounded-lg bg-[var(--muted)]/35 p-3 text-sm text-[var(--muted-foreground)]">{tr({ zh: "当前没有待处理的复习提醒。", en: "There are no pending review reminders." })}</p> : null}
      {items.length ? (
        <ul className="mt-4 space-y-2">
          {items.map((item) => {
            const disabled = busyId === item.reminder_id;
            const href = `/settings/learning-model/${encodeURIComponent(item.subject_id)}?tab=reviews`;
            return (
              <li key={item.reminder_id} className="flex flex-col gap-3 rounded-lg border border-[var(--border)] p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{tr({ zh: `知识点 ${item.kc_id} 已到复习时间`, en: `Knowledge component ${item.kc_id} is due for review` })}</p>
                  <p className="mt-1 text-xs text-[var(--muted-foreground)]">{new Intl.DateTimeFormat(zh ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.due_at))}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link href={href} className={primaryButtonClass}>{tr({ zh: "开始复习", en: "Review now" })}</Link>
                  <button type="button" disabled={disabled} onClick={() => void mutateReminder(item.reminder_id, "read")} className={secondaryButtonClass}>
                    <Check className="h-4 w-4" aria-hidden="true" />{tr({ zh: "标为已读", en: "Mark read" })}
                  </button>
                  <button type="button" disabled={disabled} onClick={() => void mutateReminder(item.reminder_id, "cancel")} className={secondaryButtonClass} aria-label={tr({ zh: "删除提醒", en: "Delete reminder" })}>
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

function SettingsSection({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5">
      <p className="text-[11px] font-medium tracking-[0.16em] text-[var(--primary)]">{eyebrow}</p>
      <h2 className="mt-1 font-semibold">{title}</h2>
      <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">{description}</p>
      <div className="mt-5 space-y-4">{children}</div>
    </section>
  );
}

function TextField({
  id,
  label,
  value,
  description,
  maxLength,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  description: string;
  maxLength: number;
  onChange: (value: string) => void;
}) {
  const descriptionId = `${id}-description`;
  return (
    <label htmlFor={id} className="block text-sm font-medium">
      {label}
      <input id={id} required maxLength={maxLength} value={value} aria-describedby={descriptionId} onChange={(event) => onChange(event.target.value)} className={`${inputClass} mt-2`} />
      <span id={descriptionId} className="mt-1 block text-xs font-normal leading-relaxed text-[var(--muted-foreground)]">{description}</span>
    </label>
  );
}

function SelectField<T extends string>({
  id,
  label,
  value,
  options,
  zh,
  disabled = false,
  onChange,
}: {
  id: string;
  label: string;
  value: T;
  options: readonly T[];
  zh: boolean;
  disabled?: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <label htmlFor={id} className="block text-sm font-medium">
      {label}
      <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value as T)} className={`${inputClass} mt-2 disabled:cursor-not-allowed disabled:opacity-55`}>
        {options.map((option) => <option key={option} value={option}>{optionLabel(option, zh)}</option>)}
      </select>
    </label>
  );
}

function TimeField({ id, label, value, disabled, onChange }: { id: string; label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <label htmlFor={id} className="block text-sm font-medium">{label}<input id={id} type="time" required value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} className={`${inputClass} mt-2 disabled:cursor-not-allowed disabled:opacity-55`} /></label>;
}

function CheckField({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description?: string }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-[var(--border)] px-3 py-2.5 transition-colors hover:border-[var(--primary)]/45 focus-within:ring-2 focus-within:ring-[var(--primary)]">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-0.5 h-5 w-5 shrink-0 accent-[var(--primary)]" />
      <span className="text-sm font-medium">{label}{description ? <span className="mt-0.5 block text-xs font-normal leading-relaxed text-[var(--muted-foreground)]">{description}</span> : null}</span>
    </label>
  );
}

function EditorLoading({ tr }: { tr: Tr }) {
  return (
    <div aria-busy="true" aria-label={tr({ zh: "正在加载 Tutor Persona", en: "Loading Tutor Persona" })} className="space-y-5">
      <div className="h-28 animate-pulse rounded-xl bg-[var(--muted)]/55" />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(20rem,0.8fr)]">
        <div className="space-y-5">{[0, 1, 2].map((item) => <div key={item} className="h-64 animate-pulse rounded-xl bg-[var(--muted)]/45" />)}</div>
        <div className="h-96 animate-pulse rounded-xl bg-[var(--muted)]/45" />
      </div>
    </div>
  );
}

function validate(settings: TutorPersonaSettings | null, tr: Tr): string {
  if (!settings) return "";
  const name = settings.name.trim();
  if (!name || name.length > 40 || !NAME_PATTERN.test(name)) {
    return tr({
      zh: "显示名称格式无效：请使用 1–40 个字母、数字、空格、点、连字符、中点或下划线。",
      en: "Display name is invalid. Use 1–40 letters, numbers, spaces, dots, hyphens, middle dots, or underscores.",
    });
  }
  if (settings.address_terms.length === 0) {
    return tr({ zh: "至少选择一种称呼方式。", en: "Choose at least one form of address." });
  }
  return "";
}

function timezoneChoices(current: string): string[] {
  let browserTimezone = "UTC";
  try {
    browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    // UTC remains the deterministic fallback in runtimes without time-zone data.
  }
  return Array.from(new Set([current, browserTimezone, ...COMMON_TIMEZONES]));
}

function messageFor(cause: unknown, tr: Tr): string {
  if (cause instanceof TutorPersonaApiError) {
    if (cause.status === 422) {
      return tr({ zh: "服务拒绝了无效配置，请检查各项设置。", en: "The service rejected invalid settings. Review the fields and try again." });
    }
    return cause.message;
  }
  return cause instanceof Error
    ? cause.message
    : tr({ zh: "操作未完成，请稍后重试。", en: "The action could not be completed. Try again shortly." });
}

function isAbort(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}

function optionLabel(value: string, zh: boolean): string {
  const labels: Record<string, Copy> = {
    default: { zh: "默认", en: "Default" }, mentor: { zh: "导师", en: "Mentor" }, guide: { zh: "向导", en: "Guide" }, study_buddy: { zh: "学习伙伴", en: "Study buddy" },
    name: { zh: "使用名称", en: "Use name" }, you: { zh: "你", en: "You" }, learner: { zh: "学习者", en: "Learner" }, classmate: { zh: "同学", en: "Classmate" },
    warm: { zh: "温暖", en: "Warm" }, neutral: { zh: "中性", en: "Neutral" }, energetic: { zh: "有活力", en: "Energetic" }, calm: { zh: "平静", en: "Calm" },
    low: { zh: "低", en: "Low" }, medium: { zh: "中", en: "Medium" }, high: { zh: "高", en: "High" },
    concise: { zh: "精简", en: "Concise" }, balanced: { zh: "平衡", en: "Balanced" }, detailed: { zh: "详细", en: "Detailed" }, socratic: { zh: "苏格拉底式", en: "Socratic" },
    off: { zh: "关闭", en: "Off" }, reminders_only: { zh: "仅提醒", en: "Reminders only" }, moderate: { zh: "适度", en: "Moderate" },
    none: { zh: "不用", en: "None" }, minimal: { zh: "少量", en: "Minimal" }, bright: { zh: "明快", en: "Bright" }, steady: { zh: "稳定", en: "Steady" },
    standard: { zh: "标准", en: "Standard" }, large: { zh: "大", en: "Large" }, extra_large: { zh: "特大", en: "Extra large" },
  };
  return labels[value]?.[zh ? "zh" : "en"] ?? value;
}

const inputClass = "h-10 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]";
const primaryButtonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButtonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-50";
