// 모든 API 호출이 거치는 얇은 클라이언트. /api 프리픽스는 BFF 프록시로 전달된다.

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });

  if (!res.ok) {
    let message = "요청을 처리하지 못했습니다.";
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) message = data.detail;
    } catch {
      // 사용자용 기본 메시지 유지
    }
    throw new ApiError(message, res.status);
  }

  return (await res.json()) as T;
}
