export function readableKnowledgeLabel(value: string): string {
  return value.toLowerCase().split("_").filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function knowledgeLoadFailure(error: unknown): { title: string; description: string; detail: string } {
  const candidate = error as { status?: unknown } | null;
  const status = typeof candidate?.status === "number" ? candidate.status : undefined;
  const detail = status ? `HTTP ${status}` : "Network or service error";
  if (status === 401) return { title: "Sign in again to view building knowledge", description: "Your session is no longer valid.", detail };
  if (status === 403) return { title: "This account cannot access this building’s knowledge", description: "Building access is enforced by the server. Select an authorized building or contact an administrator.", detail };
  if (status === 404) return { title: "The selected building was not found", description: "Refresh the authorized building list and try again.", detail };
  if (status === 503) return { title: "Building knowledge is temporarily unavailable", description: "The knowledge service configuration is not ready. No document data was changed.", detail };
  if (status && status >= 500) return { title: "Building knowledge service error", description: "The server could not load this building’s documents. Try again later.", detail };
  if (status) return { title: "Building knowledge request was rejected", description: "Review the technical status for this request.", detail };
  return { title: "Could not connect to building knowledge", description: "Check the application connection and try again.", detail };
}

export function knowledgeStatusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "READY" || status === "ACTIVE") return "success";
  if (status === "INGESTING" || status === "REGISTERED") return "warning";
  if (status === "FAILED") return "danger";
  return "neutral";
}
