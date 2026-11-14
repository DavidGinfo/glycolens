import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CalendarRange,
  CheckCircle2,
  Droplets,
  Footprints,
  KeyRound,
  LayoutDashboard,
  Loader2,
  MessageCircle,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import {
  createPersonaSession,
  fetchHealthStatus,
  fetchPersonas,
  fetchSessionReport,
  subscribeDiscoveryStream,
} from './api';
import type {
  DataProfile,
  DayReplay,
  DiscoveryReport,
  InsightCard,
  PersonaInfo,
  SessionBundleResponse,
  SwarmStreamEvent,
} from './types';
import { GlucoseBehaviorHero } from './components/GlucoseBehaviorHero';
import { InsightCardView } from './components/InsightCardView';
import { ReplayAndAgpChart } from './components/ReplayAndAgpChart';
import { WalkSimulator } from './components/WalkSimulator';
import { AskMyBodyChat } from './components/AskMyBodyChat';
import { SwarmPanel } from './components/SwarmPanel';
import { ModalMode, UploadAndKeyModal } from './components/UploadAndKeyModal';

type MainTab = 'overview' | 'walk' | 'days';

export const App: React.FC = () => {
  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  const [activePersonaId, setActivePersonaId] = useState<string | null>(null);
  const [geminiConfigured, setGeminiConfigured] = useState<boolean>(false);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [profile, setProfile] = useState<DataProfile | null>(null);
  const [replays, setReplays] = useState<DayReplay[]>([]);

  const [loadingSession, setLoadingSession] = useState<boolean>(true);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const [isDiscovering, setIsDiscovering] = useState<boolean>(false);
  const [streamEvents, setStreamEvents] = useState<SwarmStreamEvent[]>([]);
  const [liveInsights, setLiveInsights] = useState<InsightCard[]>([]);
  const [report, setReport] = useState<DiscoveryReport | null>(null);

  const [activeTab, setActiveTab] = useState<MainTab>('overview');
  const [preferredReplayDate, setPreferredReplayDate] = useState<string | null>(null);
  const [chatDrawerOpen, setChatDrawerOpen] = useState<boolean>(false);
  const [swarmDrawerOpen, setSwarmDrawerOpen] = useState<boolean>(false);
  const [modalMode, setModalMode] = useState<ModalMode>(null);

  const streamCleanupRef = useRef<(() => void) | null>(null);

  const stopActiveStream = useCallback(() => {
    if (streamCleanupRef.current) {
      streamCleanupRef.current();
      streamCleanupRef.current = null;
    }
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setChatDrawerOpen(false);
        setSwarmDrawerOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const triggerDiscovery = useCallback(
    (targetSessionId: string) => {
      stopActiveStream();
      setIsDiscovering(true);
      setStreamEvents([]);
      setLiveInsights([]);
      setReport(null);

      const cleanup = subscribeDiscoveryStream(targetSessionId, {
        onEvent: (ev) => {
          setStreamEvents((prev) => [...prev, ev]);

          if (ev.engine_mode === 'gemini_swarm') {
            setGeminiConfigured(true);
          }

          if (ev.insight) {
            const incoming = ev.insight;
            setLiveInsights((prev) => {
              const exists = prev.some((c) => c.id === incoming.id);
              if (exists) {
                return prev.map((c) => (c.id === incoming.id ? incoming : c));
              }
              return [...prev, incoming];
            });
          }

          if (ev.report) {
            setReport(ev.report);
            setLiveInsights(ev.report.insights);
          }

          if (ev.event_type === 'complete' || ev.event_type === 'error') {
            setIsDiscovering(false);
          }
        },
        onError: () => {
          setIsDiscovering(false);
          void fetchSessionReport(targetSessionId)
            .then((rep) => {
              setReport(rep);
              setLiveInsights(rep.insights);
            })
            .catch(() => {
              // ignore if not ready
            });
        },
        onClose: () => {
          setIsDiscovering(false);
        },
      });

      streamCleanupRef.current = cleanup;
    },
    [stopActiveStream]
  );

  const applySessionBundle = useCallback(
    (bundle: SessionBundleResponse, personaId: string | null) => {
      setActivePersonaId(personaId);
      setSessionId(bundle.session_id);
      setProfile(bundle.profile);
      setReplays(bundle.replays);
      setPreferredReplayDate(null);
      setSessionError(null);
      triggerDiscovery(bundle.session_id);
    },
    [triggerDiscovery]
  );

  const handleSelectPersona = useCallback(
    async (personaId: string) => {
      stopActiveStream();
      setActivePersonaId(personaId);
      setLoadingSession(true);
      setSessionError(null);
      try {
        const bundle = await createPersonaSession(personaId);
        applySessionBundle(bundle, personaId);
      } catch (err) {
        setSessionError(
          err instanceof Error ? err.message : 'Failed to load dataset.'
        );
      } finally {
        setLoadingSession(false);
      }
    },
    [applySessionBundle, stopActiveStream]
  );

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      setLoadingSession(true);
      try {
        const [healthRes, personaList] = await Promise.all([
          fetchHealthStatus().catch(() => ({
            status: 'ok',
            gemini_configured: false,
          })),
          fetchPersonas(),
        ]);

        if (cancelled) return;
        setGeminiConfigured(healthRes.gemini_configured);
        setPersonas(personaList);

        if (personaList.length > 0) {
          const initialPersona = personaList[0];
          setActivePersonaId(initialPersona.id);
          const bundle = await createPersonaSession(initialPersona.id);
          if (!cancelled) {
            applySessionBundle(bundle, initialPersona.id);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setSessionError(
            err instanceof Error
              ? err.message
              : 'Unable to connect to GlycoLens backend.'
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingSession(false);
        }
      }
    };

    void bootstrap();

    return () => {
      cancelled = true;
      stopActiveStream();
    };
  }, [applySessionBundle, stopActiveStream]);

  const displayedInsights =
    report && report.insights.length > 0 ? report.insights : liveInsights;

  const testedCount = useMemo(
    () => streamEvents.filter((e) => e.event_type === 'hypothesis_tested').length,
    [streamEvents]
  );
  const verifiedCount =
    report?.hypotheses_verified ??
    streamEvents.filter(
      (e) => e.event_type === 'hypothesis_tested' && e.status === 'verified'
    ).length;

  return (
    <div className="min-h-screen bg-[#F6F7F8] text-slate-900">
      {/* ================= WARM CLINICAL HEADER ================= */}
      <header className="sticky top-0 z-30 border-b border-slate-200/90 bg-white/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          {/* Left: Friendly Brand + Profile Picker */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setActiveTab('overview')}
              className="flex cursor-pointer items-center gap-2.5 text-left"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-teal-700 text-white shadow-2xs">
                <Droplets className="h-4.5 w-4.5" />
              </div>
              <span className="text-lg font-extrabold tracking-tight text-slate-900">
                Glyco<span className="text-teal-700">Lens</span>
              </span>
            </button>

            <div className="hidden h-5 w-px bg-slate-200 sm:block" />

            {/* Profile Select + Import */}
            <div className="flex items-center gap-1.5">
              <label htmlFor="dataset-select" className="sr-only">
                Select Health Profile
              </label>
              <select
                id="dataset-select"
                disabled={loadingSession}
                value={activePersonaId ?? '__uploaded__'}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val !== '__uploaded__') {
                    void handleSelectPersona(val);
                  }
                }}
                className="max-w-[215px] cursor-pointer truncate rounded-full border border-slate-200 bg-[#F8FAFC] px-3.5 py-1.5 text-xs font-semibold text-slate-700 outline-none transition hover:border-slate-300 focus:border-teal-600 sm:max-w-[260px]"
              >
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
                {activePersonaId === null && profile && (
                  <option value="__uploaded__">{profile.title} (Imported)</option>
                )}
              </select>

              <button
                type="button"
                onClick={() => setModalMode('upload')}
                title="Import Glooko / Smartwatch .zip export"
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
              >
                <Upload className="h-3.5 w-3.5 text-teal-700" />
                <span className="hidden md:inline">Import</span>
              </button>
            </div>
          </div>

          {/* Center: 3 Calm Segmented Tabs */}
          <nav
            aria-label="Views"
            className="order-3 flex w-full items-center justify-center gap-1 rounded-full border border-slate-200 bg-[#F8FAFC] p-1 sm:order-2 sm:w-auto"
          >
            <button
              type="button"
              onClick={() => setActiveTab('overview')}
              className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition sm:flex-initial ${
                activeTab === 'overview'
                  ? 'bg-white text-slate-900 shadow-xs ring-1 ring-slate-200/80'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <LayoutDashboard className="h-3.5 w-3.5 text-teal-700" />
              Overview
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('walk')}
              className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition sm:flex-initial ${
                activeTab === 'walk'
                  ? 'bg-white text-slate-900 shadow-xs ring-1 ring-slate-200/80'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Footprints className="h-3.5 w-3.5 text-teal-700" />
              Walk Check
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('days')}
              className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition sm:flex-initial ${
                activeTab === 'days'
                  ? 'bg-white text-slate-900 shadow-xs ring-1 ring-slate-200/80'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <CalendarRange className="h-3.5 w-3.5 text-teal-700" />
              Day Explorer
            </button>
          </nav>

          {/* Right: Quiet Methodology Button + Ask Question CTA */}
          <div className="order-2 flex items-center gap-2 sm:order-3">
            <button
              type="button"
              onClick={() => setSwarmDrawerOpen(true)}
              title="See how your data was checked and verified"
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
            >
              {isDiscovering ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-teal-700" />
                  <span className="hidden sm:inline">
                    Checking{testedCount > 0 ? ` (${testedCount})` : '…'}
                  </span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 text-teal-600" />
                  <span className="hidden sm:inline">
                    {verifiedCount > 0 ? 'How this was verified' : 'Methodology'}
                  </span>
                </>
              )}
            </button>

            <button
              type="button"
              onClick={() => setChatDrawerOpen(true)}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-teal-700 px-4 py-1.5 text-xs font-semibold text-white shadow-xs transition hover:bg-teal-800"
            >
              <MessageCircle className="h-3.5 w-3.5" />
              Ask a Question
            </button>
          </div>
        </div>
      </header>

      {/* ================= MAIN CONTENT ================= */}
      <main className="mx-auto max-w-6xl space-y-8 px-4 py-7 sm:px-6 sm:py-9">
        {sessionError && (
          <div className="flex items-center gap-3 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
            <AlertCircle className="h-5 w-5 shrink-0 text-red-600" />
            <div>{sessionError}</div>
          </div>
        )}

        {loadingSession && !profile ? (
          <div className="flex flex-col items-center justify-center rounded-3xl border border-slate-200 bg-white py-24 shadow-xs">
            <Loader2 className="h-7 w-7 animate-spin text-teal-700" />
            <p className="mt-3 text-sm font-semibold text-slate-700">
              Preparing your personal blood sugar overview…
            </p>
          </div>
        ) : (
          profile && (
            <>
              {/* VIEW 1: CALM OVERVIEW & TOP FINDINGS */}
              {activeTab === 'overview' && (
                <div className="space-y-9">
                  <GlucoseBehaviorHero
                    profile={profile}
                    report={report}
                    isDiscovering={isDiscovering}
                    onOpenDayExplorer={() => setActiveTab('days')}
                  />

                  <InsightCardView
                    insights={displayedInsights}
                    isDiscovering={isDiscovering}
                    onOpenWalkSimulator={() => setActiveTab('walk')}
                    onInspectDate={(dateStr) => {
                      setPreferredReplayDate(dateStr);
                      setActiveTab('days');
                    }}
                  />
                </div>
              )}

              {/* VIEW 2: PRE-WALK CHECK */}
              {activeTab === 'walk' && (
                <div className="mx-auto max-w-3xl space-y-5">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-teal-700">
                      Before You Head Out
                    </p>
                    <h2 className="mt-0.5 text-2xl font-bold tracking-tight text-slate-900">
                      Pre-Walk Blood Sugar Check
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      Estimate where your blood sugar is likely to finish based on how your body has responded to past walks.
                    </p>
                  </div>

                  <WalkSimulator
                    sessionId={sessionId}
                    walkModel={profile.walk_model}
                  />
                </div>
              )}

              {/* VIEW 3: DAY EXPLORER */}
              {activeTab === 'days' && (
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-teal-700">
                      Daily History
                    </p>
                    <h2 className="mt-0.5 text-2xl font-bold tracking-tight text-slate-900">
                      Day-by-Day Explorer
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      Explore real 24-hour days combining blood sugar, insulin boluses, walks, and sleep.
                    </p>
                  </div>

                  <ReplayAndAgpChart
                    hourlyAgp={profile.hourly_agp}
                    replays={replays}
                    daysCovered={profile.days_covered}
                    preferredDate={preferredReplayDate}
                  />
                </div>
              )}
            </>
          )
        )}
      </main>

      {/* Calm Clinical Footer */}
      <footer className="mt-14 border-t border-slate-200/80 bg-white/60 py-6 text-center text-xs text-slate-500">
        <div className="mx-auto max-w-3xl px-4">
          GlycoLens is for personal education and reflection—not medical advice. Always consult your physician or diabetes care team before changing insulin doses or pump settings.
        </div>
      </footer>

      {/* ================= SLIDE-OVER DRAWER: ASK ABOUT YOUR DATA ================= */}
      {chatDrawerOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/30 backdrop-blur-xs">
          <div
            className="flex-1"
            onClick={() => setChatDrawerOpen(false)}
            aria-hidden="true"
          />
          <aside className="relative h-full w-full max-w-lg border-l border-slate-200 bg-white shadow-2xl">
            <AskMyBodyChat
              sessionId={sessionId}
              datasetTitle={profile?.title}
              onClose={() => setChatDrawerOpen(false)}
              isDrawer
            />
          </aside>
        </div>
      )}

      {/* ================= MODAL: METHODOLOGY & VERIFICATION ================= */}
      {swarmDrawerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-4 backdrop-blur-xs">
          <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl sm:p-7">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 pb-4">
              <div className="flex items-center gap-3">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 border border-teal-100">
                  <ShieldCheck className="h-5 w-5" />
                </span>
                <div>
                  <h3 className="text-base font-bold text-slate-900">
                    How Your Patterns Were Verified
                  </h3>
                  <p className="text-xs text-slate-500">
                    Every insight is statistically checked against your personal history before being shown
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setSwarmDrawerOpen(false);
                    setModalMode('apikey');
                  }}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-200 bg-[#F8FAFC] px-3.5 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900"
                >
                  <KeyRound className="h-3.5 w-3.5 text-teal-700" />
                  {geminiConfigured ? 'AI Key Active' : 'Set API Key'}
                </button>

                <button
                  type="button"
                  onClick={() => setSwarmDrawerOpen(false)}
                  className="cursor-pointer rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <SwarmPanel
              isDiscovering={isDiscovering}
              events={streamEvents}
              report={report}
              geminiConfigured={geminiConfigured}
              disabled={!sessionId || loadingSession}
              onRunDiscovery={() => {
                if (sessionId) triggerDiscovery(sessionId);
              }}
            />
          </div>
        </div>
      )}

      {/* Import & API Key Modal */}
      <UploadAndKeyModal
        mode={modalMode}
        onClose={() => setModalMode(null)}
        onUploadSuccess={(bundle) => {
          applySessionBundle(bundle, null);
        }}
        onApiKeySaved={(health) => {
          setGeminiConfigured(health.gemini_configured);
          if (sessionId) {
            triggerDiscovery(sessionId);
          }
        }}
      />
    </div>
  );
};

export default App;
