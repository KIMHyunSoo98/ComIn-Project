// BFF 프록시: 브라우저의 /api/* 를 FastAPI(BACKEND_URL)로 그대로 전달한다.
// 덕분에 브라우저는 same-origin만 호출하고(백엔드 CORS 불필요), FastAPI를 직접 노출하지 않는다.
// upstream.body를 그대로 흘려보내므로 SSE 스트리밍도 통과한다.

const BLOCKED_REQUEST_HEADERS = new Set(["connection", "content-length", "host"]);

function backendBaseUrl(): string {
  return (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const incomingUrl = new URL(request.url);
  const upstreamUrl = new URL(`${backendBaseUrl()}/${path.map(encodeURIComponent).join("/")}`);
  upstreamUrl.search = incomingUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!BLOCKED_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set("accept-encoding", "identity");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const body = hasBody ? await request.arrayBuffer() : undefined;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      cache: "no-store",
      redirect: "manual",
    });
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.delete("content-length");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({ detail: "리서치 서버에 연결할 수 없습니다." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const PUT = proxy;
