import { env } from 'cloudflare:workers';
import { schemaStatements } from './schema';

let schemaReady: Promise<void> | null = null;

export function getDb(): D1Database {
  if (!env.DB) {
    throw new Error('D1 binding `DB` is unavailable.');
  }
  return env.DB;
}

export function ensureSchema(): Promise<void> {
  if (!schemaReady) {
    schemaReady = initializeSchema().catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  return schemaReady;
}

async function initializeSchema(): Promise<void> {
  const db = getDb();
  await db.batch(schemaStatements.map((statement) => db.prepare(statement)));
  await db.prepare('PRAGMA optimize').run();
}
