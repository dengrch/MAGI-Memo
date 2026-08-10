import * as React from 'react'
import { cn } from '@/lib/utils'

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  className?: string
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          'border-input file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring/60 focus-visible:ring-ring/20 flex min-h-[60px] w-full resize-none rounded-md border bg-white/[0.025] px-3 py-2 text-base shadow-[inset_0_1px_2px_rgba(0,0,0,0.08)] transition-[border-color,background-color,box-shadow] file:border-0 file:bg-transparent file:text-sm file:font-medium focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm',
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Textarea.displayName = 'Textarea'

export default Textarea
