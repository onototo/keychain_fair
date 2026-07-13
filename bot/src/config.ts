export type BotConfig = {
  telegramBotToken: string;
  apiBaseUrl: string;
  internalApiToken: string;
  databaseUrl: string;
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
    apiBaseUrl: (env.API_BASE_URL || "http://127.0.0.1:8080").replace(/\/+$/, ""),
    internalApiToken: requireEnv(env, "INTERNAL_API_TOKEN"),
    databaseUrl: requireEnv(env, "BOT_DATABASE_URL"),
  };
}
