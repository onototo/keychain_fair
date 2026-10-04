import type { Catalog, Office, Order, Session } from "./types.js";

export class KeychainApi {
  constructor(
    private readonly apiBaseUrl: string,
    private readonly internalToken: string,
  ) {}

  getCatalog(): Promise<Catalog> {
    return this.requestJson<Catalog>("/api/catalog");
  }

  searchOffices(city: string): Promise<Office[]> {
    return this.requestJson<{ offices: Office[] }>(`/api/offices?city=${encodeURIComponent(city)}`).then((payload) => payload.offices || []);
  }

  async getCover(categoryId: string, productId: string): Promise<Buffer | null> {
    const response = await fetch(
      `${this.apiBaseUrl}/api/catalog/${encodeURIComponent(categoryId)}/${encodeURIComponent(productId)}/cover`,
    );
    if (response.status === 404) return null;
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(apiErrorMessage(payload, response.status));
    }
    return Buffer.from(await response.arrayBuffer());
  }

  createOrder(payload: unknown): Promise<{ order: Order; created: boolean }> {
    return this.requestJson("/api/internal/orders", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  listOrders(status: string, limit = 10, offset = 0): Promise<Order[]> {
    return this.requestJson<{ orders: Order[] }>(
      `/api/internal/orders?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`,
    ).then((payload) => payload.orders || []);
  }

  getOrder(orderId: string): Promise<Order> {
    return this.requestJson<{ order: Order }>(`/api/internal/orders/${orderId}`).then((payload) => payload.order);
  }

  markPaid(orderId: string): Promise<Order> {
    return this.requestJson<{ order: Order }>(`/api/internal/orders/${orderId}/payment`, { method: "POST" }).then(
      (payload) => payload.order,
    );
  }

  ship(orderId: string, trackingNumber: string): Promise<Order> {
    return this.requestJson<{ order: Order }>(`/api/internal/orders/${orderId}/shipment`, {
      method: "POST",
      body: JSON.stringify({ tracking_number: trackingNumber }),
    }).then((payload) => payload.order);
  }

  cancel(orderId: string): Promise<Order> {
    return this.requestJson<{ order: Order }>(`/api/internal/orders/${orderId}/cancel`, { method: "POST" }).then(
      (payload) => payload.order,
    );
  }

  async getSession(chatId: string): Promise<Session | null> {
    const payload = await this.requestJson<{ state: Session | null }>(`/api/internal/sessions/${encodeURIComponent(chatId)}`);
    return payload.state;
  }

  async saveSession(chatId: string, state: Session): Promise<void> {
    await this.requestJson(`/api/internal/sessions/${encodeURIComponent(chatId)}`, {
      method: "PUT",
      body: JSON.stringify({ state }),
    });
  }

  private async requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (path.startsWith("/api/internal/")) {
      headers.set("X-Internal-Token", this.internalToken);
    }
    if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetch(`${this.apiBaseUrl}${path}`, { ...init, headers });
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
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  if (status === 422) return "Проверьте данные заказа.";
  return `Ошибка сервера (${status})`;
}
