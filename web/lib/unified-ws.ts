/**
 * Unified WebSocket Client
 *
 * Connects to the single `/api/v1/ws` endpoint and provides
 * a typed streaming interface for the new ChatOrchestrator protocol.
 *
 * The client sends only canonical `start_turn` and
 * `submit_user_reply.answers[]` messages.
 */

import { wsUrl } from "./api";

// ---- StreamEvent types (mirror Python StreamEventType) ----

export type StreamEventType =
  | "stage_start"
  | "stage_end"
  | "thinking"
  | "observation"
  | "content"
  | "tool_call"
  | "tool_result"
  | "progress"
  | "sources"
  | "result"
  | "error"
  | "session"
  | "session_meta"
  | "done";

interface WireStreamEvent {
  event_id: string;
  request_id: string;
  type: StreamEventType;
  source: string;
  data: { content: string; stage: string; metadata: Record<string, unknown> };
  session_id?: string;
  turn_id?: string;
  seq?: number;
  timestamp: number;
}

export interface StreamEvent {
  event_id?: string;
  request_id?: string;
  type: StreamEventType;
  source: string;
  content: string;
  stage: string;
  metadata: Record<string, unknown>;
  session_id?: string;
  turn_id?: string;
  seq?: number;
  timestamp: number;
}

export interface LLMSelection {
  profile_id: string;
  model_id: string;
}

// ---- Client message ----

export interface StartTurnMessage {
  type: "start_turn";
  content: string;
  tools?: string[];
  capability?: string | null;
  knowledge_bases?: string[];
  session_id?: string | null;
  attachments?: {
    type: string;
    url?: string;
    base64?: string;
    filename?: string;
    mime_type?: string;
  }[];
  language?: string;
  config?: Record<string, unknown>;
  notebook_references?: {
    notebook_id: string;
    record_ids: string[];
  }[];
  history_references?: string[];
  question_notebook_references?: number[];
  book_references?: {
    book_id: string;
    page_ids: string[];
  }[];
  learning_artifact_references?: {
    pack_id: string;
    artifact_type: "courseware" | "flashcards" | "quiz";
    artifact_index?: number;
  }[];
  persona?: string;
  llm_selection?: LLMSelection | null;
  /** Edit-branching: when present (even as ``null``) the new user message
   *  attaches at this exact parent — creating a sibling rather than
   *  appending to the session tail. */
  parent_message_id?: number | null;
}

/**
 * Deliver the user's answer for an ``ask_user`` paused turn so the
 * agentic loop can resume on the same turn. The user's reply is
 * substituted into the matching ``role=tool`` message body before the
 * next LLM iteration runs.
 *
 * Replies always use the canonical per-question answer shape.
 */
export interface SubmitUserReplyMessage {
  type: "submit_user_reply";
  turn_id: string;
  answers: Array<{ questionId: string; text: string }>;
}

export type ChatMessage = StartTurnMessage | SubmitUserReplyMessage;

// ---- Connection manager ----

export type EventHandler = (event: StreamEvent) => void;

const MAX_RECONNECT_ATTEMPTS = 5;
const BASE_RECONNECT_DELAY_MS = 200;

export class UnifiedWSClient {
  private ws: WebSocket | null = null;
  private onEvent: EventHandler;
  private onClose?: () => void;

  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;

  constructor(onEvent: EventHandler, onClose?: () => void) {
    this.onEvent = onEvent;
    this.onClose = onClose;
  }

  connect(): void {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    this.intentionalClose = false;

    const url = wsUrl("/api/v1/ws");
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectAttempt = 0;
    };

    this.ws.onmessage = (ev) => {
      try {
        const wire = JSON.parse(ev.data) as WireStreamEvent;
        if (!wire.data || typeof wire.data !== "object") throw new Error("Missing event data");
        const { data, ...envelope } = wire;
        this.onEvent({ ...envelope, ...data });
      } catch {
        console.warn("Unparseable WS message:", ev.data);
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      if (!this.intentionalClose) {
        this.attemptReconnect();
      }
    };

    this.ws.onerror = (err) => {
      console.error("WS error:", err);
    };
  }

  send(msg: ChatMessage): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("WebSocket not connected");
      return;
    }
    this.ws.send(JSON.stringify(msg));
  }

  disconnect(): void {
    this.intentionalClose = true;
    this.clearReconnectTimer();
    this.ws?.close();
    this.ws = null;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  // ---- Reconnect ----

  private attemptReconnect(): void {
    if (this.reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
      this.onClose?.();
      return;
    }

    const delay = BASE_RECONNECT_DELAY_MS * Math.pow(2, this.reconnectAttempt);
    this.reconnectAttempt += 1;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

}
