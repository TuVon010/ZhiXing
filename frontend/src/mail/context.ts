import { createContext, useContext } from "react";
import type { useMailWorkspace } from "./useMailWorkspace";
export const WorkspaceContext = createContext<ReturnType<
  typeof useMailWorkspace
> | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Missing mail workspace");
  return value;
}
