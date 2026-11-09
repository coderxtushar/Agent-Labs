import { createBrowserRouter } from "react-router-dom";

import Layout from "./components/Layout";
import Agents from "./screens/Agents";
import Benchmarks from "./screens/Benchmarks";
import Comparisons from "./screens/Comparisons";
import ExperimentDetail from "./screens/ExperimentDetail";
import Experiments from "./screens/Experiments";
import Failures from "./screens/Failures";
import NotFound from "./screens/NotFound";
import Overview from "./screens/Overview";
import RunDetail from "./screens/RunDetail";
import Settings from "./screens/Settings";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "benchmarks", element: <Benchmarks /> },
      { path: "agents", element: <Agents /> },
      { path: "experiments", element: <Experiments /> },
      { path: "experiments/:experimentId", element: <ExperimentDetail /> },
      { path: "runs/:runId", element: <RunDetail /> },
      { path: "comparisons", element: <Comparisons /> },
      { path: "failures", element: <Failures /> },
      { path: "settings", element: <Settings /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
