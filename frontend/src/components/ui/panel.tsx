import { cn } from "@/lib/utils";

export function Panel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <section
      className={cn(
        "rounded-md border border-border bg-card/95 p-5 text-card-foreground shadow-sm shadow-black/15 transition-colors",
        className
      )}
      {...props}
    />
  );
}

export function PanelHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mb-4 flex items-start justify-between gap-3", className)} {...props} />;
}

export function PanelTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-base font-semibold", className)} {...props} />;
}
