import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "LONG-FORM YOUTUBE OPERATING DASHBOARD",
  description: "Buồng lái vận hành YouTube long-form cho VCOS"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className="dark">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
