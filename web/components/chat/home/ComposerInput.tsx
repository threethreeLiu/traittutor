"use client";

import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";
import { UserRound } from "lucide-react";
import ChatReferenceMenu, {
  type ChatReferenceSelectionCounts,
} from "@/components/chat/ChatReferenceMenu";
import { shouldSubmitOnEnter } from "@/lib/composer-keyboard";
import { useAutoSizedTextarea } from "@/lib/use-auto-sized-textarea";
import { useImeComposing } from "@/lib/use-ime-composing";

interface ComposerInputProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  isVisualizeMode: boolean;
  // When true, parent has attachments/references queued and will accept a
  // send even if the text body is empty. Without this, Enter would silently
  // do nothing for an attachment-only message.
  canSendEmpty: boolean;
  onSend: (content: string) => boolean | void | Promise<boolean | void>;
  onInputChange: (content: string) => void;
  onPaste: (e: React.ClipboardEvent) => void;
  selectedCounts: ChatReferenceSelectionCounts;
  onSelectAttach: () => void;
  onSelectQuestionBankPicker: () => void;
  onSelectMemoryPicker: () => void;
  onSelectLearningArtifactPicker?: () => void;
  /**
   * Wires the `/persona` slash command. Typing "/" (then any prefix of
   * "persona") at the start of an empty composer pops a command hint;
   * selecting it clears the input and invokes this callback to open the
   * session persona selector. Omitted on surfaces without session
   * personas (e.g. the quiz follow-up), which disables the slash popup.
   */
  onOpenPersonaSelector?: () => void;
  /**
   * Override the default placeholder. When unset, falls back to the
   * main chat ("How can I help you today?") / visualize defaults.
   */
  placeholder?: string;
  /**
   * Minimum textarea height in pixels. The auto-sized hook grows the
   * textarea past this as the user types. Bumped on the empty-state
   * composer so the resting box looks inviting rather than crammed.
   */
  minHeight?: number;
}

export interface ComposerInputHandle {
  clear: () => void;
  getValue: () => string;
  /**
   * Programmatically replace the textarea contents (used by the
   * ``AskUserOptions`` chip click handler — picks an option, prefills
   * the composer, leaves it to the user to edit/send rather than
   * auto-firing the message).
   */
  setValue: (value: string) => void;
}

export function shouldOpenAtPopup(value: string, cursorPos: number): boolean {
  const prefix = value.slice(0, cursorPos);
  return /(^|\s)@[^\s]*$/.test(prefix);
}

export function stripTrailingAtMention(value: string): string {
  return value.replace(/(^|\s)@[^\s]*$/, "$1").replace(/\s+$/, "");
}

/**
 * `/persona` slash-command detection (Codex-style: command position is the
 * very start of the input, not mid-text like @ mentions). Active while the
 * text before the cursor is "/" plus any prefix of "persona" — `/x` or a
 * trailing space closes the popup.
 */
export function shouldOpenSlashPopup(
  value: string,
  cursorPos: number,
): boolean {
  const prefix = value.slice(0, cursorPos);
  const match = /^\/([a-z]*)$/i.exec(prefix);
  if (!match) return false;
  return "persona".startsWith(match[1].toLowerCase());
}

export const ComposerInput = memo(
  forwardRef<ComposerInputHandle, ComposerInputProps>(function ComposerInput(
    {
      textareaRef,
      isVisualizeMode,
      canSendEmpty,
      onSend,
      onInputChange,
      onPaste,
      selectedCounts,
    onSelectAttach,
    onSelectQuestionBankPicker,
    onSelectMemoryPicker,
    onSelectLearningArtifactPicker,
      onOpenPersonaSelector,
      placeholder,
      minHeight = 28,
    },
    ref,
  ) {
    const { t } = useTranslation();
    const [input, setInput] = useState("");
    const [showAtPopup, setShowAtPopup] = useState(false);
    const [showSlashPopup, setShowSlashPopup] = useState(false);
    const slashEnabled = Boolean(onOpenPersonaSelector);

    // Latest text mirrored into a ref by the change handlers (never updated
    // during render). The @references handlers and the imperative handle read
    // from this ref so their identities stay stable across keystrokes,
    // letting `memo` on ChatReferenceMenu actually skip re-renders when
    // `showAtPopup` doesn't change.
    const inputRef = useRef("");
    const { isComposingRef, onCompositionStart, onCompositionEnd } =
      useImeComposing();
    // Helper that always updates state and ref together so they can't drift.
    const setInputBoth = useCallback((value: string) => {
      inputRef.current = value;
      setInput(value);
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        clear: () => {
          setInputBoth("");
          onInputChange("");
        },
        getValue: () => inputRef.current,
        setValue: (value: string) => {
          const text = value ?? "";
          setInputBoth(text);
          onInputChange(text);
          // Focus + move caret to the end so the user can immediately
          // edit or press Enter to send.
          const el = textareaRef.current;
          if (el) {
            requestAnimationFrame(() => {
              el.focus();
              el.setSelectionRange(text.length, text.length);
            });
          }
        },
      }),
      [setInputBoth, onInputChange, textareaRef],
    );

    useAutoSizedTextarea(textareaRef, input, { min: minHeight, max: 200 });

    const handleInputChange = useCallback(
      (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const value = e.target.value;
        const cursorPos = e.target.selectionStart ?? value.length;
        setInputBoth(value);
        onInputChange(value);
        const atOpen = shouldOpenAtPopup(value, cursorPos);
        setShowAtPopup(atOpen);
        setShowSlashPopup(
          slashEnabled && shouldOpenSlashPopup(value, cursorPos),
        );
      },
      [setInputBoth, onInputChange, slashEnabled],
    );

    const handleTextareaClick = useCallback(
      (e: React.MouseEvent<HTMLTextAreaElement>) => {
        const target = e.currentTarget;
        const cursorPos = target.selectionStart ?? target.value.length;
        const atOpen = shouldOpenAtPopup(target.value, cursorPos);
        setShowAtPopup(atOpen);
        setShowSlashPopup(
          slashEnabled && shouldOpenSlashPopup(target.value, cursorPos),
        );
      },
      [slashEnabled],
    );

    const handleSelectSlashPersona = useCallback(() => {
      // The slash text is a command, not message content — clear it.
      setInputBoth("");
      onInputChange("");
      setShowSlashPopup(false);
      onOpenPersonaSelector?.();
    }, [setInputBoth, onInputChange, onOpenPersonaSelector]);

    const doSend = useCallback(async () => {
      const content = inputRef.current.trim();
      // Allow sending when text is empty but the parent has attachments or
      // references queued (canSendEmpty). This matches the send-button's
      // own enablement logic in ChatComposer (`canSend`).
      if (!content && !canSendEmpty) return;
      const submitted = await onSend(content);
      // Leave a failed/blocked route in place so the learner can revise it.
      // Successful route receipts are submissions even when no downstream
      // owner has started expensive work yet.
      if (submitted !== false) {
        setInputBoth("");
        onInputChange("");
        setShowAtPopup(false);
        setShowSlashPopup(false);
      }
    }, [canSendEmpty, onSend, setInputBoth, onInputChange]);

    const clearTrailingMention = useCallback(() => {
      const next = stripTrailingAtMention(inputRef.current);
      setInputBoth(next);
      onInputChange(next);
    }, [setInputBoth, onInputChange]);

    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // With the slash popup open, Enter/Tab confirm the command instead
        // of submitting "/persona" as a message.
        if (
          showSlashPopup &&
          !isComposingRef.current &&
          (e.key === "Enter" || e.key === "Tab")
        ) {
          e.preventDefault();
          handleSelectSlashPersona();
          return;
        }
        if (shouldSubmitOnEnter(e, isComposingRef.current)) {
          e.preventDefault();
          doSend();
        } else if (e.key === "Escape") {
          setShowAtPopup(false);
          setShowSlashPopup(false);
        }
      },
      [
        doSend,
        showSlashPopup,
        handleSelectSlashPersona,
        isComposingRef,
      ],
    );

    const handleSelectReferenceItem = useCallback(
      (
        key:
          | "attach"
          | "learning_artifacts"
          | "question_bank"
          | "memory",
      ) => {
        clearTrailingMention();
        setShowAtPopup(false);
        if (key === "attach") onSelectAttach();
        else if (key === "learning_artifacts") onSelectLearningArtifactPicker?.();
        else if (key === "question_bank") onSelectQuestionBankPicker();
        else if (key === "memory") onSelectMemoryPicker();
      },
      [
        clearTrailingMention,
        onSelectAttach,
        onSelectLearningArtifactPicker,
        onSelectQuestionBankPicker,
        onSelectMemoryPicker,
      ],
    );

    // Close the @/slash popups on outside click. Without this, clicking
    // anywhere outside the popup or textarea left the menu hovering
    // indefinitely. We bind on mousedown so the close fires before a
    // synthetic click on a sibling button (e.g. the Tools menu) can
    // re-open something else.
    const popupRef = useRef<HTMLDivElement>(null);
    const slashPopupRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
      if (!showAtPopup && !showSlashPopup) return;
      const handler = (e: MouseEvent) => {
        const target = e.target as Node | null;
        if (!target) return;
        if (popupRef.current?.contains(target)) return;
        if (slashPopupRef.current?.contains(target)) return;
        if (textareaRef.current?.contains(target)) return;
        setShowAtPopup(false);
        setShowSlashPopup(false);
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, [showAtPopup, showSlashPopup, textareaRef]);

    return (
      <div className="px-4 pt-3.5 pb-2">
        {showAtPopup && (
          <div
            ref={popupRef}
            className="absolute bottom-full left-0 z-[70] mb-2"
          >
            <ChatReferenceMenu
              variant="mention"
              selectedCounts={selectedCounts}
              onSelectItem={handleSelectReferenceItem}
              showLearningArtifacts={Boolean(onSelectLearningArtifactPicker)}
            />
          </div>
        )}
        {showSlashPopup && (
          <div
            ref={slashPopupRef}
            className="absolute bottom-full left-0 z-[70] mb-2"
          >
            <div
              role="listbox"
              aria-label={t("Commands")}
              className="w-[300px] rounded-xl border border-[var(--border)] bg-[var(--popover)] py-1.5 shadow-lg backdrop-blur-md"
            >
              <button
                type="button"
                role="option"
                aria-selected
                onClick={handleSelectSlashPersona}
                className="flex w-full items-center gap-2.5 bg-[var(--muted)]/60 px-3 py-2 text-left text-[12.5px] transition-colors"
              >
                <UserRound
                  size={14}
                  strokeWidth={1.7}
                  className="shrink-0 text-[var(--muted-foreground)]"
                />
                {/* Command syntax token — must not be localized. */}
                {/* eslint-disable-next-line i18n/no-literal-ui-text */}
                <span className="font-medium text-[var(--foreground)]">
                  /persona
                </span>
                <span className="min-w-0 truncate text-[var(--muted-foreground)]">
                  {t("Switch the persona for this chat session")}
                </span>
              </button>
            </div>
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          onClick={handleTextareaClick}
          onPaste={onPaste}
          rows={1}
          // Cap input at 32k chars. A bigger paste (e.g. an entire textbook
          // dumped via Cmd+V) would force a layout reflow on every keystroke
          // and lock the page; the cap is a defensive guard, not a real
          // product limit. Users hit by this cap should be using the
          // attachment path, not the composer body.
          maxLength={32000}
          suppressHydrationWarning
          placeholder={
            placeholder ??
            (isVisualizeMode
              ? t(
                  "Describe the chart, diagram, or animation you want to visualize...",
                )
              : t("How can I help you today?"))
          }
          className="w-full resize-none overflow-hidden bg-transparent text-[16px] leading-relaxed text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
          style={{ transition: "height 0.15s ease-out" }}
        />
      </div>
    );
  }),
);
