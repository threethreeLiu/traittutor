import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) => readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("home explains the product and starts goal-driven learning by default", () => {
  const page = read("app/(workspace)/home/[[...sessionId]]/page.tsx");
  const intro = read("components/chat/home/LearningHomeIntro.tsx");
  const canvas = read("components/learning/LearningCanvas.tsx");
  const assist = read("components/chat/home/AssistHomeIntro.tsx");
  const assistPage = read("app/(workspace)/assist/[[...sessionId]]/page.tsx");

  assert.match(page, /useState<"learn" \| "assist">\(isAssistPage \? "assist" : "learn"\)/);
  assert.match(page, /<LearningHomeIntro/);
  assert.match(page, /\(hasMessages \|\| sessionLoading\) \? <div/);
  assert.match(page, /flex min-h-full w-full items-center justify-center/);
  assert.match(page, /traittutor-scroll-area/);
  assert.match(page, /\(hasMessages \|\| state\.isStreaming\) \? <ChatComposer/);
  assert.doesNotMatch(page, /hasMessages \|\| attachments\.length > 0 \|\| state\.isStreaming/);
  assert.doesNotMatch(page, /grow-\[0\.65\]/);
  assert.match(intro, /自适应学习路径/);
  assert.match(intro, /今天，想真正学会什么/);
  assert.match(intro, /根据作答改变下一步/);
  assert.match(intro, /7 天入门个人理财/);
  assert.match(intro, /输入一个学习目标，或贴一道不会的题/);
  assert.match(intro, /ATTACHMENT_ACCEPT/);
  assert.match(intro, /<HomeAttachmentTray/);
  assert.match(intro, /if \(!prompt && !attachments\.length\) return/);
  assert.match(intro, /disabled=\{starting \|\| \(!draft\.trim\(\) && !attachments\.length\)\}/);
  assert.match(intro, /正在分析材料并建立学习组件路径/);
  assert.match(intro, /onPaste=\{addClipboardFiles\}/);
  assert.match(intro, /onDrop=\{addDroppedFiles\}/);
  assert.match(intro, /getLearnerOverview/);
  assert.match(intro, /hasLearningEvidence/);
  assert.match(intro, /理解目标/);
  assert.match(intro, /安排组件/);
  assert.match(intro, /根据证据调整/);
  assert.match(page, /isLearningGoalMessage\(content\)/);
  assert.match(page, /await handleStartLearning\(content\)/);
  assert.match(page, /onStart=\{\(goal\) => void handleStartLearning\(goal\)\}/);
  assert.match(page, /createLearningPack\(/);
  assert.match(page, /createLearningComponentPlan\(/);
  assert.match(page, /router\.push\(target\.plan\.start_url \?\? `\/space\/learning\/\$\{target\.packId\}`\)/);
  assert.match(page, /prepareTraitTutorMaterial\(first\)/);
  assert.match(page, /analyzeTraitTutorMaterial/);
  assert.match(page, /source_kind: "learning_goal"/);
  assert.match(page, /isAssistPage/);
  assert.doesNotMatch(page, /TraitTutor mode/);
  assert.match(page, /if \(isAssistPage\) return/);
  assert.match(assist, /任务助手/);
  assert.match(assist, /调研一个主题/);
  assert.match(assist, /<HomeAttachmentTray/);
  assert.match(assist, /请读取已上传文件并根据内容协助我完成任务/);
  assert.match(assistPage, /home\/\[\[\.\.\.sessionId\]\]\/page/);
  assert.doesNotMatch(page, /<LearningJourneyLaunch/);
  assert.match(canvas, /学习路径/);
  assert.match(canvas, /ComponentBody/);
  assert.match(canvas, /为什么这一步/);
});

test("learning home consumes the shared theme palette instead of a teal-only palette", () => {
  const intro = read("components/chat/home/LearningHomeIntro.tsx");
  const tray = read("components/chat/home/HomeAttachmentTray.tsx");
  const globals = read("app/globals.css");

  assert.match(intro, /learning-home-card/);
  assert.match(intro, /learning-home-submit/);
  assert.doesNotMatch(intro, /teal-|#0d9488|rgba\(45,212,191/);
  assert.match(tray, /accent = "theme"/);
  assert.match(globals, /\.learning-home-card\s*\{/);
  assert.match(globals, /var\(--primary\)/);
});

test("empty-state attachments stay inside one launch surface", () => {
  const page = read("app/(workspace)/home/[[...sessionId]]/page.tsx");
  const tray = read("components/chat/home/HomeAttachmentTray.tsx");

  assert.match(page, /attachments=\{attachments\}/);
  assert.match(page, /attachmentError=\{attachmentError\}/);
  assert.match(page, /onRemoveAttachment=\{removeAttachment\}/);
  assert.match(page, /mergeUniqueAttachments/);
  assert.match(page, /reason: "unsupported" \| "too_large" \| "quota" \| "duplicate"/);
  assert.match(page, /await handleAddFiles\(files\)/);
  assert.match(page, /await handleAddFiles\(Array\.from\(event\.dataTransfer\.files\)\)/);
  assert.match(tray, /aria-live="polite"/);
  assert.match(tray, /role="alert"/);
  assert.match(tray, /onRemove\(index\)/);
});

test("chat shows explicit source receipt instead of silently consuming a PDF", () => {
  const page = read("app/(workspace)/home/[[...sessionId]]/page.tsx");
  assert.match(page, /setAttachmentReceipt/);
  assert.match(page, /材料已提交给学习助手/);
  assert.match(page, /主题、难度、核心概念和下一步建议/);
});

test("Big Five onboarding is optional and can be deferred", () => {
  const onboarding = read("components/onboarding/OnboardingProvider.tsx");
  assert.match(onboarding, /ONBOARDING_DISMISSED_KEY/);
  assert.match(onboarding, /稍后设置，先开始学习/);
  assert.match(onboarding, /onSkip/);
  assert.match(onboarding, /localStorage\.setItem/);
});
