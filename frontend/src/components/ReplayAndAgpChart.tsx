import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Footprints,
  HeartPulse,
  Moon,
  Syringe,
} from 'lucide-react';
import type {
  DayReplay,
  DayReplayBolus,
  DayReplayBpEvent,
  DayReplayWalk,
  HourlyBucket,
} from '../types';

interface ReplayAndAgpChartProps {
  hourlyAgp: HourlyBucket[];
  replays: DayReplay[];
  daysCovered?: number;
  preferredDate?: string | null;
}

type ChartMode = 'agp' | 'replay';

function formatHourClock(hrFloat: number): string {
  const clamped = Math.max(0, Math.min(24, hrFloat));
  const h = Math.floor(clamped) % 24;
  const m = Math.round((clamped - Math.floor(clamped)) * 60) % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export const ReplayAndAgpChart: React.FC<ReplayAndAgpChartProps> = ({
  hourlyAgp,
  replays,
  daysCovered = 90,
  preferredDate = null,
}) => {
  const [mode, setMode] = useState<ChartMode>('replay');
  const [selectedReplayIdx, setSelectedReplayIdx] = useState<number>(0);
  const [hoverHour, setHoverHour] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    setSelectedReplayIdx(0);
    setHoverHour(null);
    if (replays.length === 0 && hourlyAgp.length > 0) {
      setMode('agp');
    }
  }, [replays, hourlyAgp]);

  useEffect(() => {
    if (!preferredDate || replays.length === 0) return;
    const matchIdx = replays.findIndex((r) => r.date === preferredDate);
    if (matchIdx >= 0) {
      setMode('replay');
      setSelectedReplayIdx(matchIdx);
    }
  }, [preferredDate, replays]);

  const currentReplay: DayReplay | null =
    replays[selectedReplayIdx] ?? replays[0] ?? null;

  const W = 860;
  const H = 345;
  const padL = 48;
  const padR = 22;
  const padTop = 34;
  const padBot = 56;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBot;

  const yMin = 40;
  const yMax = 320;

  const xOfHour = (hr: number) => padL + (Math.max(0, Math.min(24, hr)) / 24) * plotW;
  const yOfMg = (mg: number) => {
    const clamped = Math.max(yMin, Math.min(yMax, mg));
    return padTop + (1 - (clamped - yMin) / (yMax - yMin)) * plotH;
  };

  const sortedAgp = useMemo(
    () => [...hourlyAgp].sort((a, b) => a.hour - b.hour),
    [hourlyAgp]
  );

  const agpPaths = useMemo(() => {
    if (sortedAgp.length === 0) return null;
    const pts = [...sortedAgp, { ...sortedAgp[0], hour: 24 }];

    const polyP10P90 = [
      ...pts.map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p90).toFixed(1)}`),
      ...[...pts]
        .reverse()
        .map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p10).toFixed(1)}`),
    ].join(' ');

    const polyP25P75 = [
      ...pts.map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p75).toFixed(1)}`),
      ...[...pts]
        .reverse()
        .map((b) => `${xOfHour(b.hour).toFixed(1)},${yOfMg(b.p25).toFixed(1)}`),
    ].join(' ');

    const medianLine = pts
      .map(
        (b, idx) =>
          `${idx === 0 ? 'M' : 'L'} ${xOfHour(b.hour).toFixed(1)} ${yOfMg(
            b.median_cgm
          ).toFixed(1)}`
      )
      .join(' ');

    return { polyP10P90, polyP25P75, medianLine };
  }, [sortedAgp]);

  const replayCgmSorted = useMemo(() => {
    if (!currentReplay) return [];
    return [...currentReplay.cgm].sort((a, b) => a[0] - b[0]);
  }, [currentReplay]);

  const replayPath = useMemo(() => {
    if (replayCgmSorted.length === 0) return '';
    return replayCgmSorted
      .map(
        ([hr, mg], idx) =>
          `${idx === 0 ? 'M' : 'L'} ${xOfHour(hr).toFixed(1)} ${yOfMg(mg).toFixed(1)}`
      )
      .join(' ');
  }, [replayCgmSorted]);

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = W / rect.width;
    const svgX = (e.clientX - rect.left) * scaleX;
    if (svgX < padL || svgX > W - padR) {
      setHoverHour(null);
      return;
    }
    const hr = ((svgX - padL) / plotW) * 24;
    setHoverHour(Math.max(0, Math.min(23.99, hr)));
  };

  const hoveredAgpBucket: HourlyBucket | null = useMemo(() => {
    if (mode !== 'agp' || hoverHour === null || sortedAgp.length === 0) return null;
    const targetHr = Math.floor(hoverHour);
    return (
      sortedAgp.find((b) => b.hour === targetHr) ??
      sortedAgp.reduce((best, cur) =>
        Math.abs(cur.hour - hoverHour) < Math.abs(best.hour - hoverHour) ? cur : best
      )
    );
  }, [mode, hoverHour, sortedAgp]);

  const hoveredReplayDetails = useMemo(() => {
    if (mode !== 'replay' || hoverHour === null || !currentReplay) return null;
    let nearestCgm: [number, number] | null = null;
    for (const pt of replayCgmSorted) {
      if (
        nearestCgm === null ||
        Math.abs(pt[0] - hoverHour) < Math.abs(nearestCgm[0] - hoverHour)
      ) {
        nearestCgm = pt;
      }
    }

    const activeWalk: DayReplayWalk | undefined = currentReplay.walks.find((w) => {
      const s = w.start_hour ?? w.hour ?? 0;
      const end = w.end_hour ?? s + (w.duration_min ?? 25) / 60;
      return hoverHour >= s - 0.18 && hoverHour <= end + 0.18;
    });

    const nearBolus: DayReplayBolus | undefined = currentReplay.boluses.find(
      (b) => Math.abs(b.hour - hoverHour) <= 0.35
    );

    const nearBp: DayReplayBpEvent | undefined = currentReplay.bp_events.find(
      (bp) => Math.abs(bp.hour - hoverHour) <= 0.45
    );

    const inSleep =
      currentReplay.sleep_end_hour != null && hoverHour <= currentReplay.sleep_end_hour;

    return { nearestCgm, activeWalk, nearBolus, nearBp, inSleep };
  }, [mode, hoverHour, currentReplay, replayCgmSorted]);

  const yTicks = [70, 120, 180, 250, 300];
  const xHours = [0, 3, 6, 9, 12, 15, 18, 21, 24];

  return (
    <div className="flex flex-col rounded-3xl border border-slate-200/90 bg-white p-6 shadow-xs">
      {/* Header & Mode Switcher */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-base font-bold text-slate-900">
            {mode === 'replay'
              ? 'Example 24-Hour Days'
              : `${Math.round(daysCovered)}-Day Hourly Profile`}
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            {mode === 'replay'
              ? 'Blood sugar alongside your walks, sleep window, insulin boluses, and blood pressure.'
              : 'Usual median curve, typical spread, and hourly low-sugar frequency.'}
          </p>
        </div>

        <div className="inline-flex self-start rounded-full border border-slate-200 bg-[#F8FAFC] p-1">
          <button
            type="button"
            onClick={() => setMode('replay')}
            className={`cursor-pointer rounded-full px-3.5 py-1.5 text-xs font-semibold transition ${
              mode === 'replay'
                ? 'bg-teal-700 text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Example Days ({replays.length})
          </button>
          <button
            type="button"
            onClick={() => setMode('agp')}
            className={`cursor-pointer rounded-full px-3.5 py-1.5 text-xs font-semibold transition ${
              mode === 'agp'
                ? 'bg-teal-700 text-white shadow-2xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            {Math.round(daysCovered)}-Day Average
          </button>
        </div>
      </div>

      {/* Day Replay Selector Pills */}
      {mode === 'replay' && replays.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {replays.map((rep, idx) => {
            const selected = idx === selectedReplayIdx;
            return (
              <button
                key={`${rep.date}-${idx}`}
                type="button"
                onClick={() => setSelectedReplayIdx(idx)}
                className={`cursor-pointer rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                  selected
                    ? 'border-teal-600/50 bg-teal-50 text-teal-900'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900'
                }`}
              >
                {rep.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Legend Strip */}
      <div className="mt-3.5 flex flex-wrap items-center gap-4 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-xs bg-teal-500/20 ring-1 ring-teal-600/40" />
          Healthy Range (70–180)
        </span>
        {mode === 'replay' ? (
          <>
            <span className="inline-flex items-center gap-1.5">
              <Footprints className="h-3.5 w-3.5 text-teal-700" />
              Walk
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Moon className="h-3.5 w-3.5 text-indigo-600" />
              Sleep
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Syringe className="h-3.5 w-3.5 text-amber-600" />
              Insulin Bolus
            </span>
            <span className="inline-flex items-center gap-1.5">
              <HeartPulse className="h-3.5 w-3.5 text-red-600" />
              Blood Pressure
            </span>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0.5 w-4 bg-teal-700" />
              Median Glucose
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-3 rounded-xs bg-teal-600/25" />
              Typical Spread (25–75%)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-xs bg-red-500/80" />
              Low-Sugar Frequency
            </span>
          </>
        )}
      </div>

      {/* Daylight SVG Canvas */}
      <div className="relative mt-3 rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-3">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverHour(null)}
          className="w-full cursor-crosshair select-none"
          role="img"
          aria-label="24-hour blood sugar timeline"
        >
          {/* Inner crisp white plot background */}
          <rect
            x={padL}
            y={padTop}
            width={plotW}
            height={plotH}
            rx={6}
            fill="#ffffff"
          />

          {/* Healthy Target Band (70 - 180 mg/dL) */}
          <rect
            x={padL}
            y={yOfMg(180)}
            width={plotW}
            height={yOfMg(70) - yOfMg(180)}
            fill="rgba(13, 148, 136, 0.08)"
          />

          {/* Horizontal Grid Lines */}
          {yTicks.map((val) => {
            const y = yOfMg(val);
            const isBoundary = val === 70 || val === 180;
            return (
              <g key={val}>
                <line
                  x1={padL}
                  y1={y}
                  x2={W - padR}
                  y2={y}
                  stroke={
                    val === 70
                      ? '#fca5a5'
                      : val === 180
                        ? '#99f6e4'
                        : '#f1f5f9'
                  }
                  strokeDasharray={isBoundary ? '4 4' : undefined}
                  strokeWidth={isBoundary ? 1.2 : 1}
                />
                <text
                  x={padL - 8}
                  y={y + 4}
                  textAnchor="end"
                  fill={
                    val === 70 ? '#b91c1c' : val === 180 ? '#0f766e' : '#64748b'
                  }
                  fontSize="11"
                  fontWeight={isBoundary ? '600' : '500'}
                >
                  {val}
                </text>
              </g>
            );
          })}

          {/* Vertical Hour Grid Lines */}
          {xHours.map((hr) => {
            const x = xOfHour(hr);
            return (
              <g key={hr}>
                <line
                  x1={x}
                  y1={padTop}
                  x2={x}
                  y2={padTop + plotH}
                  stroke="#f1f5f9"
                  strokeWidth="1"
                />
                <text
                  x={x}
                  y={H - 8}
                  textAnchor="middle"
                  fill="#64748b"
                  fontSize="11"
                  fontWeight="500"
                >
                  {hr === 24 ? '24:00' : `${String(hr).padStart(2, '0')}:00`}
                </text>
              </g>
            );
          })}

          {/* AGP MODE */}
          {mode === 'agp' && agpPaths && (
            <g>
              <polygon
                points={agpPaths.polyP10P90}
                fill="rgba(15, 118, 110, 0.07)"
              />
              <polygon
                points={agpPaths.polyP25P75}
                fill="rgba(15, 118, 110, 0.16)"
              />
              <path
                d={agpPaths.medianLine}
                fill="none"
                stroke="#0f766e"
                strokeWidth="2.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />

              {sortedAgp.map((b) => {
                const barSlotW = plotW / 24;
                const bx = xOfHour(b.hour) + 2;
                const maxHypoH = 22;
                const hScaled = Math.min(
                  maxHypoH,
                  Math.max(b.low_pct > 0 ? 2.5 : 0, (b.low_pct / 20) * maxHypoH)
                );
                const by = padTop + plotH + 26 - hScaled;
                return (
                  <g key={`hypo-${b.hour}`}>
                    <rect
                      x={bx}
                      y={padTop + plotH + 6}
                      width={Math.max(4, barSlotW - 4)}
                      height={20}
                      rx={2}
                      fill="#f1f5f9"
                    />
                    {hScaled > 0 && (
                      <rect
                        x={bx}
                        y={by}
                        width={Math.max(4, barSlotW - 4)}
                        height={hScaled}
                        rx={2}
                        fill={b.low_pct >= 6 ? '#dc2626' : '#f87171'}
                      />
                    )}
                  </g>
                );
              })}
              <text
                x={padL - 8}
                y={padTop + plotH + 20}
                textAnchor="end"
                fill="#b91c1c"
                fontSize="10"
                fontWeight="600"
              >
                Lows
              </text>
            </g>
          )}

          {/* DAY REPLAY MODE */}
          {mode === 'replay' && currentReplay && (
            <g>
              {currentReplay.sleep_end_hour != null &&
                currentReplay.sleep_end_hour > 0 && (
                  <g>
                    <rect
                      x={padL}
                      y={8}
                      width={Math.max(16, xOfHour(currentReplay.sleep_end_hour) - padL)}
                      height={19}
                      rx={6}
                      fill="#eef2ff"
                      stroke="#c7d2fe"
                      strokeWidth="1"
                    />
                    <text
                      x={padL + 8}
                      y={21}
                      fill="#3730a3"
                      fontSize="10.5"
                      fontWeight="600"
                    >
                      Asleep until {formatHourClock(currentReplay.sleep_end_hour)}
                    </text>
                  </g>
                )}

              {currentReplay.walks.map((w, idx) => {
                const sHr = w.start_hour ?? w.hour ?? 0;
                const eHr =
                  w.end_hour ??
                  Math.min(24, sHr + Math.max(w.duration_min ?? 25, 12) / 60);
                const wx = xOfHour(sHr);
                const ww = Math.max(12, xOfHour(eHr) - wx);
                return (
                  <g key={`walk-${idx}`}>
                    <rect
                      x={wx}
                      y={padTop}
                      width={ww}
                      height={plotH}
                      fill="rgba(13, 148, 136, 0.11)"
                      stroke="rgba(13, 148, 136, 0.35)"
                      strokeWidth="1"
                    />
                    <rect
                      x={wx}
                      y={padTop}
                      width={ww}
                      height={17}
                      fill="#ccfbf1"
                    />
                    <text
                      x={wx + ww / 2}
                      y={padTop + 12}
                      textAnchor="middle"
                      fill="#115e59"
                      fontSize="10"
                      fontWeight="700"
                    >
                      Walk {w.steps ? `${Math.round(w.steps / 100) / 10}k` : ''}
                    </text>
                  </g>
                );
              })}

              {replayPath && (
                <path
                  d={replayPath}
                  fill="none"
                  stroke="#0f172a"
                  strokeWidth="2.3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}
              {replayCgmSorted.map(([hr, mg], idx) => {
                if (mg >= 70 && mg <= 180 && idx % 3 !== 0) return null;
                const fill =
                  mg < 70 ? '#dc2626' : mg > 180 ? '#d97706' : '#0f766e';
                return (
                  <circle
                    key={`pt-${idx}`}
                    cx={xOfHour(hr)}
                    cy={yOfMg(mg)}
                    r={mg < 70 || mg > 220 ? 3.2 : 2.3}
                    fill={fill}
                  />
                );
              })}

              {currentReplay.boluses.map((b, idx) => {
                const bx = xOfHour(b.hour);
                const bgY =
                  b.glucose_at_bolus != null
                    ? yOfMg(b.glucose_at_bolus)
                    : padTop + plotH - 16;
                const unlogged = !b.carbs_g || b.carbs_g <= 0;
                return (
                  <g key={`bolus-${idx}`}>
                    <line
                      x1={bx}
                      y1={bgY}
                      x2={bx}
                      y2={padTop + plotH + 14}
                      stroke={unlogged ? '#f59e0b' : '#0284c7'}
                      strokeDasharray="2 2"
                      strokeWidth="1"
                    />
                    <polygon
                      points={`${bx},${padTop + plotH + 6} ${bx + 5},${padTop + plotH + 13} ${bx},${padTop + plotH + 20} ${bx - 5},${padTop + plotH + 13}`}
                      fill={unlogged ? '#f59e0b' : '#0284c7'}
                      stroke="#ffffff"
                      strokeWidth="1"
                    />
                    <text
                      x={bx}
                      y={padTop + plotH + 31}
                      textAnchor="middle"
                      fill="#475569"
                      fontSize="9.5"
                      fontWeight="600"
                    >
                      {b.units.toFixed(1)}u
                    </text>
                  </g>
                );
              })}

              {currentReplay.bp_events.map((bp, idx) => {
                const bpx = xOfHour(bp.hour);
                const bpy = padTop + 26;
                const label = `${Math.round(bp.systolic_bp)}/${Math.round(
                  bp.diastolic_bp ?? 80
                )}`;
                return (
                  <g key={`bp-${idx}`}>
                    <rect
                      x={bpx - 30}
                      y={bpy - 10}
                      width={60}
                      height={18}
                      rx={9}
                      fill="#fef2f2"
                      stroke="#fecaca"
                      strokeWidth="1"
                    />
                    <text
                      x={bpx}
                      y={bpy + 2.5}
                      textAnchor="middle"
                      fill="#991b1b"
                      fontSize="9.5"
                      fontWeight="700"
                    >
                      BP {label}
                    </text>
                  </g>
                );
              })}
            </g>
          )}

          {/* Hover Crosshair */}
          {hoverHour !== null && (
            <line
              x1={xOfHour(hoverHour)}
              y1={padTop}
              x2={xOfHour(hoverHour)}
              y2={padTop + plotH}
              stroke="#64748b"
              strokeDasharray="3 3"
              strokeWidth="1.2"
            />
          )}
        </svg>

        {/* Clean Hover Readout */}
        <div className="mt-2 flex min-h-[34px] flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200/80 bg-white px-3.5 py-2 text-xs text-slate-600">
          {hoverHour === null ? (
            <span className="flex items-center gap-1.5 text-slate-500">
              <Activity className="h-3.5 w-3.5 text-teal-700" />
              Move cursor across the chart to inspect glucose, sleep, walks, and insulin at any hour.
            </span>
          ) : mode === 'agp' && hoveredAgpBucket ? (
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-bold text-slate-900">
                {String(hoveredAgpBucket.hour).padStart(2, '0')}:00–
                {String((hoveredAgpBucket.hour + 1) % 24).padStart(2, '0')}:00
              </span>
              <span>
                Usual: <strong className="text-slate-900">{hoveredAgpBucket.median_cgm.toFixed(0)} mg/dL</strong>
              </span>
              <span>
                Typical spread: {hoveredAgpBucket.p25.toFixed(0)}–{hoveredAgpBucket.p75.toFixed(0)}
              </span>
              <span className="font-semibold text-teal-800">
                In Range: {hoveredAgpBucket.tir_pct.toFixed(0)}%
              </span>
              <span
                className={
                  hoveredAgpBucket.low_pct >= 4
                    ? 'font-semibold text-red-700'
                    : 'text-slate-500'
                }
              >
                Below 70: {hoveredAgpBucket.low_pct.toFixed(1)}%
              </span>
            </div>
          ) : mode === 'replay' && hoveredReplayDetails ? (
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="font-bold text-slate-900">
                {formatHourClock(hoverHour)}
              </span>
              {hoveredReplayDetails.nearestCgm && (
                <span className="rounded-md bg-slate-100 px-2 py-0.5 font-semibold text-slate-900">
                  {hoveredReplayDetails.nearestCgm[1].toFixed(0)} mg/dL
                </span>
              )}
              {hoveredReplayDetails.inSleep && (
                <span className="rounded-md bg-indigo-50 px-2 py-0.5 font-medium text-indigo-800">
                  Asleep
                </span>
              )}
              {hoveredReplayDetails.activeWalk && (
                <span className="rounded-md bg-teal-50 px-2 py-0.5 font-medium text-teal-900">
                  Walk: {hoveredReplayDetails.activeWalk.steps ?? 0} steps ·{' '}
                  {hoveredReplayDetails.activeWalk.duration_min ?? 0} min
                  {hoveredReplayDetails.activeWalk.delta_cgm != null
                    ? ` (${hoveredReplayDetails.activeWalk.delta_cgm > 0 ? '+' : ''}${hoveredReplayDetails.activeWalk.delta_cgm.toFixed(0)} mg/dL)`
                    : ''}
                </span>
              )}
              {hoveredReplayDetails.nearBolus && (
                <span className="rounded-md bg-amber-50 px-2 py-0.5 font-medium text-amber-900">
                  Insulin: {hoveredReplayDetails.nearBolus.units.toFixed(1)} units
                  {hoveredReplayDetails.nearBolus.carbs_g &&
                  hoveredReplayDetails.nearBolus.carbs_g > 0
                    ? ` · ${hoveredReplayDetails.nearBolus.carbs_g.toFixed(0)}g carbs`
                    : ''}
                </span>
              )}
              {hoveredReplayDetails.nearBp && (
                <span className="rounded-md bg-red-50 px-2 py-0.5 font-medium text-red-900">
                  BP: {Math.round(hoveredReplayDetails.nearBp.systolic_bp)}/
                  {Math.round(hoveredReplayDetails.nearBp.diastolic_bp ?? 80)} mmHg
                </span>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
