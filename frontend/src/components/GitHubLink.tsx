/**
 * Link to the project's GitHub repository with a short launch animation.
 *
 * A plain click plays the animation, then navigates. Modified clicks (cmd,
 * ctrl, shift, middle button) keep the browser's normal behaviour, and users
 * who prefer reduced motion go straight to GitHub.
 */

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cx } from "./ui";

export const GITHUB_REPO_URL = "https://github.com/coderxtushar/Agent-Labs";
const LAUNCH_MS = 950;

/** GitHub's mark (Octicons `mark-github`, MIT). */
export function GitHubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} fill="currentColor" aria-hidden>
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  );
}

function LaunchOverlay() {
  return createPortal(
    <div className="gh-launch fixed inset-0 z-[100] flex flex-col items-center justify-center" role="status" aria-live="polite">
      <div className="relative flex h-24 w-24 items-center justify-center">
        <span className="gh-ring absolute inset-0 rounded-full border" style={{ borderColor: "rgb(var(--accent) / 0.6)" }} />
        <span className="gh-ring gh-ring-2 absolute inset-0 rounded-full border" style={{ borderColor: "rgb(var(--accent) / 0.4)" }} />
        <span className="gh-mark text-[rgb(var(--text))]">
          <GitHubMark className="h-14 w-14" />
        </span>
      </div>
      <p className="gh-caption muted mt-8 font-mono text-xs">
        Opening <span className="text-[rgb(var(--text))]">github.com/coderxtushar/Agent-Labs</span>
      </p>
    </div>,
    document.body,
  );
}

export function GitHubLink({
  children,
  className,
  title = "Agent Labs on GitHub",
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const [launching, setLaunching] = useState(false);

  // Coming back with the browser's Back button restores the page from cache;
  // make sure the overlay is not still covering it.
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setLaunching(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  useEffect(() => {
    if (!launching) return;
    const id = window.setTimeout(() => window.location.assign(GITHUB_REPO_URL), LAUNCH_MS);
    return () => window.clearTimeout(id);
  }, [launching]);

  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    const modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (modified || reduced) return;
    event.preventDefault();
    setLaunching(true);
  };

  return (
    <>
      <a href={GITHUB_REPO_URL} onClick={onClick} title={title} rel="noreferrer" className={cx("group", className)}>
        {children}
      </a>
      {launching && <LaunchOverlay />}
    </>
  );
}
