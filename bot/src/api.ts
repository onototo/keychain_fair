import type { Design, OrderPayload } from "./types.js";

export class KeychainApi {
  constructor(
    private readonly apiBaseUrl: string,
    private readonly internalToken: string,
  ) {}

  async getDesigns(): Promise<Design[]> {
    const payload = await this.requestJson<{ designs: Design[] }>("/api/designs");
    return payload.designs || [];
  }

  async createOrder(payload: OrderPayload): Promise<{ order: { id: string; status: string } }> {
    return this.requestJson("/api/internal/orders", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Token": this.internalToken,
      },
      body: JSON.stringify(payload),
    });
  }

  previewUrl(design: Design): string | null {
    if (!design.preview_image) return null;
    if (/^https?:\/\//i.test(design.preview_image)) return design.preview_image;
    return `${this.apiBaseUrl}${design.preview_image.startsWith("/") ? "" : "/"}${design.preview_image}`;
  }

  private async requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.apiBaseUrl}${path}`, init);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, response.status));
    }
    return payload as T;
  }
}

export function apiErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown; loc?: unknown };
      const loc = Array.isArray(first.loc) ? first.loc.filter((item) => item !== "body").join(".") : "";
      const msg = typeof first.msg === "string" ? first.msg : "ошибка валидации";
      return loc ? `${loc}: ${msg}` : msg;
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return `API request failed with status ${status}`;
}
