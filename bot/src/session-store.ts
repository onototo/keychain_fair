import { Pool } from "pg";
import type { DraftSession } from "./types.js";

export class SessionStore {
  private readonly pool: Pool;

  constructor(databaseUrl: string) {
    this.pool = new Pool({ connectionString: databaseUrl });
  }

  async init(): Promise<void> {
    await this.pool.query(`
      CREATE TABLE IF NOT EXISTS telegram_bot_sessions (
        chat_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT
      )
    `);
  }

  async get(chatId: string): Promise<DraftSession | null> {
    const result = await this.pool.query("SELECT state_json FROM telegram_bot_sessions WHERE chat_id = $1", [chatId]);
    const row = result.rows[0];
    return row ? (JSON.parse(row.state_json) as DraftSession) : null;
  }

  async save(chatId: string, session: DraftSession): Promise<void> {
    const now = new Date().toISOString();
    await this.pool.query(
      `
      INSERT INTO telegram_bot_sessions (chat_id, state_json, updated_at)
      VALUES ($1, $2, $3)
      ON CONFLICT (chat_id)
      DO UPDATE SET state_json = EXCLUDED.state_json, updated_at = EXCLUDED.updated_at
      `,
      [chatId, JSON.stringify(session), now],
    );
  }

  async clear(chatId: string): Promise<void> {
    await this.pool.query("DELETE FROM telegram_bot_sessions WHERE chat_id = $1", [chatId]);
  }

  async close(): Promise<void> {
    await this.pool.end();
  }
}
