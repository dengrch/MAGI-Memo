import { cn } from '@/lib/utils'

type MagiCoreMarkProps = {
  className?: string
}

export default function MagiCoreMark({ className }: MagiCoreMarkProps) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 64 64"
      className={cn('size-6 shrink-0', className)}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinejoin="round"
      strokeLinecap="round"
    >
      {/* Top core (Balthasar) */}
      <path d="M16 7h32v10l-10 10H26L16 17z" />
      {/* Lower-left core (Casper) */}
      <path d="M18 35l8 8v14H2V35z" />
      {/* Lower-right core (Melchior) */}
      <path d="M46 35l-8 8v14h24V35z" />
      {/* Three links forming the central hexagon */}
      <path d="M38 27l8 8m-8 8H26m-8-8l8-8" />
      <text
        x="32"
        y="35"
        fontFamily="Georgia, serif"
        fontSize="5.5"
        fontWeight="bold"
        textAnchor="middle"
        dominantBaseline="central"
        fill="currentColor"
        stroke="none"
        letterSpacing="0.2"
      >
        MAGI
      </text>
    </svg>
  )
}
