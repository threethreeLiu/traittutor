export interface KeyboardSubmitEventLike {
  key: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  which?: number;
  nativeEvent?: {
    isComposing?: boolean;
    keyCode?: number;
    which?: number;
  };
}

const IME_PROCESS_KEY_CODE = 229;

export function isImeComposing(
  event: KeyboardSubmitEventLike,
  compositionActive = false,
): boolean {
  const nativeEvent = event.nativeEvent;
  return Boolean(
    compositionActive ||
    event.isComposing ||
    nativeEvent?.isComposing ||
    event.keyCode === IME_PROCESS_KEY_CODE ||
    event.which === IME_PROCESS_KEY_CODE ||
    nativeEvent?.keyCode === IME_PROCESS_KEY_CODE ||
    nativeEvent?.which === IME_PROCESS_KEY_CODE,
  );
}

export function shouldSubmitOnEnter(
  event: KeyboardSubmitEventLike,
  compositionActive = false,
): boolean {
  return (
    event.key === "Enter" &&
    !event.shiftKey &&
    !isImeComposing(event, compositionActive)
  );
}
