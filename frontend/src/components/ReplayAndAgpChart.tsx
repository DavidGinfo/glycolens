import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Footprints,
  HeartPulse,
  Layers,
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

  const currentReplay: DayReplay | null =
    replays[selectedReplayIdx] ?? replays[0] ?? null;

  // SVG coordinate system
  const W = 860;
  const H = 350;
  const padL = 48;
  const padR = 22;
  const padTop = 34;
  const padBot = 58; // Extra room at bottom for <70 hypo bars & x-axis ticks
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBot;

  const yMin = 40;
  const yMax = 320;

  const xOfHour = (hr: number) => padL + (Math.max(0, Math.min(24, hr)) / 24) * plotW;
  const yOfMg = (mg: number) => {
    const clamped = Math.max(yMin, Math.min(yMax, mg));
    return padTop + (1 - (clamped - yMin) / (yMax - yMin)) * plotH;
  };

  // Build sorted AGP buckets (0..23 + wrap point at 24 for full width)
  const sortedAgp = useMemo(() => {
    return [...hourlyAgp].sort((a, b) => a.hour - b.hour);
  }, [hourlyAgp]);

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

  // Replay CGM path
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

  // Hover tooltip computations
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
    <div className="flex flex-col rounded-2xl border border-slate-800/90 bg-slate-900/70 p-5 shadow-xl shadow-black/25">
      {/* Header & Mode Switcher */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-sky-500/15 text-sky-400 ring-1 ring-sky-500/30">
              <Layers className="h-4 w-4" />
            </span>
            <h3 className="text-base font-semibold text-slate-100">
              24h Multimodal Day Replay & Circadian AGP
            </h3>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            {mode === 'replay'
              ? 'Aligned CGM trajectory overlaid with Watch walks, overnight sleep window, pump boluses, and blood pressure.'
              : `${Math.round(daysCovered)}-day Ambulatory Glucose Profile (Median, IQR P25–P75, P10–P90, and hourly <70 mg/dL hypo bars).`}
          </p>
        </div>

        <div className="inline-flex self-start rounded-xl border border-slate-800 bg-slate-950/80 p-1">
          <button
            type="button"
            onClick={() => setMode('replay')}
            className={`cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              mode === 'replay'
                ? 'bg-sky-500/20 text-sky-300 ring-1 ring-sky-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            24h Day Replays ({replays.length})
          </button>
          <button
            type="button"
            onClick={() => setMode('agp')}
            className={`cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              mode === 'agp'
                ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {Math.round(daysCovered)}d Circadian AGP
          </button>
        </div>
      </div>

      {/* Day Replay Selector Pills */}
      {mode === 'replay' && replays.length > 0 && (
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          {replays.map((rep, idx) => {
            const selected = idx === selectedReplayIdx;
            return (
              <button
                key={`${rep.date}-${idx}`}
                type="button"
                onClick={() => setSelectedReplayIdx(idx)}
                className={`cursor-pointer rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
                  selected
                    ? 'border-sky-500/50 bg-sky-500/15 text-sky-200'
                    : 'border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                }`}
              >
                {rep.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Legend Strip */}
      <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-slate-400">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500/25 ring-1 ring-emerald-500/50" />
          Target 70–180 mg/dL
        </span>
        {mode === 'replay' ? (
          <>
            <span className="inline-flex items-center gap-1.5">
              <Footprints className="h-3.5 w-3.5 text-emerald-400" />
              Watch Walk Band
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Moon className="h-3.5 w-3.5 text-indigo-400" />
              Overnight Sleep Bar
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Syringe className="h-3.5 w-3.5 text-amber-400" />
              Insulin Bolus (U / Carbs)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <HeartPulse className="h-3.5 w-3.5 text-rose-400" />
              BP Reading
            </span>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0.5 w-4 bg-sky-400" />
              Median CGM
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-3 rounded-sm bg-sky-400/35" />
              IQR (P25–P75)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-3 rounded-sm bg-sky-400/15" />
              P10–P90
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-rose-500/80" />
              Hourly &lt;70 mg/dL Hypo %
            </span>
          </>
        )}
      </div>

      {/* Main Interactive SVG Canvas */}
      <div className="relative mt-3 rounded-xl border border-slate-800/80 bg-slate-950/85 p-2">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverHour(null)}
          className="w-full cursor-crosshair select-none"
          role="img"
          aria-label="24-hour glucose and multimodal overlay chart"
        >
          {/* Target In-Range Band (70 - 180 mg/dL) */}
          <rect
            x={padL}
            y={yOfMg(180)}
            width={plotW}
            height={yOfMg(70) - yOfMg(180)}
            fill="rgba(16, 185, 129, 0.07)"
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
                      ? 'rgba(244, 63, 94, 0.45)'
                      : val === 180
                        ? 'rgba(16, 185, 129, 0.40)'
                        : 'rgba(51, 65, 85, 0.35)'
                  }
                  strokeDasharray={isBoundary ? '4 4' : '2 4'}
                  strokeWidth={isBoundary ? 1.1 : 0.8}
                />
                <text
                  x={padL - 8}
                  y={y + 4}
                  textAnchor="end"
                  fill={
                    val === 70 ? '#fda4af' : val === 180 ? '#6ee7b7' : '#94a3b8'
                  }
                  fontSize="10.5"
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
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
                  stroke="rgba(30, 41, 59, 0.7)"
                  strokeWidth="0.8"
                />
                <text
                  x={x}
                  y={H - 8}
                  textAnchor="middle"
                  fill="#94a3b8"
                  fontSize="10.5"
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  {hr === 24 ? '24:00' : `${String(hr).padStart(2, '0')}:00`}
                </text>
              </g>
            );
          })}

          {/* ================= AGP MODE ================= */}
          {mode === 'agp' && agpPaths && (
            <g>
              <polygon
                points={agpPaths.polyP10P90}
                fill="rgba(56, 189, 248, 0.11)"
              />
              <polygon
                points={agpPaths.polyP25P75}
                fill="rgba(56, 189, 248, 0.24)"
              />
              <path
                d={agpPaths.medianLine}
                fill="none"
                stroke="#38bdf8"
                strokeWidth="2.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />

              {/* Bottom Hourly <70 mg/dL Hypo Bars */}
              {sortedAgp.map((b) => {
                const barSlotW = plotW / 24;
                const bx = xOfHour(b.hour) + 2;
                const maxHypoH = 26;
                const hScaled = Math.min(
                  maxHypoH,
                  Math.max(b.low_pct > 0 ? 2.5 : 0, (b.low_pct / 20) * maxHypoH)
                );
                const by = padTop + plotH + 28 - hScaled;
                return (
                  <g key={`hypo-${b.hour}`}>
                    <rect
                      x={bx}
                      y={padTop + plotH + 6}
                      width={Math.max(4, barSlotW - 4)}
                      height={22}
                      rx={2}
                      fill="rgba(15, 23, 42, 0.85)"
                    />
                    {hScaled > 0 && (
                      <rect
                        x={bx}
                        y={by}
                        width={Math.max(4, barSlotW - 4)}
                        height={hScaled}
                        rx={2}
                        fill={b.low_pct >= 6 ? '#f43f5e' : 'rgba(244, 63, 94, 0.65)'}
                      />
                    )}
                  </g>
                );
              })}
              <text
                x={padL - 8}
                y={padTop + plotH + 21}
                textAnchor="end"
                fill="#fb7185"
                fontSize="9.5"
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                &lt;70%
              </text>
            </g>
          )}

          {/* ================= DAY REPLAY MODE ================= */}
          {mode === 'replay' && currentReplay && (
            <g>
              {/* Top Sleep Window Bar */}
              {currentReplay.sleep_end_hour != null &&
                currentReplay.sleep_end_hour > 0 && (
                  <g>
                    <rect
                      x={padL}
                      y={8}
                      width={Math.max(16, xOfHour(currentReplay.sleep_end_hour) - padL)}
                      height={18}
                      rx={6}
                      fill="rgba(99, 102, 241, 0.25)"
                      stroke="rgba(129, 140, 248, 0.55)"
                      strokeWidth="1"
                    />
                    <text
                      x={padL + 8}
                      y={20}
                      fill="#c7d2fe"
                      fontSize="10"
                      fontWeight="600"
                    >
                      Sleep until {formatHourClock(currentReplay.sleep_end_hour)}
                    </text>
                  </g>
                )}

              {/* Green Watch Walk Vertical Bands */}
              {currentReplay.walks.map((w, idx) => {
                const sHr = w.start_hour ?? w.hour ?? 0;
                const eHr =
                  w.end_hour ?? Math.min(24, sHr + Math.max(w.duration_min ?? 25, 12) / 60);
                const wx = xOfHour(sHr);
                const ww = Math.max(10, xOfHour(eHr) - wx);
                return (
                  <g key={`walk-${idx}`}>
                    <rect
                      x={wx}
                      y={padTop}
                      width={ww}
                      height={plotH}
                      fill="rgba(16, 185, 129, 0.16)"
                      stroke="rgba(16, 185, 129, 0.5)"
                      strokeWidth="1"
                    />
                    <rect
                      x={wx}
                      y={padTop}
                      width={ww}
                      height={16}
                      fill="rgba(16, 185, 129, 0.30)"
                    />
                    <text
                      x={wx + ww / 2}
                      y={padTop + 11.5}
                      textAnchor="middle"
                      fill="#a7f3d0"
                      fontSize="9.5"
                      fontWeight="700"
                    >
                      Walk {w.steps ? `${Math.round(w.steps / 100) / 10}k` : ''}
                    </text>
                  </g>
                );
              })}

              {/* CGM Line + Colored Dots */}
              {replayPath && (
                <path
                  d={replayPath}
                  fill="none"
                  stroke="#38bdf8"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}
              {replayCgmSorted.map(([hr, mg], idx) => {
                if (mg >= 70 && mg <= 180 && idx % 3 !== 0) return null;
                const fill =
                  mg < 70 ? '#f43f5e' : mg > 180 ? '#f59e0b' : '#34d399';
                return (
                  <circle
                    key={`pt-${idx}`}
                    cx={xOfHour(hr)}
                    cy={yOfMg(mg)}
                    r={mg < 70 || mg > 220 ? 3.2 : 2.2}
                    fill={fill}
                  />
                );
              })}

              {/* Bolus Markers */}
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
                      stroke={unlogged ? 'rgba(245, 158, 11, 0.5)' : 'rgba(56, 189, 248, 0.5)'}
                      strokeDasharray="2 2"
                      strokeWidth="1"
                    />
                    <polygon
                      points={`${bx},${padTop + plotH + 6} ${bx + 5},${padTop + plotH + 13} ${bx},${padTop + plotH + 20} ${bx - 5},${padTop + plotH + 13}`}
                      fill={unlogged ? '#f59e0b' : '#38bdf8'}
                      stroke="#020617"
                      strokeWidth="1"
                    />
                    <text
                      x={bx}
                      y={padTop + plotH + 31}
                      textAnchor="middle"
                      fill={unlogged ? '#fcd34d' : '#bae6fd'}
                      fontSize="9"
                      fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                    >
                      {b.units.toFixed(1)}U
                    </text>
                  </g>
                );
              })}

              {/* BP Event Badges */}
              {currentReplay.bp_events.map((bp, idx) => {
                const bpx = xOfHour(bp.hour);
                const bpy = padTop + 26;
                const label = `${Math.round(bp.systolic_bp)}/${Math.round(bp.diastolic_bp ?? 80)}`;
                return (
                  <g key={`bp-${idx}`}>
                    <rect
                      x={bpx - 28}
                      y={bpy - 10}
                      width={56}
                      height={18}
                      rx={9}
                      fill="rgba(244, 63, 94, 0.2)"
                      stroke="rgba(251, 113, 133, 0.65)"
                      strokeWidth="1"
                    />
                    <text
                      x={bpx}
                      y={bpy + 2.5}
                      textAnchor="middle"
                      fill="#fecdd3"
                      fontSize="9.5"
                      fontWeight="700"
                      fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
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
              stroke="rgba(226, 232, 240, 0.6)"
              strokeDasharray="3 3"
              strokeWidth="1.2"
            />
          )}
        </svg>

        {/* Interactive Hover Readout Strip */}
        <div className="mt-1 flex min-h-[34px] flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800/80 bg-slate-900/90 px-3 py-1.5 text-xs">
          {hoverHour === null ? (
            <span className="flex items-center gap-1.5 text-slate-400">
              <Activity className="h-3.5 w-3.5 text-sky-400" />
              Hover anywhere across the 24h timeline to inspect synchronized CGM, sleep, walk, bolus, and BP telemetry.
            </span>
          ) : mode === 'agp' && hoveredAgpBucket ? (
            <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]">
              <span className="font-semibold text-sky-300">
                {String(hoveredAgpBucket.hour).padStart(2, '0')}:00–
                {String((hoveredAgpBucket.hour + 1) % 24).padStart(2, '0')}:00
              </span>
              <span className="text-slate-200">
                Median: <strong>{hoveredAgpBucket.median_cgm.toFixed(0)} mg/dL</strong>
              </span>
              <span className="text-slate-300">
                IQR: {hoveredAgpBucket.p25.toFixed(0)}–{hoveredAgpBucket.p75.toFixed(0)}
              </span>
              <span className="text-slate-400">
                P10–P90: {hoveredAgpBucket.p10.toFixed(0)}–{hoveredAgpBucket.p90.toFixed(0)}
              </span>
              <span className="text-emerald-300">
                TIR: {hoveredAgpBucket.tir_pct.toFixed(0)}%
              </span>
              <span
                className={
                  hoveredAgpBucket.low_pct >= 4
                    ? 'font-semibold text-rose-400'
                    : 'text-slate-400'
                }
              >
                &lt;70 Hypo: {hoveredAgpBucket.low_pct.toFixed(1)}%
              </span>
            </div>
          ) : mode === 'replay' && hoveredReplayDetails ? (
            <div className="flex flex-wrap items-center gap-2.5 text-[11px]">
              <span className="font-mono font-semibold text-sky-300">
                {formatHourClock(hoverHour)}
              </span>
              {hoveredReplayDetails.nearestCgm && (
                <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-slate-100">
                  CGM:{' '}
                  <strong>
                    {hoveredReplayDetails.nearestCgm[1].toFixed(0)} mg/dL
                  </strong>
                </span>
              )}
              {hoveredReplayDetails.inSleep && (
                <span className="rounded bg-indigo-500/20 px-2 py-0.5 text-indigo-200">
                  Asleep
                </span>
              )}
              {hoveredReplayDetails.activeWalk && (
                <span className="rounded bg-emerald-500/20 px-2 py-0.5 font-mono text-emerald-200">
                  Walk: {hoveredReplayDetails.activeWalk.steps ?? 0} steps ·{' '}
                  {hoveredReplayDetails.activeWalk.duration_min ?? 0}m
                  {hoveredReplayDetails.activeWalk.delta_cgm != null
                    ? ` (Δ ${hoveredReplayDetails.activeWalk.delta_cgm > 0 ? '+' : ''}${hoveredReplayDetails.activeWalk.delta_cgm.toFixed(0)} mg/dL, IOB ${(hoveredReplayDetails.activeWalk.iob_at_start ?? 0).toFixed(1)}U)`
                    : ''}
                </span>
              )}
              {hoveredReplayDetails.nearBolus && (
                <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-amber-200">
                  Bolus: {hoveredReplayDetails.nearBolus.units.toFixed(2)}U ·{' '}
                  {hoveredReplayDetails.nearBolus.carbs_g &&
                  hoveredReplayDetails.nearBolus.carbs_g > 0
                    ? `${hoveredReplayDetails.nearBolus.carbs_g.toFixed(0)}g carbs`
                    : '0g carbs logged'}
                </span>
              )}
              {hoveredReplayDetails.nearBp && (
                <span className="rounded bg-rose-500/20 px-2 py-0.5 font-mono text-rose-200">
                  BP: {Math.round(hoveredReplayDetails.nearBp.systolic_bp)}/
                  {Math.round(hoveredReplayDetails.nearBp.diastolic_bp ?? 80)} mmHg
                  {hoveredReplayDetails.nearBp.pulse_bpm
                    ? ` · ${Math.round(hoveredReplayDetails.nearBp.pulse_bpm)} bpm`
                    : ''}
                </span>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
