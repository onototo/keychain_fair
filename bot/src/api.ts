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

  async createOrder(payload: OrderPayload): Promise<{ order: { id: string; order_number?: string; status: string } }> {
    return this.requestJson("/api/internal/orders", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Token": this.internalToken,
      },
      body: JSON.stringify(payload),
    });
  }

  async renderOrderPreview(payload: OrderPayload): Promise<Buffer> {
    const response = await fetch(`${this.apiBaseUrl}/api/internal/order-preview/render.png`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Token": this.internalToken,
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(apiErrorMessage(errorPayload, response.status));
    }
    return Buffer.from(await response.arrayBuffer());
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
    if (typeof detail === "string") return translateApiMessage(detail);
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((item) => validationErrorMessage(item))
        .filter(Boolean)
        .join("\n");
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return translateApiMessage(message);
    }
  }
  return `Сервер вернул ошибку ${status}. Попробуйте ещё раз или обратитесь к оператору.`;
}

const FIELD_LABELS: Record<string, string> = {
  customer_name: "имя",
  car_number: "номер авто",
  design_id: "дизайн",
  size_id: "размер",
  elements: "элементы",
  print_line_1: "текст",
  print_line_2: "текст",
  idempotency_key: "ключ заказа",
};

function validationErrorMessage(item: unknown): string {
  if (!item || typeof item !== "object") return "Ошибка валидации. Проверьте данные заказа.";

  const error = item as { msg?: unknown; loc?: unknown; ctx?: unknown };
  const field = fieldName(error.loc);
  const msg = typeof error.msg === "string" ? error.msg : "Ошибка валидации";
  const translated = translateApiMessage(msg, field);
  return translated || (field ? `Проверьте поле «${fieldLabel(field)}».` : "Ошибка валидации. Проверьте данные заказа.");
}

function fieldName(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  const path = loc.filter((item) => item !== "body").map(String);
  return path[0] || "";
}

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] || field;
}

function translateApiMessage(message: string, field = ""): string {
  const cleaned = message.replace(/^Value error,\s*/i, "").trim();

  if (cleaned === "Car number must use BY format 1234 AB-7 with Latin letters") {
    return "Введите номер авто в белорусском формате 1234 AB-7, буквы должны быть латиницей.";
  }
  if (cleaned === "Name must be from 2 to 30 characters") {
    return "Имя должно быть от 2 до 30 символов.";
  }
  if (cleaned === "Name may contain only Latin/Cyrillic letters and spaces") {
    return "Имя может содержать только русские или латинские буквы и пробелы.";
  }
  if (cleaned === "Print text is required for custom designs") {
    return "Введите текст для печати.";
  }
  if (cleaned === "Order draft is incomplete") {
    return "Не все данные заказа заполнены. Вернитесь назад и проверьте заказ.";
  }
  if (cleaned === "Order contains prohibited words. You can create a new order in 5 minutes.") {
    return "В заказе есть запрещённые слова. Новый заказ можно будет создать через 5 минут.";
  }
  if (cleaned === "You can have only 2 unpaid orders. Please pay for an existing order before creating another one.") {
    return "У вас уже есть 2 неоплаченных заказа. Оплатите один из них перед созданием нового.";
  }

  const customTextLimit = cleaned.match(/^Custom text is too long\. Maximum length is (\d+) characters\.$/);
  if (customTextLimit) {
    return `Текст слишком длинный. Максимум ${customTextLimit[1]} символов.`;
  }

  const muted = cleaned.match(/^You are temporarily muted\. Try again in (\d+) seconds\.$/);
  if (muted) {
    return `Временно нельзя создать заказ. Попробуйте снова через ${formatSeconds(Number(muted[1]))}.`;
  }

  if (cleaned === "Field required") {
    return field ? `Заполните поле «${fieldLabel(field)}».` : "Заполните обязательные поля заказа.";
  }

  const minLength = cleaned.match(/^String should have at least (\d+) characters?$/);
  if (minLength) {
    if (field === "customer_name") return "Имя должно быть от 2 до 30 символов.";
    return `Поле «${fieldLabel(field)}» должно быть не короче ${minLength[1]} символов.`;
  }

  const maxLength = cleaned.match(/^String should have at most (\d+) characters?$/);
  if (maxLength) {
    if (field === "customer_name") return "Имя должно быть от 2 до 30 символов.";
    return `Поле «${fieldLabel(field)}» должно быть не длиннее ${maxLength[1]} символов.`;
  }

  return cleaned;
}

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "несколько секунд";
  if (seconds < 60) return `${seconds} сек.`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} мин.`;
}
