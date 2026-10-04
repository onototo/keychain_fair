export type BotConfig = {
  telegramBotToken: string;
  apiBaseUrl: string;
  internalApiToken: string;
  adminUserIds: string[];
  paymentCardNumber: string;
  paymentCardHolder: string;
  onlinePaymentUrl: string;
};

function requireEnv(env: NodeJS.ProcessEnv, key: string): string {
  const value = env[key]?.trim();
  if (!value) {
    throw new Error(`${key} is required`);
  }
  return value;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): BotConfig {
  return {
    telegramBotToken: requireEnv(env, "TELEGRAM_BOT_TOKEN"),
    apiBaseUrl: (env.API_BASE_URL || "http://127.0.0.1:8120").replace(/\/+$/, ""),
    internalApiToken: requireEnv(env, "INTERNAL_API_TOKEN"),
    adminUserIds: (env.ADMIN_TELEGRAM_USER_IDS || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    paymentCardNumber: (env.PAYMENT_CARD_NUMBER || "").trim(),
    paymentCardHolder: (env.PAYMENT_CARD_HOLDER || "").trim(),
    onlinePaymentUrl: (env.ONLINE_PAYMENT_URL || "").trim(),
  };
}
