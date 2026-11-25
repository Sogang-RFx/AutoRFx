import type { SVGProps } from "react";

/** Small 1.6px-stroke icon set. Decorative by default (aria-hidden). */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconLock = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="11" width="14" height="9" rx="1.5" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </Svg>
);
export const IconWarn = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4 2.8 19.5h18.4Z" />
    <path d="M12 10v4.5M12 17.2v.1" />
  </Svg>
);
export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5 12.5 4.2 4L19 7" />
  </Svg>
);
export const IconFlag = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
  </Svg>
);
export const IconUpload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
  </Svg>
);
export const IconDoc = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3h8l4 4v14H6Z" />
    <path d="M14 3v4h4M9 12h6M9 16h6" />
  </Svg>
);
export const IconTimeline = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="6" cy="6" r="2" />
    <circle cx="6" cy="18" r="2" />
    <path d="M6 8v8M11 6h9M11 18h9M11 12h6" />
  </Svg>
);
export const IconTable = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3.5" y="5" width="17" height="14" rx="1" />
    <path d="M3.5 10h17M3.5 14.5h17M9.5 5v14" />
  </Svg>
);
export const IconCalc = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="3" width="14" height="18" rx="1.5" />
    <path d="M8 7h8M8.5 12h.1M12 12h.1M15.5 12h.1M8.5 16h.1M12 16h.1M15.5 16h.1" />
  </Svg>
);
export const IconChat = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 5h16v11H9l-5 4Z" />
  </Svg>
);
export const IconGauge = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 18a8 8 0 1 1 16 0" />
    <path d="m12 18 4-6" />
  </Svg>
);
export const IconPresent = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="12" rx="1" />
    <path d="M12 16v4M8 20h8" />
  </Svg>
);
export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v12M7 11l5 5 5-5M4 20h16" />
  </Svg>
);
export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
);
export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);
/** Two documents side by side: cross-document consistency. */
export const IconCompare = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 5h7v14h-7ZM13.5 5h7v14h-7Z" />
    <path d="M6 9.5h2M16 9.5h2M6 13h2M16 13h2" />
  </Svg>
);
export const IconPause = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M10 9v6M14 9v6" />
  </Svg>
);
export const IconSpinner = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3a9 9 0 1 0 9 9" />
  </Svg>
);
/** Settings: a gear. */
export const IconGear = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
  </Svg>
);
export const IconUser = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="8" r="3.5" />
    <path d="M5 20c.6-3.6 3.4-5.5 7-5.5s6.4 1.9 7 5.5" />
  </Svg>
);
export const IconKey = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="15" r="3.5" />
    <path d="m10.5 12.5 8-8M15.5 7.5l2.5 2.5" />
  </Svg>
);
export const IconMic = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6" />
  </Svg>
);
/** Microphone with a slash: not listening. */
export const IconMicOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 9V6a3 3 0 0 1 5.7-1.3M15 10.5V11a3 3 0 0 1-4.6 2.5" />
    <path d="M5.5 11a6.5 6.5 0 0 0 10.2 5.3M18.5 11a6.4 6.4 0 0 1-.5 2.5M12 17.5V21M9 21h6M4 4l16 16" />
  </Svg>
);
export const IconSpeaker = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z" />
    <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
  </Svg>
);
/** A filled-outline square: stop. */
export const IconStop = (p: IconProps) => (
  <Svg {...p}>
    <rect x="6" y="6" width="12" height="12" rx="1.5" />
  </Svg>
);
/** Screen effects: a four-point sparkle. */
export const IconSparkle = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5c.7 4.6 2.9 6.8 7.5 7.5-4.6.7-6.8 2.9-7.5 7.5-.7-4.6-2.9-6.8-7.5-7.5 4.6-.7 6.8-2.9 7.5-7.5Z" />
    <path d="M19 3v3M17.5 4.5h3" />
  </Svg>
);
/** Play again: an arrow running back round. */
export const IconReplay = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5M4.5 4.5v4h4" />
  </Svg>
);
/** Three connected nodes: the RFQ flow view. */
export const IconTopology = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="6" cy="7" r="2.2" />
    <circle cx="18" cy="7" r="2.2" />
    <circle cx="12" cy="18" r="2.2" />
    <path d="M8.2 7h7.6M7.2 8.9l3.7 7M16.8 8.9l-3.7 7" />
  </Svg>
);
export const IconList = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 6h11M9 12h11M9 18h11" />
    <path d="M4.5 6h.1M4.5 12h.1M4.5 18h.1" />
  </Svg>
);
