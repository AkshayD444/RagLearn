"use client";
import { useState } from "react";

const QuizSection = ({ subjectId, hasReadyDocuments, onAttemptComplete }) => <div>QuizSection</div>;

export default function Test() {
  const [subject, setSubject] = useState(null);
  const [hasReadyDocuments, setHasReadyDocuments] = useState(false);

  // Inline pattern without block parentheses
  const quizElement = subject && hasReadyDocuments && <QuizSection subjectId={subject.id} hasReadyDocuments={hasReadyDocuments} onAttemptComplete={() => {}} />;

  return (
    <div>
      {quizElement}
    </div>
  )
}
