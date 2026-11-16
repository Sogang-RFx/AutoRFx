import { AppShell } from "@/components/shell/app-shell";

export default async function RfqLayout({ children, params }: LayoutProps<"/rfq/[id]">) {
  const { id } = await params;
  return <AppShell rfqId={decodeURIComponent(id)}>{children}</AppShell>;
}
