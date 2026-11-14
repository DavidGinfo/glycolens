import type {
  ChatRequest,
  ChatResponse,
  DataProfile,
  DayReplay,
  DiscoveryReport,
  EngineHealthStatus,
  PersonaInfo,
  SessionBundleResponse,
  SwarmStreamEvent,
  WalkSimulationRequest,
  WalkSimulationResponse,
} from './types';

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errMessage = `HTTP ${res.status}: ${res.statusText}`;
    try {
      const errBody = (await res.json()) as { detail?: string; message?: string };
      if (errBody?.detail) {
        errMessage = errBody.detail;
      } else if (errBody?.message) {
        errMessage = errBody.message;
      }
    } catch {
      // ignore json parse error on failure
    }
    throw new Error(errMessage);
  }
  return (await res.json()) as T;
}

function normalizeHealth(raw: Record<string, unknown>): EngineHealthStatus {
  const configured = Boolean(
    raw.gemini_configured ?? raw.has_api_key ?? (raw.engine_mode === 'gemini_swarm')
  );
  return {
    status: String(raw.status ?? 'ok'),
    gemini_configured: configured,
    has_api_key: configured,
    model: typeof raw.model === 'string' ? raw.model : undefined,
  };
}

export async function fetchHealthStatus(): Promise<EngineHealthStatus> {
  const res = await fetch('/api/health');
  const raw = await parseJsonOrThrow<Record<string, unknown>>(res);
  return normalizeHealth(raw);
}

export async function saveGeminiApiKey(apiKey: string): Promise<EngineHealthStatus> {
  const res = await fetch('/api/config/apikey', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  });
  const raw = await parseJsonOrThrow<Record<string, unknown>>(res);
  return normalizeHealth(raw);
}

export async function fetchPersonas(): Promise<PersonaInfo[]> {
  const res = await fetch('/api/personas');
  const raw = await parseJsonOrThrow<PersonaInfo[] | { personas: PersonaInfo[] }>(res);
  if (Array.isArray(raw)) {
    return raw;
  }
  return raw.personas ?? [];
}

export async function createPersonaSession(
  personaId: string
): Promise<SessionBundleResponse> {
  const res = await fetch(`/api/sessions/persona/${encodeURIComponent(personaId)}`, {
    method: 'POST',
  });
  return parseJsonOrThrow<SessionBundleResponse>(res);
}

export async function uploadExportsSession(
  glookoZip: File,
  watchZip?: File | null,
  title?: string
): Promise<SessionBundleResponse> {
  const formData = new FormData();
  formData.append('glooko_file', glookoZip);
  if (watchZip) {
    formData.append('watch_file', watchZip);
  }
  if (title && title.trim()) {
    formData.append('title', title.trim());
  }

  const res = await fetch('/api/sessions/upload', {
    method: 'POST',
    body: formData,
  });
  return parseJsonOrThrow<SessionBundleResponse>(res);
}

export async function fetchSessionProfile(sessionId: string): Promise<DataProfile> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/profile`);
  return parseJsonOrThrow<DataProfile>(res);
}

export async function fetchSessionReplays(sessionId: string): Promise<DayReplay[]> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/replays`);
  return parseJsonOrThrow<DayReplay[]>(res);
}

export async function fetchSessionReport(sessionId: string): Promise<DiscoveryReport> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/report`);
  return parseJsonOrThrow<DiscoveryReport>(res);
}

export interface DiscoveryStreamCallbacks {
  onEvent: (event: SwarmStreamEvent) => void;
  onError?: (err: string) => void;
  onClose?: () => void;
}

export function subscribeDiscoveryStream(
  sessionId: string,
  callbacks: DiscoveryStreamCallbacks
): () => void {
  const url = `/api/sessions/${encodeURIComponent(sessionId)}/discover/stream`;
  const source = new EventSource(url);
  let completed = false;

  source.onmessage = (msg) => {
    try {
      const parsed = JSON.parse(msg.data) as SwarmStreamEvent;
      callbacks.onEvent(parsed);
      if (parsed.event_type === 'complete' || parsed.event_type === 'error') {
        completed = true;
        source.close();
        callbacks.onClose?.();
      }
    } catch (err) {
      callbacks.onError?.(
        err instanceof Error ? err.message : 'Failed to parse stream event'
      );
    }
  };

  source.onerror = () => {
    source.close();
    if (!completed) {
      callbacks.onError?.('Discovery stream disconnected.');
      callbacks.onClose?.();
    }
  };

  return () => {
    completed = true;
    source.close();
  };
}

export async function simulateWalkRx(
  sessionId: string,
  req: WalkSimulationRequest,
  signal?: AbortSignal
): Promise<WalkSimulationResponse> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/simulate/walk`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    }
  );
  return parseJsonOrThrow<WalkSimulationResponse>(res);
}

export async function askMyBody(
  sessionId: string,
  req: ChatRequest
): Promise<ChatResponse> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  return parseJsonOrThrow<ChatResponse>(res);
}
