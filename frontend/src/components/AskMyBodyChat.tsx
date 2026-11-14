import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ChevronDown,
  ChevronUp,
  Loader2,
  MessageCircle,
  Send,
  X,
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
  onClose?: () => void;
  isDrawer?: boolean;
}

const STARTER_CHIPS = [
  'Why does my sugar rise in the morning?',
  'How much does a 25-min brisk walk drop my sugar?',
  'What happens when I bolus late for meals?',
  'What should I ask my doctor?',
];

function cleanMarkdownBody(raw: string): string {
  return raw
    .replace(/\n*\s*#*\s*\*{0,2}SUPPORTING_NUMBERS\*{0,2}\s*:[\s\S]*$/i, '')
    .trim();
}

const AssistantBubble: React.FC<{
  content: string;
  supportingNumbers?: string[];
}> = ({ content, supportingNumbers }) => {
  const [showNums, setShowNums] = useState(false);
  const cleaned = cleanMarkdownBody(content);
  const hasNums = Boolean(supportingNumbers && supportingNumbers.length > 0);

  return (
    <div className="max-w-[92%] rounded-2xl border border-slate-200/90 bg-white px-4 py-3.5 text-sm leading-relaxed text-slate-800 shadow-2xs">
      <div className="space-y-2.5">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h4 className="mt-2 text-sm font-bold text-slate-900 first:mt-0">
                {children}
              </h4>
            ),
            h2: ({ children }) => (
              <h4 className="mt-2 text-sm font-bold text-slate-900 first:mt-0">
                {children}
              </h4>
            ),
            h3: ({ children }) => (
              <h4 className="mt-2 text-sm font-bold text-teal-800 first:mt-0">
                {children}
              </h4>
            ),
            h4: ({ children }) => (
              <h5 className="mt-1.5 text-xs font-bold uppercase tracking-wider text-teal-800 first:mt-0">
                {children}
              </h5>
            ),
            p: ({ children }) => (
              <p className="leading-relaxed text-slate-700">{children}</p>
            ),
            strong: ({ children }) => (
              <strong className="font-semibold text-slate-950">{children}</strong>
            ),
            em: ({ children }) => (
              <em className="italic text-slate-600">{children}</em>
            ),
            ul: ({ children }) => (
              <ul className="my-1.5 list-disc space-y-1 pl-5 text-slate-700">
                {children}
              </ul>
            ),
            ol: ({ children }) => (
              <ol className="my-1.5 list-decimal space-y-1 pl-5 text-slate-700">
                {children}
              </ol>
            ),
            li: ({ children }) => (
              <li className="leading-relaxed marker:text-teal-600">{children}</li>
            ),
            code: ({ children }) => (
              <code className="rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-semibold text-teal-800">
                {children}
              </code>
            ),
            blockquote: ({ children }) => (
              <blockquote className="border-l-3 border-blue-500 bg-blue-50/70 py-1.5 pl-3 pr-2 text-xs text-blue-950 rounded-r-lg">
                {children}
              </blockquote>
            ),
            table: ({ children }) => (
              <div className="my-2 overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full text-left text-xs">{children}</table>
              </div>
            ),
            th: ({ children }) => (
              <th className="border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 font-semibold text-slate-800">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="border-b border-slate-100 px-2.5 py-1.5 text-slate-700">
                {children}
              </td>
            ),
          }}
        >
          {cleaned}
        </ReactMarkdown>
      </div>

      {hasNums && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <button
            type="button"
            onClick={() => setShowNums((v) => !v)}
            className="inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-teal-700 hover:text-teal-900"
          >
            {showNums
              ? 'Hide supporting data'
              : `View supporting numbers (${supportingNumbers!.length})`}
            {showNums ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>

          {showNums && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {supportingNumbers!.map((num, nIdx) => (
                <span
                  key={nIdx}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700"
                >
                  {num}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export const AskMyBodyChat: React.FC<AskMyBodyChatProps> = ({
  sessionId,
  datasetTitle,
  onClose,
  isDrawer = false,
}) => {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState<string>('');
  const [sending, setSending] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

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
    <div
      className={`flex flex-col justify-between ${
        isDrawer
          ? 'h-full bg-white p-5 sm:p-6'
          : 'rounded-3xl border border-slate-200 bg-white p-6 shadow-xs'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-slate-200/80 pb-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 border border-teal-100">
            <MessageCircle className="h-4.5 w-4.5" />
          </span>
          <div>
            <h3 className="text-base font-bold text-slate-900">Ask About Your Data</h3>
            <p className="text-xs text-slate-500">
              Based on {datasetTitle || 'your personal glucose & watch history'}
            </p>
          </div>
        </div>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close chat"
            className="cursor-pointer rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Starter Questions */}
      <div className="mt-3.5 flex flex-wrap gap-1.5">
        {STARTER_CHIPS.map((chip) => (
          <button
            key={chip}
            type="button"
            disabled={!sessionId || sending}
            onClick={() => void submitQuestion(chip)}
            className="cursor-pointer rounded-full border border-slate-200 bg-[#F8FAFC] px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-teal-600/40 hover:bg-teal-50/60 hover:text-teal-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {chip}
          </button>
        ))}
      </div>

      {/* Conversation Thread */}
      <div
        ref={scrollRef}
        className={`mt-3.5 space-y-4 overflow-y-auto rounded-2xl border border-slate-200/80 bg-[#F8FAFC] p-4 ${
          isDrawer ? 'flex-1 min-h-0' : 'h-80'
        }`}
      >
        {turns.length === 0 && !sending ? (
          <div className="flex h-full flex-col items-center justify-center px-4 text-center">
            <p className="text-sm font-semibold text-slate-800">
              Have a question about your blood sugar?
            </p>
            <p className="mt-1 max-w-xs text-xs leading-relaxed text-slate-500">
              Ask about mornings, meals, exercise, or sleep—answers are calculated directly from your personal logs.
            </p>
          </div>
        ) : (
          turns.map((turn, idx) => (
            <div
              key={idx}
              className={`flex ${
                turn.role === 'user' ? 'justify-end' : 'justify-start'
              }`}
            >
              {turn.role === 'user' ? (
                <div className="max-w-[85%] rounded-2xl bg-teal-700 px-4 py-2.5 text-sm leading-relaxed text-white shadow-2xs">
                  {turn.content}
                </div>
              ) : (
                <AssistantBubble
                  content={turn.content}
                  supportingNumbers={turn.supportingNumbers}
                />
              )}
            </div>
          ))
        )}

        {sending && (
          <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-teal-700" />
            Looking through your records…
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
            {error}
          </div>
        )}
      </div>

      {/* Input Form */}
      <form onSubmit={handleFormSubmit} className="mt-3.5 flex items-center gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a question about your patterns…"
          disabled={!sessionId || sending}
          className="flex-1 rounded-full border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder-slate-400 outline-none transition focus:border-teal-600 focus:ring-2 focus:ring-teal-600/15"
        />
        <button
          type="submit"
          disabled={!sessionId || sending || !input.trim()}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          Ask
        </button>
      </form>
    </div>
  );
};
