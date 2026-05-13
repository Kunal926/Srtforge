// Display-format helpers shared between Queue and History views.

export const EMPTY_VALUE = "\u2014";
export const DONE_VALUE = "\u2713";

export const formatDuration = (sec: number): string => {
  if (!Number.isFinite(sec) || sec <= 0) return EMPTY_VALUE;
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
};

export const formatTotalDuration = (sec: number): string => {
  if (!Number.isFinite(sec) || sec <= 0) return "0:00:00";
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};

export const parseClockDuration = (value: string | undefined): number | null => {
  if (!value) return null;
  const parts = value.trim().split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  const nums = parts.map((part) => Number(part));
  if (nums.some((part) => !Number.isFinite(part) || part < 0)) return null;
  const [h, m, s] = parts.length === 3 ? nums : [0, nums[0], nums[1]];
  if (m >= 60 || s >= 60) return null;
  return h * 3600 + m * 60 + s;
};
