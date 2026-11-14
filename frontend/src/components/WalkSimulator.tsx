import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Footprints,
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
        setError(err instanceof Error ? err.message : 'Could not run estimate');
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
      <span className="inline-flex items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-semibold text-red-800">
        <ShieldAlert className="h-3.5 w-3.5 text-red-600" />
        Higher Low-Sugar Risk
      </span>
    ) : risk === 'moderate' ? (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
        <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
        Moderate Low-Sugar Risk
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-800">
        <CheckCircle2 className="h-3.5 w-3.5 text-teal-600" />
        Comfortable Safe Range
      </span>
    );

  const pctOfMg = (mg: number) =>
    Math.max(2, Math.min(98, ((mg - 40) / (320 - 40)) * 100));

  return (
    <div className="rounded-3xl border border-slate-200/90 bg-white p-6 shadow-xs sm:p-7">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 border border-teal-100">
            <Footprints className="h-5 w-5" />
          </span>
          <div>
            <h3 className="text-base font-bold text-slate-900">
              Personal Walk Estimate
            </h3>
            <p className="text-xs text-slate-500">
              {walkModel && walkModel.sample_count >= 10
                ? `Personalized from ${walkModel.sample_count} of your past logged walks`
                : 'Estimates how meal insulin and walking combine'}
            </p>
          </div>
        </div>
      </div>

      {/* 3 Friendly Sliders */}
      <div className="mt-5 space-y-5 rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-5">
        {/* Starting Blood Sugar */}
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="sim-start-cgm" className="text-sm font-semibold text-slate-700">
              Current blood sugar
            </label>
            <span className="text-base font-bold text-slate-900">
              {startCgm}{' '}
              <span className="text-xs font-medium text-slate-500">mg/dL</span>
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
          <div className="mt-1.5 flex justify-between text-xs text-slate-400">
            <span>70 mg/dL</span>
            <span>180 (top of target)</span>
            <span>320 mg/dL</span>
          </div>
        </div>

        {/* Active Meal Insulin */}
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="sim-active-iob" className="text-sm font-semibold text-slate-700">
              Active meal insulin (recent bolus)
            </label>
            <span className="text-base font-bold text-slate-900">
              {activeIob.toFixed(1)}{' '}
              <span className="text-xs font-medium text-slate-500">units</span>
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
          <div className="mt-1.5 flex justify-between text-xs text-slate-400">
            <span>0 units (none active)</span>
            <span>2 units</span>
            <span>6 units (right after meal)</span>
          </div>
        </div>

        {/* Planned Walk Length */}
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="sim-planned-steps" className="text-sm font-semibold text-slate-700">
              Planned walk duration
            </label>
            <span className="text-base font-bold text-slate-900">
              ~{approxMinutes} min{' '}
              <span className="text-xs font-medium text-slate-500">
                ({plannedSteps.toLocaleString()} steps)
              </span>
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
          <div className="mt-1.5 flex justify-between text-xs text-slate-400">
            <span>5 min stroll</span>
            <span>25 min brisk walk</span>
            <span>75 min long walk</span>
          </div>
        </div>
      </div>

      {/* Outcome Card */}
      <div className="mt-5 rounded-2xl border border-slate-200/90 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div>
              <div className="text-xs font-medium text-slate-500">Expected change</div>
              <div className="mt-0.5 text-2xl font-extrabold text-slate-900">
                {delta > 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0)}{' '}
                <span className="text-xs font-medium text-slate-500">mg/dL</span>
              </div>
            </div>

            <ArrowRight className="h-4 w-4 text-slate-400" />

            <div>
              <div className="text-xs font-medium text-slate-500">
                Estimated after walk
              </div>
              <div
                className={`mt-0.5 text-2xl font-extrabold ${
                  endCgm < 80
                    ? 'text-red-700'
                    : endCgm <= 180
                      ? 'text-teal-700'
                      : 'text-amber-700'
                }`}
              >
                {endCgm.toFixed(0)}{' '}
                <span className="text-xs font-medium text-slate-500">mg/dL</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {loading && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
            {riskBadge}
          </div>
        </div>

        {/* Visual Range Meter */}
        <div className="relative mt-4 h-3.5 w-full overflow-hidden rounded-full bg-slate-100 ring-1 ring-slate-200">
          <div
            className="absolute top-0 bottom-0 left-0 bg-red-200/80"
            style={{ width: `${pctOfMg(70)}%` }}
          />
          <div
            className="absolute top-0 bottom-0 bg-teal-200/75"
            style={{
              left: `${pctOfMg(70)}%`,
              width: `${pctOfMg(180) - pctOfMg(70)}%`,
            }}
          />
          <div
            className="absolute top-0 bottom-0 w-1.5 rounded-full bg-slate-400"
            style={{ left: `calc(${pctOfMg(startCgm)}% - 3px)` }}
            title={`Start: ${startCgm} mg/dL`}
          />
          <div
            className={`absolute top-0 bottom-0 w-3 rounded-full ring-2 ring-white ${
              endCgm < 80
                ? 'bg-red-600'
                : endCgm <= 180
                  ? 'bg-teal-700'
                  : 'bg-amber-500'
            }`}
            style={{ left: `calc(${pctOfMg(endCgm)}% - 6px)` }}
            title={`Estimated finish: ${endCgm.toFixed(0)} mg/dL`}
          />
        </div>

        {error ? (
          <p className="mt-3 text-xs text-red-700">{error}</p>
        ) : (
          result?.recommendation && (
            <p className="mt-3.5 text-sm leading-relaxed text-slate-700">
              {result.recommendation}
            </p>
          )
        )}
      </div>
    </div>
  );
};
