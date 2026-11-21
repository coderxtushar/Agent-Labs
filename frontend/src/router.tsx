import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

import Layout from "./components/Layout";
import { LoadingRows } from "./components/ui";

const Agents = lazy(() => import("./screens/Agents"));
const Home = lazy(() => import("./screens/Home"));
const Analytics = lazy(() => import("./screens/Analytics"));
const Benchmarks = lazy(() => import("./screens/Benchmarks"));
const CompareStudio = lazy(() => import("./screens/CompareStudio"));
const ExperimentDetail = lazy(() => import("./screens/ExperimentDetail"));
const Experiments = lazy(() => import("./screens/Experiments"));
const Failures = lazy(() => import("./screens/Failures"));
const PublishedResults = lazy(() => import("./screens/PublishedResults"));
const Leaderboard = lazy(() => import("./screens/Leaderboard"));
const LocalModels = lazy(() => import("./screens/LocalModels"));
const ModelProfile = lazy(() => import("./screens/ModelProfile"));
const NotFound = lazy(() => import("./screens/NotFound"));
const Overview = lazy(() => import("./screens/Overview"));
const RunDetail = lazy(() => import("./screens/RunDetail"));
const Settings = lazy(() => import("./screens/Settings"));
const SystemMonitor = lazy(() => import("./screens/SystemMonitor"));
const TaskDetail = lazy(() => import("./screens/TaskDetail"));
const Trajectories = lazy(() => import("./screens/Trajectories"));

function page(node: ReactNode) {
  return <Suspense fallback={<LoadingRows rows={6} label="Loading page" />}>{node}</Suspense>;
}

export const router = createBrowserRouter([
  // Public home page: what Agent Labs is, with links into every feature.
  { path: "/", element: page(<Home />) },
  {
    // The app: every feature page shares the sidebar shell.
    element: <Layout />,
    children: [
      { path: "overview", element: page(<Overview />) },
      // workspace
      { path: "leaderboard", element: page(<Leaderboard />) },
      { path: "leaderboard/:agentId", element: page(<ModelProfile />) },
      { path: "experiments", element: page(<Experiments />) },
      { path: "experiments/:experimentId", element: page(<ExperimentDetail />) },
      { path: "compare", element: page(<CompareStudio />) },
      { path: "comparisons", element: <Navigate to="/compare" replace /> },
      { path: "benchmarks", element: page(<Benchmarks />) },
      { path: "benchmarks/tasks/:taskKey", element: page(<TaskDetail />) },
      // analysis
      { path: "trajectories", element: page(<Trajectories />) },
      { path: "runs/:runId", element: page(<RunDetail />) },
      { path: "failures", element: page(<Failures />) },
      { path: "analytics", element: page(<Analytics />) },
      { path: "published", element: page(<PublishedResults />) },
      // system
      { path: "models", element: page(<LocalModels />) },
      { path: "agents", element: page(<Agents />) },
      { path: "system", element: page(<SystemMonitor />) },
      { path: "settings", element: page(<Settings />) },
      { path: "*", element: page(<NotFound />) },
    ],
  },
]);
