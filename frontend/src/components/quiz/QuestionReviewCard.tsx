import { CheckCircle2, XCircle, FileText } from "lucide-react";

/**
 * One graded question, rendered identically wherever a review appears: the live
 * quiz result screen and the past-attempt review both pass their own shape
 * through this card, so the two views never drift apart.
 */
export interface QuestionReviewItem {
  questionText: string;
  selectedAnswer: string;
  correctAnswer: string;
  isCorrect: boolean;
  /** Null when the question row is gone and only the snapshot survives. */
  explanation: string | null;
  /** The A–D option texts, when the review can recover them. */
  options: { letter: string; text: string }[] | null;
  sourceDocumentTitle: string | null;
  sourcePageNumber: number | null;
  sourceSnippet: string | null;
}

export function QuestionReviewCard({
  index,
  question,
}: {
  index: number;
  question: QuestionReviewItem;
}) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-bold text-white leading-relaxed">
          <span className="text-slate-500 mr-1.5">{index + 1}.</span>
          {question.questionText}
        </p>
        {question.isCorrect ? (
          <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />
        ) : (
          <XCircle className="h-5 w-5 text-rose-400 shrink-0" />
        )}
      </div>

      <div className="flex flex-wrap gap-2 text-[11px] font-semibold">
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border ${
            question.isCorrect
              ? "bg-emerald-950/80 border-emerald-800/60 text-emerald-400"
              : "bg-rose-950/80 border-rose-800/60 text-rose-400"
          }`}
        >
          Your answer: {question.selectedAnswer}
        </span>
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-950/80 border border-emerald-800/60 text-emerald-400">
          Correct: {question.correctAnswer}
        </span>
      </div>

      {question.options && question.options.length > 0 && (
        <ul className="space-y-1">
          {question.options.map((option) => (
            <li key={option.letter} className="flex items-start gap-2 text-xs text-slate-300">
              <span className="shrink-0 inline-flex items-center justify-center h-5 w-5 rounded-md bg-slate-800 text-slate-300 text-[10px] font-extrabold">
                {option.letter}
              </span>
              <span className="leading-relaxed">{option.text}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="p-3 rounded-xl bg-slate-950/40 border border-slate-800 text-xs text-slate-300 leading-relaxed">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">Explanation</p>
        {question.explanation ?? "Explanation wasn't saved for this question."}
      </div>

      {question.sourceDocumentTitle && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <FileText className="h-3.5 w-3.5 text-sky-400 shrink-0" />
            <span className="truncate font-semibold text-sky-300">{question.sourceDocumentTitle}</span>
            {question.sourcePageNumber != null && (
              <span className="shrink-0">• Page {question.sourcePageNumber}</span>
            )}
          </div>
          {question.sourceSnippet && (
            <p className="text-[11px] text-slate-500 leading-relaxed pl-5">
              &ldquo;{question.sourceSnippet}&rdquo;
            </p>
          )}
        </div>
      )}
    </div>
  );
}