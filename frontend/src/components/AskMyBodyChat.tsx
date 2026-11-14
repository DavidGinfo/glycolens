import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Loader2,
  MessageSquareHeart,
  Send,
  Sparkles,
  User,
} from 'lucide-react';
import { askMyBody } from '../api';
import type { EngineMode } from '../types';

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  supportingNumbers?: string[];
  engineMode?: EngineMode;
}

interface AskMyBodyChatProps {
  sessionId: string | null;
  datasetTitle?: string;
}

const STARTER_CHIPS = [
  'Why do I go low around midnight?',
  'How much does a 25-min brisk walk drop my sugar?',
  'What should I ask my endocrinologist?',
];

export const AskMyBodyChat: React.FC<AskMyBodyChatProps> = ({
  sessionId,
  datasetTitle,
}) => {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState<string>('');
  const [sending, setSending] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Reset conversation when switching dataset sessions
  useEffect(() => {
    setTurns([]);
    setError(null);
  }, [sessionId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [turns, sending]);

  const submitQuestion = async (rawQuestion: string) => {
    const question = rawQuestion.trim();
    if (!question || !sessionId || sending) return;

    const historyPayload = turns.map((t) => ({
      role: t.role,
      content: t.content,
    }));

    setTurns((prev) => [...prev, { role: 'user', content: question }]);
    setInput('');
    setSending(true);
    setError(null);

    try {
      const resp = await askMyBody(sessionId, {
        question,
        history: historyPayload,
      });
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: resp.answer,
          supportingNumbers: resp.supporting_numbers,
          engineMode: resp.engine_mode,
        },
      ]);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Unable to answer question right now.'
      );
    } finally {
      setSending(false);
    }
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void submitQuestion(input);
  };

  return (
    <div className="flex flex-col justify-between rounded-2xl border border-slate-800/90 bg-slate-900/70 p-5 shadow-xl shadow-black/25">
      <div>
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-400 ring-1 ring-indigo-500/30">
              <MessageSquareHeart className="h-4 w-4" />
            </span>
            <div>
              <h3 className="text-base font-semibold text-slate-100">
                &ldquo;Ask My Body&rdquo; Data Q&amp;A
              </h3>
              <p className="text-xs text-slate-400">
                Grounded strictly in {datasetTitle || 'your aligned CGM + wearable profile'}
              </p>
            </div>
          </div>
        </div>

        {/* Suggested Starter Chips */}
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          {STARTER_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              disabled={!sessionId || sending}
              onClick={() => void submitQuestion(chip)}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-slate-800 bg-slate-950/75 px-3 py-1.5 text-xs text-slate-300 transition hover:border-indigo-500/50 hover:bg-indigo-500/10 hover:text-indigo-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Sparkles className="h-3 w-3 text-indigo-400" />
              {chip}
            </button>
          ))}
        </div>

        {/* Conversation Thread */}
        <div
          ref={scrollRef}
          className="mt-3.5 h-64 space-y-3 overflow-y-auto rounded-xl border border-slate-800/80 bg-slate-950/75 p-3.5"
        >
          {turns.length === 0 && !sending ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <Bot className="h-7 w-7 text-indigo-400/70" />
              <p className="mt-2 text-xs font-medium text-slate-300">
                Ask any question about your overnight glucose, walks, sleep stages, or boluses.
              </p>
              <p className="mt-1 max-w-sm text-[11px] text-slate-500">
                Every response cites verified numbers from your personal dataset and respects clinical non-prescriptive guardrails.
              </p>
            </div>
          ) : (
            turns.map((turn, idx) => (
              <div
                key={idx}
                className={`flex gap-2.5 ${
                  turn.role === 'user' ? 'justify-end' : 'justify-start'
                }`}
              >
                {turn.role === 'assistant' && (
                  <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-300">
                    <Bot className="h-3.5 w-3.5" />
                  </span>
                )}
                <div
                  className={`max-w-[88%] rounded-xl px-3.5 py-2.5 text-xs leading-relaxed ${
                    turn.role === 'user'
                      ? 'bg-emerald-500/20 text-emerald-100 ring-1 ring-emerald-500/35'
                      : 'border border-slate-800 bg-slate-900/95 text-slate-200'
                  }`}
                >
                  <p className="whitespace-pre-line">{turn.content}</p>
                  {turn.supportingNumbers && turn.supportingNumbers.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-800/80 pt-2">
                      {turn.supportingNumbers.map((num, nIdx) => (
                        <span
                          key={nIdx}
                          className="rounded bg-slate-950 px-2 py-0.5 font-mono text-[10px] text-emerald-300 ring-1 ring-slate-800"
                        >
                          {num}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {turn.role === 'user' && (
                  <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-300">
                    <User className="h-3.5 w-3.5" />
                  </span>
                )}
              </div>
            ))
          )}

          {sending && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />
              Querying your aligned metabolic profile...
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-rose-500/35 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              {error}
            </div>
          )}
        </div>
      </div>

      {/* Input Form */}
      <form onSubmit={handleFormSubmit} className="mt-3 flex items-center gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your glucose patterns, walks, sleep, or boluses..."
          disabled={!sessionId || sending}
          className="flex-1 rounded-xl border border-slate-800 bg-slate-950/90 px-3.5 py-2.5 text-xs text-slate-100 placeholder-slate-500 outline-none transition focus:border-indigo-500/60 focus:ring-1 focus:ring-indigo-500/40"
        />
        <button
          type="submit"
          disabled={!sessionId || sending || !input.trim()}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl bg-indigo-500 px-3.5 py-2.5 text-xs font-semibold text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          Ask
        </button>
      </form>
    </div>
  );
};
