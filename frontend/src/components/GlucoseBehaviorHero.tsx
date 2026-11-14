import React, { useMemo, useState } from 'react';
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Moon,
  Sun,
} from 'lucide-react';
import type { DataProfile, DiscoveryReport, HourlyBucket } from '../types';

interface GlucoseBehaviorHeroProps {
  profile: DataProfile;
  report: DiscoveryReport | null;
  isDiscovering: boolean;
  onOpenDayExplorer: () => void;
}

function formatFriendlyHour(hour: number): string {
  const h = ((Math.round(hour) % 24) + 24) % 24;
  if (h === 0) return '12 AM';
  if (h === 12) return '12 PM';
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

function buildSmoothSvgPath(points: Array<[number, number]>): string {
  if (points.length === 0) return '';
  if (points.length === 1) {
    return `M ${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  }
  let d = `M ${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];

    const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;

    d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

export const GlucoseBehaviorHero: React.FC<GlucoseBehaviorHeroProps> = ({
  profile,
  report,
  isDiscovering,
  onOpenDayExplorer,
}) => {
  const [showMoreStats, setShowMoreStats] = useState(false);
  const [showFullSummary, setShowFullSummary] = useState(false);
  const [hoverHour, setHoverHour] = useState<number | null>(null);

  const sortedAgp = useMemo(
    () => [...profile.hourly_agp].sort((a, b) => a.hour - b.hour),
    [profile.hourly_agp]
  );

  const { peakBucket, calmBucket, plainRhythmLine } = useMemo(() => {
    if (sortedAgp.length === 0) {
      return {
        peakBucket: null as HourlyBucket | null,
        calmBucket: null as HourlyBucket | null,
        plainRhythmLine:
          'Your glucose readings are aligned with your meals, activity, and sleep.',
      };
    }

    const peak = sortedAgp.reduce((best, b) =>
      b.median_cgm > best.median_cgm ? b : best
    );
    const calm = sortedAgp.reduce((best, b) =>
      b.median_cgm < best.median_cgm ? b : best
    );

    const lowNote =
      profile.tbr_pct < 1.5
        ? 'Low blood sugar events are rare'
        : profile.tbr_pct < 4.0
          ? 'Occasional dips below 70 mg/dL appear'
          : 'Dips below 70 mg/dL occur regularly';

    const text = `${lowNote} (${profile.tbr_pct.toFixed(1)}%). Your steadiest time of day is around ${formatFriendlyHour(
      calm.hour
    )} (~${Math.round(
      calm.median_cgm
    )} mg/dL), while levels typically peak around ${formatFriendlyHour(
      peak.hour
    )} (~${Math.round(peak.median_cgm)} mg/dL).`;

    return { peakBucket: peak, calmBucket: calm, plainRhythmLine: text };
  }, [sortedAgp, profile.tbr_pct]);

  const W = 680;
  const H = 250;
  const padL = 42;
  const padR = 18;
  const padTop = 26;
  const padBot = 34;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBot;

  const yMin = 50;
  const yMax = 280;

  const xOfHour = (hr: number) => padL + (Math.max(0, Math.min(24, hr)) / 24) * plotW;
  const yOfMg = (mg: number) => {
    const clamped = Math.max(yMin, Math.min(yMax, mg));
    return padTop + (1 - (clamped - yMin) / (yMax - yMin)) * plotH;
  };

  const chartGeometry = useMemo(() => {
    if (sortedAgp.length === 0) return null;
    const pts = [...sortedAgp, { ...sortedAgp[0], hour: 24 }];

    const medianCoords: Array<[number, number]> = pts.map((b) => [
      xOfHour(b.hour),
      yOfMg(b.median_cgm),
    ]);
    const medianPath = buildSmoothSvgPath(medianCoords);

    const polyIqr = [
      ...pts.map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p75).toFixed(1)}`),
      ...[...pts]
        .reverse()
        .map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p25).toFixed(1)}`),
    ].join(' ');

    const polyOuter = [
      ...pts.map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p90).toFixed(1)}`),
      ...[...pts]
        .reverse()
        .map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p10).toFixed(1)}`),
    ].join(' ');

    return { medianPath, polyIqr, polyOuter };
  }, [sortedAgp]);

  const hoveredBucket = useMemo(() => {
    if (hoverHour === null || sortedAgp.length === 0) return null;
    return sortedAgp.reduce((best, cur) =>
      Math.abs(cur.hour - hoverHour) < Math.abs(best.hour - hoverHour) ? cur : best
    );
  }, [hoverHour, sortedAgp]);

  const handleSvgMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * W;
    if (svgX < padL || svgX > W - padR) {
      setHoverHour(null);
      return;
    }
    const hr = ((svgX - padL) / plotW) * 24;
    setHoverHour(Math.max(0, Math.min(23, Math.round(hr))));
  };

  const tirStatusLabel =
    profile.tir_pct >= 70
      ? 'Meeting the 70% clinical goal'
      : profile.tir_pct >= 55
        ? 'Close to the 70% clinical goal'
        : 'Room to improve daily steadiness';

  return (
    <section className="rounded-3xl border border-slate-200/90 bg-white p-6 shadow-xs sm:p-7">
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-teal-700">
            Overview
          </p>
          <h2 className="mt-0.5 text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">
            How Your Blood Sugar Behaves
          </h2>
        </div>

        <span className="text-xs font-medium text-slate-500">
          Based on {Math.round(profile.days_covered)} days ({profile.start_date} to{' '}
          {profile.end_date})
        </span>
      </div>

      {/* 2-Column Split */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12 lg:items-center">
        {/* LEFT: Friendly Time-in-Range + Daily Summary */}
        <div className="space-y-4 lg:col-span-5">
          <div className="rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-5">
            <div className="flex items-baseline justify-between gap-3">
              <div>
                <div className="text-xs font-medium text-slate-500">
                  Time in Healthy Range (70–180 mg/dL)
                </div>
                <div className="mt-1 flex items-baseline gap-2.5">
                  <span className="text-4xl font-extrabold tracking-tight text-slate-900">
                    {profile.tir_pct.toFixed(0)}%
                  </span>
                </div>
                <div className="mt-0.5 text-xs font-medium text-teal-700">
                  {tirStatusLabel}
                </div>
              </div>

              <div className="text-right">
                <div className="text-xs text-slate-500">Average Glucose</div>
                <div className="mt-0.5 text-xl font-bold text-slate-800">
                  {profile.mean_cgm.toFixed(0)}{' '}
                  <span className="text-xs font-medium text-slate-500">mg/dL</span>
                </div>
              </div>
            </div>

            {/* Clean Segmented Bar */}
            <div className="mt-4 flex h-3 w-full overflow-hidden rounded-full bg-slate-200/80 gap-0.5">
              {profile.tbr_pct > 0 && (
                <div
                  className="bg-red-500 transition-all"
                  style={{ width: `${Math.max(2, profile.tbr_pct)}%` }}
                  title={`Below 70 mg/dL: ${profile.tbr_pct.toFixed(1)}%`}
                />
              )}
              <div
                className="bg-teal-600 transition-all"
                style={{ width: `${profile.tir_pct}%` }}
                title={`In Range (70–180 mg/dL): ${profile.tir_pct.toFixed(1)}%`}
              />
              {profile.tar_pct > 0 && (
                <div
                  className="bg-amber-400 transition-all"
                  style={{ width: `${profile.tar_pct}%` }}
                  title={`Above 180 mg/dL: ${profile.tar_pct.toFixed(1)}%`}
                />
              )}
            </div>

            <div className="mt-2.5 flex items-center justify-between text-xs text-slate-600">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-red-500" />
                Low: <strong className="text-slate-900">{profile.tbr_pct.toFixed(1)}%</strong>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-teal-600" />
                In Range:{' '}
                <strong className="text-slate-900">{profile.tir_pct.toFixed(0)}%</strong>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                High: <strong className="text-slate-900">{profile.tar_pct.toFixed(0)}%</strong>
              </span>
            </div>
          </div>

          {/* Everyday Takeaway */}
          <div className="px-1">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Daily Rhythm Summary
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-700">
              {plainRhythmLine}
            </p>

            {report?.executive_summary && (
              <div className="mt-2">
                {showFullSummary && (
                  <p className="mb-2 rounded-xl bg-slate-50 p-3 text-xs leading-relaxed text-slate-600 border border-slate-200/70">
                    {report.executive_summary}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => setShowFullSummary((v) => !v)}
                  className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-teal-700 hover:text-teal-800"
                >
                  {showFullSummary ? 'Show less' : 'Read full clinical summary'}
                  {showFullSummary ? (
                    <ChevronUp className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
            )}

            {isDiscovering && !report && (
              <p className="mt-2 text-xs text-teal-700">
                Reviewing your meals, sleep, and activity logs for patterns…
              </p>
            )}
          </div>

          {/* Optional Additional Clinical Numbers */}
          <div className="border-t border-slate-100 pt-2 px-1">
            <button
              type="button"
              onClick={() => setShowMoreStats((v) => !v)}
              className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-slate-500 transition hover:text-slate-800"
            >
              {showMoreStats
                ? 'Hide clinical metrics'
                : 'More clinical metrics (Estimated A1C, Variability, Insulin)'}
              {showMoreStats ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>

            {showMoreStats && (
              <div className="mt-3 grid grid-cols-2 gap-3 rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-3.5 text-xs sm:grid-cols-3">
                <div>
                  <div className="text-slate-500">Est. A1C (GMI)</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {profile.gmi_pct.toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Variability</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {profile.cv_pct.toFixed(1)}% (±{profile.sd_cgm.toFixed(0)})
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Daily Insulin</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {profile.mean_tdd_units.toFixed(1)} units/day
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Sensor Readings</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {profile.cgm_count.toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Watch Sessions</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {profile.workout_count} walks · {profile.sleep_count} sleeps
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Carb Logging</div>
                  <div className="mt-0.5 font-bold text-slate-900">
                    {(100 - profile.pct_zero_carb_boluses).toFixed(0)}% with carbs
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: Clean Daylight 24-Hour Curve */}
        <div className="rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-4 sm:p-5 lg:col-span-7">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-bold text-slate-900">
                Typical 24-Hour Glucose Curve
              </h3>
              <p className="text-xs text-slate-500">
                Solid line shows your usual level · Shaded green band is 70–180 mg/dL
              </p>
            </div>

            <button
              type="button"
              onClick={onOpenDayExplorer}
              className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 shadow-2xs transition hover:border-teal-600/40 hover:text-teal-700"
            >
              Browse single days
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {chartGeometry ? (
            <div>
              <svg
                viewBox={`0 0 ${W} ${H}`}
                onMouseMove={handleSvgMove}
                onMouseLeave={() => setHoverHour(null)}
                className="w-full select-none overflow-visible"
                role="img"
                aria-label="Typical 24-hour blood sugar curve"
              >
                {/* White inner plot area */}
                <rect
                  x={padL}
                  y={padTop}
                  width={plotW}
                  height={plotH}
                  rx={8}
                  fill="#ffffff"
                />

                {/* Soft Sage Healthy Target Band (70 - 180 mg/dL) */}
                <rect
                  x={padL}
                  y={yOfMg(180)}
                  width={plotW}
                  height={yOfMg(70) - yOfMg(180)}
                  fill="rgba(13, 148, 136, 0.08)"
                />
                <line
                  x1={padL}
                  x2={W - padR}
                  y1={yOfMg(180)}
                  y2={yOfMg(180)}
                  stroke="#cbd5e1"
                  strokeDasharray="4 4"
                  strokeWidth="1"
                />
                <line
                  x1={padL}
                  x2={W - padR}
                  y1={yOfMg(70)}
                  y2={yOfMg(70)}
                  stroke="#fca5a5"
                  strokeDasharray="4 4"
                  strokeWidth="1"
                />

                {/* Y-axis clean sans labels */}
                {[70, 120, 180, 250].map((mg) => (
                  <text
                    key={mg}
                    x={padL - 8}
                    y={yOfMg(mg) + 3.5}
                    textAnchor="end"
                    fill={mg === 70 ? '#b91c1c' : mg === 180 ? '#0f766e' : '#64748b'}
                    fontSize="11"
                    fontWeight={mg === 70 || mg === 180 ? '600' : '500'}
                  >
                    {mg}
                  </text>
                ))}

                {/* Usual spread bands */}
                <polygon
                  points={chartGeometry.polyOuter}
                  fill="rgba(15, 118, 110, 0.06)"
                />
                <polygon
                  points={chartGeometry.polyIqr}
                  fill="rgba(15, 118, 110, 0.14)"
                />

                {/* Smooth Median Curve in Deep Forest Teal */}
                <path
                  d={chartGeometry.medianPath}
                  fill="none"
                  stroke="#0f766e"
                  strokeWidth="3"
                  strokeLinecap="round"
                />

                {/* Calmest & Peak Callout Pins */}
                {hoverHour === null && calmBucket && (
                  <g>
                    <circle
                      cx={xOfHour(calmBucket.hour)}
                      cy={yOfMg(calmBucket.median_cgm)}
                      r={4.5}
                      fill="#0f766e"
                      stroke="#ffffff"
                      strokeWidth="2"
                    />
                    <text
                      x={Math.min(
                        W - padR - 58,
                        Math.max(padL + 58, xOfHour(calmBucket.hour))
                      )}
                      y={Math.min(H - padBot - 8, yOfMg(calmBucket.median_cgm) + 18)}
                      textAnchor="middle"
                      fill="#0f766e"
                      fontSize="11"
                      fontWeight="600"
                    >
                      Steadiest · {Math.round(calmBucket.median_cgm)} mg/dL
                    </text>
                  </g>
                )}

                {hoverHour === null && peakBucket && (
                  <g>
                    <circle
                      cx={xOfHour(peakBucket.hour)}
                      cy={yOfMg(peakBucket.median_cgm)}
                      r={4.5}
                      fill="#d97706"
                      stroke="#ffffff"
                      strokeWidth="2"
                    />
                    <text
                      x={Math.min(
                        W - padR - 58,
                        Math.max(padL + 58, xOfHour(peakBucket.hour))
                      )}
                      y={Math.max(padTop + 13, yOfMg(peakBucket.median_cgm) - 10)}
                      textAnchor="middle"
                      fill="#b45309"
                      fontSize="11"
                      fontWeight="600"
                    >
                      Usual peak · {Math.round(peakBucket.median_cgm)} mg/dL
                    </text>
                  </g>
                )}

                {/* Hover Crosshair */}
                {hoveredBucket && (
                  <g>
                    <line
                      x1={xOfHour(hoveredBucket.hour)}
                      x2={xOfHour(hoveredBucket.hour)}
                      y1={padTop}
                      y2={H - padBot}
                      stroke="#94a3b8"
                      strokeDasharray="3 3"
                      strokeWidth="1"
                    />
                    <circle
                      cx={xOfHour(hoveredBucket.hour)}
                      cy={yOfMg(hoveredBucket.median_cgm)}
                      r={5.5}
                      fill="#0f766e"
                      stroke="#ffffff"
                      strokeWidth="2"
                    />
                  </g>
                )}

                {/* Friendly Time Labels */}
                {[
                  { hr: 0, label: 'Midnight' },
                  { hr: 6, label: '6 AM' },
                  { hr: 12, label: 'Noon' },
                  { hr: 18, label: '6 PM' },
                  { hr: 24, label: 'Midnight' },
                ].map(({ hr, label }) => (
                  <text
                    key={`${hr}-${label}`}
                    x={xOfHour(hr)}
                    y={H - 8}
                    textAnchor={
                      hr === 0 ? 'start' : hr === 24 ? 'end' : 'middle'
                    }
                    fill="#64748b"
                    fontSize="11"
                    fontWeight="500"
                  >
                    {label}
                  </text>
                ))}
              </svg>

              {/* Hover Strip */}
              <div className="mt-1.5 flex min-h-[24px] items-center justify-between text-xs text-slate-500">
                {hoveredBucket ? (
                  <div className="flex flex-wrap items-center gap-2.5 text-slate-700">
                    <span className="font-semibold text-teal-800">
                      Around {formatFriendlyHour(hoveredBucket.hour)}:
                    </span>
                    <span>
                      Usual{' '}
                      <strong className="text-slate-900">
                        {Math.round(hoveredBucket.median_cgm)} mg/dL
                      </strong>{' '}
                      (typically {Math.round(hoveredBucket.p25)}–
                      {Math.round(hoveredBucket.p75)})
                    </span>
                    <span className="text-slate-500">
                      · {Math.round(hoveredBucket.tir_pct)}% in range
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-4 text-xs text-slate-500">
                    <span className="inline-flex items-center gap-1">
                      <Moon className="h-3.5 w-3.5 text-slate-400" /> Overnight
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Sun className="h-3.5 w-3.5 text-amber-500" /> Daytime
                    </span>
                    <span>Move cursor over the curve to see hourly averages</span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex h-48 items-center justify-center text-xs text-slate-400">
              No hourly glucose profile available.
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
