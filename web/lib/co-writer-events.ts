// Lightweight client-side event bus for Co-Writer document mutations.
//
// `CoWriterRecent` (sidebar) and the home page list need to refresh whenever a
// document is created, updated (autosave), or deleted from anywhere in the
// app. We avoid a full Context provider here because the sidebar lives at the
// workspace shell level and mutations happen in disparate routes.

const EVENT_NAME = "co-writer:changed";

type Listener = () => void;

function getTarget(): EventTarget | null {
  if (typeof window === "undefined") return null;
  return window;
}

export function notifyCoWriterChanged(): void {
  const target = getTarget();
  if (!target) return;
  target.dispatchEvent(new Event(EVENT_NAME));
}

export function subscribeCoWriterChanges(listener: Listener): () => void {
  const target = getTarget();
  if (!target) return () => {};
  target.addEventListener(EVENT_NAME, listener);
  return () => target.removeEventListener(EVENT_NAME, listener);
}
