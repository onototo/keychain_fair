export type Product = {
  id: string;
  title: string;
  description: string;
  price_kopecks: number;
  price_byn: string;
  has_image: boolean;
};

export type IndexedProduct = Product & { code: string };

export type IndexedCategory = {
  id: string;
  code: string;
  title: string;
  products: IndexedProduct[];
};

export type IndexedCatalog = {
  maxUnits: number;
  byCode: Map<string, IndexedProduct>;
  byCategory: Map<string, IndexedCategory>;
  categories: IndexedCategory[];
};

export type Category = {
  id: string;
  title: string;
  products: Product[];
};

export type Catalog = {
  max_units: number;
  categories: Category[];
};

export type Office = {
  id: string;
  number: string;
  city: string;
  address: string;
};

export type OrderItem = {
  product_id: string;
  title: string;
  unit_price_kopecks: number;
  quantity: number;
  line_byn: string;
};

export type Order = {
  id: string;
  order_number_label: string;
  status: string;
  payment_method: string;
  customer_name: string;
  phone: string;
  telegram_chat_id?: string | null;
  telegram_username?: string | null;
  office_number: string;
  office_city: string;
  office_address: string;
  total_byn: string;
  tracking_number?: string | null;
  shipment_status: string;
  items: OrderItem[];
};

export type CartLine = {
  productId: string;
  quantity: number;
};

export type PaymentMethod = "cod" | "transfer" | "online";

export type Session = {
  step: "browse" | "city" | "name" | "phone" | "confirm" | "admin_tracking";
  cart: CartLine[];
  cartPage?: number;
  paymentMethod?: PaymentMethod;
  offices?: Office[];
  officePage?: number;
  officeId?: string;
  customerName?: string;
  phone?: string;
  draftId?: string;
  adminOrderId?: string;
};

export function emptySession(): Session {
  return { step: "browse", cart: [] };
}
