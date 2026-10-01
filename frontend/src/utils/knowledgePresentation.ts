export function readableKnowledgeLabel(value: string): string {
  return value.toLowerCase().split("_").filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function knowledgeStatusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "READY" || status === "ACTIVE") return "success";
  if (status === "INGESTING" || status === "REGISTERED") return "warning";
  if (status === "FAILED") return "danger";
  return "neutral";
}
