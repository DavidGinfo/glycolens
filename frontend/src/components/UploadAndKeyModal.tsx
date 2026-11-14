import React, { useState } from 'react';
import {
  CheckCircle2,
  FileArchive,
  KeyRound,
  Loader2,
  Lock,
  Upload,
  X,
} from 'lucide-react';
import { saveGeminiApiKey, uploadExportsSession } from '../api';
import type { EngineHealthStatus, SessionBundleResponse } from '../types';

export type ModalMode = 'upload' | 'apikey' | null;

interface UploadAndKeyModalProps {
  mode: ModalMode;
  onClose: () => void;
  onUploadSuccess: (bundle: SessionBundleResponse) => void;
  onApiKeySaved: (health: EngineHealthStatus) => void;
}

export const UploadAndKeyModal: React.FC<UploadAndKeyModalProps> = ({
  mode,
  onClose,
  onUploadSuccess,
  onApiKeySaved,
}) => {
  const [glookoFile, setGlookoFile] = useState<File | null>(null);
  const [watchFile, setWatchFile] = useState<File | null>(null);
  const [datasetTitle, setDatasetTitle] = useState<string>('My Personal Export');

  const [apiKeyInput, setApiKeyInput] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  if (!mode) return null;

  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!glookoFile) {
      setError('Please select a Glooko CGM/Pump export .zip archive.');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const bundle = await uploadExportsSession(glookoFile, watchFile, datasetTitle);
      onUploadSuccess(bundle);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to parse uploaded .zip');
    } finally {
      setSubmitting(false);
    }
  };

  const handleApiKeySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKeyInput.trim()) {
      setError('Please enter a valid Gemini API key.');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const health = await saveGeminiApiKey(apiKeyInput.trim());
      setApiKeyInput('');
      onApiKeySaved(health);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to configure API key');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="relative w-full max-w-lg rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl shadow-black/60">
        <button
          type="button"
          onClick={onClose}
          className="absolute top-4 right-4 cursor-pointer rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
          aria-label="Close modal"
        >
          <X className="h-4 w-4" />
        </button>

        {mode === 'upload' ? (
          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="flex items-center gap-2.5">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
                <Upload className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-semibold text-slate-100">
                  Upload Multimodal Health Exports (.zip)
                </h2>
                <p className="text-xs text-slate-400">
                  Aligns Glooko CGM + insulin boluses with Samsung Galaxy Watch workouts, sleep &amp; BP.
                </p>
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-300">
                Session Label
              </label>
              <input
                type="text"
                value={datasetTitle}
                onChange={(e) => setDatasetTitle(e.target.value)}
                className="w-full rounded-xl border border-slate-800 bg-slate-950 px-3.5 py-2 text-xs text-slate-100 outline-none focus:border-emerald-500/50"
                placeholder="My Personal Export"
              />
            </div>

            {/* Glooko ZIP */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-200">
                1. Glooko Export Archive (.zip) <span className="text-rose-400">*</span>
              </label>
              <label className="flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-slate-700 bg-slate-950/70 px-4 py-3 transition hover:border-emerald-500/50">
                <div className="flex items-center gap-2.5 text-xs">
                  <FileArchive className="h-4 w-4 text-emerald-400" />
                  <span className="text-slate-300">
                    {glookoFile ? glookoFile.name : 'Choose Glooko export_*.zip'}
                  </span>
                </div>
                {glookoFile && <CheckCircle2 className="h-4 w-4 text-emerald-400" />}
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setGlookoFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
              </label>
            </div>

            {/* Samsung Watch ZIP */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-200">
                2. Samsung Health / Watch Export (.zip){' '}
                <span className="text-slate-400">(Optional, unlocks Activity/Sleep/BP)</span>
              </label>
              <label className="flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-slate-700 bg-slate-950/70 px-4 py-3 transition hover:border-sky-500/50">
                <div className="flex items-center gap-2.5 text-xs">
                  <FileArchive className="h-4 w-4 text-sky-400" />
                  <span className="text-slate-300">
                    {watchFile ? watchFile.name : 'Choose watch_data*.zip (optional)'}
                  </span>
                </div>
                {watchFile && <CheckCircle2 className="h-4 w-4 text-sky-400" />}
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setWatchFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
              </label>
            </div>

            <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2 text-[11px] text-slate-400">
              <Lock className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              Parsed strictly locally in ephemeral memory; raw archives are never persisted to git.
            </div>

            {error && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="cursor-pointer rounded-xl border border-slate-700 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !glookoFile}
                className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Ingesting &amp; Aligning...
                  </>
                ) : (
                  <>
                    <Upload className="h-3.5 w-3.5" />
                    Align &amp; Run Swarm
                  </>
                )}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleApiKeySubmit} className="space-y-4">
            <div className="flex items-center gap-2.5">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30">
                <KeyRound className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-semibold text-slate-100">
                  Activate Gemini Parallel Agent Swarm
                </h2>
                <p className="text-xs text-slate-400">
                  Upgrades Statistical Mode to live Gemini multi-agent hypothesis generation + synthesis, then immediately re-runs discovery.
                </p>
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">
                Google AI Studio `GEMINI_API_KEY`
              </label>
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder="AIzaSy..."
                autoFocus
                className="w-full rounded-xl border border-slate-800 bg-slate-950 px-3.5 py-2.5 font-mono text-xs text-slate-100 outline-none focus:border-amber-500/60"
              />
            </div>

            {error && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="cursor-pointer rounded-xl border border-slate-700 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
              >
                Stay in Statistical Mode
              </button>
              <button
                type="submit"
                disabled={submitting || !apiKeyInput.trim()}
                className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-amber-400 px-4 py-2 text-xs font-semibold text-slate-950 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Activating...
                  </>
                ) : (
                  <>
                    <KeyRound className="h-3.5 w-3.5" />
                    Save Key &amp; Re-Run Swarm
                  </>
                )}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};
