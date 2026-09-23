import React, { ReactNode } from "react";

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description: string;
}

export function EmptyState({ icon, title, description }: EmptyStateProps) {
  return (
    <div className="p-8 rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 text-center space-y-2">
      <div className="inline-flex p-3 rounded-2xl bg-slate-800/60 text-slate-400">
        {icon}
      </div>
      <p className="text-sm font-semibold text-slate-300">{title}</p>
      <p className="text-xs text-slate-500 max-w-md mx-auto">{description}</p>
    </div>
  );
}