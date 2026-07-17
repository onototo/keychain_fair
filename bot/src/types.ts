export type DesignSize = {
  id: string;
  label: string;
  price?: number;
  width_mm?: number;
  height_mm?: number;
  row_height_mm?: number;
  custom_text_limits?: {
    max_total_chars: number;
    max_line_1_chars: number;
    max_line_2_chars: number;
  };
};

export type DesignElement = {
  id: string;
  label: string;
  kind?: string;
  shape?: string;
};

export type Design = {
  id: string;
  name: string;
  description?: string;
  layout?: string;
  print_mode?: string;
  preview_image?: string;
  default_elements?: string[];
  default_size_id?: string;
  sizes: DesignSize[];
  elements: DesignElement[];
};

export type DraftData = {
  designId?: string;
  sizeId?: string;
  elements: string[];
  customerName?: string;
  carNumber?: string;
  printLine1?: string;
};

export type DraftStep = "design" | "size" | "elements" | "loop" | "print_text" | "name" | "car" | "confirm";

export type DraftSession = {
  draftId: string;
  step: DraftStep;
  data: DraftData;
  editing?: boolean;
};

export type TelegramUserMeta = {
  chatId: string;
  userId?: string;
  username?: string;
};

export type OrderPayload = {
  customer_name: string;
  car_number: string;
  design_id: string;
  size_id: string;
  elements: string[];
  print_line_1?: string;
  print_line_2?: string;
  idempotency_key: string;
  source: "telegram";
  telegram_chat_id: string;
  telegram_user_id?: string;
  telegram_username?: string;
};
