import { chatGPTSignInPath, getChatGPTUser } from '@/app/chatgpt-auth';
import { ensureSchema, getDb } from '@/db';
import { apiError } from './http';

export type AppUser = {
  id: string;
  email: string;
  displayName: string;
};

export async function requireAppUser(): Promise<AppUser | Response> {
  const user = await getChatGPTUser();
  if (!user) {
    return apiError(401, 'AUTH_REQUIRED', '请先使用 ChatGPT 登录。', {
      details: { sign_in_url: chatGPTSignInPath('/') },
    });
  }

  await ensureSchema();
  const now = new Date().toISOString();
  await getDb()
    .prepare(
      `INSERT INTO users (id, email, display_name, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         email = excluded.email,
         display_name = excluded.display_name,
         updated_at = excluded.updated_at`,
    )
    .bind(user.userId, user.email, user.displayName, now, now)
    .run();

  return {
    id: user.userId,
    email: user.email,
    displayName: user.displayName,
  };
}

export function isResponse(value: AppUser | Response): value is Response {
  return value instanceof Response;
}
