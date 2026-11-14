import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Footprints,
  Gauge,
  Loader2,
  ShieldAlert,
} from 'lucide-react';
import { simulateWalkRx } from '../api';
import type { WalkModelWeights, WalkSimulationResponse } from '../types';

interface WalkSimulatorProps {
  sessionId: string | null;
  walkModel?: WalkModelWeights | null;
}

export const WalkSimulator: React.FC<WalkSimulatorProps> = ({
  sessionId,
  walkModel,
}) => {
  const [startCgm, setStartCgm] = useState<number>(210);
  const [activeIob, setActiveIob] = useState<number>(1.8);
  const [plannedSteps, setPlannedSteps] = useState<number>(2500);

  const [result, setResult] = useState<WalkSimulationResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Debounced 120ms live call to POST /api/sessions/{id}/simulate/walk
  useEffect(() => {
    if (!sessionId) return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await simulateWalkRx(
          sessionId,
          {
            start_cgm: startCgm,
            active_iob: activeIob,
            planned_steps: plannedSteps,
          },
          controller.signal
        );
        setResult(res);
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return;
        }
        setError(err instanceof Error ? err.message : 'Simulation failed');
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }, 120);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [sessionId, startCgm, activeIob, plannedSteps]);

  const approxMinutes = Math.max(5, Math.round(plannedSteps / 98));
  const delta = result?.predicted_delta_mg_dl ?? 0;
  const endCgm = result?.predicted_end_cgm ?? startCgm;
  const risk = result?.hypo_risk_level ?? 'low';

  const riskBadge =
    risk === 'high' ? (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-500/45 bg-rose-500/15 px-3 py-1 text-xs font-semibold text-rose-300">
        <ShieldAlert className="h-3.5 w-3.5" />
        High Hypo Risk
      </span>
    ) : risk === 'moderate' ? (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/45 bg-amber-500/15 px-3 py-1 text-xs font-semibold text-amber-300">
        <AlertTriangle className="h-3.5 w-3.5" />
        Moderate Buffer Risk
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/45 bg-emerald-500/15 px-3 py-1 text-xs font-semibold text-emerald-300">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Low Hypo Risk · Safe Zone
      </span>
    );

  // Position markers on 40..320 scale
  const pctOfMg = (mg: number) =>
    Math.max(2, Math.min(98, ((mg - 40) / (320 - 40)) * 100));

  return (
    <div className="flex flex-col justify-between rounded-2xl border border-slate-800/90 bg-slate-900/70 p-5 shadow-xl shadow-black/25">
      <div>
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Footprints className="h-4 w-4" />
            </span>
            <div>
              <h3 className="text-base font-semibold text-slate-100">
                Personal Walk Rx Simulator
              </h3>
              <p className="text-xs text-slate-400">
                Ridge model calibrated on your own CGM + Watch walks (Steps × Active IOB synergy)
              </p>
            </div>
          </div>

          <span className="inline-flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/75 px-2.5 py-1 font-mono text-[11px] text-slate-300">
            <Gauge className="h-3.5 w-3.5 text-emerald-400" />
            {walkModel && walkModel.sample_count >= 10
              ? `n=${walkModel.sample_count} walks · r=${(result?.model_r ?? walkModel.holdout_r).toFixed(2)}`
              : 'GLUT4 + IOB calibrated'}
          </span>
        </div>

        {/* 3 Interactive Sliders */}
        <div className="mt-4 space-y-3.5 rounded-xl border border-slate-800/80 bg-slate-950/65 p-4">
          {/* Start CGM */}
          <div>
            <div className="mb-1.5 flex items-center justify-between text-xs">
              <label htmlFor="sim-start-cgm" className="font-medium text-slate-300">
                Starting CGM
              </label>
              <span className="font-mono font-semibold text-sky-300">
                {startCgm} mg/dL
              </span>
            </div>
            <input
              id="sim-start-cgm"
              type="range"
              min={70}
              max={320}
              step={5}
              value={startCgm}
              onChange={(e) => setStartCgm(Number(e.target.value))}
              className="glyco-slider w-full"
            />
            <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
              <span>70 mg/dL</span>
              <span>180 Target Top</span>
              <span>320 mg/dL</span>
            </div>
          </div>

          {/* Active Bolus IOB */}
          <div>
            <div className="mb-1.5 flex items-center justify-between text-xs">
              <label htmlFor="sim-active-iob" className="font-medium text-slate-300">
                Active Bolus IOB (Insulin on Board)
              </label>
              <span
                className={`font-mono font-semibold ${
                  activeIob >= 2.0 ? 'text-amber-300' : 'text-emerald-300'
                }`}
              >
                {activeIob.toFixed(1)} U
              </span>
            </div>
            <input
              id="sim-active-iob"
              type="range"
              min={0}
              max={6}
              step={0.1}
              value={activeIob}
              onChange={(e) => setActiveIob(Number(e.target.value))}
              className="glyco-slider w-full"
            />
            <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
              <span>0.0 U (Fasted)</span>
              <span>2.0 U</span>
              <span>6.0 U (Recent Meal Bolus)</span>
            </div>
          </div>

          {/* Planned Steps */}
          <div>
            <div className="mb-1.5 flex items-center justify-between text-xs">
              <label htmlFor="sim-planned-steps" className="font-medium text-slate-300">
                Planned Walk Steps
              </label>
              <span className="font-mono font-semibold text-emerald-300">
                {plannedSteps.toLocaleString()} steps (~{approxMinutes} min)
              </span>
            </div>
            <input
              id="sim-planned-steps"
              type="range"
              min={500}
              max={7500}
              step={100}
              value={plannedSteps}
              onChange={(e) => setPlannedSteps(Number(e.target.value))}
              className="glyco-slider w-full"
            />
            <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
              <span>500 (5m)</span>
              <span>2,500 (25m brisk)</span>
              <span>7,500 (75m)</span>
            </div>
          </div>
        </div>

        {/* Predicted Outcome Readout */}
        <div className="mt-4 rounded-xl border border-slate-800/90 bg-slate-950/85 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-slate-400">
                  Predicted ΔCGM
                </div>
                <div className="font-mono text-2xl font-bold text-emerald-400">
                  {delta > 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0)}{' '}
                  <span className="text-xs font-normal text-slate-400">mg/dL</span>
                </div>
              </div>

              <ArrowRight className="h-4 w-4 text-slate-600" />

              <div>
                <div className="text-[11px] uppercase tracking-wider text-slate-400">
                  Projected End Glucose
                </div>
                <div
                  className={`font-mono text-2xl font-bold ${
                    endCgm < 80
                      ? 'text-rose-400'
                      : endCgm <= 180
                        ? 'text-emerald-300'
                        : 'text-amber-300'
                  }`}
                >
                  {endCgm.toFixed(0)}{' '}
                  <span className="text-xs font-normal text-slate-400">mg/dL</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
              {riskBadge}
            </div>
          </div>

          {/* Visual Range Meter */}
          <div className="relative mt-3.5 h-3 w-full overflow-hidden rounded-full bg-slate-900 ring-1 ring-slate-800">
            {/* <70 Hypo zone */}
            <div
              className="absolute top-0 bottom-0 left-0 bg-rose-500/25"
              style={{ width: `${pctOfMg(70)}%` }}
            />
            {/* 70-180 Target zone */}
            <div
              className="absolute top-0 bottom-0 bg-emerald-500/25"
              style={{
                left: `${pctOfMg(70)}%`,
                width: `${pctOfMg(180) - pctOfMg(70)}%`,
              }}
            />
            {/* Start & End markers */}
            <div
              className="absolute top-0 bottom-0 w-1.5 rounded-full bg-slate-300"
              style={{ left: `calc(${pctOfMg(startCgm)}% - 3px)` }}
              title={`Start: ${startCgm} mg/dL`}
            />
            <div
              className={`absolute top-0 bottom-0 w-2.5 rounded-full ring-2 ring-slate-950 ${
                endCgm < 80
                  ? 'bg-rose-400'
                  : endCgm <= 180
                    ? 'bg-emerald-400'
                    : 'bg-amber-400'
              }`}
              style={{ left: `calc(${pctOfMg(endCgm)}% - 5px)` }}
              title={`Projected End: ${endCgm.toFixed(0)} mg/dL`}
            />
          </div>

          {error ? (
            <p className="mt-3 text-xs text-rose-400">{error}</p>
          ) : (
            result?.recommendation && (
              <p className="mt-3 text-xs leading-relaxed text-slate-200">
                {result.recommendation}
              </p>
            )
          )}
        </div>
      </div>
    </div>
  );
};
