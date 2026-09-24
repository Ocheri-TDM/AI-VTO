"use client";

import { ResearchWorkspace } from "./research/research-workspace";

/** Backward-compatible component name for integrations that imported Workspace. */
export function Workspace() {
  return <ResearchWorkspace />;
}
