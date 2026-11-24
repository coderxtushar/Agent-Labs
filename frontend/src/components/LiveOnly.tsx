import { Link } from "react-router-dom";

import { GitHubLink, GitHubMark } from "./GitHubLink";
import { Card, Note, PageHeader } from "./ui";

/**
 * Stands in for a page that describes the live machine (RAM, Docker, Ollama)
 * when the UI is a static snapshot: a frozen copy would only ever show the
 * export machine's state, so it is not exported or shown.
 */
export function LiveOnly({ title, what }: { title: string; what: string }) {
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="System" title={title} description={what} />
      <Card className="space-y-3 p-5">
        <Note>
          This page reads the machine Agent Labs runs on, live. This site is a static snapshot of recorded
          results, so there is no machine behind it to read. Every run still carries the host memory evidence
          recorded while it ran, on its run page.
        </Note>
        <div className="flex flex-wrap gap-4 text-xs">
          <Link to="/experiments" className="link">
            Browse recorded experiments
          </Link>
          <GitHubLink className="link inline-flex items-center gap-1.5">
            <GitHubMark className="h-3.5 w-3.5" />
            Run Agent Labs yourself
          </GitHubLink>
        </div>
      </Card>
    </div>
  );
}
