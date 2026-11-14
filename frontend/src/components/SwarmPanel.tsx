import React from 'react';
import {
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
  Icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    domain: 'activity',
    title: 'Movement & Walks',
    subtitle: 'Walk length & active insulin',
    Icon: Footprints,
  },
  {
    domain: 'sleep',
    title: 'Sleep & Mornings',
    subtitle: 'Sleep stages & morning rise',
    Icon: Moon,
  },
  {
    domain: 'dosing',
    title: 'Meals & Insulin',
    subtitle: 'Meal timing & low recovery',
    Icon: Syringe,
  },
  {
    domain: 'vitals',
    title: 'Vitals & Routine',
    subtitle: 'Blood pressure & weekday trends',
    Icon: HeartPulse,
  },
  {
    domain: 'synthesizer',
    title: 'Clinical Summary',
    subtitle: 'Prioritizes verified findings',
    Icon: Sparkles,
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
        <span className="inline-flex items-center gap-1 rounded-full bg-teal-50 px-2.5 py-0.5 text-xs font-semibold text-teal-800 border border-teal-200/80">
          <Loader2 className="h-3 w-3 animate-spin" />
          Checking
        </span>
      );
    case 'verified':
    case 'done':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-teal-50 px-2.5 py-0.5 text-xs font-semibold text-teal-800 border border-teal-200/80">
          <CheckCircle2 className="h-3 w-3 text-teal-600" />
          Complete
        </span>
      );
    case 'rejected':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
          Filtered
        </span>
      );
    case 'error':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700">
          <XCircle className="h-3 w-3" />
          Error
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-500">
          <Clock className="h-3 w-3" />
          {active ? 'Queued' : 'Ready'}
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
  onRunDiscovery,
  disabled = false,
}) => {
  const agentMap: Record<SwarmDomain, AgentCardState> = {
    activity: {
      domain: 'activity',
      title: 'Movement & Walks',
      subtitle: 'Walk length & active insulin',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: 'Checks how walks interact with active insulin.',
      testedCount: 0,
      verifiedCount: 0,
    },
    sleep: {
      domain: 'sleep',
      title: 'Sleep & Mornings',
      subtitle: 'Sleep stages & morning rise',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: 'Checks sleep stages and morning wake trends.',
      testedCount: 0,
      verifiedCount: 0,
    },
    dosing: {
      domain: 'dosing',
      title: 'Meals & Insulin',
      subtitle: 'Meal timing & low recovery',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: 'Checks pre-meal timing and post-low rebounds.',
      testedCount: 0,
      verifiedCount: 0,
    },
    vitals: {
      domain: 'vitals',
      title: 'Vitals & Routine',
      subtitle: 'Blood pressure & weekday trends',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: 'Checks blood pressure and workweek patterns.',
      testedCount: 0,
      verifiedCount: 0,
    },
    synthesizer: {
      domain: 'synthesizer',
      title: 'Clinical Summary',
      subtitle: 'Prioritizes verified findings',
      status: isDiscovering ? 'queued' : report ? 'done' : 'queued',
      message: 'Ranks confirmed findings in plain language.',
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
      if (isSig) streamVerified += 1;
      if (dom && agentMap[dom]) {
        agentMap[dom].testedCount += 1;
        if (isSig) agentMap[dom].verifiedCount += 1;
      }
    }
    if (dom && agentMap[dom]) {
      if (ev.status) agentMap[dom].status = ev.status;
      if (ev.message) agentMap[dom].message = ev.message;
    }
    if (ev.event_type === 'complete') {
      (Object.keys(agentMap) as SwarmDomain[]).forEach((k) => {
        agentMap[k].status = 'done';
      });
      if (ev.message) agentMap.synthesizer.message = ev.message;
    }
  }

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
  }

  const totalTested = report
    ? Math.max(report.hypotheses_tested, streamTested)
    : streamTested;
  const totalVerified = report
    ? Math.max(report.hypotheses_verified, report.insights.length, streamVerified)
    : streamVerified;

  const hypothesisLogs = events.filter((e) => e.event_type === 'hypothesis_tested');

  return (
    <div className="space-y-5">
      {/* Top Explanation + Re-run Button */}
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-900">
            Tested {totalTested} candidate patterns · Confirmed {totalVerified}{' '}
            statistically reliable effects
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Patterns that could be explained by day-to-day chance are automatically filtered out.
          </p>
        </div>

        <button
          type="button"
          onClick={onRunDiscovery}
          disabled={disabled || isDiscovering}
          className="inline-flex shrink-0 cursor-pointer items-center gap-2 rounded-full bg-teal-700 px-4 py-2 text-xs font-semibold text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isDiscovering ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Analyzing…
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5 fill-current" />
              Re-run Analysis
            </>
          )}
        </button>
      </div>

      {/* 5 Clean Specialist Cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {AGENT_DEFINITIONS.map(({ domain, title, subtitle, Icon }) => {
          const st = agentMap[domain];
          return (
            <div
              key={domain}
              className="flex flex-col justify-between rounded-2xl border border-slate-200 bg-white p-4"
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
                    <Icon className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <h4 className="truncate text-xs font-bold text-slate-900">
                      {title}
                    </h4>
                    <p className="truncate text-[11px] text-slate-500">{subtitle}</p>
                  </div>
                </div>

                <div className="mt-3 flex items-center justify-between gap-2">
                  {renderStatusBadge(st.status, isDiscovering)}
                  {domain !== 'synthesizer' && (
                    <span className="text-xs font-semibold text-slate-600">
                      {st.verifiedCount}/{st.testedCount} confirmed
                    </span>
                  )}
                </div>
              </div>

              <p className="mt-2.5 line-clamp-2 text-xs leading-relaxed text-slate-500">
                {st.message}
              </p>
            </div>
          );
        })}
      </div>

      {/* Confirmed vs Filtered Hypothesis Table */}
      {hypothesisLogs.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="mb-2.5 flex items-center justify-between">
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500">
              All Tested Patterns (Confirmed &amp; Filtered Out)
            </h4>
            <span className="text-xs text-slate-400">
              {hypothesisLogs.length} checks run
            </span>
          </div>

          <div className="max-h-52 space-y-1.5 overflow-y-auto pr-1 text-xs">
            {hypothesisLogs.map((ev, idx) => {
              const evi = ev.evidence;
              const isVerified =
                ev.status === 'verified' || Boolean(evi?.statistically_significant);

              return (
                <div
                  key={`${ev.agent_name}-${idx}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 bg-[#F8FAFC] px-3 py-2"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    {isVerified ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-teal-600" />
                    ) : (
                      <XCircle className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                    <span
                      className={`truncate font-medium ${
                        isVerified ? 'text-slate-900' : 'text-slate-500'
                      }`}
                    >
                      {evi ? evi.title : ev.message}
                    </span>
                  </div>

                  {evi && (
                    <div className="flex shrink-0 items-center gap-2 text-xs">
                      <span className="text-slate-500">
                        {evi.n_a} vs {evi.n_b} events
                      </span>
                      <span
                        className={`rounded-full px-2.5 py-0.5 font-semibold ${
                          evi.statistically_significant
                            ? 'bg-teal-50 text-teal-800 border border-teal-200/70'
                            : 'bg-slate-200/70 text-slate-600'
                        }`}
                      >
                        {evi.statistically_significant ? 'Confirmed' : 'No clear effect'}{' '}
                        ({formatSigned(evi.diff)} {evi.unit})
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
