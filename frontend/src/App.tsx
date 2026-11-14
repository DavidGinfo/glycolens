import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Database,
  Footprints,
  KeyRound,
  Loader2,
  Microscope,
  Sparkles,
  Syringe,
  Target,
  Upload,
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
import { SwarmPanel } from './components/SwarmPanel';
import { InsightCardView } from './components/InsightCardView';
import { ReplayAndAgpChart } from './components/ReplayAndAgpChart';
import { WalkSimulator } from './components/WalkSimulator';
import { AskMyBodyChat } from './components/AskMyBodyChat';
import { ModalMode, UploadAndKeyModal } from './components/UploadAndKeyModal';

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

  const [modalMode, setModalMode] = useState<ModalMode>(null);

  const streamCleanupRef = useRef<(() => void) | null>(null);

  const stopActiveStream = useCallback(() => {
    if (streamCleanupRef.current) {
      streamCleanupRef.current();
      streamCleanupRef.current = null;
    }
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
          // Fallback: check if report finished on backend
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
          err instanceof Error ? err.message : 'Failed to load persona dataset.'
        );
      } finally {
        setLoadingSession(false);
      }
    },
    [applySessionBundle, stopActiveStream]
  );

  // Initial bootstrap: fetch health + personas, then auto-load first persona
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
              : 'Unable to connect to GlycoLens backend on /api.'
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

  return (
    <div className="min-h-screen text-slate-100">
      {/* ================= 1. TOP HEADER & DATA SWITCHER ================= */}
      <header className="sticky top-0 z-30 border-b border-slate-800/85 bg-slate-950/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3.5 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Brand Logo */}
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400/25 via-teal-500/20 to-sky-500/25 text-emerald-400 ring-1 ring-emerald-400/35 shadow-lg shadow-emerald-500/10">
                <Microscope className="h-5 w-5" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-lg font-bold tracking-tight text-white sm:text-xl">
                    Glyco<span className="text-emerald-400">Lens</span>
                  </h1>
                  <span className="hidden rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-emerald-300 sm:inline-block">
                    v0.1 Lab
                  </span>
                </div>
                <p className="text-xs text-slate-400">
                  Autonomous Multimodal Metabolic Scientist
                </p>
              </div>
            </div>

            {/* Right Controls: Engine Status Pill + Upload Button */}
            <div className="flex flex-wrap items-center gap-2.5">
              {geminiConfigured ? (
                <button
                  type="button"
                  onClick={() => setModalMode('apikey')}
                  title="Gemini API key configured — click to update key"
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-300 transition hover:bg-emerald-500/25"
                >
                  <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
                  Gemini Swarm Active
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setModalMode('apikey')}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-amber-500/45 bg-amber-500/15 px-3 py-1.5 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/25"
                >
                  <KeyRound className="h-3.5 w-3.5 text-amber-400" />
                  Statistical Mode · Add Gemini Key
                </button>
              )}

              <button
                type="button"
                onClick={() => setModalMode('upload')}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl border border-slate-700/90 bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-emerald-500/50 hover:bg-slate-800 hover:text-white"
              >
                <Upload className="h-3.5 w-3.5 text-emerald-400" />
                Upload .zip Exports
              </button>
            </div>
          </div>

          {/* Persona Selector Pills */}
          <div className="flex items-center gap-2 overflow-x-auto pb-0.5">
            <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">
              <Database className="h-3 w-3" />
              Dataset:
            </span>
            {personas.map((p) => {
              const isSelected = activePersonaId === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  disabled={loadingSession}
                  onClick={() => void handleSelectPersona(p.id)}
                  title={p.subtitle}
                  className={`inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    isSelected
                      ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-200 shadow-sm shadow-emerald-500/10'
                      : 'border-slate-800 bg-slate-900/70 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                  }`}
                >
                  {p.is_local_real && (
                    <Sparkles className="h-3 w-3 text-emerald-400" />
                  )}
                  <span>{p.title}</span>
                  {p.badges.length > 0 && (
                    <span className="rounded bg-slate-800/90 px-1.5 py-0.2 font-mono text-[10px] text-slate-300">
                      {p.badges[0]}
                    </span>
                  )}
                </button>
              );
            })}

            {activePersonaId === null && profile && (
              <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-sky-500/50 bg-sky-500/15 px-3 py-1 text-xs font-semibold text-sky-200">
                <CheckCircle2 className="h-3 w-3 text-sky-400" />
                {profile.title} (Uploaded)
              </span>
            )}
          </div>
        </div>
      </header>

      {/* ================= MAIN WORKSPACE ================= */}
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        {sessionError && (
          <div className="flex items-center gap-3 rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200">
            <AlertCircle className="h-5 w-5 shrink-0 text-rose-400" />
            <div>{sessionError}</div>
          </div>
        )}

        {loadingSession && !profile ? (
          <div className="flex flex-col items-center justify-center rounded-2xl border border-slate-800/80 bg-slate-900/50 py-20">
            <Loader2 className="h-8 w-8 animate-spin text-emerald-400" />
            <p className="mt-3 text-sm font-medium text-slate-200">
              Aligning multimodal CGM, pump boluses, Watch workouts, sleep &amp; vitals...
            </p>
          </div>
        ) : (
          profile && (
            <>
              {/* Dataset Context Strip */}
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800/75 bg-slate-900/45 px-4 py-2.5 text-xs text-slate-300">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-100">{profile.title}</span>
                  {profile.description && (
                    <span className="text-slate-400">— {profile.description}</span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-slate-400">
                  <span>
                    {profile.start_date} → {profile.end_date} (
                    {Math.round(profile.days_covered)}d)
                  </span>
                  {profile.source_tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded border border-slate-800 bg-slate-950 px-2 py-0.5 text-[10px] text-slate-300"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {/* ================= 2. KPI SUMMARY RIBBON ================= */}
              <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {/* KPI 1: Time in Range % */}
                <div className="rounded-2xl border border-slate-800/90 bg-slate-900/70 p-4 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-400">
                      Time in Range (70–180)
                    </span>
                    <Target className="h-4 w-4 text-emerald-400" />
                  </div>
                  <div className="mt-2 flex items-baseline gap-2">
                    <span
                      className={`font-mono text-2xl font-bold ${
                        profile.tir_pct >= 70 ? 'text-emerald-400' : 'text-amber-300'
                      }`}
                    >
                      {profile.tir_pct.toFixed(1)}%
                    </span>
                    <span className="text-xs text-slate-400">
                      Clinical target &ge;70%
                    </span>
                  </div>
                  {/* Stacked Glycemic Distribution Bar */}
                  <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-slate-950">
                    <div
                      className="bg-rose-500"
                      style={{ width: `${Math.max(1, profile.tbr_pct)}%` }}
                      title={`<70 Low: ${profile.tbr_pct.toFixed(1)}%`}
                    />
                    <div
                      className="bg-emerald-500"
                      style={{ width: `${profile.tir_pct}%` }}
                      title={`70-180 In Range: ${profile.tir_pct.toFixed(1)}%`}
                    />
                    <div
                      className="bg-amber-500"
                      style={{ width: `${profile.tar_pct}%` }}
                      title={`>180 High: ${profile.tar_pct.toFixed(1)}%`}
                    />
                  </div>
                  <div className="mt-1.5 flex justify-between font-mono text-[10px] text-slate-400">
                    <span className="text-rose-400">
                      &lt;70: {profile.tbr_pct.toFixed(1)}%
                    </span>
                    <span className="text-amber-300">
                      &gt;180: {profile.tar_pct.toFixed(1)}%
                    </span>
                  </div>
                </div>

                {/* KPI 2: Mean CGM & GMI% */}
                <div className="rounded-2xl border border-slate-800/90 bg-slate-900/70 p-4 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-400">
                      Mean CGM &amp; GMI
                    </span>
                    <Activity className="h-4 w-4 text-sky-400" />
                  </div>
                  <div className="mt-2 flex items-baseline gap-2.5">
                    <span className="font-mono text-2xl font-bold text-slate-100">
                      {profile.mean_cgm.toFixed(0)}{' '}
                      <span className="text-xs font-normal text-slate-400">
                        mg/dL
                      </span>
                    </span>
                    <span className="rounded-md border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 font-mono text-xs font-semibold text-sky-300">
                      GMI {profile.gmi_pct.toFixed(1)}%
                    </span>
                  </div>
                  <p className="mt-3 font-mono text-[11px] text-slate-400">
                    CV {profile.cv_pct.toFixed(1)}% (&plusmn;{profile.sd_cgm.toFixed(0)}) ·{' '}
                    {profile.cgm_count.toLocaleString()} CGM pts
                  </p>
                </div>

                {/* KPI 3: Workouts & Sleep Sessions Aligned */}
                <div className="rounded-2xl border border-slate-800/90 bg-slate-900/70 p-4 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-400">
                      Multimodal Wearable Alignment
                    </span>
                    <Footprints className="h-4 w-4 text-indigo-400" />
                  </div>
                  <div className="mt-2 flex items-baseline gap-3">
                    <div>
                      <span className="font-mono text-2xl font-bold text-emerald-300">
                        {profile.workout_count}
                      </span>
                      <span className="ml-1 text-xs text-slate-400">workouts</span>
                    </div>
                    <span className="text-slate-700">/</span>
                    <div>
                      <span className="font-mono text-2xl font-bold text-indigo-300">
                        {profile.sleep_count}
                      </span>
                      <span className="ml-1 text-xs text-slate-400">sleeps</span>
                    </div>
                  </div>
                  <p className="mt-3 font-mono text-[11px] text-slate-400">
                    {profile.bp_count} BP logs
                    {profile.has_skin_temp ? ' · Skin Temp' : ''}
                    {profile.has_resp_rate ? ' · Resp Rate' : ''}
                  </p>
                </div>

                {/* KPI 4: Unlogged Bolus Context % */}
                <div className="rounded-2xl border border-slate-800/90 bg-slate-900/70 p-4 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-400">
                      Unlogged Bolus Context
                    </span>
                    <Syringe className="h-4 w-4 text-amber-400" />
                  </div>
                  <div className="mt-2 flex items-baseline gap-2">
                    <span
                      className={`font-mono text-2xl font-bold ${
                        profile.pct_zero_carb_boluses >= 30
                          ? 'text-amber-300'
                          : 'text-slate-100'
                      }`}
                    >
                      {profile.pct_zero_carb_boluses.toFixed(0)}%
                    </span>
                    <span className="text-xs text-slate-400">
                      boluses missing carbs
                    </span>
                  </div>
                  <p className="mt-3 font-mono text-[11px] text-slate-400">
                    {profile.bolus_count.toLocaleString()} boluses · Mean TDD{' '}
                    {profile.mean_tdd_units.toFixed(1)}U/d
                  </p>
                </div>
              </section>

              {/* ================= 3. LIVE PARALLEL SWARM DISCOVERY BAR ================= */}
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

              {/* ================= 4. VERIFIED ACTIONABLE FINDINGS GRID ================= */}
              <InsightCardView
                insights={displayedInsights}
                isDiscovering={isDiscovering}
              />

              {/* ================= 5. INTERACTIVE EXPLORATION LAB ================= */}
              <section className="space-y-4 pt-2">
                <div>
                  <h2 className="text-lg font-semibold tracking-tight text-slate-100">
                    Interactive Metabolic Exploration Lab
                  </h2>
                  <p className="text-xs text-slate-400">
                    Inspect 24h multimodal day replays, simulate personalized post-meal walks, and interrogate your own telemetry.
                  </p>
                </div>

                {/* Full-Width 24h Multimodal Day Replay & Circadian AGP */}
                <ReplayAndAgpChart
                  hourlyAgp={profile.hourly_agp}
                  replays={replays}
                  daysCovered={profile.days_covered}
                />

                {/* Bottom Split: Personal Walk Rx Simulator + Ask My Body Chat */}
                <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                  <WalkSimulator
                    sessionId={sessionId}
                    walkModel={profile.walk_model}
                  />
                  <AskMyBodyChat
                    sessionId={sessionId}
                    datasetTitle={profile.title}
                  />
                </div>
              </section>
            </>
          )
        )}
      </main>

      {/* Footer Clinical Disclaimer */}
      <footer className="mt-10 border-t border-slate-900 bg-slate-950/80 py-5 text-center text-xs text-slate-500">
        <div className="mx-auto max-w-5xl px-4">
          GlycoLens is an educational multimodal metabolic pattern discovery tool—not a medical device. Never modify basal insulin rates, insulin-to-carb ratios, or dosing regimens without consulting your physician or diabetes care team.
        </div>
      </footer>

      {/* Upload .zip & Gemini API Key Modal */}
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
