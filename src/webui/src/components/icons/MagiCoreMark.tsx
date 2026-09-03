import { cn } from '@/lib/utils'

type MagiCoreMarkProps = {
  className?: string
  variant?: 'outline' | 'solid'
}

export default function MagiCoreMark({ className, variant = 'outline' }: MagiCoreMarkProps) {
  if (variant === 'solid') {
    return (
      <svg
        aria-hidden="true"
        viewBox="0 0 64 64"
        className={cn('size-6 shrink-0', className)}
      >
        <g fill="currentColor">
          <path d="M14 6h36v9L39 26H25L14 15z" />
          <path d="M3 35h13l11 10v13H3z" />
          <path d="M48 35H61v23H37V45z" />
        </g>
        <path
          d="M39 26l9 9M37 45H27M16 35l9-9"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    )
  }

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
      <path d="M14 6h36v9L39 26H25L14 15z" />
      {/* Lower-left core (Casper) */}
      <path d="M3 35h13l11 10v13H3z" />
      {/* Lower-right core (Melchior) */}
      <path d="M48 35H61v23H37V45z" />
      {/* Three links forming the central hexagon */}
      <path d="M39 26l9 9M37 45H27M16 35l9-9" />
    </svg>
  )
}
