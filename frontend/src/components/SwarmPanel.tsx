import React from 'react';
import {
  Activity,
  BrainCircuit,
  CheckCircle2,
  Clock,
  Footprints,
  HeartPulse,
  Loader2,
  Moon,
  Play,
  Sparkles,
  Syringe,
  XCircle,
} from 'lucide-react';
import type {
  DiscoveryReport,
  SwarmDomain,
  SwarmStatus,
  SwarmStreamEvent,
} from '../types';

export interface AgentCardState {
  domain: SwarmDomain;
  title: string;
  subtitle: string;
  status: SwarmStatus;
  message: string;
  testedCount: number;
  verifiedCount: number;
}

interface SwarmPanelProps {
  isDiscovering: boolean;
  events: SwarmStreamEvent[];
  report: DiscoveryReport | null;
  geminiConfigured: boolean;
  onRunDiscovery: () => void;
  disabled?: boolean;
}

const AGENT_DEFINITIONS: Array<{
  domain: SwarmDomain;
  title: string;
  subtitle: string;
  accentText: string;
  accentRing: string;
  Icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    domain: 'activity',
    title: 'Activity & Movement',
    subtitle: 'GLUT4 walks · IOB synergy · cadence',
    accentText: 'text-emerald-400',
    accentRing: 'border-emerald-500/30 bg-emerald-500/5',
    Icon: Footprints,
  },
  {
    domain: 'sleep',
    title: 'Sleep & Circadian',
    subtitle: 'Deep/REM architecture · dawn surge',
    accentText: 'text-indigo-400',
    accentRing: 'border-indigo-500/30 bg-indigo-500/5',
    Icon: Moon,
  },
  {
    domain: 'dosing',
    title: 'Dosing & Habits',
    subtitle: 'Meal timing · unlogged boluses · stacking',
    accentText: 'text-sky-400',
    accentRing: 'border-sky-500/30 bg-sky-500/5',
    Icon: Syringe,
  },
  {
    domain: 'vitals',
    title: 'Vitals & BP',
    subtitle: 'Morning BP · heart rate · recovery',
    accentText: 'text-rose-400',
    accentRing: 'border-rose-500/30 bg-rose-500/5',
    Icon: HeartPulse,
  },
  {
    domain: 'synthesizer',
    title: 'Chief Clinical Synthesizer',
    subtitle: 'Cross-domain ranking & guardrails',
    accentText: 'text-amber-400',
    accentRing: 'border-amber-500/30 bg-amber-500/5',
    Icon: BrainCircuit,
  },
];

function resolveEventDomain(ev: SwarmStreamEvent): SwarmDomain | null {
  if (ev.domain) return ev.domain;
  const name = (ev.agent_name || '').toLowerCase();
  if (name.includes('activ') || name.includes('walk') || name.includes('move')) {
    return 'activity';
  }
  if (name.includes('sleep') || name.includes('circad') || name.includes('night')) {
    return 'sleep';
  }
  if (name.includes('dos') || name.includes('bolus') || name.includes('insulin')) {
    return 'dosing';
  }
  if (name.includes('vital') || name.includes('bp') || name.includes('heart')) {
    return 'vitals';
  }
  if (
    name.includes('synth') ||
    name.includes('chief') ||
    ev.event_type === 'complete'
  ) {
    return 'synthesizer';
  }
  return null;
}

function renderStatusBadge(status: SwarmStatus, active: boolean) {
  switch (status) {
    case 'running':
    case 'testing':
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-sky-400/40 bg-sky-500/15 px-2.5 py-0.5 text-[11px] font-medium text-sky-300">
          <Loader2 className="h-3 w-3 animate-spin" />
          {status === 'testing' ? 'Testing 95% CI' : 'Analyzing'}
        </span>
      );
    case 'verified':
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/40 bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
          <CheckCircle2 className="h-3 w-3" />
          Verified
        </span>
      );
    case 'done':
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
          <CheckCircle2 className="h-3 w-3" />
          Complete
        </span>
      );
    case 'rejected':
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-slate-600/60 bg-slate-800/80 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">
          <XCircle className="h-3 w-3" />
          Noise Filtered
        </span>
      );
    case 'error':
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/15 px-2.5 py-0.5 text-[11px] font-medium text-rose-300">
          <XCircle className="h-3 w-3" />
          Error
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-slate-700/80 bg-slate-800/60 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">
          <Clock className={`h-3 w-3 ${active ? 'animate-pulse text-slate-300' : ''}`} />
          {active ? 'Queued' : 'Standby'}
        </span>
      );
  }
}

function formatSigned(val: number): string {
  const rounded = Math.abs(val) >= 10 ? val.toFixed(1) : val.toFixed(2);
  return val > 0 ? `+${rounded}` : rounded;
}

export const SwarmPanel: React.FC<SwarmPanelProps> = ({
  isDiscovering,
  events,
  report,
  geminiConfigured,
  onRunDiscovery,
  disabled = false,
}) => {
  // Derive per-agent state from stream events
  const agentMap: Record<SwarmDomain, AgentCardState> = {
    activity: {
      domain: 'activity',
      title: 'Activity & Movement',
      subtitle: 'GLUT4 walks · IOB synergy',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: isDiscovering
        ? 'Awaiting aligned CGM + Watch workouts...'
        : 'Ready to scan activity bouts',
      testedCount: 0,
      verifiedCount: 0,
    },
    sleep: {
      domain: 'sleep',
      title: 'Sleep & Circadian',
      subtitle: 'Deep/REM stages · dawn curve',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: isDiscovering
        ? 'Awaiting overnight sleep intervals...'
        : 'Ready to scan sleep stages',
      testedCount: 0,
      verifiedCount: 0,
    },
    dosing: {
      domain: 'dosing',
      title: 'Dosing & Habits',
      subtitle: 'Meal timing · unlogged boluses',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: isDiscovering
        ? 'Awaiting pump bolus trajectories...'
        : 'Ready to scan dosing patterns',
      testedCount: 0,
      verifiedCount: 0,
    },
    vitals: {
      domain: 'vitals',
      title: 'Vitals & BP',
      subtitle: 'Blood pressure · HR · recovery',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: isDiscovering
        ? 'Awaiting daily vitals & BP logs...'
        : 'Ready to scan vitals & BP',
      testedCount: 0,
      verifiedCount: 0,
    },
    synthesizer: {
      domain: 'synthesizer',
      title: 'Chief Clinical Synthesizer',
      subtitle: 'Cross-domain synthesis',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: isDiscovering
        ? 'Waiting for specialist verifications...'
        : 'Synthesizes verified findings',
      testedCount: 0,
      verifiedCount: 0,
    },
  };

  let streamTested = 0;
  let streamVerified = 0;

  for (const ev of events) {
    const dom = resolveEventDomain(ev);
    if (ev.event_type === 'hypothesis_tested') {
      streamTested += 1;
      const isSig =
        ev.status === 'verified' || Boolean(ev.evidence?.statistically_significant);
      if (isSig) {
        streamVerified += 1;
      }
      if (dom && agentMap[dom]) {
        agentMap[dom].testedCount += 1;
        if (isSig) agentMap[dom].verifiedCount += 1;
      }
    }

    if (dom && agentMap[dom]) {
      if (ev.status) {
        agentMap[dom].status = ev.status;
      }
      if (ev.message) {
        agentMap[dom].message = ev.message;
      }
    }

    if (ev.event_type === 'complete') {
      (Object.keys(agentMap) as SwarmDomain[]).forEach((k) => {
        agentMap[k].status = 'done';
      });
      if (ev.message) {
        agentMap.synthesizer.message = ev.message;
      }
    }
  }

  // If report exists and stream events were sparse, backfill counts from report
  if (report && streamVerified === 0 && report.insights.length > 0) {
    for (const ins of report.insights) {
      if (agentMap[ins.domain]) {
        agentMap[ins.domain].verifiedCount += 1;
        agentMap[ins.domain].testedCount = Math.max(
          agentMap[ins.domain].testedCount,
          agentMap[ins.domain].verifiedCount
        );
      }
    }
    agentMap.synthesizer.verifiedCount = report.insights.length;
    agentMap.synthesizer.message = `Synthesized ${report.insights.length} verified insights`;
  }

  const totalTested = report ? Math.max(report.hypotheses_tested, streamTested) : streamTested;
  const totalVerified = report
    ? Math.max(report.hypotheses_verified, report.insights.length, streamVerified)
    : streamVerified;

  const hypothesisLogs = events.filter(
    (e) =>
      e.event_type === 'hypothesis_tested' ||
      e.event_type === 'insight_ready' ||
      e.event_type === 'complete' ||
      e.event_type === 'error'
  );

  const activeEngineMode =
    report?.engine_mode ??
    events[events.length - 1]?.engine_mode ??
    (geminiConfigured ? 'gemini_swarm' : 'statistical_fallback');

  return (
    <section className="rounded-2xl border border-slate-800/90 bg-slate-900/65 p-5 shadow-xl shadow-black/25 backdrop-blur-md">
      {/* Top Bar */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Activity className="h-4 w-4" />
            </span>
            <h2 className="text-base font-semibold tracking-tight text-slate-100 sm:text-lg">
              Autonomous Parallel Hypothesis Swarm
            </h2>
            <span className="rounded-full border border-slate-700/80 bg-slate-800/80 px-2.5 py-0.5 text-xs font-medium text-slate-300">
              {activeEngineMode === 'gemini_swarm'
                ? 'Gemini Multi-Agent + Bootstrap 95% CI'
                : 'Deterministic Bootstrap 95% CI Verifier'}
            </span>
          </div>
          <p className="text-xs text-slate-400 sm:text-sm">
            4 domain specialists propose personalized physiological hypotheses in parallel; only effects passing bootstrap 95% confidence intervals survive to synthesis.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Live Counters */}
          <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/70 px-3.5 py-2 text-xs">
            <div>
              <span className="text-slate-400">Tested: </span>
              <span className="font-mono font-semibold text-slate-200">{totalTested}</span>
            </div>
            <span className="h-3.5 w-px bg-slate-800" />
            <div>
              <span className="text-slate-400">Verified (95% CI): </span>
              <span className="font-mono font-semibold text-emerald-400">
                {totalVerified}
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={onRunDiscovery}
            disabled={disabled || isDiscovering}
            className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 px-4 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-emerald-500/20 transition hover:from-emerald-400 hover:to-teal-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isDiscovering ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Running Swarm Discovery...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 fill-current" />
                Run Autonomous Discovery
              </>
            )}
          </button>
        </div>
      </div>

      {/* 4 Parallel Specialist Cards + Chief Clinical Synthesizer */}
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {AGENT_DEFINITIONS.map(({ domain, title, subtitle, accentText, accentRing, Icon }) => {
          const st = agentMap[domain];
          const isBusy = st.status === 'running' || st.status === 'testing';
          return (
            <div
              key={domain}
              className={`relative flex flex-col justify-between rounded-xl border p-3.5 transition-all duration-200 ${
                isBusy
                  ? `${accentRing} shadow-md`
                  : 'border-slate-800/90 bg-slate-950/60 hover:border-slate-700/80'
              }`}
            >
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-900 ${accentText}`}
                    >
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="truncate text-xs font-semibold text-slate-100">
                        {title}
                      </h3>
                      <p className="truncate text-[11px] text-slate-400">{subtitle}</p>
                    </div>
                  </div>
                </div>

                <div className="mt-2.5 flex items-center justify-between gap-2">
                  {renderStatusBadge(st.status, isDiscovering)}
                  {domain !== 'synthesizer' ? (
                    <span className="font-mono text-[11px] text-slate-400">
                      <span className="font-semibold text-emerald-400">
                        {st.verifiedCount}
                      </span>
                      /{st.testedCount} pass
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-amber-300/90">
                      <Sparkles className="h-3 w-3" />
                      {totalVerified} ranked
                    </span>
                  )}
                </div>
              </div>

              <p className="mt-2.5 line-clamp-2 min-h-[2rem] text-[11px] leading-relaxed text-slate-300/90">
                {st.message}
              </p>
            </div>
          );
        })}
      </div>

      {/* Live Hypothesis Verification Log Stream */}
      {hypothesisLogs.length > 0 && (
        <div className="mt-4 rounded-xl border border-slate-800/90 bg-slate-950/75 p-3">
          <div className="mb-2 flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-slate-400">
            <span>Live Statistical Verification Telemetry (95% Bootstrap CI)</span>
            <span className="font-mono text-slate-500">{hypothesisLogs.length} events</span>
          </div>
          <div className="max-h-36 space-y-1.5 overflow-y-auto pr-1 text-xs">
            {hypothesisLogs.map((ev, idx) => {
              const evi = ev.evidence;
              const isVerified =
                ev.status === 'verified' ||
                ev.event_type === 'insight_ready' ||
                Boolean(evi?.statistically_significant);
              const isRejected =
                ev.status === 'rejected' ||
                (ev.event_type === 'hypothesis_tested' &&
                  evi &&
                  !evi.statistically_significant);

              return (
                <div
                  key={`${ev.event_type}-${ev.agent_name}-${idx}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800/70 bg-slate-900/60 px-2.5 py-1.5"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    {isVerified ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                    ) : isRejected ? (
                      <XCircle className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                    ) : (
                      <Sparkles className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                    )}
                    <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {ev.domain || ev.agent_name}
                    </span>
                    <span
                      className={`truncate ${
                        isRejected ? 'text-slate-400 line-through decoration-slate-600' : 'text-slate-200'
                      }`}
                    >
                      {evi ? evi.title : ev.insight ? ev.insight.headline : ev.message}
                    </span>
                  </div>

                  {evi && (
                    <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
                      <span className="text-slate-400">
                        n={evi.n_a} vs {evi.n_b}
                      </span>
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          evi.statistically_significant
                            ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                            : 'border border-slate-700 bg-slate-800/80 text-slate-400'
                        }`}
                      >
                        Δ {formatSigned(evi.diff)} {evi.unit} · 95% CI [
                        {formatSigned(evi.ci_95_low)}, {formatSigned(evi.ci_95_high)}]
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Executive Summary Banner */}
      {report?.executive_summary && (
        <div className="mt-4 flex items-start gap-3 rounded-xl border border-emerald-500/25 bg-gradient-to-r from-emerald-500/10 via-slate-900/80 to-sky-500/10 px-4 py-3">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
          <div className="text-xs leading-relaxed text-slate-200 sm:text-sm">
            <span className="font-semibold text-emerald-300">Executive Synthesis: </span>
            {report.executive_summary}
          </div>
        </div>
      )}
    </section>
  );
};
