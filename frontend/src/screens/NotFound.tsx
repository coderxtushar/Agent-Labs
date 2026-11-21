import { Link } from "react-router-dom";

import { EmptyState, PageHeader } from "../components/ui";

export default function NotFound() {
  return (
    <div className="space-y-6">
      <PageHeader title="Page not found" />
      <EmptyState
      title="Page not found"
      description="That route does not exist."
      action={
        <Link to="/overview" className="accent-text text-xs font-medium">
          Back to overview
        </Link>
      }
      />
    </div>
  );
}
