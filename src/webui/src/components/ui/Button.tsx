import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip'
import { cn } from '@/lib/utils'

// eslint-disable-next-line react-refresh/only-export-components
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-45 active:translate-y-px [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'border border-primary/80 bg-primary text-primary-foreground shadow-[inset_0_1px_rgba(255,255,255,0.14),0_1px_2px_rgba(0,0,0,0.12)] hover:bg-[#6b75da]',
        destructive: 'border border-destructive/80 bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline: 'border border-border bg-white/[0.025] text-foreground shadow-[inset_0_1px_rgba(255,255,255,0.025)] hover:border-white/15 hover:bg-accent hover:text-accent-foreground',
        secondary: 'border border-transparent bg-secondary text-secondary-foreground hover:bg-accent',
        ghost: 'border border-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
        link: 'text-[#7170ff] underline-offset-4 hover:text-[#828fff] hover:underline'
      },
      size: {
        default: 'h-9 px-3.5 py-2',
        sm: 'h-8 rounded-md px-2.5 text-xs',
        lg: 'h-10 rounded-md px-6',
        icon: 'size-8'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
  VariantProps<typeof buttonVariants> {
  asChild?: boolean
  side?: 'top' | 'right' | 'bottom' | 'left'
  tooltip?: string
  tooltipAlign?: 'start' | 'center' | 'end'
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({
    className,
    variant,
    tooltip,
    tooltipAlign = 'start',
    size,
    side = 'right',
    asChild = false,
    ...props
  }, ref) => {
    const Comp = asChild ? Slot : 'button'
    if (!tooltip) {
      return (
        <Comp
          className={cn(buttonVariants({ variant, size, className }), 'cursor-pointer')}
          ref={ref}
          {...props}
        />
      )
    }

    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Comp
              className={cn(buttonVariants({ variant, size, className }), 'cursor-pointer')}
              ref={ref}
              {...props}
            />
          </TooltipTrigger>
          <TooltipContent side={side} align={tooltipAlign}>{tooltip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }
)
Button.displayName = 'Button'

export type ButtonVariantType = Exclude<
  NonNullable<Parameters<typeof buttonVariants>[0]>['variant'],
  undefined
>

export default Button
