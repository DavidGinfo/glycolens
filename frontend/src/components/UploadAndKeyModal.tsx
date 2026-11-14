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
      setError('Please select a Glooko export .zip archive.');
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
      setError(err instanceof Error ? err.message : 'Failed to save API key');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-xs">
      <div className="relative w-full max-w-lg rounded-3xl border border-slate-200 bg-white p-6 shadow-xl">
        <button
          type="button"
          onClick={onClose}
          className="absolute top-4 right-4 cursor-pointer rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          aria-label="Close modal"
        >
          <X className="h-4 w-4" />
        </button>

        {mode === 'upload' ? (
          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 border border-teal-100">
                <Upload className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-bold text-slate-900">
                  Import Health Export (.zip)
                </h2>
                <p className="text-xs text-slate-500">
                  Connect your Glooko export alongside an optional Smartwatch export.
                </p>
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">
                Label
              </label>
              <input
                type="text"
                value={datasetTitle}
                onChange={(e) => setDatasetTitle(e.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 outline-none focus:border-teal-600"
                placeholder="My Personal Export"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">
                1. Glooko Export (.zip) <span className="text-red-600">*</span>
              </label>
              <label className="flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-slate-300 bg-[#F8FAFC] px-4 py-3 transition hover:border-teal-600">
                <div className="flex items-center gap-2.5 text-xs">
                  <FileArchive className="h-4 w-4 text-teal-700" />
                  <span className="font-medium text-slate-700">
                    {glookoFile ? glookoFile.name : 'Choose Glooko .zip file'}
                  </span>
                </div>
                {glookoFile && <CheckCircle2 className="h-4 w-4 text-teal-600" />}
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setGlookoFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
              </label>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">
                2. Smartwatch / Samsung Health Export (.zip){' '}
                <span className="font-normal text-slate-400">(Optional)</span>
              </label>
              <label className="flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-slate-300 bg-[#F8FAFC] px-4 py-3 transition hover:border-teal-600">
                <div className="flex items-center gap-2.5 text-xs">
                  <FileArchive className="h-4 w-4 text-slate-500" />
                  <span className="font-medium text-slate-700">
                    {watchFile ? watchFile.name : 'Choose watch export .zip (optional)'}
                  </span>
                </div>
                {watchFile && <CheckCircle2 className="h-4 w-4 text-teal-600" />}
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setWatchFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
              </label>
            </div>

            <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-[#F8FAFC] px-3 py-2 text-xs text-slate-600">
              <Lock className="h-3.5 w-3.5 shrink-0 text-teal-700" />
              Processed privately on your device; files are never uploaded to version control.
            </div>

            {error && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="cursor-pointer rounded-full border border-slate-300 px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !glookoFile}
                className="inline-flex cursor-pointer items-center gap-2 rounded-full bg-teal-700 px-5 py-2 text-xs font-semibold text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Importing…
                  </>
                ) : (
                  <>
                    <Upload className="h-3.5 w-3.5" />
                    Import &amp; Analyze
                  </>
                )}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleApiKeySubmit} className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 border border-teal-100">
                <KeyRound className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-bold text-slate-900">
                  Gemini API Key Settings
                </h2>
                <p className="text-xs text-slate-500">
                  Enables natural-language explanations and personalized Q&amp;A.
                </p>
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-semibold text-slate-700">
                Google AI Studio API Key
              </label>
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder="Paste your Gemini API key…"
                autoFocus
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-teal-600"
              />
            </div>

            {error && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="cursor-pointer rounded-full border border-slate-300 px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !apiKeyInput.trim()}
                className="inline-flex cursor-pointer items-center gap-2 rounded-full bg-teal-700 px-5 py-2 text-xs font-semibold text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Saving…
                  </>
                ) : (
                  'Save Key'
                )}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};
