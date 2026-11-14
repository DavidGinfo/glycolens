import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  FlaskConical,
  Footprints,
  HeartPulse,
  Moon,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Syringe,
} from 'lucide-react';
import type {
  InsightCard,
  InsightCategory,
  PairedBarItem,
  SpecialistDomain,
} from '../types';

interface InsightCardViewProps {
  insights: InsightCard[];
  isDiscovering: boolean;
}

type FilterTab = 'all' | InsightCategory;

const DOMAIN_META: Record<
  SpecialistDomain,
  {
    label: string;
    badgeClass: string;
    Icon: React.ComponentType<{ className?: string }>;
  }
> = {
  activity: {
    label: 'Activity & Movement',
    badgeClass: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-300',
    Icon: Footprints,
  },
  sleep: {
    label: 'Sleep & Circadian',
    badgeClass: 'border-indigo-500/35 bg-indigo-500/10 text-indigo-300',
    Icon: Moon,
  },
  dosing: {
    label: 'Dosing & Habits',
    badgeClass: 'border-sky-500/35 bg-sky-500/10 text-sky-300',
    Icon: Syringe,
  },
  vitals: {
    label: 'Vitals & BP',
    badgeClass: 'border-rose-500/35 bg-rose-500/10 text-rose-300',
    Icon: HeartPulse,
  },
};

const CATEGORY_META: Record<
  InsightCategory,
  {
    label: string;
    pillClass: string;
    Icon: React.ComponentType<{ className?: string }>;
  }
> = {
  habit_experiment: {
    label: 'Habit Experiment',
    pillClass: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
    Icon: FlaskConical,
  },
  pre_activity_shield: {
    label: 'Pre-Activity Shield',
    pillClass: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    Icon: ShieldCheck,
  },
  doctor_discussion: {
    label: 'Question for Doctor',
    pillClass: 'border-amber-500/35 bg-amber-500/15 text-amber-300',
    Icon: Stethoscope,
  },
};

function formatVal(val: number, unit: string): string {
  const abs = Math.abs(val);
  const formatted =
    Number.isInteger(val) || abs >= 100
      ? val.toFixed(0)
      : abs >= 10
        ? val.toFixed(1)
        : val.toFixed(1);
  if (!unit) return formatted;
  return unit === '%' ? `${formatted}%` : `${formatted} ${unit}`;
}

function formatSigned(val: number): string {
  const str = Math.abs(val) >= 10 ? val.toFixed(1) : val.toFixed(2);
  return val > 0 ? `+${str}` : str;
}

const PairedBarSvgChart: React.FC<{ card: InsightCard }> = ({ card }) => {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const { evidence } = card;
  const spec = evidence.chart_spec;

  const bars: PairedBarItem[] = useMemo(() => {
    if (spec?.bars && spec.bars.length > 0) {
      return spec.bars;
    }
    const maxVal = Math.max(Math.abs(evidence.mean_a), Math.abs(evidence.mean_b), 1) * 1.15;
    return [
      {
        label: evidence.title,
        condition_a_val: evidence.mean_a,
        condition_b_val: evidence.mean_b,
        max_val: maxVal,
        unit: evidence.unit,
        higher_is_better: evidence.mean_b >= evidence.mean_a,
      },
    ];
  }, [spec, evidence]);

  const condALabel = spec?.condition_a_label || 'Baseline / Risk Pattern';
  const condBLabel = spec?.condition_b_label || 'Target / Protective Pattern';

  const svgWidth = 480;
  const rowHeight = 76;
  const topPad = 10;
  const svgHeight = topPad + bars.length * rowHeight + 8;
  const barStartX = 16;
  const maxBarWidth = 315;

  return (
    <div className="rounded-xl border border-slate-800/90 bg-slate-950/80 p-3.5">
      {/* Legend + Sample sizes + 95% CI badge */}
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-slate-300">
            <span className="h-2.5 w-2.5 rounded-sm bg-gradient-to-r from-rose-500 to-amber-500" />
            <span className="truncate max-w-[165px]" title={condALabel}>
              {condALabel}
            </span>
            <span className="font-mono text-slate-400">(n={evidence.n_a})</span>
          </span>
          <span className="inline-flex items-center gap-1.5 text-slate-200">
            <span className="h-2.5 w-2.5 rounded-sm bg-gradient-to-r from-emerald-500 to-teal-400" />
            <span className="truncate max-w-[165px]" title={condBLabel}>
              {condBLabel}
            </span>
            <span className="font-mono text-slate-400">(n={evidence.n_b})</span>
          </span>
        </div>

        <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] font-medium text-emerald-300">
          95% CI [{formatSigned(evidence.ci_95_low)}, {formatSigned(evidence.ci_95_high)}]{' '}
          {evidence.unit}
        </span>
      </div>

      {/* Custom SVG Paired Bar Chart */}
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="w-full select-none overflow-visible"
        role="img"
        aria-label={`Paired comparison chart for ${card.headline}`}
      >
        <defs>
          <linearGradient id={`grad-a-${card.id}`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#f43f5e" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity="0.9" />
          </linearGradient>
          <linearGradient id={`grad-b-${card.id}`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#10b981" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#2dd4bf" stopOpacity="0.95" />
          </linearGradient>
        </defs>

        {bars.map((item, idx) => {
          const yBase = topPad + idx * rowHeight;
          const absA = Math.abs(item.condition_a_val);
          const absB = Math.abs(item.condition_b_val);
          const denom = Math.max(item.max_val || 0, absA, absB, 1e-6);
          const widthA = Math.max(8, Math.min(maxBarWidth, (absA / denom) * maxBarWidth));
          const widthB = Math.max(8, Math.min(maxBarWidth, (absB / denom) * maxBarWidth));
          const delta = item.condition_b_val - item.condition_a_val;
          const isHovered = hoveredIdx === idx;

          return (
            <g
              key={`${item.label}-${idx}`}
              onMouseEnter={() => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
              className="cursor-pointer"
            >
              {isHovered && (
                <rect
                  x={4}
                  y={yBase - 4}
                  width={svgWidth - 8}
                  height={rowHeight - 4}
                  rx={8}
                  fill="rgba(30, 41, 59, 0.45)"
                />
              )}

              {/* Metric Label + Delta Pill */}
              <text
                x={barStartX}
                y={yBase + 13}
                fill="#e2e8f0"
                fontSize="11.5"
                fontWeight="600"
              >
                {item.label}
              </text>
              <text
                x={svgWidth - 14}
                y={yBase + 13}
                textAnchor="end"
                fill="#34d399"
                fontSize="10.5"
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                Δ {formatSigned(delta)} {item.unit}
              </text>

              {/* Condition A Track + Bar (Risky / Baseline) */}
              <rect
                x={barStartX}
                y={yBase + 21}
                width={maxBarWidth}
                height={15}
                rx={4}
                fill="rgba(15, 23, 42, 0.9)"
                stroke="rgba(51, 65, 85, 0.5)"
                strokeWidth="0.75"
              />
              <rect
                x={barStartX}
                y={yBase + 21}
                width={widthA}
                height={15}
                rx={4}
                fill={`url(#grad-a-${card.id})`}
              />
              <text
                x={barStartX + widthA + 8}
                y={yBase + 32}
                fill="#fda4af"
                fontSize="11"
                fontWeight="600"
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                {formatVal(item.condition_a_val, item.unit)}
              </text>

              {/* Condition B Track + Bar (Improved / Target) */}
              <rect
                x={barStartX}
                y={yBase + 42}
                width={maxBarWidth}
                height={15}
                rx={4}
                fill="rgba(15, 23, 42, 0.9)"
                stroke="rgba(51, 65, 85, 0.5)"
                strokeWidth="0.75"
              />
              <rect
                x={barStartX}
                y={yBase + 42}
                width={widthB}
                height={15}
                rx={4}
                fill={`url(#grad-b-${card.id})`}
              />
              <text
                x={barStartX + widthB + 8}
                y={yBase + 53}
                fill="#6ee7b7"
                fontSize="11"
                fontWeight="700"
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                {formatVal(item.condition_b_val, item.unit)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};

export const InsightCardView: React.FC<InsightCardViewProps> = ({
  insights,
  isDiscovering,
}) => {
  const [activeTab, setActiveTab] = useState<FilterTab>('all');

  const counts = useMemo(() => {
    return {
      all: insights.length,
      habit_experiment: insights.filter((i) => i.category === 'habit_experiment').length,
      pre_activity_shield: insights.filter((i) => i.category === 'pre_activity_shield')
        .length,
      doctor_discussion: insights.filter((i) => i.category === 'doctor_discussion').length,
    };
  }, [insights]);

  const filteredInsights = useMemo(() => {
    const sorted = [...insights].sort((a, b) => a.severity_rank - b.severity_rank);
    if (activeTab === 'all') return sorted;
    return sorted.filter((i) => i.category === activeTab);
  }, [insights, activeTab]);

  const tabs: Array<{ id: FilterTab; label: string; count: number }> = [
    { id: 'all', label: 'All Verified', count: counts.all },
    {
      id: 'habit_experiment',
      label: 'Habit Experiments',
      count: counts.habit_experiment,
    },
    {
      id: 'pre_activity_shield',
      label: 'Pre-Activity Shields',
      count: counts.pre_activity_shield,
    },
    {
      id: 'doctor_discussion',
      label: 'Questions for Doctor',
      count: counts.doctor_discussion,
    },
  ];

  return (
    <section className="space-y-4">
      {/* Header & Category Filter Tabs */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-slate-100">
            Verified Actionable Findings
          </h2>
          <p className="text-xs text-slate-400">
            Every card below is backed by statistically significant personal evidence (bootstrap 95% CI excluding zero).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/80 p-1">
          {tabs.map((tab) => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                  active
                    ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40'
                    : 'text-slate-400 hover:bg-slate-800/70 hover:text-slate-200'
                }`}
              >
                <span>{tab.label}</span>
                <span
                  className={`rounded-full px-1.5 py-0.2 font-mono text-[10px] ${
                    active
                      ? 'bg-emerald-500/25 text-emerald-200'
                      : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {tab.count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Prominent Clinical Disclaimer Banner when viewing Questions for Doctor */}
      {activeTab === 'doctor_discussion' && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 text-xs text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
          <div>
            <span className="font-semibold text-amber-100">
              Clinical Decision Guardrail — Non-Prescriptive Discussion Topics Only:{' '}
            </span>
            GlycoLens never prescribes insulin doses, basal rates, or carb ratios. Use these data-backed observations purely as structured questions to review with your endocrinologist or certified diabetes care specialist.
          </div>
        </div>
      )}

      {/* Empty / Loading States */}
      {filteredInsights.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/40 py-14 text-center">
          <Sparkles className="h-8 w-8 text-emerald-400/70" />
          <p className="mt-3 text-sm font-medium text-slate-200">
            {isDiscovering
              ? 'Specialist agents are testing hypotheses on your aligned signals...'
              : insights.length === 0
                ? 'Click "Run Autonomous Discovery" above to surface verified insights.'
                : 'No insights in this category filter for the current dataset.'}
          </p>
          <p className="mt-1 text-xs text-slate-400">
            Verified cards stream in live as soon as their 95% confidence interval passes.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {filteredInsights.map((card) => {
            const dom = DOMAIN_META[card.domain] ?? DOMAIN_META.activity;
            const cat = CATEGORY_META[card.category] ?? CATEGORY_META.habit_experiment;
            const DomainIcon = dom.Icon;
            const CatIcon = cat.Icon;
            const isDoctorCard = card.category === 'doctor_discussion';

            return (
              <article
                key={card.id}
                className="flex flex-col justify-between rounded-2xl border border-slate-800/90 bg-slate-900/70 p-5 shadow-lg shadow-black/20 transition hover:border-slate-700"
              >
                <div className="space-y-3.5">
                  {/* Top Badges Row */}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${dom.badgeClass}`}
                      >
                        <DomainIcon className="h-3 w-3" />
                        {dom.label}
                      </span>
                      <span
                        className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${cat.pillClass}`}
                      >
                        <CatIcon className="h-3 w-3" />
                        {cat.label}
                      </span>
                    </div>

                    <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/35 bg-emerald-500/15 px-2.5 py-1 font-mono text-xs font-semibold text-emerald-300">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      {card.impact_badge}
                    </span>
                  </div>

                  {/* Doctor Card Clinical Disclaimer */}
                  {isDoctorCard && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                      <span>
                        <strong>For Clinician Review Only:</strong> Retrospective pattern for discussion with your endocrinologist—do not alter pump or insulin settings unilaterally.
                      </span>
                    </div>
                  )}

                  {/* Headline & Plain Explanation */}
                  <div>
                    <h3 className="text-base font-semibold leading-snug text-slate-100">
                      {card.headline}
                    </h3>
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-300 sm:text-sm">
                      {card.plain_explanation}
                    </p>
                  </div>

                  {/* Interactive Custom SVG Paired-Bar Comparison Chart */}
                  <PairedBarSvgChart card={card} />

                  {/* Personal Timestamp Anchor */}
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800/80 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
                    <CalendarClock className="h-3.5 w-3.5 shrink-0 text-sky-400" />
                    <span className="font-medium text-slate-200">Personal Anchor:</span>
                    <span className="text-slate-300">{card.personal_anchor}</span>
                    {card.evidence.example_timestamps.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {card.evidence.example_timestamps.slice(0, 3).map((ts) => (
                          <span
                            key={ts}
                            className="rounded bg-slate-800/90 px-1.5 py-0.5 font-mono text-[10px] text-sky-300"
                          >
                            {ts}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Highlighted "Try This Week" Box */}
                <div
                  className={`mt-4 rounded-xl border p-3.5 ${
                    isDoctorCard
                      ? 'border-amber-500/30 bg-gradient-to-r from-amber-500/10 to-slate-900/70'
                      : 'border-emerald-500/30 bg-gradient-to-r from-emerald-500/12 to-teal-500/5'
                  }`}
                >
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
                    <Sparkles className="h-3.5 w-3.5" />
                    {isDoctorCard ? 'Bring to Next Appointment' : 'Try This Week'}
                  </div>
                  <p className="mt-1 text-xs font-medium leading-relaxed text-slate-100 sm:text-sm">
                    {card.try_this_week}
                  </p>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
};
