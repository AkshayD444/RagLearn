import "./globals.css";
import type { Metadata } from "next";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata: Metadata = {
  title: "RagLearn",
  description: "Retrieval-Augmented Growth & Learning",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ThemeProvider>
      <html lang="en">
        <body className="antialiased min-h-screen">
          {children}
        </body>
      </html>
    </ThemeProvider>
  );
}