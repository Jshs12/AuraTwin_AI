/** Format backend RFC 3339 instants consistently for users in Asia/Kolkata. */
export function formatISTTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "time unavailable";
  const clock = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hour12: true,
  }).format(date);
  return `${clock} IST`;
}
