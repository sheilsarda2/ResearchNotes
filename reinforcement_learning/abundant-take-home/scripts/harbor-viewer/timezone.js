// Harbor's timestamps without offsets come from this workspace's UTC clock.
// Use fixed PST (UTC-08:00), including during daylight saving time.
const pstFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "Etc/GMT+8",
  dateStyle: "short",
  timeStyle: "short",
});

export function parseTimestamp(value) {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
    if (!hasOffset) value += "Z";
  }
  return new Date(value);
}

export function formatTimestamp(value, includeZone = false) {
  if (!value) return "-";
  const date = parseTimestamp(value);
  if (Number.isNaN(date.getTime())) return "-";
  return pstFormatter.format(date) + (includeZone ? " PST" : "");
}
