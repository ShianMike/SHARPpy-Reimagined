import type { SVGProps } from "react";

export function BrandMark({
  className,
  ...props
}: SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 40 40"
      {...props}
    >
      <circle
        cx="20"
        cy="20"
        r="14.4"
        stroke="var(--brand-mark-ink, currentColor)"
        strokeDasharray="35 8 33 14.48"
        strokeLinecap="round"
        strokeWidth="var(--brand-orbit-stroke)"
        transform="rotate(-34 20 20)"
      />
      <path
        d="M10.7 29.6c4.5-2.4 5.8-5 3.9-7.3-1.8-2.2-.4-4.4 3.3-5.3 4.3-1.1 5.2-3.5 3.7-5.9-.8-1.4 1.4-2.6 7-3.8"
        stroke="var(--brand-orange)"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="var(--brand-profile-stroke)"
      />
    </svg>
  );
}
