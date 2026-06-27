import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, Info, LucideIcon } from "lucide-react";

import { FriendlyStatusBadge } from "@/components/friendly-status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";

export function Breadcrumb({ items }: { items: Array<{ label: string; href?: string }> }) {
  return (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {items.map((item, index) => (
        <span key={`${item.label}-${index}`} className="flex items-center gap-2">
          {index > 0 ? <span>/</span> : null}
          {item.href ? <Link className="hover:text-foreground" href={item.href}>{item.label}</Link> : <span>{item.label}</span>}
        </span>
      ))}
    </nav>
  );
}

export function TopActionBar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

export function PageHeader({
  title,
  subtitle,
  breadcrumbs,
  primaryAction,
  meta
}: {
  title: string;
  subtitle: string;
  breadcrumbs?: Array<{ label: string; href?: string }>;
  primaryAction?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <section className="space-y-3">
      {breadcrumbs ? <Breadcrumb items={breadcrumbs} /> : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-normal text-foreground md:text-3xl">{title}</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{subtitle}</p>
          {meta ? <div className="mt-3">{meta}</div> : null}
        </div>
        {primaryAction ? <TopActionBar>{primaryAction}</TopActionBar> : null}
      </div>
    </section>
  );
}

export function MetricSummaryCard({
  label,
  value,
  status,
  hint,
  icon: Icon
}: {
  label: string;
  value: ReactNode;
  status?: string | number | null;
  hint?: string | null;
  icon?: LucideIcon;
}) {
  return (
    <Panel className="min-h-32">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm text-muted-foreground">{label}</div>
        {Icon ? <Icon aria-hidden="true" className="text-primary" size={18} /> : status ? <FriendlyStatusBadge value={status} /> : null}
      </div>
      <div className="mt-4 text-3xl font-semibold">{value ?? "Chưa có dữ liệu"}</div>
      {hint ? <p className="mt-3 text-sm leading-5 text-muted-foreground">{hint}</p> : null}
    </Panel>
  );
}

export function ActionHintCard({
  title,
  body,
  href,
  actionLabel,
  icon: Icon = Info
}: {
  title: string;
  body: string;
  href?: string;
  actionLabel?: string;
  icon?: LucideIcon;
}) {
  const content = (
    <Panel className="min-h-32 transition hover:border-primary/60 hover:bg-muted/35">
      <div className="flex items-start gap-3">
        <Icon aria-hidden="true" className="mt-0.5 text-primary" size={20} />
        <div className="min-w-0">
          <h3 className="text-base font-semibold">{title}</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{body}</p>
          {actionLabel ? (
            <div className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-primary">
              {actionLabel}
              <ArrowRight size={16} aria-hidden="true" />
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
  return href ? <Link href={href}>{content}</Link> : content;
}

export function TechnicalAppendix({ children }: { children: ReactNode }) {
  return (
    <details className="rounded-md border border-border/80 bg-background/35 p-3 text-sm">
      <summary className="cursor-pointer text-muted-foreground">Phụ lục kỹ thuật</summary>
      <div className="mt-3 space-y-2 text-muted-foreground">{children}</div>
    </details>
  );
}

export function EmptyStateCard({
  title,
  description,
  actions,
  children
}: {
  title: string;
  description: string;
  actions?: Array<{ label: string; href: string; variant?: "primary" | "secondary" }>;
  children?: ReactNode;
}) {
  return (
    <Panel className="min-h-52 border-dashed bg-card/80">
      <div className="mx-auto flex max-w-2xl flex-col items-center text-center">
        <div className="flex h-11 w-11 items-center justify-center rounded-md border border-border bg-muted text-primary">
          <Info size={20} aria-hidden="true" />
        </div>
        <h2 className="mt-4 text-xl font-semibold">{title}</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
        {actions?.length ? (
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            {actions.map((action) => (
              <Button key={action.label} asChild variant={action.variant ?? "secondary"}>
                <Link href={action.href}>{action.label}</Link>
              </Button>
            ))}
          </div>
        ) : null}
        {children ? <div className="mt-5 w-full">{children}</div> : null}
      </div>
    </Panel>
  );
}
