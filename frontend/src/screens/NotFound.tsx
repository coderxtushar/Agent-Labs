import { Link } from "react-router-dom";

import { EmptyState } from "../components/ui";

export default function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      description="That route does not exist."
      action={
        <Link to="/" className="accent-text text-xs font-medium">
          Back to overview
        </Link>
      }
    />
  );
}
