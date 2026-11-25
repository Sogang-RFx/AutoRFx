import type { Metadata } from "next";
import { Source_Serif_4 } from "next/font/google";
import localFont from "next/font/local";
import { AuthGate } from "@/components/auth/auth-gate";
import { AmbientFieldCanvas } from "@/components/fx/ambient-field";
import { FxProvider } from "@/components/fx/fx-provider";
import { Intro } from "@/components/intro/intro";
import { SessionProvider } from "@/lib/rfq/session";
import "./globals.css";

const pretendard = localFont({
  src: "../../node_modules/pretendard/dist/web/variable/woff2/PretendardVariable.woff2",
  variable: "--font-pretendard",
  weight: "100 900",
  display: "swap",
  preload: false,
});

const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "AutoRFx — RFQ 분석 데모",
  description: "다국어 RFQ 패키지 분석 · 정밀 인용 추적 · 문서 간 정합성 · 사전원가 판단",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${pretendard.variable} ${sourceSerif.variable}`}>
      <body className="min-h-dvh">
        <AmbientFieldCanvas />
        <SessionProvider>
          <FxProvider>
            <Intro />
            <div id="app-root" className="contents">
              <AuthGate>{children}</AuthGate>
            </div>
          </FxProvider>
        </SessionProvider>
      </body>
    </html>
  );
}
