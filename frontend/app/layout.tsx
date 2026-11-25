import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ReleaseGuard AI — Release Governance & Flag Control Plane",
  description:
    "AI-orchestrated PR Intelligence, Flagsmith Feature Flag Control, and Bayesian Release Governance under strict Policy Gates.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full bg-[#090c12] text-slate-100 selection:bg-indigo-500/30 selection:text-indigo-200">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
