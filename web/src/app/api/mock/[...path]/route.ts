import { routeMock } from "@/lib/rfq/mock/router";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: Request, { params }: Ctx): Promise<Response> {
  return routeMock(req, (await params).path);
}

export async function POST(req: Request, { params }: Ctx): Promise<Response> {
  return routeMock(req, (await params).path);
}
