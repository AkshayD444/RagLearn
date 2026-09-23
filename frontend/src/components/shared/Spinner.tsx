import React from "react";

interface SpinnerProps {
  className?: string;
}

export function Spinner({ className = "h-4 w-4" }: SpinnerProps) {
  return (
    <span
      className={`${className} border-2 border-sky-500 border-t-transparent rounded-full animate-spin inline-block`}
      aria-hidden="true"
    />
  );
}