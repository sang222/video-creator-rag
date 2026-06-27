import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex min-h-6 items-center rounded-md border px-2 py-0.5 text-xs font-medium", {
  variants: {
    tone: {
      neutral: "border-border bg-muted text-muted-foreground",
      success: "border-emerald-500/30 bg-emerald-500/12 text-emerald-200",
      warning: "border-amber-500/35 bg-amber-500/12 text-amber-100",
      danger: "border-rose-500/35 bg-rose-500/12 text-rose-100",
      info: "border-cyan-500/35 bg-cyan-500/12 text-cyan-100"
    }
  },
  defaultVariants: {
    tone: "neutral"
  }
});

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
