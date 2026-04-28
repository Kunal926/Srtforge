// Minimal stroke icon set. Ported from the original prototype.

import type { CSSProperties, ReactNode } from "react";

interface IconProps {
  d?: string;
  size?: number;
  fill?: string;
  stroke?: string;
  sw?: number;
  viewBox?: string;
  children?: ReactNode;
  style?: CSSProperties;
}

export const Icon = ({
  d,
  size = 16,
  fill = "none",
  stroke = "currentColor",
  sw = 1.6,
  viewBox = "0 0 24 24",
  children,
  style,
}: IconProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox={viewBox}
    fill={fill}
    stroke={stroke}
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={style}
  >
    {d ? <path d={d} /> : children}
  </svg>
);

type P = Omit<IconProps, "d" | "children">;

export const I = {
  Plus: (p: P) => <Icon {...p} d="M12 5v14M5 12h14" />,
  Play: (p: P) => <Icon {...p} fill="currentColor" stroke="none" d="M8 5.5v13l11-6.5z" />,
  Pause: (p: P) => <Icon {...p} fill="currentColor" stroke="none" d="M7 5h3v14H7zM14 5h3v14h-3z" />,
  Stop: (p: P) => <Icon {...p} fill="currentColor" stroke="none" d="M6 6h12v12H6z" />,
  Trash: (p: P) => <Icon {...p} d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />,
  Settings: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </Icon>
  ),
  Folder: (p: P) => (
    <Icon {...p} d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  ),
  FolderOpen: (p: P) => (
    <Icon
      {...p}
      d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3zM3 9h18l-2 8a2 2 0 0 1-2 1.6H5A2 2 0 0 1 3 17z"
    />
  ),
  Check: (p: P) => <Icon {...p} d="M5 12.5l4 4L19 7" sw={2} />,
  X: (p: P) => <Icon {...p} d="M6 6l12 12M6 18L18 6" />,
  Min: (p: P) => <Icon {...p} d="M5 12h14" />,
  Sq: (p: P) => <Icon {...p} d="M6 6h12v12H6z" />,
  Search: (p: P) => (
    <Icon {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.35-4.35" />
    </Icon>
  ),
  Chevron: (p: P) => <Icon {...p} d="M9 6l6 6-6 6" />,
  ChevronD: (p: P) => <Icon {...p} d="M6 9l6 6 6-6" />,
  Cpu: (p: P) => (
    <Icon {...p}>
      <rect x="5" y="5" width="14" height="14" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
    </Icon>
  ),
  Wave: (p: P) => <Icon {...p} d="M3 12h2l2-7 3 14 3-9 3 6 2-4h3" />,
  List: (p: P) => <Icon {...p} d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />,
  Grid: (p: P) => (
    <Icon {...p}>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </Icon>
  ),
  Layers: (p: P) => <Icon {...p} d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 18l9 5 9-5" />,
  Inbox: (p: P) => (
    <Icon {...p} d="M3 13l3-8h12l3 8M3 13v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6M3 13h5l1 3h6l1-3h5" />
  ),
  Files: (p: P) => (
    <Icon {...p}>
      <path d="M8 4h8l4 4v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" />
      <path d="M16 4v4h4" />
      <path d="M5 7v13a1 1 0 0 0 1 1h11" />
    </Icon>
  ),
  Clock: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Icon>
  ),
  Done: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l3 3 5-6" />
    </Icon>
  ),
  Drop: (p: P) => <Icon {...p} d="M12 3l6 9a6 6 0 1 1-12 0z" />,
  Help: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01" />
    </Icon>
  ),
  Pulse: (p: P) => <Icon {...p} d="M3 12h3l2-6 4 12 3-9 2 3h4" />,
  Archive: (p: P) => (
    <Icon {...p}>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" />
    </Icon>
  ),
  Sliders: (p: P) => (
    <Icon
      {...p}
      d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M10 10v4M18 16v4"
    />
  ),
  Music: (p: P) => (
    <Icon {...p}>
      <path d="M9 18V5l11-2v13" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="17" cy="16" r="2.5" />
    </Icon>
  ),
  Antenna: (p: P) => <Icon {...p} d="M5 8a8 8 0 0 1 14 0M7.5 10a5 5 0 0 1 9 0M12 12v9M9 21h6" />,
  Tv: (p: P) => (
    <Icon {...p}>
      <rect x="3" y="6" width="18" height="13" rx="2" />
      <path d="M9 22h6M8 3l4 3 4-3" />
    </Icon>
  ),
  Film: (p: P) => (
    <Icon {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 8h18M3 16h18M7 4v16M17 4v16" />
    </Icon>
  ),
  Open: (p: P) => (
    <Icon {...p}>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5" />
    </Icon>
  ),
};
