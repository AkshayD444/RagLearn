"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  AnswerItem,
  Quiz,
  QuizDifficulty,
  QuizResultResponse,
  SelectedOption,
} from "@/lib/api";
import {
  Sparkles,
  CheckCircle2,
  XCircle,
  ChevronLeft,
  ChevronRight,
  Flag,
  BookOpen,
  RotateCcw,
  Target,
} from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import { QuestionReviewCard } from "@/components/quiz/QuestionReviewCard";

interface QuizSectionProps {
  subjectId: string;
  hasReadyDocuments: boolean;
  initialTopic?: string;
  onAttemptComplete?: () => void;
}

type View = "browse" | "taking" | "results";

const DIFFICULTY_OPTIONS: { value: QuizDifficulty; label: string }[] = [
  { value: "easy", label: "Easy" },
  { value: "medium", label: "Medium" },
  { value: "hard", label: "Hard" },
];

const QUESTION_COUNTS = [3, 5, 10, 15, 20];

export function QuizSection({ subjectId, hasReadyDocuments, initialTopic, onAttemptComplete }: QuizSectionProps) {
  const [view, setView] = useState<View>("browse");
  const [questionCount, setQuestionCount] = useState(5);
  const [difficulty, setDifficulty] = useState<QuizDifficulty>("medium");
  const [topic, setTopic] = useState(initialTopic ?? "");

  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selections, setSelections] = useState<Record<string, SelectedOption>>({});
  const [result, setResult] = useState<QuizResultResponse | null>(null);

  const [questionStartTimes, setQuestionStartTimes] = useState<Record<string, number>>({});
  const [questionTimes, setQuestionTimes] = useState<Record<string, number>>({});
  const quizStartedAt = useRef<number | null>(null);

  const [isGenerating, setIsGenerating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentQuestion = quiz?.questions[currentIndex] ?? null;
  const answeredCount = useMemo(
    () => (quiz ? Object.keys(selections).filter((id) => !!selections[id]).length : 0),
    [quiz, selections],
  );
  const isLastQuestion = quiz !== null && currentIndex >= quiz.questions.length - 1;

  useEffect(() => {
    if (!quiz || !currentQuestion) return;
    setQuestionStartTimes((prev) =>
      prev[currentQuestion.id] !== undefined ? prev : { ...prev, [currentQuestion.id]: Date.now() },
    );
  }, [quiz, currentQuestion]);

  const resetQuizTimer = () => {
    quizStartedAt.current = Date.now();
    setQuestionStartTimes({});
    setQuestionTimes({});
  };

  const handleGenerate = async () => {
    if (!hasReadyDocuments) return;

    setIsGenerating(true);
    setError(null);
    try {
      const generated = await api.generateQuiz(subjectId, questionCount, difficulty, topic.trim() || null);
      setSelections({});
      setCurrentIndex(0);
      setQuiz(generated);
      resetQuizTimer();
      setView("taking");
    } catch (err: any) {
      setQuiz(null);
      setError(err.message || "Failed to generate quiz. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  };

  const handleSelect = (questionId: string, option: SelectedOption) => {
    setSelections((prev) => ({ ...prev, [questionId]: option }));
    setQuestionTimes((prev) => {
      if (prev[questionId] !== undefined) return prev;
      // The effect that seeds the start time runs after the question is
      // painted, so the very first click can beat it. Fall back to this
      // instant instead of leaving the answer untimed, which would persist a
      // null time_taken and lose the data the backend records per question.
      const started = questionStartTimes[questionId] ?? Date.now();
      const elapsed = Math.max(0.1, Math.round((Date.now() - started) / 100) / 10);
      return { ...prev, [questionId]: elapsed };
    });
  };

  const handleNext = () => {
    if (!currentQuestion || !selections[currentQuestion.id]) return;
    if (isLastQuestion) return;
    setCurrentIndex((i) => i + 1);
  };

  const handlePrevious = () => {
    setCurrentIndex((i) => Math.max(0, i - 1));
  };

  const handleSubmit = async () => {
    if (!quiz) return;
    if (answeredCount !== quiz.questions.length) {
      setError("Please answer every question before submitting.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      const answers: AnswerItem[] = quiz.questions.map((q) => ({
        question_id: q.id,
        selected_option: selections[q.id],
        time_taken: questionTimes[q.id] ?? null,
      }));
      const startedAt = quizStartedAt.current ? new Date(quizStartedAt.current).toISOString() : null;
      const response = await api.submitQuiz(quiz.id, answers, { startedAt });
      setResult(response);
      setView("results");
      onAttemptComplete?.();
    } catch (err: any) {
      setError(err.message || "Failed to submit answers. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRestart = () => {
    setQuiz(null);
    setResult(null);
    setSelections({});
    setCurrentIndex(0);
    setError(null);
    setView("browse");
  };

  const handleCancel = () => {
    setQuiz(null);
    setSelections({});
    setCurrentIndex(0);
    setError(null);
    setView("browse");
  };

  if (!hasReadyDocuments) {
    return (
      <EmptyState
        icon={<Sparkles className="h-6 w-6" />}
        title="Quiz Engine needs indexed materials"
        description="Upload at least one PDF and wait for it to finish processing. Quizzes are generated strictly from the chunks extracted from your study documents."
      />
    );
  }

  if (view === "taking" && quiz && currentQuestion) {
    return (
      <div className="space-y-4">
        {error && <ErrorBanner message={error} />}

        {/* Progress */}
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-bold text-white truncate">{quiz.title}</p>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Question {currentIndex + 1} of {quiz.questions.length} • Answers are revealed after submission
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-purple-950/80 border border-purple-800/60 text-purple-300 text-[10px] font-semibold capitalize">
                {quiz.difficulty}
              </span>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 text-[10px] font-semibold">
                <Target className="h-3 w-3 text-sky-400" /> {answeredCount}/{quiz.questions.length} answered
              </span>
            </div>
          </div>
          <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-500 to-purple-500 transition-all duration-300"
              style={{ width: `${((currentIndex + 1) / quiz.questions.length) * 100}%` }}
            />
          </div>
        </div>

        {/* Question */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 space-y-5">
          <p className="text-sm sm:text-base font-bold text-white leading-relaxed">
            {currentQuestion.question_text}
          </p>

          <ul className="space-y-2.5">
            {(["a", "b", "c", "d"] as const).map((key, index) => {
              const letter = key.toUpperCase() as SelectedOption;
              const optionText = currentQuestion[`option_${key}`];
              const isSelected = selections[currentQuestion.id] === letter;
              return (
                <li key={key}>
                  <button
                    onClick={() => handleSelect(currentQuestion.id, letter)}
                    aria-pressed={isSelected}
                    className={`w-full flex items-start gap-3 p-3.5 rounded-xl border text-left text-sm transition-all ${
                      isSelected
                        ? "border-sky-500 bg-sky-500/10 text-white ring-2 ring-sky-500/20"
                        : "border-slate-800 bg-slate-950/40 text-slate-300 hover:border-slate-600 hover:bg-slate-800/50"
                    }`}
                  >
                    <span
                      className={`shrink-0 inline-flex items-center justify-center h-6 w-6 rounded-lg text-[11px] font-extrabold border ${
                        isSelected
                          ? "bg-sky-500 border-sky-500 text-white"
                          : "border-slate-700 text-slate-400 bg-slate-900"
                      }`}
                    >
                      {letter}
                    </span>
                    <span className="leading-relaxed">{optionText}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={handlePrevious}
            disabled={currentIndex === 0}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Previous
          </button>

          <button
            onClick={handleCancel}
            className="inline-flex items-center gap-2 px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Cancel
          </button>

          {isLastQuestion ? (
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || answeredCount !== quiz.questions.length}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 text-white text-xs font-semibold shadow-lg shadow-purple-600/25 hover:from-purple-500 hover:to-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              {isSubmitting ? <Spinner className="h-3.5 w-3.5" /> : <Flag className="h-3.5 w-3.5" />}
              {isSubmitting ? "Submitting..." : "Submit Quiz"}
            </button>
          ) : (
            <button
              onClick={handleNext}
              disabled={!selections[currentQuestion.id]}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 text-white text-xs font-semibold hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  if (view === "results" && result) {
    return (
      <div className="space-y-5">
        {error && <ErrorBanner message={error} />}

        {/* Score Summary */}
        <div className="rounded-3xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8 text-center">
          <div
            className={`inline-flex h-24 w-24 items-center justify-center rounded-full border-4 ${
              result.percentage >= 70
                ? "border-emerald-500 text-emerald-400"
                : result.percentage >= 40
                  ? "border-amber-500 text-amber-400"
                  : "border-rose-500 text-rose-400"
            }`}
          >
            <span className="text-3xl font-extrabold">{Math.round(result.percentage)}%</span>
          </div>
          <h3 className="mt-4 text-lg font-bold text-white">{result.title}</h3>
          <p className="mt-1 text-sm text-slate-400">
            You scored <span className="font-bold text-white">{result.score}</span> out of{" "}
            <span className="font-bold text-white">{result.total_questions}</span> questions correct.
          </p>

          {result.attempt_id && (
            <p className="mt-3 text-[11px] text-emerald-400/90">
              Attempt saved — your performance and topic mastery have been updated.
            </p>
          )}

          <div className="flex flex-wrap items-center justify-center gap-2 mt-4">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-950/80 border border-emerald-800/60 text-emerald-400 text-[10px] font-semibold">
              <CheckCircle2 className="h-3 w-3" />{" "}
              {result.results.filter((r) => r.is_correct).length} Correct
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-rose-950/80 border border-rose-800/60 text-rose-400 text-[10px] font-semibold">
              <XCircle className="h-3 w-3" /> {result.results.filter((r) => !r.is_correct).length} Incorrect
            </span>
          </div>
        </div>

        {/* Per-Question Review */}
        <div className="space-y-3">
          <h4 className="text-sm font-bold text-white flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-purple-400" /> Review & Explanations
          </h4>

          {result.results.map((item, index) => (
            <QuestionReviewCard
              key={item.question_id}
              index={index}
              question={{
                questionText: item.question_text,
                selectedAnswer: item.selected_option,
                correctAnswer: item.correct_option,
                isCorrect: item.is_correct,
                explanation: item.explanation,
                options: null,
                sourceDocumentTitle: item.source_document_title,
                sourcePageNumber: item.source_page_number,
                sourceSnippet: item.source_snippet,
              }}
            />
          ))}
        </div>

        <div className="flex justify-end">
          <button
            onClick={handleRestart}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 text-white text-xs font-semibold shadow-lg shadow-purple-600/25 hover:from-purple-500 hover:to-indigo-500 transition-all"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Take Another Quiz
          </button>
        </div>
      </div>
    );
  }

  // Browse / configuration view
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Question Count */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Number of Questions
            </label>
            <div className="flex flex-wrap gap-1.5">
              {QUESTION_COUNTS.map((count) => (
                <button
                  key={count}
                  onClick={() => setQuestionCount(count)}
                  aria-pressed={questionCount === count}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    questionCount === count
                      ? "bg-purple-600 border-purple-500 text-white"
                      : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500"
                  }`}
                >
                  {count}
                </button>
              ))}
            </div>
          </div>

          {/* Difficulty */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Difficulty</label>
            <div className="flex gap-1.5">
              {DIFFICULTY_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  onClick={() => setDifficulty(option.value)}
                  aria-pressed={difficulty === option.value}
                  className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold border capitalize transition-colors ${
                    difficulty === option.value
                      ? "bg-purple-600 border-purple-500 text-white"
                      : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {/* Topic */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Topic <span className="normal-case text-slate-600 font-medium">(optional)</span>
            </label>
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Photosynthesis"
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 outline-none text-sm text-white placeholder:text-slate-500 px-3 py-2 transition-colors"
            />
          </div>
        </div>

        <div className="flex items-center justify-end">
          <button
            onClick={handleGenerate}
            disabled={isGenerating}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 text-white text-xs font-semibold shadow-lg shadow-purple-600/25 hover:from-purple-500 hover:to-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {isGenerating ? <Spinner className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
            {isGenerating ? "Generating grounded questions..." : "Generate Quiz"}
          </button>
        </div>

        {error && <ErrorBanner message={error} />}

        {isGenerating && (
          <div className="flex items-center gap-3 p-4 rounded-xl border border-dashed border-purple-800/60 bg-purple-950/20">
            <Spinner className="h-5 w-5" />
            <p className="text-xs text-purple-300">
              Retrieving this subject&apos;s chunks and composing multiple-choice questions. The correct answers stay
              hidden until you submit.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}