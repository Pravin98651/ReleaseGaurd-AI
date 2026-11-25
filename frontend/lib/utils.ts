import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const pct = value <= 1 && value >= -1 ? value * 100 : value;
  return `${pct.toFixed(digits)}%`;
}

export function formatRate(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffSec = Math.round((d.getTime() - Date.now()) / 1000);
  const absSec = Math.abs(diffSec);
  const suffix = diffSec >= 0 ? "from now" : "ago";
  if (absSec < 60) return `${absSec}s ${suffix}`;
  const mins = Math.floor(absSec / 60);
  if (mins < 60) return `${mins}m ${suffix}`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${suffix}`;
  const days = Math.floor(hrs / 24);
  return `${days}d ${suffix}`;
}

export function truncateHash(hash: string | null | undefined, len = 12): string {
  if (!hash) return "—";
  if (hash.length <= len) return hash;
  return `${hash.slice(0, len)}…${hash.slice(-4)}`;
}

export function isProductionEnv(envName: string | null | undefined, isProdFlag?: boolean): boolean {
  if (isProdFlag === true) return true;
  if (!envName) return false;
  const lower = envName.toLowerCase();
  return lower === "prod" || lower === "production" || lower === "live" || lower === "prd" || lower.includes("prod");
}
