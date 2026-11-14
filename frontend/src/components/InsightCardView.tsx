import React, { useMemo, useState } from 'react';
import {
  ArrowRight,
  CalendarClock,
  ChevronDown,
  ChevronUp,
  Footprints,
  HeartPulse,
  Moon,
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
  onOpenWalkSimulator?: () => void;
  onInspectDate?: (dateStr: string) => void;
}

type FilterTab = 'all' | InsightCategory;

const DOMAIN_META: Record<
  SpecialistDomain,
  {
    label: string;
    Icon: React.ComponentType<{ className?: string }>;
  }
> = {
  activity: { label: 'Movement', Icon: Footprints },
  sleep: { label: 'Sleep & Morning', Icon: Moon },
  dosing: { label: 'Meals & Insulin', Icon: Syringe },
  vitals: { label: 'Body Vitals', Icon: HeartPulse },
};

const CATEGORY_META: Record<
  InsightCategory,
  {
    label: string;
    actionPrefix: string;
  }
> = {
  habit_experiment: {
    label: 'Daily Habit',
    actionPrefix: 'What to try:',
  },
  pre_activity_shield: {
    label: 'Pre-Walk Check',
    actionPrefix: 'Before walking:',
  },
  doctor_discussion: {
    label: 'Topic for Your Doctor',
    actionPrefix: 'Question for your doctor:',
  },
};

function formatVal(val: number, unit: string): string {
  const abs = Math.abs(val);
  const formatted =
    Number.isInteger(val) || abs >= 100 ? val.toFixed(0) : val.toFixed(1);
  if (!unit) return formatted;
  return unit === '%' ? `${formatted}%` : `${formatted} ${unit}`;
}

function formatSigned(val: number): string {
  const str = Math.abs(val) >= 10 ? val.toFixed(1) : val.toFixed(2);
  return val > 0 ? `+${str}` : str;
}

const SimpleTwoBarComparison: React.FC<{
  bar: PairedBarItem;
  condALabel: string;
  condBLabel: string;
}> = ({ bar, condALabel, condBLabel }) => {
  const absA = Math.abs(bar.condition_a_val);
  const absB = Math.abs(bar.condition_b_val);
  const denom = Math.max(bar.max_val || 0, absA, absB, 1e-6);

  const pctA = Math.max(8, Math.min(100, (absA / denom) * 100));
  const pctB = Math.max(8, Math.min(100, (absB / denom) * 100));

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-4">
      <div className="mb-3 text-xs font-semibold text-slate-500">{bar.label}</div>

      <div className="space-y-3">
        {/* Pattern A (Trigger / Current Risk) */}
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate font-medium text-slate-600" title={condALabel}>
              {condALabel}
            </span>
            <span className="shrink-0 font-bold text-amber-800">
              {formatVal(bar.condition_a_val, bar.unit)}
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200/80">
            <div
              className="h-full rounded-full bg-amber-500/85 transition-all duration-500"
              style={{ width: `${pctA}%` }}
            />
          </div>
        </div>

        {/* Pattern B (Healthier / Baseline Comparison) */}
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate font-medium text-slate-800" title={condBLabel}>
              {condBLabel}
            </span>
            <span className="shrink-0 font-bold text-teal-800">
              {formatVal(bar.condition_b_val, bar.unit)}
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200/80">
            <div
              className="h-full rounded-full bg-teal-600 transition-all duration-500"
              style={{ width: `${pctB}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export const InsightCardView: React.FC<InsightCardViewProps> = ({
  insights,
  isDiscovering,
  onOpenWalkSimulator,
  onInspectDate,
}) => {
  const [activeTab, setActiveTab] = useState<FilterTab>('all');
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [showAllCards, setShowAllCards] = useState(false);

  const toggleCard = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

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

  const INITIAL_VISIBLE = 4;
  const visibleInsights =
    showAllCards || activeTab !== 'all'
      ? filteredInsights
      : filteredInsights.slice(0, INITIAL_VISIBLE);
  const hiddenCount = filteredInsights.length - visibleInsights.length;

  const tabs: Array<{ id: FilterTab; label: string; count: number }> = [
    { id: 'all', label: 'All', count: counts.all },
    { id: 'habit_experiment', label: 'Daily Habits', count: counts.habit_experiment },
    {
      id: 'pre_activity_shield',
      label: 'Walks & Exercise',
      count: counts.pre_activity_shield,
    },
    { id: 'doctor_discussion', label: 'Ask Doctor', count: counts.doctor_discussion },
  ];

  return (
    <section className="space-y-5">
      {/* Header & Calm Segmented Filter */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-teal-700">
            Personal Insights
          </p>
          <h2 className="mt-0.5 text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">
            Your Most Important Findings
          </h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Select any card to see why it happens and view specific dates from your history.
          </p>
        </div>

        <div className="inline-flex flex-wrap items-center gap-1 rounded-full border border-slate-200 bg-white p-1 shadow-2xs">
          {tabs.map((tab) => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                  active
                    ? 'bg-teal-700 text-white shadow-2xs'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <span>{tab.label}</span>
                {tab.count > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-0.2 text-[11px] ${
                      active
                        ? 'bg-white/20 text-white'
                        : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {tab.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Empty / Loading Placeholder */}
      {visibleInsights.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-3xl border border-slate-200 bg-white py-16 text-center shadow-xs">
          <p className="text-sm font-semibold text-slate-800">
            {isDiscovering
              ? 'Reviewing your meals, walks, and sleep for personal patterns…'
              : 'No findings in this category.'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Only consistent, repeatable patterns from your own logs are shown here.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {visibleInsights.map((card) => {
              const isExpanded = Boolean(expandedIds[card.id]);
              const dom = DOMAIN_META[card.domain] ?? DOMAIN_META.activity;
              const cat = CATEGORY_META[card.category] ?? CATEGORY_META.habit_experiment;
              const DomainIcon = dom.Icon;
              const isDoctorCard = card.category === 'doctor_discussion';
              const isWalkRelated =
                card.domain === 'activity' ||
                card.evidence.hypothesis_id.includes('walk');

              const spec = card.evidence.chart_spec;
              const fallbackBar: PairedBarItem = {
                label: card.evidence.title,
                condition_a_val: card.evidence.mean_a,
                condition_b_val: card.evidence.mean_b,
                max_val:
                  Math.max(
                    Math.abs(card.evidence.mean_a),
                    Math.abs(card.evidence.mean_b),
                    1
                  ) * 1.15,
                unit: card.evidence.unit,
                higher_is_better: card.evidence.mean_b >= card.evidence.mean_a,
              };
              const allBars =
                spec?.bars && spec.bars.length > 0 ? spec.bars : [fallbackBar];
              const primaryBar = allBars[0];
              const extraBars = allBars.slice(1);
              const condALabel = spec?.condition_a_label || 'Current pattern';
              const condBLabel = spec?.condition_b_label || 'Comparison pattern';

              return (
                <article
                  key={card.id}
                  className={`flex flex-col justify-between rounded-3xl border bg-white p-6 transition ${
                    isExpanded
                      ? 'border-teal-600/45 shadow-md ring-2 ring-teal-600/10'
                      : 'border-slate-200/90 shadow-xs hover:border-slate-300 hover:shadow-sm'
                  }`}
                >
                  {/* Clickable Summary Surface */}
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => toggleCard(card.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggleCard(card.id);
                      }
                    }}
                    className="cursor-pointer space-y-4 outline-none"
                  >
                    {/* Calm Single Eyebrow + Soft Impact Pill */}
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
                          isDoctorCard ? 'text-blue-700' : 'text-slate-500'
                        }`}
                      >
                        {isDoctorCard ? (
                          <Stethoscope className="h-3.5 w-3.5 text-blue-600" />
                        ) : (
                          <DomainIcon className="h-3.5 w-3.5 text-teal-700" />
                        )}
                        {dom.label} · {cat.label}
                      </span>

                      <span
                        className={`rounded-full px-3 py-1 text-xs font-bold ${
                          isDoctorCard
                            ? 'bg-blue-50 text-blue-800 border border-blue-200/80'
                            : 'bg-teal-50 text-teal-800 border border-teal-200/80'
                        }`}
                      >
                        {card.impact_badge}
                      </span>
                    </div>

                    {/* Clear Headline */}
                    <h3 className="text-lg font-bold leading-snug text-slate-900">
                      {card.headline}
                    </h3>

                    {/* Simple 2-Bar Visual */}
                    <SimpleTwoBarComparison
                      bar={primaryBar}
                      condALabel={condALabel}
                      condBLabel={condBLabel}
                    />

                    {/* Calm Action Callout */}
                    <div
                      className={`rounded-2xl px-4 py-3 text-xs leading-relaxed sm:text-[13px] ${
                        isDoctorCard
                          ? 'bg-blue-50/75 text-slate-800 border border-blue-100'
                          : 'bg-teal-50/65 text-slate-800 border border-teal-100'
                      }`}
                    >
                      <span
                        className={`mr-1.5 font-bold ${
                          isDoctorCard ? 'text-blue-800' : 'text-teal-800'
                        }`}
                      >
                        {cat.actionPrefix}
                      </span>
                      <span className={isExpanded ? '' : 'line-clamp-2'}>
                        {card.try_this_week}
                      </span>
                    </div>
                  </div>

                  {/* EXPANDED DETAILS (Progressive Disclosure) */}
                  {isExpanded && (
                    <div className="mt-5 space-y-4 border-t border-slate-100 pt-4 text-xs sm:text-[13px]">
                      <div>
                        <div className="mb-1 text-xs font-semibold text-slate-500">
                          Why this happens
                        </div>
                        <p className="leading-relaxed text-slate-700">
                          {card.plain_explanation}
                        </p>
                      </div>

                      {extraBars.length > 0 && (
                        <div className="space-y-2.5">
                          {extraBars.map((b, idx) => (
                            <SimpleTwoBarComparison
                              key={`${b.label}-${idx}`}
                              bar={b}
                              condALabel={condALabel}
                              condBLabel={condBLabel}
                            />
                          ))}
                        </div>
                      )}

                      {/* Supporting Dates */}
                      <div className="rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-3.5">
                        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                          <CalendarClock className="h-3.5 w-3.5 text-teal-700" />
                          Examples from your history
                        </div>
                        <p className="mt-1 text-xs text-slate-600">{card.personal_anchor}</p>
                        {card.evidence.example_timestamps.length > 0 && (
                          <div className="mt-2.5 flex flex-wrap gap-1.5">
                            {card.evidence.example_timestamps.slice(0, 4).map((ts) => {
                              const datePart = ts.slice(0, 10);
                              return (
                                <button
                                  key={ts}
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onInspectDate?.(datePart);
                                  }}
                                  title="View in Day Explorer"
                                  className="cursor-pointer rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 shadow-2xs transition hover:border-teal-600/40 hover:text-teal-800"
                                >
                                  {ts}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>

                      {/* Quiet Data Confidence Note */}
                      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                        <span>
                          Compared <strong>{card.evidence.n_a}</strong> vs{' '}
                          <strong>{card.evidence.n_b}</strong> instances
                        </span>
                        <span>
                          95% confidence range: {formatSigned(card.evidence.ci_95_low)} to{' '}
                          {formatSigned(card.evidence.ci_95_high)} {card.evidence.unit}
                        </span>
                      </div>

                      {isDoctorCard && (
                        <div className="rounded-xl border border-blue-200/80 bg-blue-50/70 p-3 text-xs leading-relaxed text-blue-900">
                          <strong>Note for your clinic visit:</strong> GlycoLens highlights retrospective patterns to discuss with your doctor and never recommends changing insulin doses on your own.
                        </div>
                      )}
                    </div>
                  )}

                  {/* Footer Actions */}
                  <div className="mt-4 flex items-center justify-between gap-2 border-t border-slate-100 pt-3">
                    {isWalkRelated && onOpenWalkSimulator ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenWalkSimulator();
                        }}
                        className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-teal-700 transition hover:text-teal-900"
                      >
                        Try Pre-Walk Check
                        <ArrowRight className="h-3.5 w-3.5" />
                      </button>
                    ) : (
                      <span className="text-xs text-slate-400">
                        {card.evidence.n_a + card.evidence.n_b} events analyzed
                      </span>
                    )}

                    <button
                      type="button"
                      onClick={() => toggleCard(card.id)}
                      className="inline-flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
                    >
                      {isExpanded ? (
                        <>
                          Show less
                          <ChevronUp className="h-3.5 w-3.5" />
                        </>
                      ) : (
                        <>
                          Details &amp; dates
                          <ChevronDown className="h-3.5 w-3.5" />
                        </>
                      )}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>

          {activeTab === 'all' && filteredInsights.length > INITIAL_VISIBLE && (
            <div className="flex justify-center pt-2">
              <button
                type="button"
                onClick={() => setShowAllCards((v) => !v)}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-200 bg-white px-5 py-2.5 text-xs font-semibold text-slate-700 shadow-xs transition hover:border-slate-300 hover:text-slate-950"
              >
                {showAllCards ? (
                  <>
                    Show fewer findings
                    <ChevronUp className="h-3.5 w-3.5" />
                  </>
                ) : (
                  <>
                    Show {hiddenCount} more finding{hiddenCount > 1 ? 's' : ''}
                    <ChevronDown className="h-3.5 w-3.5" />
                  </>
                )}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
};
