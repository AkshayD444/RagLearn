"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";

import { BookOpen, Grid, FileText, Sparkles, Layout, MenuSquare, Calendar, BarChart3, Settings, User, LogOut } from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", group: "workspace", icon: BookOpen, description: "Overview of your learning progress" },
  { href: "/subjects", label: "Subjects", group: "workspace", icon: Grid, description: "Manage your course modules" },
  { href: "/documents", label: "Documents", group: "workspace", icon: FileText, description: "Upload and manage PDFs" },
  { href: "/ai-tutor", label: "AI Tutor", group: "workspace", icon: Sparkles, description: "Ask questions about your materials" },
  { href: "/flashcards", label: "Flashcards", group: "learning", icon: Layout, description: "Review with spaced repetition" },
  { href: "/quizzes", label: "Quizzes", group: "learning", icon: MenuSquare, description: "Test your knowledge" },
  { href: "/study-plans", label: "Study Plans", group: "learning", icon: Calendar, description: "Personalized learning sequences" },
  { href: "/analytics", label: "Analytics", group: "learning", icon: BarChart3, description: "View your performance data" },
  { href: "/settings", label: "Settings", group: "account", icon: Settings, description: "Account and preferences" },
  { href: "/profile", label: "Profile", group: "account", icon: User, description: "Your profile and details" },
];

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();

  const isActive = (href: string) => {
    const normalizedCurrent = pathname.replace(/\/?$/, "/");
    const normalizedHref = href.replace(/\/?$/, "/");
    return normalizedCurrent === normalizedHref;
  };

  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  useEffect(() => {
    const prefersReduced = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
      setIsCollapsed(true);
    }
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem("sidebar-collapse");
    if (saved) {
      setIsCollapsed(saved === "collapsed");
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("sidebar-collapse", isCollapsed ? "collapsed" : "expanded");
  }, [isCollapsed]);

  const handleCollapse = () => setIsCollapsed(!isCollapsed);

  const handleMobileOpen = () => setIsMobileOpen(!isMobileOpen);

  const handleMobileClose = () => setIsMobileOpen(false);

  useEffect(() => {
    if (isMobileOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
  }, [isMobileOpen]);

  const groups = [
    { label: "Workspace", items: NAV_ITEMS.filter((i) => i.group === "workspace") },
    { label: "Learning", items: NAV_ITEMS.filter((i) => i.group === "learning") },
    { label: "Account", items: NAV_ITEMS.filter((i) => i.group === "account") },
  ];

  const getItemClassName = (item: typeof NAV_ITEMS[0]) => {
    const isActiveItem = isActive(item.href);
    return [
      "flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium text-slate-400 whitespace-nowrap",
      "hover:text-white hover:bg-slate-800/60 transition-all",
      "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:bg-slate-800/60",
      "[&>svg]:shrink-0 [&>svg]:h-4 [&>svg]:w-4",
      "group-[data-state=collapsed]:hidden",
      "group-[data-state=expanded]:inline",
      isActiveItem ? "bg-emerald-600/10 text-emerald-400" : "",
    ].filter(Boolean).join(" ");
  };

  return (
    <aside
      className="fixed left-0 top-0 h-full w-64 sm:w-80 transition-all duration-300 bg-slate-950/95 border-r border-slate-800/50 data-[state=collapsed]:w-0 sm:data-[state=collapsed]:w-16 data-[state=expanded]:w-64 sm:data-[state=expanded]:w-80 backdrop-blur-lg"
      data-state={isCollapsed ? "collapsed" : "expanded"}
    >
      <div className="p-4 sm:p-6 h-full">
        {/* Logo + Name + Mobile Toggle */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 mb-8 border-b border-slate-800/50 pb-6">
          <div className="flex items-center gap-2">
            <img
              src="/brand/raglearn-logo.jpeg"
              alt="RagLearn"
              className="h-9 w-auto object-contain"
              style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.5))" }}
            />
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">RagLearn</span>
          </div>
          <button
            onClick={() => setIsMobileOpen(!isMobileOpen)}
            className="sm:hidden p-2 rounded-lg hover:bg-slate-800/60 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:bg-slate-800/60"
            aria-label={isMobileOpen ? "Close navigation menu" : "Open navigation menu"}
          >
            <svg
              className="h-5 w-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {isMobileOpen ? (
                <path d="M6 18l8-8 8+8"></path>
              ) : (
                <path d="M6 6h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2z"></path>
              )}
            </svg>
          </button>
        </div>

        {/* Navigation groups */}
        <nav className="flex flex-col gap-2">
          {groups.map((group) => (
            <div
              key={group.label}
              className="flex items-center gap-2 px-2 py-1.5 rounded-xl text-xs font-medium uppercase tracking-wider border-b border-slate-800/50 mb-1 last:mb-0"
            >
              <span
                className="bg-slate-800/30 text-slate-400"
              >
                {group.label}
              </span>
            </div>
          ))}

          {NAV_ITEMS.map((item) => (
            <button
              key={item.href}
              type="button"
              className={getItemClassName(item)}
              onClick={() => router.push(item.href)}
              aria-label={item.label}
            >
              <item.icon
                className="h-4 w-4 text-emerald-400"
              />
              {isCollapsed ? (
                <span className="relative flex-1 whitespace-nowrap"
                  title={item.label}
                >
                  {item.label}
                </span>
              ) : (
                <span className="hidden sm:inline whitespace-nowrap">{item.label}</span>
              )}
            </button>
          ))}
        </nav>

        {/* Mobile drawer */}
        {isMobileOpen && (
          <div
            className="fixed left-0 top-0 right-0 bottom-0 z-40 backdrop-blur-lg bg-slate-950/90"
          >
            <div
              className="h-full w-64 sm:w-80 max-h-screen overflow-y-auto transform translate-x-full sm:translate-x-0 transition-transform duration-300 ease-out bg-slate-950/95 border-r border-slate-800/50 p-6 sm:p-8"
            >
              {/* Close button */}
              <button
                onClick={handleMobileClose}
                className="absolute right-2 top-2 p-2 rounded-lg hover:bg-slate-800/60 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                aria-label="Close navigation menu"
              >
                <svg
                  className="h-5 w-5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <line x1="15" y1="9" x2="21" y2="15"></line>
                  <line x1="9" y1="15" x2="15" y2="9"></line>
                </svg>
              </button>

              {/* Group headings and nav items */}
              {groups.map((group) => (
                <div
                  key={group.label}
                  className="flex items-center gap-2 px-2 py-1.5 rounded-xl text-xs font-medium uppercase tracking-wider border-b border-slate-800/50 mb-1 last:mb-0"
                >
                  <span
                    className="bg-slate-800/30 text-slate-400"
                  >
                    {group.label}
                  </span>
                </div>
              ))}

              {NAV_ITEMS.map((item) => (
                <button
                  key={item.href}
                  type="button"
                  className="flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium text-slate-400 whitespace-nowrap hover:text-white hover:bg-slate-800/60 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:bg-slate-800/60 [&>svg]:shrink-0 [&>svg]:h-4 [&>svg]:w-4"
                  onClick={() => {
                    router.push(item.href);
                    handleMobileClose();
                  }}
                  aria-label={item.label}
                >
                  <item.icon
                    className="h-4 w-4 text-emerald-400"
                  />
                  <span className="whitespace-nowrap">{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}