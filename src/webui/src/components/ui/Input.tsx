import * as React from 'react'
import { cn } from '@/lib/utils'

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'border-input file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring/60 focus-visible:ring-ring/20 flex h-9 rounded-md border bg-white/[0.025] px-3 py-1 text-base shadow-[inset_0_1px_2px_rgba(0,0,0,0.08)] transition-[border-color,background-color,box-shadow] file:border-0 file:bg-transparent file:text-sm file:font-medium focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm [&::-webkit-inner-spin-button]:opacity-50 [&::-webkit-outer-spin-button]:opacity-50',
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = 'Input'

export default Input
