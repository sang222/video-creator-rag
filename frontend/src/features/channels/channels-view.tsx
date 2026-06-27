"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { getChannels, queryKeys } from "@/lib/api";
import type { ChannelSummary } from "@/lib/types";

const columnHelper = createColumnHelper<ChannelSummary>();
const columns = [
  columnHelper.accessor("name", {
    header: "Channel",
    cell: (info) => <span className="font-medium">{info.getValue()}</span>
  }),
  columnHelper.accessor("lifecycle_state", {
    header: "Lifecycle",
    cell: (info) => <StatusBadge value={info.getValue()} />
  }),
  columnHelper.accessor("health_status", {
    header: "Health",
    cell: (info) => <StatusBadge value={info.getValue()} />
  }),
  columnHelper.accessor("next_action", {
    header: "Next Action"
  }),
  columnHelper.display({
    id: "open",
    header: "",
    cell: ({ row }) => (
      <Button asChild>
        <Link href={`/channels/${row.original.id}`}>Open</Link>
      </Button>
    )
  })
];

export function ChannelsView() {
  const query = useQuery({ queryKey: queryKeys.channels, queryFn: getChannels });
  const table = useReactTable({ data: query.data ?? [], columns, getCoreRowModel: getCoreRowModel() });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading channels" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading channels" /></div>;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Channel Workspaces</h1>
          <p className="mt-1 text-sm text-muted-foreground">Lifecycle is human-decided; health only observes and warns.</p>
        </div>
        <Button asChild variant="primary">
          <Link href="/channels/new">Create Channel</Link>
        </Button>
      </div>
      {query.data.length ? (
        <Panel className="overflow-x-auto p-0">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-border">
                  {headerGroup.headers.map((header) => (
                    <th key={header.id} className="px-4 py-3 text-left font-medium text-muted-foreground">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="border-b border-border/60">
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-4 py-3 align-top">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : (
        <EmptyState title="No channels yet" body="Create a channel, compile its policy snapshot, then activate it for daily generation." />
      )}
    </div>
  );
}
