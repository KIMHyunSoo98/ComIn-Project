import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ComIn 기업 리서치",
  description: "공시·뉴스 기반 기업 리서치 에이전트",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
