// Relative timestamp helpers shared by the workspace history panel and the admin
// tables. `timeAgo` gives a scannable "3 min ago"; `exactWhen` gives the precise
// locale string we hang in a `title=` tooltip so the exact time is one hover away
// (relative for the eye, absolute for the record). Anything older than ~30 days
// falls back to an absolute date so distant timestamps stay unambiguous.
export function timeAgo(input) {
  const then = new Date(input);
  if (Number.isNaN(then.getTime())) return "";
  const secs = Math.round((Date.now() - then.getTime()) / 1000);
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs === 1 ? "" : "s"} ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return then.toLocaleDateString();
}

// The precise, unambiguous timestamp — used as the hover tooltip beside timeAgo.
export function exactWhen(input) {
  const d = new Date(input);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}
