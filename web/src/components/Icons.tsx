// Small inline stroke icons (decorative unless labelled).
import type { ReactNode } from "react";

type P = { size?: number; label?: string };

function Svg({ size = 18, label, children, width = 2 }: P & { children: ReactNode; width?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      {children}
    </svg>
  );
}

export const Shield = (p: P) => (
  <Svg {...p} width={1.8}>
    <path d="M12 2.5 4 5.5v6c0 5 3.4 8.8 8 10 4.6-1.2 8-5 8-10v-6z" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </Svg>
);
export const Search = (p: P) => (
  <Svg {...p} width={1.8}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Svg>
);
export const Check = (p: P) => (
  <Svg {...p} width={2.4}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
);
export const Alert = (p: P) => (
  <Svg {...p} width={2.4}>
    <path d="M12 6.5v7" />
    <path d="M12 17.5h.01" />
  </Svg>
);
export const Question = (p: P) => (
  <Svg {...p} width={2.2}>
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7" />
    <path d="M12 17h.01" />
  </Svg>
);
export const Arrow = (p: P) => (
  <Svg {...p}>
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </Svg>
);
export const Inbox = (p: P) => (
  <Svg {...p} width={1.8}>
    <path d="M9 11.5 11.5 14 16 9" />
    <rect x="3.5" y="3.5" width="17" height="17" rx="4" />
  </Svg>
);
export const External = (p: P) => (
  <Svg {...p} width={1.8}>
    <path d="M14 4h6v6" />
    <path d="M20 4 11 13" />
    <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
  </Svg>
);

export function VerdictIcon({ tone, size = 18 }: { tone: "ok" | "warn" | "bad"; size?: number }) {
  return tone === "ok" ? <Check size={size} /> : tone === "bad" ? <Alert size={size} /> : <Question size={size} />;
}
