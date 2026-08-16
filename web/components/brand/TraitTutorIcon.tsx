"use client";

import type { CSSProperties, SVGProps } from "react";

/** Product-owned learning icons. Utility controls still use familiar system symbols. */
export type TraitTutorIconName =
  | "home" | "chat" | "personality" | "courseware" | "matched" | "mismatched"
  | "standard" | "motivation" | "srl" | "measurement" | "experiment"
  | "analytics" | "research" | "profile" | "quiz" | "explore" | "visualize"
  | "solve" | "mastery" | "learning" | "settings";

interface TraitTutorIconProps extends Omit<SVGProps<SVGSVGElement>, "color"> {
  name: TraitTutorIconName;
  size?: number;
  strokeWidth?: number;
}

const generatedIconNames = new Set<TraitTutorIconName>([
  "home",
  "personality",
  "courseware",
  "matched",
  "mismatched",
  "standard",
  "motivation",
  "srl",
  "measurement",
  "experiment",
  "analytics",
  "research",
  "profile",
]);

function Glyph({ name }: { name: TraitTutorIconName }) {
  const line = { fill: "none", stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (name) {
    case "home": return <><path {...line} d="M4 11.5 12 5l8 6.5V20H4v-8.5Z" /><path {...line} d="M9 20v-5h6v5" /><circle cx="17" cy="8" r="1.25" fill="currentColor" /></>;
    case "chat": return <><path {...line} d="M4 5.5h16v10H9l-5 3v-13Z" /><path {...line} d="M8 10h8M8 13h5" /></>;
    case "personality": return <><path {...line} d="M8 6.5 12 4l4 2.5v4L19 13l-3 2.5V19l-4 2-4-2v-3.5L5 13l3-2.5v-4Z" /><path {...line} d="M10 10.5c.8-1 3.2-1 4 0M9.5 14h5" /></>;
    case "courseware": return <><path {...line} d="M7 3.5h8l3 3V20.5H7z" /><path {...line} d="M15 3.5v3h3M10 10h5M10 13.5h5M10 17h3" /><circle cx="18.5" cy="18.5" r="2" fill="currentColor" /></>;
    case "matched": return <><circle {...line} cx="12" cy="12" r="8.5" /><path {...line} d="m8.5 12 2.3 2.3 4.8-5" /></>;
    case "mismatched": return <><circle {...line} cx="12" cy="12" r="8.5" /><path {...line} d="m9 9 6 6m0-6-6 6" /></>;
    case "standard": return <><path {...line} d="m4 8 8-4 8 4-8 4-8-4Z" /><path {...line} d="m4 12 8 4 8-4M4 16l8 4 8-4" /></>;
    case "motivation": return <><path {...line} d="M5 17v-4l4-4 3 3 6-6" /><path {...line} d="M14 6h4v4" /><path {...line} d="M5 20h14" /></>;
    case "srl": return <><path {...line} d="M8 5.5A5.5 5.5 0 0 0 8 17c1 0 1.5 1 4 2.5 2.5-1.5 3-2.5 4-2.5A5.5 5.5 0 0 0 16 5.5c-1.8 0-3 .8-4 2-1-1.2-2.2-2-4-2Z" /><circle {...line} cx="8" cy="10" r="1.5" /><circle {...line} cx="16" cy="10" r="1.5" /><path {...line} d="m9.5 11 2.5 2 2.5-2" /></>;
    case "measurement": return <><path {...line} d="M8 5h8v15H8z" /><path {...line} d="M10 5V3h4v2M10.5 10h3M10.5 13h3M10.5 16h2" /><path {...line} d="m5 10 1.2 1.2L8 9.5" /></>;
    case "experiment": return <><path {...line} d="M6 6h5v5H6zM13 6h5v5h-5zM9.5 14h5v5h-5z" /><path {...line} d="M11 8.5h2M8.5 11v3M15.5 11v3" /></>;
    case "analytics": return <><path {...line} d="M5 19v-5M10 19V9M15 19v-3M20 19V6" /><path {...line} d="m5 10 5-4 5 3 5-5" /><circle cx="20" cy="4" r="1.25" fill="currentColor" /></>;
    case "research": return <><path {...line} d="M4.5 6.5c3-1.2 5.5-.7 7.5 1.5 2-2.2 4.5-2.7 7.5-1.5V18c-3-1.2-5.5-.7-7.5 1.5-2-2.2-4.5-2.7-7.5-1.5V6.5Z" /><path {...line} d="M12 8v11M16.5 13.5l3 3M18 13a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></>;
    case "profile": return <><path {...line} d="m12 3 7 4v10l-7 4-7-4V7l7-4Z" /><circle {...line} cx="12" cy="10" r="2.5" /><path {...line} d="M8.5 17c.8-2.4 2-3.5 3.5-3.5s2.7 1.1 3.5 3.5" /></>;
    case "quiz": return <><path {...line} d="M7 4h10v16H7z" /><path {...line} d="M9 4V2.5h6V4M9.5 10.5l1.2 1.2 2-2.2M9.5 15.5l1.2 1.2 2-2.2M14.5 10.5H16M14.5 15.5H16" /></>;
    case "explore": return <><circle {...line} cx="10.5" cy="10.5" r="5.5" /><path {...line} d="m15 15 4.5 4.5M10.5 7.5v3l2 1" /><path {...line} d="M4 20h8" /></>;
    case "visualize": return <><path {...line} d="M5 19V5M5 19h14M9 16v-4M13 16V8M17 16v-7" /><path {...line} d="m8 9 4-3 4 1 3-3" /></>;
    case "solve": return <><path {...line} d="M6 5h12v14H6z" /><path {...line} d="m9 9 2 2-2 2M13 14h3M13 9h3" /></>;
    case "mastery": return <><path {...line} d="m4 8 8-4 8 4-8 4-8-4Z" /><path {...line} d="M7 11v4.5c2.8 2 7.2 2 10 0V11M20 9v5" /><circle cx="20" cy="15.5" r="1.25" fill="currentColor" /></>;
    case "learning": return <><path {...line} d="M4.5 7c3.2-1.3 5.7-.9 7.5 1.2C13.8 6.1 16.3 5.7 19.5 7v10c-3.1-1.3-5.6-.9-7.5 1.2C10.1 16.1 7.6 15.7 4.5 17V7Z" /><path {...line} d="M12 8.2v10" /></>;
    case "settings": return <><circle {...line} cx="12" cy="12" r="3" /><path {...line} d="M12 4v2M12 18v2M20 12h-2M6 12H4M17.7 6.3l-1.4 1.4M7.7 16.3l-1.4 1.4M17.7 17.7l-1.4-1.4M7.7 7.7 6.3 6.3" /></>;
    default: return null;
  }
}

export function TraitTutorIcon({
  name,
  size = 20,
  strokeWidth = 1.7,
  className,
  style,
  ...props
}: TraitTutorIconProps) {
  const usesGeneratedAsset = generatedIconNames.has(name);
  const iconClassName = [
    usesGeneratedAsset ? `traittutor-generated-icon traittutor-generated-icon--${name}` : "",
    className,
  ].filter(Boolean).join(" ");

  if (usesGeneratedAsset) {
    const generatedStyle = {
      width: size,
      height: size,
      "--traittutor-icon-light": `url('/brand/icons/light/${name}.png')`,
      "--traittutor-icon-dark": `url('/brand/icons/dark/${name}.png')`,
      "--traittutor-icon-snow": `url('/brand/icons/snow/${name}.png')`,
      "--traittutor-icon-teal": `url('/brand/icons/teal/${name}.png')`,
      ...style,
    } as CSSProperties;

    return <span
      className={iconClassName}
      style={generatedStyle}
      data-icon-name={name}
      role={props.role ?? (props["aria-label"] ? "img" : undefined)}
      aria-label={props["aria-label"]}
      aria-hidden={props["aria-label"] ? undefined : true}
    />;
  }

  return <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={iconClassName || undefined}
    style={style}
    data-icon-name={name}
    aria-hidden={props["aria-label"] ? undefined : true}
    {...props}
  ><Glyph name={name} /></svg>;
}
