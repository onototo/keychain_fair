const orderId = window.location.pathname.split("/").filter(Boolean).pop();
const orderIdNode = document.querySelector("#orderId");
const statusLabel = document.querySelector("#statusLabel");
const updatedAt = document.querySelector("#updatedAt");
const customer = document.querySelector("#customer");
const car = document.querySelector("#car");
const details = document.querySelector("#details");

orderIdNode.textContent = orderId;

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

async function refresh() {
  const response = await fetch(`/api/orders/${orderId}`);
  const result = await response.json();
  if (!response.ok) {
    statusLabel.textContent = "Заказ не найден";
    details.textContent = result.detail || "";
    return;
  }

  const order = result.order;
  orderIdNode.textContent = order.order_number ? `Заказ #${order.order_number}` : orderId;
  statusLabel.textContent = order.status_label;
  updatedAt.textContent = formatDate(order.updated_at);
  customer.textContent = order.customer_name;
  car.textContent = order.car_number;
  details.textContent = order.error_message || `${order.design_name} · ${order.size_label}`;
  statusLabel.className = order.status === "error" ? "pill error" : "";
}

refresh();
setInterval(refresh, 4000);
