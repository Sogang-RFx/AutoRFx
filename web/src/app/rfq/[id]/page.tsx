import { redirect } from "next/navigation";

export default async function RfqIndex({ params }: PageProps<"/rfq/[id]">) {
  const { id } = await params;
  redirect(`/rfq/${id}/clauses`);
}
