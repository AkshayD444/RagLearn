"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  StudyPlan,
  StudyPlanAction,
  StudyPlanItem,
  Subject,
} from "@/lib/api";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import {
  CalendarDays,
  Check,
  CheckCircle2,
  Clock3,
  ListChecks,
  PlayCircle,
  Sparkles,
  Upload,
} from "lucide-react";

const ACTION_STYLES: Record<StudyPlanAction, string> = {
  LEARN: "bg-sky-950/80 border-sky-800/60 text-sky-300",
  PRACTICE: "bg-amber-950/80 border-amber-800/60 text-amber-300",
  REVIEW: "bg-rose-950/80 border-rose-800/60 text-rose-300",
  MAINTAIN: "bg-emerald-950/80 border-emerald-800/60 text-emerald-300",
};

const ACTION_BAR_STYLES: Record<StudyPlanAction, string> = {
  LEARN: "from-sky-500 to-cyan-500",
  PRACTICE: "from-amber-500 to-orange-500",
  REVIEW: "from-rose-500 to-red-500",
  MAINTAIN: "from-emerald-500 to-teal-500",
};

interface StudyPlanSectionProps {
  subjects: Subject[];
}

function formatDate(value: string | null): string {
  if (!value) return "Priority sequence";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(parsed);
}

function parseOptionalNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function StudyPlanSection({ subjects }: StudyPlanSectionProps) {
  const router = useRouter();
  const [selectedSubjectId, setSelectedSubjectId] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [availableTime, setAvailableTime] = useState("");
  const [requestedSessions, setRequestedSessions] = useState("");
  const [maxTopics, setMaxTopics] = useState("");
  const [plan, setPlan] = useState<StudyPlan | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [completingItemId, setCompletingItemId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    const isSelectedSubjectActive = subjects.some(
      (subject) => subject.id === selectedSubjectId,
    );
    if (isSelectedSubjectActive) return;

    requestIdRef.current += 1;
    setSelectedSubjectId(subjects[0]?.id ?? "");
    setPlan(null);
    setIsGenerating(false);
    setCompletingItemId(null);
  }, [selectedSubjectId, subjects]);

  useEffect(() => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsGenerating(false);
    setCompletingItemId(null);

    if (!selectedSubjectId) {
      setPlan(null);
      setIsLoading(false);
      return;
    }

    let active = true;
    const loadLatest = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const latest = await api.getLatestStudyPlan(selectedSubjectId);
        if (active && requestIdRef.current === requestId) setPlan(latest);
      } catch (err: any) {
        if (active && requestIdRef.current === requestId) {
          setPlan(null);
          setError(err.message || "Failed to load the latest study plan.");
        }
      } finally {
        if (active && requestIdRef.current === requestId) setIsLoading(false);
      }
    };

    loadLatest();
    return () => {
      active = false;
    };
  }, [selectedSubjectId]);

  const handleGenerate = async () => {
    if (!selectedSubjectId) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsGenerating(true);
    setError(null);
    try {
      const generated = await api.generateStudyPlan({
        subject_id: selectedSubjectId,
        target_date: targetDate || null,
        available_minutes_per_day: parseOptionalNumber(availableTime),
        requested_sessions: parseOptionalNumber(requestedSessions),
        max_topics_per_session: parseOptionalNumber(maxTopics),
      });
      if (requestIdRef.current === requestId) setPlan(generated);
    } catch (err: any) {
      if (requestIdRef.current === requestId) {
        setError(err.message || "Failed to generate a study plan.");
      }
    } finally {
      if (requestIdRef.current === requestId) setIsGenerating(false);
    }
  };

  const handleCompletion = async (item: StudyPlanItem) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setCompletingItemId(item.id);
    setError(null);
    try {
      const updated = await api.completeStudyPlanItem(item.id, !item.completed);
      if (requestIdRef.current === requestId) setPlan(updated);
    } catch (err: any) {
      if (requestIdRef.current === requestId) {
        setError(err.message || "Failed to update study-plan progress.");
      }
    } finally {
      if (requestIdRef.current === requestId) setCompletingItemId(null);
    }
  };

  const openTopic = (item: StudyPlanItem) => {
    if (!plan) return;
    const focus = item.action === "LEARN" ? "&focus=materials" : "";
    router.push(
      `/subjects/${plan.subject_id}?topic=${encodeURIComponent(item.topic)}${focus}`,
    );
  };

  if (subjects.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
        <EmptyState
          icon={<ListChecks className="h-6 w-6" />}
          title="Create a subject to build a study plan"
          description="Study plans use your measured topics, quiz history, and adaptive recommendations from an active subject."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 space-y-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Subject
            </span>
            <select
              value={selectedSubjectId}
              onChange={(event) => {
                setSelectedSubjectId(event.target.value);
                setPlan(null);
              }}
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-sm text-white px-3 py-2.5"
            >
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.title}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={handleGenerate}
            disabled={!selectedSubjectId || isGenerating}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-sky-600 hover:bg-sky-500 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2.5 text-xs font-semibold text-white transition-colors"
          >
            {isGenerating ? <Spinner className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
            {isGenerating ? "Generating..." : "Generate Study Plan"}
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <label className="space-y-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              <CalendarDays className="h-3.5 w-3.5" /> Target date
            </span>
            <input
              type="date"
              value={targetDate}
              onChange={(event) => setTargetDate(event.target.value)}
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-xs text-white px-3 py-2.5"
            />
          </label>
          <label className="space-y-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              <Clock3 className="h-3.5 w-3.5" /> Minutes per day
            </span>
            <input
              type="number"
              min="1"
              max="1440"
              value={availableTime}
              onChange={(event) => setAvailableTime(event.target.value)}
              placeholder="Optional"
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-xs text-white px-3 py-2.5 placeholder:text-slate-600"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Study sessions
            </span>
            <input
              type="number"
              min="1"
              max="60"
              value={requestedSessions}
              onChange={(event) => setRequestedSessions(event.target.value)}
              placeholder="Optional"
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-xs text-white px-3 py-2.5 placeholder:text-slate-600"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Topics per session
            </span>
            <input
              type="number"
              min="1"
              max="30"
              value={maxTopics}
              onChange={(event) => setMaxTopics(event.target.value)}
              placeholder="Optional"
              className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-xs text-white px-3 py-2.5 placeholder:text-slate-600"
            />
          </label>
        </div>
        <p className="text-[11px] text-slate-500">
          Estimates are planning estimates only; completion records activity but does not change mastery until future quiz
          performance is submitted.
        </p>
      </div>

      {error && <ErrorBanner message={error} />}

      {isLoading && !plan && (
        <div className="flex items-center justify-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-8">
          <Spinner className="h-5 w-5" />
          <span className="text-xs text-slate-400">Loading your latest plan...</span>
        </div>
      )}

      {!isLoading && !plan && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <EmptyState
            icon={<ListChecks className="h-6 w-6" />}
            title="No study plan yet"
            description="Generate a plan after completing a few topic-based quiz questions. Only measured topics from your adaptive recommendations will be scheduled."
          />
        </div>
      )}

      {plan && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-bold text-white">{plan.subject_title}</p>
                <p className="text-[11px] text-slate-500">
                  Generated {new Date(plan.generated_at).toLocaleString()}
                </p>
              </div>
              <div className="flex flex-wrap gap-2 text-[10px] font-semibold text-slate-400">
                <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1">
                  <CalendarDays className="h-3 w-3" /> {formatDate(plan.target_date)}
                </span>
                <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1">
                  <Clock3 className="h-3 w-3" /> {plan.sessions.reduce((sum, session) => sum + session.estimated_minutes, 0)} min planned
                </span>
              </div>
            </div>
            {!plan.material_available && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-800/50 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-200">
                <Upload className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>Indexed study material is unavailable for this subject. Upload a document before learning or practicing.</span>
              </div>
            )}
            {plan.omitted_topic_count > 0 && (
              <p className="text-[11px] text-slate-500">
                {plan.omitted_topic_count} lower-priority topic{plan.omitted_topic_count === 1 ? "" : "s"} omitted to fit the requested session limits.
              </p>
            )}
          </div>

          {plan.sessions.length === 0 && (
            <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
              <EmptyState
                icon={<ListChecks className="h-6 w-6" />}
                title="More quiz data is needed"
                description="Complete a few topic-based quiz questions and generate the plan again. No topic is invented from an unmeasured subject."
              />
            </div>
          )}

          {plan.sessions.map((session) => (
            <div key={session.id} className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-sky-500/10 text-xs font-bold text-sky-400 border border-sky-500/20">
                    {session.session_number}
                  </span>
                  <div>
                    <p className="text-sm font-bold text-white">Session {session.session_number}</p>
                    <p className="text-[11px] text-slate-500">
                      {formatDate(session.scheduled_date)} • {session.estimated_minutes} min estimated
                    </p>
                  </div>
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                  {session.items.length} topic{session.items.length === 1 ? "" : "s"}
                </span>
              </div>

              <div className="space-y-2">
                {session.items.map((item) => (
                  <div key={item.id} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3.5 space-y-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-bold text-white truncate">{item.topic}</p>
                        <p className="text-[11px] text-slate-500">
                          Mastery {item.mastery}% • {item.questions_attempted} questions • {item.questions_incorrect} missed
                        </p>
                      </div>
                      <span className={`shrink-0 inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold ${ACTION_STYLES[item.action]}`}>
                        {item.action}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 space-y-1">
                        <div className="flex justify-between text-[10px] text-slate-500">
                          <span>Priority {item.priority}</span>
                          <span>{item.estimated_minutes} min estimated</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
                          <div
                            className={`h-full rounded-full bg-gradient-to-r ${ACTION_BAR_STYLES[item.action]}`}
                            style={{ width: `${Math.min(100, Math.max(0, item.priority))}%` }}
                          />
                        </div>
                      </div>
                      <button
                        onClick={() => openTopic(item)}
                        className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 px-3 py-1.5 text-[11px] font-semibold text-white transition-colors"
                      >
                        {item.action === "LEARN" ? <Upload className="h-3.5 w-3.5" /> : <PlayCircle className="h-3.5 w-3.5" />}
                        {item.action === "LEARN" ? "Materials" : "Practice"}
                      </button>
                    </div>
                    <p className="text-[11px] leading-relaxed text-slate-400">{item.reason}</p>
                    <button
                      onClick={() => handleCompletion(item)}
                      disabled={completingItemId === item.id}
                      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                        item.completed
                          ? "border-emerald-800/60 bg-emerald-950/50 text-emerald-300"
                          : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500"
                      }`}
                    >
                      {completingItemId === item.id ? (
                        <Spinner className="h-3 w-3" />
                      ) : item.completed ? (
                        <CheckCircle2 className="h-3 w-3" />
                      ) : (
                        <Check className="h-3 w-3" />
                      )}
                      {item.completed ? "Completed" : "Mark complete"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
