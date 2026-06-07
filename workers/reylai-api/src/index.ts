const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store"
};

const TEXT_HEADERS = {
  "content-type": "text/plain; charset=utf-8",
  "cache-control": "no-store"
};

const VALID_ID = /^[A-Za-z0-9_-]{6,200}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_CONTEXT_PAGES = 8;
const CONTEXT_CHAR_LIMIT = 24000;
const FALLBACK_CHAR_LIMIT = 9000;
const PASSWORD_ITERATIONS = 100000;
const SESSION_LONG_DAYS = 30;
const SESSION_SHORT_HOURS = 12;
const MAX_CHAT_HISTORY_CHATS = 200;
const MAX_CHAT_HISTORY_MESSAGES = 120;
const MAX_CHAT_HISTORY_TEXT_CHARS = 12000;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const BOOK_ARCHIVE_PDF_RE = /(?:href|data-name)=["'](?:\.\/)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.pdf["']/gi;
const ADMIN_EMAIL = "mynamesreyli@gmail.com";
const VERIFY_CODE_TTL_MINUTES = 10;
const VERIFY_CODE_COOLDOWN_SECONDS = 60;
const PASSWORD_CHANGE_TOKEN_TTL_MINUTES = 8;
const ADMIN_ACTION_TOKEN_TTL_MINUTES = 20;
const AVATAR_DATA_URL_LIMIT = 360_000;
const BOOK_COVER_FILE_LIMIT = 700_000;
const BOOK_COVER_DATA_URL_LIMIT = 950_000;
const DM_TEXT_LIMIT = 4000;
const DM_FORWARD_TEXT_LIMIT = 9000;
const DM_ATTACHMENT_DATA_URL_LIMIT = 900_000;
const DM_ATTACHMENT_FILE_LIMIT = 650_000;
const NO_REPLY_FROM = "no-reply@reyliar.xyz";

type Book = {
  book_id?: string;
  drive_id?: string;
  name?: string;
  title?: string;
  grade?: string;
  local_path?: string;
  pdf_url?: string;
  source_url?: string;
  remote_url?: string;
  pdf_source?: string;
  cover_url?: string;
  cover_data_url?: string;
  scan_status?: string;
  scan_pages?: number;
  scan_extractor?: string;
  added_at?: string;
  updated_at?: string;
  cover_updated_at?: string;
};

type BookAdminChange = {
  book_key: string;
  title?: string | null;
  name?: string | null;
  cover_data_url?: string | null;
  cover_mime_type?: string | null;
  cover_updated_at?: string | null;
  deleted_at?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
};

type ScanPage = {
  page?: number;
  text?: string;
};

type ScanData = {
  total_pages?: number;
  pages?: ScanPage[];
  extractor?: string;
};

type AnalyzePayload = {
  book_id?: string;
  drive_id?: string;
  book_name?: string;
  prompt?: string;
  title_requested?: boolean;
  chat_history?: Array<{ role?: string; text?: string }>;
};

type MistralMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

type UserRow = {
  id: string;
  email: string;
  display_name: string;
  password_hash: string;
  created_at: string;
  updated_at: string;
  role?: string | null;
  avatar_data_url?: string | null;
  email_verified_at?: string | null;
  last_login_ip?: string | null;
  last_login_at?: string | null;
  password_updated_at?: string | null;
  email_verification_code_hash?: string | null;
  email_verification_expires_at?: string | null;
  email_verification_sent_at?: string | null;
  pending_email?: string | null;
  pending_email_code_hash?: string | null;
  pending_email_expires_at?: string | null;
  pending_email_sent_at?: string | null;
  password_change_code_hash?: string | null;
  password_change_expires_at?: string | null;
  password_change_sent_at?: string | null;
  password_change_token_hash?: string | null;
  password_change_token_expires_at?: string | null;
  presence_status?: string | null;
  presence_updated_at?: string | null;
};

type PublicUser = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
  role: string;
  roles: Array<{ label: string; icon: string }>;
  is_admin: boolean;
  email_verified: boolean;
  email_verified_at: string;
  avatar_data_url: string;
  presence_status: string;
  presence_updated_at: string;
};

type AuthPayload = {
  email?: string;
  password?: string;
  display_name?: string;
  remember_device?: boolean;
  turnstile_token?: string;
};

type ProfilePayload = {
  display_name?: string;
  email?: string;
  avatar_data_url?: string;
};

type VerificationPayload = {
  code?: string;
};

type AuthContext = {
  user: PublicUser;
  tokenHash: string;
};

type ChatStore = {
  chats: Array<Record<string, unknown>>;
};

type TurnstileResponse = {
  success?: boolean;
  hostname?: string;
  action?: string;
  "error-codes"?: string[];
};

type EmailBinding = {
  send: (message: Record<string, unknown>) => Promise<unknown>;
};

type DmMessageRow = {
  id: string;
  sender_id: string;
  recipient_id: string;
  body?: string | null;
  kind?: string | null;
  attachment_data_url?: string | null;
  attachment_name?: string | null;
  attachment_mime_type?: string | null;
  attachment_size?: number | null;
  voice_duration_ms?: number | null;
  forward_json?: string | null;
  created_at: string;
  read_at?: string | null;
  deleted_at?: string | null;
};

const USER_SELECT_COLUMNS = [
  "id",
  "email",
  "display_name",
  "password_hash",
  "created_at",
  "updated_at",
  "role",
  "avatar_data_url",
  "email_verified_at",
  "last_login_ip",
  "last_login_at",
  "password_updated_at",
  "email_verification_code_hash",
  "email_verification_expires_at",
  "email_verification_sent_at",
  "pending_email",
  "pending_email_code_hash",
  "pending_email_expires_at",
  "pending_email_sent_at",
  "password_change_code_hash",
  "password_change_expires_at",
  "password_change_sent_at",
  "password_change_token_hash",
  "password_change_token_expires_at",
  "presence_status",
  "presence_updated_at"
].join(", ");

export default {
  async fetch(request, env): Promise<Response> {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      console.error(JSON.stringify({
        level: "error",
        message: error instanceof Error ? error.message : String(error)
      }));
      return json({ error: "Sunucu hatası." }, 500);
    }
  }
} satisfies ExportedHandler<Env>;

async function handleRequest(request: Request, env: Env): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(request) });
  }

  const url = new URL(request.url);
  const path = url.pathname;

  if (!path.startsWith("/api/")) {
    return text("Not found", 404);
  }

  if (request.method === "GET" && path === "/api/health") {
    return json({ ok: true, service: "reylai-api" });
  }

  if (path === "/api/auth/config" && request.method === "GET") {
    return handleAuthConfig(env);
  }

  if (path === "/api/auth/signup" && request.method === "POST") {
    return handleSignup(request, env);
  }

  if (path === "/api/auth/login" && request.method === "POST") {
    return handleLogin(request, env);
  }

  if (path === "/api/auth/me" && request.method === "GET") {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    return json({ success: true, user: auth.user });
  }

  if (path === "/api/auth/logout" && request.method === "POST") {
    return handleLogout(request, env);
  }

  if (path === "/api/auth/profile" && request.method === "PATCH") {
    return handleProfileUpdate(request, env);
  }

  if (path === "/api/auth/presence" && request.method === "PATCH") {
    return handlePresenceUpdate(request, env);
  }

  if (path === "/api/auth/verification/send" && request.method === "POST") {
    return handleVerificationSend(request, env);
  }

  if (path === "/api/auth/verification/confirm" && request.method === "POST") {
    return handleVerificationConfirm(request, env);
  }

  if (path === "/api/auth/email-change/send" && request.method === "POST") {
    return handleEmailChangeSend(request, env);
  }

  if (path === "/api/auth/email-change/confirm" && request.method === "POST") {
    return handleEmailChangeConfirm(request, env);
  }

  if (path === "/api/auth/password-change/send" && request.method === "POST") {
    return handlePasswordChangeSend(request, env);
  }

  if (path === "/api/auth/password-change/confirm" && request.method === "POST") {
    return handlePasswordChangeConfirm(request, env);
  }

  if (path === "/api/auth/password-change/complete" && request.method === "POST") {
    return handlePasswordChangeComplete(request, env);
  }

  if (path === "/api/admin/accounts" && request.method === "GET") {
    return handleAdminAccounts(request, env);
  }

  if (path === "/api/admin/accounts/sensitive" && request.method === "POST") {
    return handleAdminAccountsSensitive(request, env);
  }

  if (path === "/api/dm/users" && request.method === "GET") {
    return handleDmUsers(request, env);
  }

  if (path === "/api/dm/threads" && request.method === "GET") {
    return handleDmThreads(request, env);
  }

  if (path === "/api/dm/messages" && request.method === "GET") {
    return handleDmMessagesGet(request, env, url);
  }

  if (path === "/api/dm/messages" && request.method === "POST") {
    return handleDmMessageSend(request, env);
  }

  if (path === "/api/dm/read" && request.method === "POST") {
    return handleDmRead(request, env);
  }

  if (request.method === "GET" && path === "/api/library") {
    return handleLibrary(url, env);
  }

  if (request.method === "POST" && path === "/api/rename_book") {
    return handleBookRename(request, env);
  }

  if (request.method === "POST" && path === "/api/update_cover") {
    return handleBookCoverUpdate(request, env);
  }

  if (request.method === "POST" && path === "/api/delete") {
    return handleBookDelete(request, env);
  }

  if (path === "/api/chat_history") {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    if (request.method === "GET") return handleChatHistoryGet(env, auth.user.id);
    if (request.method === "POST" || request.method === "PUT") return handleChatHistorySave(request, env, auth.user.id);
  }

  if (request.method === "DELETE" && path.startsWith("/api/chat_history/")) {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    return handleChatHistoryDelete(env, auth.user.id, path.split("/").pop() || "");
  }

  if (path === "/api/config") {
    if (request.method === "GET") return handleConfig(env);
    if (request.method === "POST") return json({ success: false, error: "Statik yayında ayar kaydetme kapalı." }, 405);
  }

  if (request.method === "GET" && path === "/api/debug_gas") {
    return handleDebugGas(env);
  }

  if (request.method === "POST" && path === "/api/sync_cloud") {
    return json({ success: true, uploaded: 0, skipped: 0, errors: [], static_hosted: true });
  }

  if (request.method === "POST" && path === "/api/analyze") {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    if (!auth.user.email_verified) {
      return json({ success: false, error: "AI kullanmak için e-posta doğrulaması gerekli.", email_verification_required: true }, 403);
    }
    return handleAnalyze(request, env);
  }

  if (request.method === "POST" && path === "/api/analyze_start") {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    if (!auth.user.email_verified) {
      return json({ success: false, error: "AI kullanmak için e-posta doğrulaması gerekli.", email_verification_required: true }, 403);
    }
    const data = await readJson<AnalyzePayload>(request);
    const response = await analyzePayload(data, env);
    return json({ success: !response.error, analysis_id: crypto.randomUUID(), ...response });
  }

  if (request.method === "GET" && path.startsWith("/api/analyze_status/")) {
    return json({ done: true, message: "Hazır" });
  }

  if (request.method === "GET" && path.startsWith("/api/scan_status/")) {
    const id = safeId(path.split("/").pop() || "");
    if (!id) return json({ scan_status: "failed", scan_pages: 0 }, 400);
    const scan = await fetchScanData(env, [id]);
    if (!scan) return json({ scan_status: "failed", scan_pages: 0 });
    return json({
      scan_status: "done",
      scan_pages: scan.total_pages || scan.pages?.length || 0,
      scan_extractor: publicScanExtractor(scan.extractor || "")
    });
  }

  if (request.method === "GET" && path.startsWith("/api/cover/")) {
    const id = safeId(path.split("/").pop() || "");
    if (!id) return text("", 404);
    return handleBookCover(id, env);
  }

  if (request.method === "GET" && path.startsWith("/api/serve_pdf/")) {
    const id = safeId(path.split("/").pop() || "");
    if (!id) return text("PDF bulunamadı", 404);
    return handleServePdf(id, env);
  }

  if (request.method === "GET" && path.startsWith("/api/page_image/")) {
    return text("Statik yayında sayfa görseli üretimi desteklenmiyor.", 404);
  }

  if (
    ["POST", "PUT", "DELETE"].includes(request.method) &&
    ["/api/upload", "/api/add_book", "/api/scan_missing_books", "/api/scan_missing_books_cancel"].some((prefix) => path.startsWith(prefix))
  ) {
    return json({ success: false, error: "Bu işlem statik Cloudflare yayında desteklenmiyor." }, 405);
  }

  if (request.method === "GET" && path === "/api/scan_missing_books_status") {
    return json({
      running: false,
      completed: true,
      total: 0,
      processed: 0,
      success: 0,
      failed: 0,
      already_ready: 0,
      current_message: "Statik yayında tarama işi yok.",
      logs: []
    });
  }

  if (request.method === "POST" && path === "/api/verify_password") {
    return handleAdminPasswordVerify(request, env);
  }

  return json({ error: "API endpoint bulunamadı." }, 404);
}

function handleAuthConfig(env: Env): Response {
  const siteKey = optionalEnv(env, "TURNSTILE_SITE_KEY");
  const secretConfigured = Boolean(optionalEnv(env, "TURNSTILE_SECRET_KEY"));
  const turnstileRequired = optionalEnv(env, "AUTH_REQUIRE_TURNSTILE") !== "false";
  return json({
    turnstile_site_key: siteKey,
    turnstile_required: turnstileRequired,
    turnstile_configured: Boolean(siteKey && secretConfigured)
  });
}

async function handleSignup(request: Request, env: Env): Promise<Response> {
  const payload = await readJson<AuthPayload>(request);
  const turnstileError = await verifyTurnstile(request, env, payload.turnstile_token);
  if (turnstileError) return turnstileError;

  const email = normalizeEmail(payload.email || "");
  const password = String(payload.password || "");
  const displayName = normalizeDisplayName(payload.display_name || "");
  const validationError = validateAccountInput(email, password, displayName);
  if (validationError) return json({ success: false, error: validationError }, 400);

  const now = new Date().toISOString();
  const userId = crypto.randomUUID();
  const passwordHash = await hashPassword(password);
  const role = roleForEmail(email);
  const ipAddress = clientIp(request);

  try {
    await env.DB.prepare(
      "INSERT INTO users (id, email, display_name, password_hash, created_at, updated_at, role, last_login_ip, last_login_at, password_updated_at) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(userId, email, displayName, passwordHash, now, now, role, ipAddress, now, now).run();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.toLowerCase().includes("unique")) {
      return json({ success: false, error: "Bu e-posta ile zaten bir hesap var." }, 409);
    }
    throw error;
  }

  const user = await getUserById(env, userId);
  if (!user) return json({ success: false, error: "Hesap oluşturuldu ama oturum açılamadı." }, 500);
  const delivery = await sendVerificationCode(env, user);
  const session = await createSession(request, env, user, payload.remember_device !== false);
  return json({
    success: true,
    token: session.token,
    user: publicUser(user),
    verification_email_sent: delivery.sent,
    email_delivery_configured: delivery.configured
  }, 201);
}

async function handleLogin(request: Request, env: Env): Promise<Response> {
  const payload = await readJson<AuthPayload>(request);
  const turnstileError = await verifyTurnstile(request, env, payload.turnstile_token);
  if (turnstileError) return turnstileError;

  const email = normalizeEmail(payload.email || "");
  const password = String(payload.password || "");
  if (!EMAIL_RE.test(email) || password.length < 1) {
    return json({ success: false, error: "E-posta veya şifre hatalı." }, 401);
  }

  const user = await getUserByEmail(env, email);
  if (!user || !await verifyPassword(password, user.password_hash)) {
    return json({ success: false, error: "E-posta veya şifre hatalı." }, 401);
  }

  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE users SET last_login_ip = ?, last_login_at = ?, role = ?, updated_at = ? WHERE id = ?")
    .bind(clientIp(request), now, roleForEmail(user.email), now, user.id)
    .run();
  const freshUser = await getUserById(env, user.id) || user;
  const session = await createSession(request, env, freshUser, payload.remember_device !== false);
  return json({ success: true, token: session.token, user: publicUser(freshUser) });
}

async function handleLogout(request: Request, env: Env): Promise<Response> {
  const token = bearerToken(request);
  if (token) {
    const tokenHash = await sha256Base64Url(token);
    await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(tokenHash).run();
  }
  return json({ success: true });
}

async function handleProfileUpdate(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  {
    const payload = await readJson<ProfilePayload>(request);
    const user = await getUserById(env, auth.user.id);
    if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);

    const updates: string[] = [];
    const values: unknown[] = [];
    const now = new Date().toISOString();
    let shouldSendEmailChange = false;
    let pendingEmailForVerification = "";

    if (Object.prototype.hasOwnProperty.call(payload, "display_name")) {
      const displayName = normalizeDisplayName(payload.display_name || "");
      if (!displayName || displayName.length < 2 || displayName.length > 40) {
        return json({ success: false, error: "Görünen ad 2-40 karakter olmalı." }, 400);
      }
      updates.push("display_name = ?");
      values.push(displayName);
    }

    if (Object.prototype.hasOwnProperty.call(payload, "avatar_data_url")) {
      const avatarError = validateAvatarDataUrl(payload.avatar_data_url || "");
      if (avatarError) return json({ success: false, error: avatarError }, 400);
      updates.push("avatar_data_url = ?");
      values.push(String(payload.avatar_data_url || "").trim() || null);
    }

    const requestedEmail = Object.prototype.hasOwnProperty.call(payload, "email") ? normalizeEmail(payload.email || "") : "";
    const changingEmail = Boolean(requestedEmail && requestedEmail !== user.email);

    if (changingEmail) {
      if (!EMAIL_RE.test(requestedEmail) || requestedEmail.length > 254) {
        return json({ success: false, error: "Geçerli bir e-posta girin." }, 400);
      }
      const existing = await getUserByEmail(env, requestedEmail);
      if (existing && existing.id !== user.id) {
        return json({ success: false, error: "Bu e-posta başka bir hesapta kullanılıyor." }, 409);
      }
      updates.push(
        "pending_email = ?",
        "pending_email_code_hash = NULL",
        "pending_email_expires_at = NULL",
        "pending_email_sent_at = NULL"
      );
      values.push(requestedEmail);
      shouldSendEmailChange = true;
      pendingEmailForVerification = requestedEmail;
    }

    if (!updates.length) {
      return json({ success: true, user: publicUser(user) });
    }

    updates.push("updated_at = ?");
    values.push(now, auth.user.id);

    try {
      await env.DB.prepare(`UPDATE users SET ${updates.join(", ")} WHERE id = ?`).bind(...values).run();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.toLowerCase().includes("unique")) {
        return json({ success: false, error: "Bu e-posta başka bir hesapta kullanılıyor." }, 409);
      }
      throw error;
    }

    const freshUser = await getUserById(env, auth.user.id);
    const emailChangeDelivery = shouldSendEmailChange && freshUser
      ? await sendEmailChangeCode(env, freshUser, pendingEmailForVerification)
      : null;
    return json({
      success: true,
      user: freshUser ? publicUser(freshUser) : auth.user,
      verification_email_sent: emailChangeDelivery?.sent || false,
      email_delivery_configured: emailChangeDelivery ? emailChangeDelivery.configured : undefined,
      email_change_pending: shouldSendEmailChange,
      pending_email: shouldSendEmailChange ? pendingEmailForVerification : ""
    });
  }
  const authCtx = auth as AuthContext;
  const payload = await readJson<{ display_name?: string }>(request);
  const displayName = normalizeDisplayName(payload.display_name || "");
  if (!displayName || displayName.length < 2 || displayName.length > 40) {
    return json({ success: false, error: "Görünen ad 2-40 karakter olmalı." }, 400);
  }
  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?")
    .bind(displayName, now, authCtx.user.id)
    .run();
  const user = await getUserById(env, authCtx.user.id);
  if (user !== null) return json({ success: true, user: publicUser(user as UserRow) });
  return json({ success: true, user: { ...authCtx.user, display_name: displayName } });
}

async function handlePresenceUpdate(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;

  const payload = await readJson<{ status?: string }>(request);
  const status = normalizePresenceStatus(payload.status || "");
  const now = new Date().toISOString();

  await env.DB.prepare(
    "UPDATE users SET presence_status = ?, presence_updated_at = ?, updated_at = ? WHERE id = ?"
  ).bind(status, now, now, auth.user.id).run();

  const user = await getUserById(env, auth.user.id);
  return json({
    success: true,
    user: user ? publicUser(user) : { ...auth.user, presence_status: status, presence_updated_at: now }
  });
}

async function handleVerificationSend(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  if (user.email_verified_at) {
    return json({ success: true, already_verified: true, user: publicUser(user) });
  }

  const sentAt = user.email_verification_sent_at ? Date.parse(user.email_verification_sent_at) : 0;
  const waitMs = sentAt ? VERIFY_CODE_COOLDOWN_SECONDS * 1000 - (Date.now() - sentAt) : 0;
  if (waitMs > 0) {
    return json({
      success: false,
      error: `Yeni kod için ${Math.ceil(waitMs / 1000)} saniye bekleyin.`,
      retry_after: Math.ceil(waitMs / 1000)
    }, 429);
  }

  const delivery = await sendVerificationCode(env, user);
  return json({
    success: delivery.sent,
    email_delivery_configured: delivery.configured,
    error: delivery.sent ? undefined : delivery.error || "E-posta gönderilemedi."
  }, delivery.sent ? 200 : 503);
}

async function handleVerificationConfirm(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<VerificationPayload>(request);
  const code = String(payload.code || "").replace(/\D+/g, "").slice(0, 12);
  if (code.length !== 6) return json({ success: false, error: "6 haneli kodu girin." }, 400);

  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  if (user.email_verified_at) return json({ success: true, already_verified: true, user: publicUser(user) });
  if (!user.email_verification_code_hash || !user.email_verification_expires_at) {
    return json({ success: false, error: "Önce yeni bir doğrulama kodu isteyin." }, 400);
  }
  if (Date.parse(user.email_verification_expires_at) <= Date.now()) {
    return json({ success: false, error: "Kodun süresi doldu. Yeni kod isteyin." }, 400);
  }

  const expected = await verificationHash(user.id, user.email, code);
  if (expected !== user.email_verification_code_hash) {
    return json({ success: false, error: "Doğrulama kodu hatalı." }, 400);
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE users SET email_verified_at = ?, email_verification_code_hash = NULL, email_verification_expires_at = NULL, email_verification_sent_at = NULL, updated_at = ? WHERE id = ?"
  ).bind(now, now, user.id).run();
  const freshUser = await getUserById(env, user.id);
  return json({ success: true, user: freshUser ? publicUser(freshUser) : auth.user });
}

async function handleEmailChangeSend(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  const pendingEmail = normalizeEmail(user.pending_email || "");
  if (!pendingEmail) {
    return json({ success: false, error: "Bekleyen e-posta değişikliği bulunamadı." }, 400);
  }

  const sentAt = user.pending_email_sent_at ? Date.parse(user.pending_email_sent_at) : 0;
  const waitMs = sentAt ? VERIFY_CODE_COOLDOWN_SECONDS * 1000 - (Date.now() - sentAt) : 0;
  if (waitMs > 0) {
    return json({
      success: false,
      error: `Yeni kod için ${Math.ceil(waitMs / 1000)} saniye bekleyin.`,
      retry_after: Math.ceil(waitMs / 1000)
    }, 429);
  }

  const delivery = await sendEmailChangeCode(env, user, pendingEmail);
  return json({
    success: delivery.sent,
    email_delivery_configured: delivery.configured,
    pending_email: pendingEmail,
    error: delivery.sent ? undefined : delivery.error || "E-posta değişiklik kodu gönderilemedi."
  }, delivery.sent ? 200 : 503);
}

async function handleEmailChangeConfirm(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<VerificationPayload>(request);
  const code = String(payload.code || "").replace(/\D+/g, "").slice(0, 12);
  if (code.length !== 6) return json({ success: false, error: "6 haneli kodu girin." }, 400);

  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  const pendingEmail = normalizeEmail(user.pending_email || "");
  if (!pendingEmail || !user.pending_email_code_hash || !user.pending_email_expires_at) {
    return json({ success: false, error: "Bekleyen e-posta değişikliği bulunamadı." }, 400);
  }
  if (Date.parse(user.pending_email_expires_at) <= Date.now()) {
    return json({ success: false, error: "Kodun süresi doldu. Yeni kod isteyin." }, 400);
  }

  const expected = await verificationHash(user.id, pendingEmail, code);
  if (expected !== user.pending_email_code_hash) {
    return json({ success: false, error: "Doğrulama kodu hatalı." }, 400);
  }

  const existing = await getUserByEmail(env, pendingEmail);
  if (existing && existing.id !== user.id) {
    return json({ success: false, error: "Bu e-posta başka bir hesapta kullanılıyor." }, 409);
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE users SET email = ?, role = ?, email_verified_at = ?, " +
    "email_verification_code_hash = NULL, email_verification_expires_at = NULL, email_verification_sent_at = NULL, " +
    "pending_email = NULL, pending_email_code_hash = NULL, pending_email_expires_at = NULL, pending_email_sent_at = NULL, " +
    "updated_at = ? WHERE id = ?"
  ).bind(pendingEmail, roleForEmail(pendingEmail), now, now, user.id).run();
  const freshUser = await getUserById(env, user.id);
  return json({ success: true, user: freshUser ? publicUser(freshUser) : auth.user });
}

async function handlePasswordChangeSend(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  if (!user.email_verified_at) {
    return json({ success: false, error: "Şifre değiştirmek için önce e-postanı doğrula.", email_verification_required: true }, 403);
  }

  const sentAt = user.password_change_sent_at ? Date.parse(user.password_change_sent_at) : 0;
  const waitMs = sentAt ? VERIFY_CODE_COOLDOWN_SECONDS * 1000 - (Date.now() - sentAt) : 0;
  if (waitMs > 0) {
    return json({
      success: false,
      error: `Yeni kod için ${Math.ceil(waitMs / 1000)} saniye bekleyin.`,
      retry_after: Math.ceil(waitMs / 1000)
    }, 429);
  }

  const delivery = await sendPasswordChangeCode(env, user);
  return json({
    success: delivery.sent,
    email_delivery_configured: delivery.configured,
    error: delivery.sent ? undefined : delivery.error || "Şifre değişiklik kodu gönderilemedi."
  }, delivery.sent ? 200 : 503);
}

async function handlePasswordChangeConfirm(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<VerificationPayload>(request);
  const code = String(payload.code || "").replace(/\D+/g, "").slice(0, 12);
  if (code.length !== 6) return json({ success: false, error: "6 haneli kodu girin." }, 400);

  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  if (!user.password_change_code_hash || !user.password_change_expires_at) {
    return json({ success: false, error: "Önce yeni bir şifre kodu isteyin." }, 400);
  }
  if (Date.parse(user.password_change_expires_at) <= Date.now()) {
    return json({ success: false, error: "Kodun süresi doldu. Yeni kod isteyin." }, 400);
  }

  const expected = await verificationHash(user.id, user.email, code);
  if (expected !== user.password_change_code_hash) {
    return json({ success: false, error: "Doğrulama kodu hatalı." }, 400);
  }

  const token = randomToken(24);
  const tokenHash = await sha256Base64Url(token);
  const now = new Date();
  const expiresAt = new Date(now.getTime() + PASSWORD_CHANGE_TOKEN_TTL_MINUTES * 60 * 1000).toISOString();
  await env.DB.prepare(
    "UPDATE users SET password_change_code_hash = NULL, password_change_expires_at = NULL, " +
    "password_change_token_hash = ?, password_change_token_expires_at = ?, updated_at = ? WHERE id = ?"
  ).bind(tokenHash, expiresAt, now.toISOString(), user.id).run();
  return json({ success: true, token, expires_at: expiresAt });
}

async function handlePasswordChangeComplete(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<{ token?: string; new_password?: string }>(request);
  const token = String(payload.token || "");
  const newPassword = String(payload.new_password || "");
  if (newPassword.length < 8 || newPassword.length > 128) {
    return json({ success: false, error: "Şifre 8-128 karakter olmalı." }, 400);
  }

  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadı." }, 404);
  if (!token || !user.password_change_token_hash || !user.password_change_token_expires_at) {
    return json({ success: false, error: "Şifre değişimi için kod onayı gerekli." }, 400);
  }
  if (Date.parse(user.password_change_token_expires_at) <= Date.now()) {
    return json({ success: false, error: "Şifre değişim oturumunun süresi doldu." }, 400);
  }
  const tokenHash = await sha256Base64Url(token);
  if (tokenHash !== user.password_change_token_hash) {
    return json({ success: false, error: "Şifre değişim oturumu doğrulanamadı." }, 401);
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE users SET password_hash = ?, password_updated_at = ?, " +
    "password_change_token_hash = NULL, password_change_token_expires_at = NULL, password_change_sent_at = NULL, updated_at = ? WHERE id = ?"
  ).bind(await hashPassword(newPassword), now, now, user.id).run();
  const freshUser = await getUserById(env, user.id);
  return json({ success: true, user: freshUser ? publicUser(freshUser) : auth.user });
}

async function handleAdminPasswordVerify(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  if (!auth.user.is_admin) {
    return json({ success: false, auth: false, error: "Bu işlem sadece yönetici hesabı ile yapılabilir." }, 403);
  }

  const payload = await readJson<{ turnstile_token?: string }>(request);
  const turnstileError = await verifyTurnstile(request, env, payload.turnstile_token);
  if (turnstileError) return turnstileError;
  const token = randomToken(16);
  const now = new Date();
  const expiresAt = new Date(now.getTime() + ADMIN_ACTION_TOKEN_TTL_MINUTES * 60 * 1000).toISOString();
  await env.DB.prepare(
    "INSERT INTO admin_action_tokens (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)"
  ).bind(await sha256Base64Url(token), auth.user.id, now.toISOString(), expiresAt).run();
  return json({ success: true, token, expires_at: expiresAt });
}

async function handleAdminAccounts(request: Request, env: Env): Promise<Response> {
  const admin = await requireAdmin(request, env);
  if (admin instanceof Response) return admin;

  const rows = await env.DB.prepare(
    "SELECT u.id, u.email, u.display_name, u.role, u.avatar_data_url, u.email_verified_at, u.created_at, u.updated_at, " +
    "u.last_login_ip, u.last_login_at, u.password_updated_at, " +
    "COUNT(s.id) AS session_count, MAX(s.last_seen_at) AS last_seen_at, MAX(s.ip_address) AS session_ip " +
    "FROM users u LEFT JOIN sessions s ON s.user_id = u.id AND s.expires_at > ? " +
    "GROUP BY u.id ORDER BY u.created_at DESC LIMIT 200"
  ).bind(new Date().toISOString()).all<Record<string, unknown>>();

  const accounts = (rows.results || []).map((row) => ({
    id: String(row.id || ""),
    email: String(row.email || ""),
    display_name: String(row.display_name || ""),
    role: effectiveRole(String(row.email || ""), String(row.role || "")),
    roles: roleBadgesForEmail(String(row.email || ""), String(row.role || "")),
    avatar_data_url: String(row.avatar_data_url || ""),
    email_verified: Boolean(row.email_verified_at),
    email_verified_at: String(row.email_verified_at || ""),
    created_at: String(row.created_at || ""),
    updated_at: String(row.updated_at || ""),
    last_login_ip: String(row.last_login_ip || ""),
    last_login_at: String(row.last_login_at || ""),
    password_updated_at: String(row.password_updated_at || ""),
    session_count: Number(row.session_count || 0),
    last_seen_at: String(row.last_seen_at || ""),
    session_ip: String(row.session_ip || "")
  }));

  const verified = accounts.filter((account) => account.email_verified).length;
  const admins = accounts.filter((account) => account.role === "admin").length;
  return json({
    success: true,
    stats: {
      total_accounts: accounts.length,
      verified_accounts: verified,
      unverified_accounts: accounts.length - verified,
      admin_accounts: admins,
      active_sessions: accounts.reduce((sum, account) => sum + account.session_count, 0)
    },
    accounts
  });
}

async function handleAdminAccountsSensitive(request: Request, env: Env): Promise<Response> {
  const admin = await requireAdmin(request, env);
  if (admin instanceof Response) return admin;
  const payload = await readJson<{ turnstile_token?: string }>(request);
  const turnstileError = await verifyTurnstile(request, env, payload.turnstile_token);
  if (turnstileError) return turnstileError;

  const usersResult = await env.DB.prepare(
    "SELECT id, email, display_name, role, password_hash, password_updated_at, created_at, updated_at, " +
    "last_login_ip, last_login_at, email_verified_at FROM users ORDER BY created_at DESC LIMIT 200"
  ).all<UserRow>();
  const sessionsResult = await env.DB.prepare(
    "SELECT id, user_id, created_at, expires_at, last_seen_at, user_agent, ip_address " +
    "FROM sessions ORDER BY last_seen_at DESC LIMIT 600"
  ).all<Record<string, unknown>>();

  const sessionsByUser = new Map<string, Array<Record<string, unknown>>>();
  for (const session of sessionsResult.results || []) {
    const userId = String(session.user_id || "");
    if (!userId) continue;
    const list = sessionsByUser.get(userId) || [];
    list.push({
      id: String(session.id || ""),
      ip_address: String(session.ip_address || ""),
      user_agent: String(session.user_agent || ""),
      created_at: String(session.created_at || ""),
      expires_at: String(session.expires_at || ""),
      last_seen_at: String(session.last_seen_at || ""),
      active: Date.parse(String(session.expires_at || "")) > Date.now()
    });
    sessionsByUser.set(userId, list);
  }

  const accounts = await Promise.all((usersResult.results || []).map(async (user) => {
    const passwordParts = parsePasswordHash(user.password_hash || "");
    return {
      id: user.id,
      email: user.email,
      display_name: user.display_name,
      role: effectiveRole(user.email, user.role || ""),
      email_verified: Boolean(user.email_verified_at),
      created_at: user.created_at,
      updated_at: user.updated_at,
      last_login_ip: user.last_login_ip || "",
      last_login_at: user.last_login_at || "",
      password_updated_at: user.password_updated_at || "",
      password_storage: {
        readable_password_available: false,
        note: "Şifreler geri okunamaz; PBKDF2-SHA256 hash olarak korunur.",
        algorithm: passwordParts.algorithm,
        iterations: passwordParts.iterations,
        fingerprint: user.password_hash ? (await sha256Base64Url(user.password_hash)).slice(0, 18) : ""
      },
      sessions: sessionsByUser.get(user.id) || []
    };
  }));

  return json({ success: true, accounts });
}

async function handleChatHistoryGet(env: Env, userId: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT store_json FROM chat_history WHERE user_id = ?")
    .bind(userId)
    .first<{ store_json: string }>();
  if (!row?.store_json) return json({ chats: [] });
  try {
    return json(normalizeServerChatStore(JSON.parse(row.store_json)));
  } catch {
    return json({ chats: [] });
  }
}

async function handleChatHistorySave(request: Request, env: Env, userId: string): Promise<Response> {
  const store = normalizeServerChatStore(await readJson<unknown>(request));
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO chat_history (user_id, store_json, updated_at) VALUES (?, ?, ?) " +
    "ON CONFLICT(user_id) DO UPDATE SET store_json = excluded.store_json, updated_at = excluded.updated_at"
  ).bind(userId, JSON.stringify(store), now).run();
  return json({ success: true, store });
}

async function handleChatHistoryDelete(env: Env, userId: string, rawChatId: string): Promise<Response> {
  const chatId = decodeURIComponent(rawChatId || "").trim();
  if (!chatId) return json({ success: false, error: "Sohbet kimliği eksik." }, 400);
  const row = await env.DB.prepare("SELECT store_json FROM chat_history WHERE user_id = ?")
    .bind(userId)
    .first<{ store_json: string }>();
  const store = normalizeServerChatStore(row?.store_json ? JSON.parse(row.store_json) : { chats: [] });
  store.chats = store.chats.filter((chat) => String(chat.id || "") !== chatId);
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO chat_history (user_id, store_json, updated_at) VALUES (?, ?, ?) " +
    "ON CONFLICT(user_id) DO UPDATE SET store_json = excluded.store_json, updated_at = excluded.updated_at"
  ).bind(userId, JSON.stringify(store), now).run();
  return json({ success: true, store });
}

async function handleDmUsers(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const now = new Date().toISOString();

  const rows = await env.DB.prepare(
    "SELECT u.id, u.email, u.display_name, u.role, u.avatar_data_url, u.email_verified_at, u.created_at, " +
    "u.presence_status, u.presence_updated_at, MAX(s.last_seen_at) AS last_seen_at " +
    "FROM users u LEFT JOIN sessions s ON s.user_id = u.id AND s.expires_at > ? " +
    "WHERE u.id <> ? GROUP BY u.id ORDER BY lower(u.display_name), lower(u.email) LIMIT 300"
  ).bind(now, auth.user.id).all<Record<string, unknown>>();

  const users = (rows.results || []).map(publicDmUser);
  return json({ success: true, users });
}

async function handleDmThreads(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const userId = auth.user.id;
  const now = new Date().toISOString();

  const rows = await env.DB.prepare(
    "SELECT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END AS other_user_id, MAX(created_at) AS last_at " +
    "FROM dm_messages WHERE deleted_at IS NULL AND (sender_id = ? OR recipient_id = ?) " +
    "GROUP BY other_user_id ORDER BY last_at DESC LIMIT 200"
  ).bind(userId, userId, userId).all<{ other_user_id: string; last_at: string }>();

  const threads = [];
  for (const row of rows.results || []) {
    const otherId = String(row.other_user_id || "");
    if (!otherId) continue;
    const other = await env.DB.prepare(
      "SELECT u.id, u.email, u.display_name, u.role, u.avatar_data_url, u.email_verified_at, u.created_at, " +
      "u.presence_status, u.presence_updated_at, MAX(s.last_seen_at) AS last_seen_at " +
      "FROM users u LEFT JOIN sessions s ON s.user_id = u.id AND s.expires_at > ? " +
      "WHERE u.id = ? GROUP BY u.id"
    ).bind(now, otherId).first<Record<string, unknown>>();
    if (!other) continue;
    const latest = await env.DB.prepare(
      "SELECT * FROM dm_messages WHERE deleted_at IS NULL AND " +
      "((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?)) " +
      "ORDER BY created_at DESC LIMIT 1"
    ).bind(userId, otherId, otherId, userId).first<DmMessageRow>();
    const unread = await env.DB.prepare(
      "SELECT COUNT(*) AS count FROM dm_messages WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL AND deleted_at IS NULL"
    ).bind(otherId, userId).first<{ count: number }>();
    threads.push({
      user: publicDmUser(other),
      latest_message: latest ? publicDmMessage(latest, userId) : null,
      unread_count: Number(unread?.count || 0),
      updated_at: latest?.created_at || row.last_at || ""
    });
  }

  return json({ success: true, threads });
}

async function handleDmMessagesGet(request: Request, env: Env, url: URL): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const userId = auth.user.id;
  const otherId = String(url.searchParams.get("user_id") || "").trim();
  if (!otherId || otherId === userId) return json({ success: false, error: "Mesajlaşılacak hesap seçilemedi." }, 400);

  const other = await getDmUserById(env, otherId);
  if (!other) return json({ success: false, error: "Hesap bulunamadı." }, 404);

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE dm_messages SET read_at = ? WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL"
  ).bind(now, otherId, userId).run();

  const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 80) || 80, 120));
  const rows = await env.DB.prepare(
    "SELECT * FROM dm_messages WHERE deleted_at IS NULL AND " +
    "((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?)) " +
    "ORDER BY created_at DESC LIMIT ?"
  ).bind(userId, otherId, otherId, userId, limit).all<DmMessageRow>();

  const messages = (rows.results || []).reverse().map((message) => publicDmMessage(message, userId));
  return json({ success: true, user: publicDmUser(other), messages });
}

async function handleDmMessageSend(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<Record<string, unknown>>(request);
  const senderId = auth.user.id;
  const recipientId = String(payload.recipient_id || "").trim();
  if (!recipientId || recipientId === senderId) return json({ success: false, error: "Geçerli bir alıcı seç." }, 400);

  const recipient = await getDmUserById(env, recipientId);
  if (!recipient) return json({ success: false, error: "Alıcı hesap bulunamadı." }, 404);

  const body = cleanDmText(payload.body || payload.text || "", DM_TEXT_LIMIT);
  const attachment = normalizeDmAttachment(payload.attachment);
  if ("error" in attachment) return json({ success: false, error: attachment.error }, 400);
  const forward = normalizeDmForward(payload.forward);
  const hasForward = Boolean(forward);
  const hasAttachment = Boolean(attachment.data_url);
  if (!body && !hasAttachment && !hasForward) {
    return json({ success: false, error: "Boş mesaj gönderilemez." }, 400);
  }

  const now = new Date().toISOString();
  const kind = hasForward ? "forward" : (hasAttachment ? "file" : "text");
  const id = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO dm_messages (id, sender_id, recipient_id, body, kind, attachment_data_url, attachment_name, attachment_mime_type, attachment_size, voice_duration_ms, forward_json, created_at) " +
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(
    id,
    senderId,
    recipientId,
    body,
    kind,
    attachment.data_url,
    attachment.name,
    attachment.mime_type,
    attachment.size || null,
    Number(payload.voice_duration_ms || 0) || null,
    forward ? JSON.stringify(forward) : null,
    now
  ).run();

  const row = await env.DB.prepare("SELECT * FROM dm_messages WHERE id = ?").bind(id).first<DmMessageRow>();
  const unread = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM dm_messages WHERE recipient_id = ? AND read_at IS NULL AND deleted_at IS NULL"
  ).bind(recipientId).first<{ count: number }>();
  await sendDmNotificationEmail(env, auth.user, recipient, Number(unread?.count || 1), row ? publicDmMessage(row, senderId) : null);
  return json({ success: true, message: row ? publicDmMessage(row, senderId) : null }, 201);
}

async function handleDmRead(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<Record<string, unknown>>(request);
  const otherId = String(payload.user_id || "").trim();
  if (!otherId || otherId === auth.user.id) return json({ success: false, error: "Hesap seçilemedi." }, 400);
  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE dm_messages SET read_at = ? WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL"
  ).bind(now, otherId, auth.user.id).run();
  return json({ success: true });
}

async function requireAuth(request: Request, env: Env): Promise<AuthContext | Response> {
  const token = bearerToken(request);
  if (!token) return json({ success: false, auth: false, error: "Oturum gerekli." }, 401);
  const tokenHash = await sha256Base64Url(token);
  const now = new Date().toISOString();
  const row = await env.DB.prepare(
    `SELECT ${USER_SELECT_COLUMNS.split(", ").map((column) => `u.${column}`).join(", ")} ` +
    "FROM sessions s JOIN users u ON u.id = s.user_id " +
    "WHERE s.token_hash = ? AND s.expires_at > ?"
  ).bind(tokenHash, now).first<UserRow>();

  if (!row) {
    await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ? OR expires_at <= ?").bind(tokenHash, now).run();
    return json({ success: false, auth: false, error: "Oturum süresi doldu." }, 401);
  }

  await env.DB.prepare("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?").bind(now, tokenHash).run();
  return { user: publicUser(row), tokenHash };
}

async function createSession(
  request: Request,
  env: Env,
  user: UserRow,
  rememberDevice: boolean
): Promise<{ token: string }> {
  const token = randomToken(32);
  const tokenHash = await sha256Base64Url(token);
  const nowDate = new Date();
  const expiresDate = new Date(nowDate.getTime() + (rememberDevice ? SESSION_LONG_DAYS * 24 : SESSION_SHORT_HOURS) * 60 * 60 * 1000);
  await env.DB.prepare(
    "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at, user_agent, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(
    crypto.randomUUID(),
    user.id,
    tokenHash,
    nowDate.toISOString(),
    expiresDate.toISOString(),
    nowDate.toISOString(),
    (request.headers.get("user-agent") || "").slice(0, 240),
    clientIp(request)
  ).run();
  return { token };
}

async function verifyTurnstile(request: Request, env: Env, tokenValue: unknown): Promise<Response | null> {
  if (optionalEnv(env, "AUTH_REQUIRE_TURNSTILE") === "false") return null;

  const siteKey = optionalEnv(env, "TURNSTILE_SITE_KEY");
  const secret = optionalEnv(env, "TURNSTILE_SECRET_KEY");
  if (!siteKey || !secret) {
    return json({
      success: false,
      error: "Cloudflare bot doğrulaması henüz yapılandırılmadı."
    }, 503);
  }

  const token = String(tokenValue || "").trim();
  if (!token || token.length > 2048) {
    return json({ success: false, error: "Cloudflare bot doğrulamasını tamamlayın." }, 400);
  }

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      secret,
      response: token,
      remoteip: request.headers.get("CF-Connecting-IP") || undefined,
      idempotency_key: crypto.randomUUID()
    })
  });

  if (!response.ok) {
    return json({ success: false, error: "Cloudflare bot doğrulaması şu an cevap vermiyor." }, 502);
  }

  const result = await response.json() as TurnstileResponse;
  if (!result.success) {
    return json({
      success: false,
      error: "Cloudflare bot doğrulaması başarısız oldu.",
      turnstile_errors: result["error-codes"] || []
    }, 400);
  }
  return null;
}

async function getUserByEmail(env: Env, email: string): Promise<UserRow | null> {
  return await env.DB.prepare(
    `SELECT ${USER_SELECT_COLUMNS} FROM users WHERE email = ?`
  ).bind(email).first<UserRow>();
}

async function getUserById(env: Env, id: string): Promise<UserRow | null> {
  return await env.DB.prepare(
    `SELECT ${USER_SELECT_COLUMNS} FROM users WHERE id = ?`
  ).bind(id).first<UserRow>();
}

function publicUser(user: UserRow): PublicUser {
  const role = effectiveRole(user.email, user.role || "");
  return {
    id: user.id,
    email: user.email,
    display_name: user.display_name,
    created_at: user.created_at,
    role,
    roles: roleBadgesForEmail(user.email, role),
    is_admin: role === "admin" || normalizeEmail(user.email) === ADMIN_EMAIL,
    email_verified: Boolean(user.email_verified_at),
    email_verified_at: user.email_verified_at || "",
    avatar_data_url: user.avatar_data_url || "",
    presence_status: normalizePresenceStatus(user.presence_status || ""),
    presence_updated_at: user.presence_updated_at || ""
  };
}

async function getDmUserById(env: Env, id: string): Promise<Record<string, unknown> | null> {
  const now = new Date().toISOString();
  return await env.DB.prepare(
    "SELECT u.id, u.email, u.display_name, u.role, u.avatar_data_url, u.email_verified_at, u.created_at, " +
    "u.presence_status, u.presence_updated_at, MAX(s.last_seen_at) AS last_seen_at " +
    "FROM users u LEFT JOIN sessions s ON s.user_id = u.id AND s.expires_at > ? " +
    "WHERE u.id = ? GROUP BY u.id"
  ).bind(now, id).first<Record<string, unknown>>();
}

function publicDmUser(user: Record<string, unknown>): Record<string, unknown> {
  const email = String(user.email || "");
  const role = effectiveRole(email, String(user.role || ""));
  const presenceStatus = normalizePresenceStatus(String(user.presence_status || ""));
  const lastSeenAt = String(user.last_seen_at || "");
  const effectivePresence = effectivePresenceStatus(presenceStatus, lastSeenAt);
  return {
    id: String(user.id || ""),
    email,
    display_name: String(user.display_name || email || "ReylAI kullanıcısı"),
    role,
    roles: roleBadgesForEmail(email, role),
    is_admin: role === "admin" || normalizeEmail(email) === ADMIN_EMAIL,
    email_verified: Boolean(user.email_verified_at),
    avatar_data_url: String(user.avatar_data_url || ""),
    created_at: String(user.created_at || ""),
    presence_status: presenceStatus,
    presence_updated_at: String(user.presence_updated_at || ""),
    last_seen_at: lastSeenAt,
    effective_presence: effectivePresence,
    is_active: effectivePresence !== "offline"
  };
}

function publicDmMessage(message: DmMessageRow, currentUserId: string): Record<string, unknown> {
  let forward: unknown = null;
  if (message.forward_json) {
    try {
      forward = JSON.parse(message.forward_json);
    } catch {
      forward = null;
    }
  }
  const attachment = message.attachment_data_url ? {
    data_url: message.attachment_data_url,
    name: message.attachment_name || "dosya",
    mime_type: message.attachment_mime_type || "application/octet-stream",
    size: Number(message.attachment_size || 0)
  } : null;
  return {
    id: message.id,
    sender_id: message.sender_id,
    recipient_id: message.recipient_id,
    body: message.body || "",
    kind: message.kind || "text",
    attachment,
    forward,
    voice_duration_ms: Number(message.voice_duration_ms || 0),
    created_at: message.created_at,
    read_at: message.read_at || "",
    outgoing: message.sender_id === currentUserId
  };
}

function normalizePresenceStatus(value: string): string {
  const status = String(value || "online").trim().toLowerCase();
  return ["online", "idle", "dnd"].includes(status) ? status : "online";
}

function effectivePresenceStatus(status: string, lastSeenAt: string): string {
  const seenAt = lastSeenAt ? Date.parse(lastSeenAt) : 0;
  if (!seenAt || Date.now() - seenAt > 2 * 60 * 1000) return "offline";
  return normalizePresenceStatus(status);
}

async function requireAdmin(request: Request, env: Env): Promise<AuthContext | Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  if (!auth.user.is_admin) {
    return json({ success: false, auth: false, error: "Bu işlem sadece yönetici hesabı ile yapılabilir." }, 403);
  }
  return auth;
}

async function requireAdminAction(request: Request, env: Env): Promise<AuthContext | Response> {
  const admin = await requireAdmin(request, env);
  if (admin instanceof Response) return admin;
  const rawToken = String(request.headers.get("x-auth-token") || "").trim();
  if (!rawToken) {
    return json({ success: false, auth: false, error: "Cloudflare doğrulaması gerekli." }, 401);
  }

  const tokenHash = await sha256Base64Url(rawToken);
  const now = new Date().toISOString();
  const row = await env.DB.prepare(
    "SELECT user_id FROM admin_action_tokens WHERE token_hash = ? AND user_id = ? AND expires_at > ?"
  ).bind(tokenHash, admin.user.id, now).first<{ user_id: string }>();

  if (!row) {
    await env.DB.prepare("DELETE FROM admin_action_tokens WHERE expires_at <= ?").bind(now).run();
    return json({ success: false, auth: false, error: "Cloudflare doğrulamasının süresi doldu." }, 401);
  }
  return admin;
}

function roleForEmail(email: string): string {
  return normalizeEmail(email) === ADMIN_EMAIL ? "admin" : "user";
}

function effectiveRole(email: string, storedRole = ""): string {
  return normalizeEmail(email) === ADMIN_EMAIL ? "admin" : (storedRole || "user");
}

function roleBadgesForEmail(email: string, roleValue = ""): Array<{ label: string; icon: string }> {
  const role = effectiveRole(email, roleValue);
  if (role === "admin" || normalizeEmail(email) === ADMIN_EMAIL) {
    return [
      { label: "Admin", icon: "shield" },
      { label: "Staff", icon: "sparkles" }
    ];
  }
  return [{ label: "Member", icon: "user" }];
}

function parsePasswordHash(value: string): { algorithm: string; iterations: number } {
  const parts = String(value || "").split("$");
  return {
    algorithm: parts[0] || "unknown",
    iterations: Number(parts[1] || 0) || 0
  };
}

function clientIp(request: Request): string {
  return String(
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("x-forwarded-for")?.split(",")[0] ||
    request.headers.get("x-real-ip") ||
    ""
  ).trim().slice(0, 80);
}

function validateAvatarDataUrl(value: string): string {
  const avatar = String(value || "").trim();
  if (!avatar) return "";
  if (avatar.length > AVATAR_DATA_URL_LIMIT) return "Profil fotoğrafı çok büyük. Daha küçük bir görsel seçin.";
  if (!/^data:image\/(?:png|jpeg|jpg|webp);base64,[a-z0-9+/=]+$/i.test(avatar)) {
    return "Profil fotoğrafı PNG, JPG veya WEBP olmalı.";
  }
  return "";
}

async function sendVerificationCode(env: Env, user: UserRow): Promise<{ sent: boolean; configured: boolean; error?: string }> {
  const binding = getEmailBinding(env);
  if (!binding) {
    return { sent: false, configured: false, error: "E-postana kod göndermek için servis henüz hazır değil." };
  }

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0");
  const now = new Date();
  const expiresAt = new Date(now.getTime() + VERIFY_CODE_TTL_MINUTES * 60 * 1000).toISOString();
  const codeHash = await verificationHash(user.id, user.email, code);

  await env.DB.prepare(
    "UPDATE users SET email_verification_code_hash = ?, email_verification_expires_at = ?, email_verification_sent_at = ?, updated_at = ? WHERE id = ?"
  ).bind(codeHash, expiresAt, now.toISOString(), now.toISOString(), user.id).run();

  try {
    await binding.send({
      to: user.email,
      from: { email: NO_REPLY_FROM, name: "ReylAI" },
      subject: "ReylAI doğrulama kodun",
      html: verificationEmailHtml(user, code),
      text: `ReylAI doğrulama kodun: ${code}. Kod ${VERIFY_CODE_TTL_MINUTES} dakika geçerlidir.`
    });
    return { sent: true, configured: true };
  } catch (error) {
    console.error(JSON.stringify({
      level: "error",
      message: "verification email failed",
      detail: error instanceof Error ? error.message : String(error)
    }));
    return { sent: false, configured: true, error: "Doğrulama e-postası gönderilemedi." };
  }
}

async function sendEmailChangeCode(env: Env, user: UserRow, nextEmail: string): Promise<{ sent: boolean; configured: boolean; error?: string }> {
  const email = normalizeEmail(nextEmail);
  const binding = getEmailBinding(env);
  if (!binding) {
    return { sent: false, configured: false, error: "E-postana kod göndermek için servis henüz hazır değil." };
  }

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0");
  const now = new Date();
  const expiresAt = new Date(now.getTime() + VERIFY_CODE_TTL_MINUTES * 60 * 1000).toISOString();
  const codeHash = await verificationHash(user.id, email, code);

  await env.DB.prepare(
    "UPDATE users SET pending_email = ?, pending_email_code_hash = ?, pending_email_expires_at = ?, pending_email_sent_at = ?, updated_at = ? WHERE id = ?"
  ).bind(email, codeHash, expiresAt, now.toISOString(), now.toISOString(), user.id).run();

  try {
    await binding.send({
      to: email,
      from: { email: NO_REPLY_FROM, name: "ReylAI" },
      subject: "ReylAI e-posta değişiklik kodun",
      html: verificationEmailHtml(user, code),
      text: `ReylAI e-posta değişiklik kodun: ${code}. Kod ${VERIFY_CODE_TTL_MINUTES} dakika geçerlidir.`
    });
    return { sent: true, configured: true };
  } catch (error) {
    console.error(JSON.stringify({
      level: "error",
      message: "email change verification failed",
      detail: error instanceof Error ? error.message : String(error)
    }));
    return { sent: false, configured: true, error: "E-posta değişiklik kodu gönderilemedi." };
  }
}

async function sendPasswordChangeCode(env: Env, user: UserRow): Promise<{ sent: boolean; configured: boolean; error?: string }> {
  const binding = getEmailBinding(env);
  if (!binding) {
    return { sent: false, configured: false, error: "E-postana kod göndermek için servis henüz hazır değil." };
  }

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0");
  const now = new Date();
  const expiresAt = new Date(now.getTime() + VERIFY_CODE_TTL_MINUTES * 60 * 1000).toISOString();
  const codeHash = await verificationHash(user.id, user.email, code);

  await env.DB.prepare(
    "UPDATE users SET password_change_code_hash = ?, password_change_expires_at = ?, password_change_sent_at = ?, " +
    "password_change_token_hash = NULL, password_change_token_expires_at = NULL, updated_at = ? WHERE id = ?"
  ).bind(codeHash, expiresAt, now.toISOString(), now.toISOString(), user.id).run();

  try {
    await binding.send({
      to: user.email,
      from: { email: NO_REPLY_FROM, name: "ReylAI" },
      subject: "ReylAI şifre değişiklik kodun",
      html: verificationEmailHtml(user, code),
      text: `ReylAI şifre değişiklik kodun: ${code}. Kod ${VERIFY_CODE_TTL_MINUTES} dakika geçerlidir.`
    });
    return { sent: true, configured: true };
  } catch (error) {
    console.error(JSON.stringify({
      level: "error",
      message: "password change email failed",
      detail: error instanceof Error ? error.message : String(error)
    }));
    return { sent: false, configured: true, error: "Şifre değişiklik e-postası gönderilemedi." };
  }
}

async function sendDmNotificationEmail(
  env: Env,
  sender: PublicUser,
  recipient: Record<string, unknown>,
  unreadCount: number,
  message: Record<string, unknown> | null
): Promise<void> {
  const binding = getEmailBinding(env);
  if (!binding) return;

  const to = normalizeEmail(String(recipient.email || ""));
  if (!EMAIL_RE.test(to)) return;

  const senderName = sender.display_name || "Bir kullanıcı";
  const count = Math.max(1, Math.floor(unreadCount || 1));
  const snippet = dmEmailSnippet(message);
  const subject = `${senderName} sana mesaj gönderdi`;
  const countText = `${count} okunmamış mesajın var.`;

  try {
    await binding.send({
      to,
      from: { email: NO_REPLY_FROM, name: "ReylAI" },
      subject,
      html: dmNotificationEmailHtml(senderName, countText, snippet),
      text: `${senderName} sana mesaj gönderdi. ${countText}${snippet ? ` Mesaj: ${snippet}` : ""}`
    });
  } catch (error) {
    console.error(JSON.stringify({
      level: "error",
      message: "dm notification email failed",
      detail: error instanceof Error ? error.message : String(error)
    }));
  }
}

function dmEmailSnippet(message: Record<string, unknown> | null): string {
  if (!message) return "";
  if (message.forward) return "AI mesajı iletti.";
  if (message.attachment) return "Dosya gönderdi.";
  return String(message.body || "").replace(/\s+/g, " ").trim().slice(0, 220);
}

function dmNotificationEmailHtml(senderName: string, countText: string, snippet: string): string {
  const safeName = escapeHtml(senderName);
  const safeCount = escapeHtml(countText);
  const safeSnippet = escapeHtml(snippet);
  return `<!doctype html>
<html lang="tr">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ReylAI mesaj bildirimi</title>
  </head>
  <body style="margin:0;background:#030712;color:#eef5ff;font-family:Inter,Segoe UI,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:radial-gradient(circle at 16% 0%,rgba(37,99,235,.24),transparent 34%),linear-gradient(135deg,#061a3a,#030712);padding:32px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;border:1px solid rgba(255,255,255,.16);border-radius:28px;background:rgba(18,31,58,.86);box-shadow:0 30px 90px rgba(0,0,0,.38);overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 18px;">
                <div style="font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#93c5fd;font-weight:900;">ReylAI DM</div>
                <h1 style="margin:10px 0 8px;font-size:28px;line-height:1.12;color:#fff;">${safeName} sana mesaj gönderdi</h1>
                <p style="margin:0;color:#c7d8f2;font-size:15px;line-height:1.65;">${safeCount}</p>
              </td>
            </tr>
            ${safeSnippet ? `<tr><td style="padding:10px 28px 28px;"><div style="border:1px solid rgba(96,165,250,.24);border-radius:22px;background:linear-gradient(135deg,rgba(96,165,250,.16),rgba(15,23,42,.54));padding:18px;color:#eef5ff;font-size:15px;line-height:1.6;">${safeSnippet}</div></td></tr>` : ""}
            <tr>
              <td style="padding:0 28px 30px;color:#8ea0bd;font-size:13px;line-height:1.6;">
                Mesajlarını ReylAI içindeki DM ekranından görebilirsin.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>`;
}

function getEmailBinding(env: Env): EmailBinding | null {
  const value = (env as unknown as Record<string, unknown>).EMAIL;
  return isRecord(value) && typeof value.send === "function" ? value as EmailBinding : null;
}

async function verificationHash(userId: string, email: string, code: string): Promise<string> {
  return await sha256Base64Url(`${userId}:${normalizeEmail(email)}:${code}`);
}

function verificationEmailHtml(user: UserRow, code: string): string {
  const name = escapeHtml(user.display_name || "ReylAI kullanıcısı");
  const spacedCode = code.split("").join(" ");
  return `<!doctype html>
<html lang="tr">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ReylAI doğrulama kodu</title>
  </head>
  <body style="margin:0;background:#030712;color:#eef5ff;font-family:Inter,Segoe UI,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:radial-gradient(circle at 20% 0%,rgba(37,99,235,.24),transparent 32%),linear-gradient(135deg,#061a3a,#030712);padding:32px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;border:1px solid rgba(255,255,255,.16);border-radius:28px;background:rgba(18,31,58,.82);box-shadow:0 30px 90px rgba(0,0,0,.38);overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 18px;">
                <div style="font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#93c5fd;font-weight:900;">ReylAI Güvenlik</div>
                <h1 style="margin:10px 0 8px;font-size:28px;line-height:1.1;color:#fff;">E-postanı doğrula</h1>
                <p style="margin:0;color:#c7d8f2;font-size:15px;line-height:1.65;">Merhaba ${name}, hesabını güvene almak için bu kodu ReylAI ayarlarında kullan.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px;">
                <div style="border:1px solid rgba(96,165,250,.26);border-radius:22px;background:linear-gradient(135deg,rgba(96,165,250,.18),rgba(37,99,235,.16));padding:24px;text-align:center;">
                  <div style="font-size:12px;color:#a8b7d1;font-weight:800;text-transform:uppercase;letter-spacing:.16em;">Güvenlik kodu</div>
                  <div style="margin-top:10px;font-size:36px;letter-spacing:.22em;font-weight:950;color:#ffffff;">${spacedCode}</div>
                  <div style="margin-top:10px;color:#ffd44d;font-size:13px;font-weight:800;">${VERIFY_CODE_TTL_MINUTES} dakika geçerlidir.</div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:10px 28px 30px;color:#8ea0bd;font-size:13px;line-height:1.6;">
                Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin. Şifreni kimseyle paylaşma.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>`;
}

function escapeHtml(value: string): string {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char] || char));
}

function normalizeEmail(email: string): string {
  return String(email || "").trim().toLowerCase();
}

function normalizeDisplayName(value: string): string {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function validateAccountInput(email: string, password: string, displayName: string): string {
  if (!EMAIL_RE.test(email) || email.length > 254) return "Geçerli bir e-posta girin.";
  if (password.length < 8 || password.length > 128) return "Şifre 8-128 karakter olmalı.";
  if (!displayName || displayName.length < 2 || displayName.length > 40) return "Görünen ad 2-40 karakter olmalı.";
  return "";
}

function normalizeServerChatStore(rawStore: unknown): ChatStore {
  const rawChats = isRecord(rawStore) && Array.isArray(rawStore.chats) ? rawStore.chats : [];
  const chats = rawChats.filter(isRecord).slice(0, MAX_CHAT_HISTORY_CHATS).map((chat) => {
    const messages = Array.isArray(chat.messages) ? chat.messages.filter(isRecord).slice(-MAX_CHAT_HISTORY_MESSAGES).map((message) => ({
      id: String(message.id || crypto.randomUUID()).slice(0, 120),
      role: message.role === "user" ? "user" : "ai",
      text: String(message.text || "").slice(0, MAX_CHAT_HISTORY_TEXT_CHARS),
      created_at: String(message.created_at || new Date().toISOString()).slice(0, 40)
    })) : [];
    return {
      id: String(chat.id || crypto.randomUUID()).slice(0, 120),
      book_id: String(chat.book_id || "").slice(0, 220),
      book_title: String(chat.book_title || "Kitap").slice(0, 220),
      book_cover: String(chat.book_cover || "").slice(0, 1200),
      drive_id: String(chat.drive_id || "").slice(0, 220),
      book_grade: String(chat.book_grade || "").slice(0, 12),
      title: String(chat.title || "Yeni sohbet").slice(0, 120),
      messages,
      created_at: String(chat.created_at || new Date().toISOString()).slice(0, 40),
      updated_at: String(chat.updated_at || new Date().toISOString()).slice(0, 40)
    };
  });
  chats.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  return { chats };
}

function cleanDmText(value: unknown, limit: number): string {
  return String(value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim().slice(0, limit);
}

function normalizeDmAttachment(value: unknown): { data_url: string; name: string; mime_type: string; size: number } | { error: string } {
  if (!isRecord(value)) return { data_url: "", name: "", mime_type: "", size: 0 };
  const dataUrl = String(value.data_url || "").trim();
  if (!dataUrl) return { data_url: "", name: "", mime_type: "", size: 0 };
  if (dataUrl.length > DM_ATTACHMENT_DATA_URL_LIMIT) {
    return { error: "Dosya çok büyük. DM ekleri için daha küçük bir dosya seç." };
  }
  const match = dataUrl.match(/^data:([^;,]+);base64,([a-z0-9+/=]+)$/i);
  if (!match) return { error: "Dosya okunamadı. Lütfen tekrar seç." };
  const mime = normalizeDmMime(match[1]);
  if (!mime) return { error: "Bu dosya türü DM için desteklenmiyor." };
  const size = estimateBase64Bytes(match[2]);
  if (size > DM_ATTACHMENT_FILE_LIMIT) {
    return { error: "Dosya çok büyük. 650 KB altında bir dosya seç." };
  }
  return {
    data_url: dataUrl,
    name: cleanDmFileName(value.name || "dosya"),
    mime_type: mime,
    size
  };
}

function normalizeDmForward(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const text = cleanDmText(value.text || "", DM_FORWARD_TEXT_LIMIT);
  if (!text) return null;
  return {
    text,
    source_role: String(value.source_role || "ai").slice(0, 24),
    book_title: String(value.book_title || "").trim().slice(0, 180),
    created_at: String(value.created_at || "").slice(0, 40)
  };
}

function normalizeDmMime(value: string): string {
  const mime = String(value || "").trim().toLowerCase();
  const allowed = new Set([
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav"
  ]);
  if (mime === "image/jpg") return "image/jpeg";
  if (allowed.has(mime)) return mime;
  return "";
}

function cleanDmFileName(value: unknown): string {
  const clean = String(value || "dosya").replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim();
  return clean.slice(0, 120) || "dosya";
}

function estimateBase64Bytes(value: string): number {
  const clean = String(value || "").replace(/\s+/g, "");
  const padding = clean.endsWith("==") ? 2 : (clean.endsWith("=") ? 1 : 0);
  return Math.max(0, Math.floor(clean.length * 3 / 4) - padding);
}

async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const hash = await derivePasswordHash(password, salt, PASSWORD_ITERATIONS);
  return `pbkdf2_sha256$${PASSWORD_ITERATIONS}$${bytesToBase64Url(salt)}$${bytesToBase64Url(hash)}`;
}

async function verifyPassword(password: string, storedHash: string): Promise<boolean> {
  const parts = storedHash.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2_sha256") return false;
  const iterations = Number(parts[1]);
  if (!Number.isFinite(iterations) || iterations < 100000 || iterations > PASSWORD_ITERATIONS) return false;
  const salt = base64UrlToBytes(parts[2]);
  const expected = base64UrlToBytes(parts[3]);
  const actual = await derivePasswordHash(password, salt, iterations);
  return constantTimeEqual(actual, expected);
}

async function derivePasswordHash(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const saltBuffer = new ArrayBuffer(salt.byteLength);
  new Uint8Array(saltBuffer).set(salt);
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: saltBuffer, iterations },
    key,
    256
  );
  return new Uint8Array(bits);
}

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return bytesToBase64Url(new Uint8Array(digest));
}

function randomToken(byteLength: number): string {
  return bytesToBase64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
  let mismatch = a.length ^ b.length;
  const maxLength = Math.max(a.length, b.length);
  for (let index = 0; index < maxLength; index += 1) {
    mismatch |= (a[index] || 0) ^ (b[index] || 0);
  }
  return mismatch === 0;
}

function bearerToken(request: Request): string {
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : "";
}

function optionalEnv(env: Env, key: string): string {
  const value = (env as unknown as Record<string, unknown>)[key];
  return typeof value === "string" ? value : "";
}

async function handleLibrary(url: URL, env: Env): Promise<Response> {
  const grade = url.searchParams.get("grade") || "";
  const books = await fetchLibrary(env);
  const filtered = books.filter((book) => !grade || (book.grade || "9") === grade);
  const enriched = await Promise.all(filtered.map((book) => enrichBook(book, env)));
  return json(enriched);
}

async function handleBookRename(request: Request, env: Env): Promise<Response> {
  const admin = await requireAdminAction(request, env);
  if (admin instanceof Response) return admin;
  const payload = await readJson<{ book_id?: string; drive_id?: string; name?: string }>(request);
  const requestedId = safeId(payload.book_id || payload.drive_id || "");
  const name = normalizeBookName(payload.name || "");
  if (!requestedId) return json({ success: false, error: "Kitap kimliği eksik." }, 400);
  if (!name) return json({ success: false, error: "İsim boş olamaz." }, 400);

  const book = await findVisibleBook(env, requestedId);
  if (!book) return json({ success: false, error: "Kitap bulunamadı." }, 404);
  const key = bookKey(book) || requestedId;
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO book_admin_changes (book_key, title, name, deleted_at, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, ?) " +
    "ON CONFLICT(book_key) DO UPDATE SET title = excluded.title, name = excluded.name, deleted_at = NULL, updated_at = excluded.updated_at, updated_by = excluded.updated_by"
  ).bind(key, name, name, now, admin.user.id).run();
  return json({ success: true, book_id: key, title: name });
}

async function handleBookCoverUpdate(request: Request, env: Env): Promise<Response> {
  const admin = await requireAdminAction(request, env);
  if (admin instanceof Response) return admin;
  const form = await request.formData();
  const requestedId = safeId(String(form.get("book_id") || form.get("drive_id") || ""));
  if (!requestedId) return json({ success: false, error: "Kitap kimliği eksik." }, 400);
  const book = await findVisibleBook(env, requestedId);
  if (!book) return json({ success: false, error: "Kitap bulunamadı." }, 404);

  const file = form.get("cover");
  if (!(file instanceof File) || !file.size) {
    return json({ success: false, error: "Thumbnail dosyası bulunamadı." }, 400);
  }
  const mime = normalizeImageMime(file.type);
  if (!mime) {
    return json({ success: false, error: "Sadece JPG, PNG veya WebP görsel yüklenebilir." }, 400);
  }
  if (file.size > BOOK_COVER_FILE_LIMIT) {
    return json({ success: false, error: "Kapak görseli çok büyük. Daha küçük bir görsel seçin." }, 413);
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  const dataUrl = `data:${mime};base64,${bytesToBase64(bytes)}`;
  const dataUrlError = validateCoverDataUrl(dataUrl);
  if (dataUrlError) return json({ success: false, error: dataUrlError }, 400);

  const key = bookKey(book) || requestedId;
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO book_admin_changes (book_key, cover_data_url, cover_mime_type, cover_updated_at, deleted_at, updated_at, updated_by) VALUES (?, ?, ?, ?, NULL, ?, ?) " +
    "ON CONFLICT(book_key) DO UPDATE SET cover_data_url = excluded.cover_data_url, cover_mime_type = excluded.cover_mime_type, " +
    "cover_updated_at = excluded.cover_updated_at, deleted_at = NULL, updated_at = excluded.updated_at, updated_by = excluded.updated_by"
  ).bind(key, dataUrl, mime, now, now, admin.user.id).run();

  return json({
    success: true,
    cover_url: `/api/cover/${encodeURIComponent(key)}?v=${encodeURIComponent(now)}`,
    cover_data_url: dataUrl
  });
}

async function handleBookDelete(request: Request, env: Env): Promise<Response> {
  const admin = await requireAdminAction(request, env);
  if (admin instanceof Response) return admin;
  const payload = await readJson<{ book_id?: string; drive_id?: string }>(request);
  const requestedId = safeId(payload.book_id || payload.drive_id || "");
  if (!requestedId) return json({ success: false, error: "Kitap kimliği eksik." }, 400);

  const baseBooks = await fetchBaseLibrary(env);
  const book = findBook(baseBooks, requestedId);
  if (!book) return json({ success: false, error: "Kitap bulunamadı." }, 404);
  const key = bookKey(book) || requestedId;
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO book_admin_changes (book_key, deleted_at, updated_at, updated_by) VALUES (?, ?, ?, ?) " +
    "ON CONFLICT(book_key) DO UPDATE SET deleted_at = excluded.deleted_at, updated_at = excluded.updated_at, updated_by = excluded.updated_by"
  ).bind(key, now, now, admin.user.id).run();
  return json({ success: true, book_id: key });
}

async function handleBookCover(id: string, env: Env): Promise<Response> {
  const change = await getBookAdminChange(env, id);
  if (change?.cover_data_url && !change.deleted_at) {
    const response = dataUrlImageResponse(change.cover_data_url, change.cover_updated_at || change.updated_at || "");
    if (response) return response;
  }
  return redirectIfExists(env, `/reylai_assets/covers/${encodeURIComponent(id)}.jpg`);
}

async function handleConfig(env: Env): Promise<Response> {
  const config = await fetchStaticJson<Record<string, unknown>>(env, "/reylai_config.json");
  return json(config || { folder_ids: {} });
}

async function handleDebugGas(env: Env): Promise<Response> {
  if (!env.GAS_WEB_APP_URL) {
    return json({ error: "GAS_WEB_APP_URL ayarlanmamış" }, 500);
  }
  const results: Record<string, unknown> = {};
  for (const grade of ["9", "10"]) {
    const target = new URL(env.GAS_WEB_APP_URL);
    target.searchParams.set("action", "list");
    target.searchParams.set("grade", grade);
    try {
      const response = await fetch(target.toString(), { redirect: "follow" });
      results[grade] = {
        status: response.status,
        raw: await readTextSnippet(response, 2000)
      };
    } catch (error) {
      results[grade] = { error: error instanceof Error ? error.message : String(error) };
    }
  }
  return json(results);
}

async function handleAnalyze(request: Request, env: Env): Promise<Response> {
  const payload = await readJson<AnalyzePayload>(request);
  const response = await analyzePayload(payload, env);
  return json(response, response.error ? 400 : 200);
}

async function analyzePayload(payload: AnalyzePayload, env: Env): Promise<Record<string, unknown>> {
  const prompt = String(payload.prompt || "").trim();
  const selectedId = safeId(payload.book_id || payload.drive_id || "");
  const bookName = String(payload.book_name || "Kitap").trim() || "Kitap";

  if (!env.MISTRAL_API_KEY) return { error: "MISTRAL_API_KEY yapılandırılmamış." };
  if (!prompt) return { error: "Prompt eksik." };
  if (!selectedId) return { error: "book_id eksik." };

  const smallTalk = smallTalkResponse(prompt);
  if (smallTalk) {
    return {
      result: smallTalk,
      local: true,
      chat_title: payload.title_requested ? fallbackChatTitle(prompt) : ""
    };
  }

  const library = await fetchLibrary(env);
  const book = findBook(library, selectedId);
  const scanKeys = scanKeysForBook(book, selectedId);
  const scanData = await fetchScanData(env, scanKeys);
  if (!scanData?.pages?.length) {
    return {
      error: "Seçili kitap için hazır tarama metni bulunamadı.",
      missing_scan: true
    };
  }

  const contextText = buildContextExcerpt(scanData.pages, prompt);
  if (!contextText) {
    return {
      error: "Seçili kitap için kullanılabilir tarama metni bulunamadı.",
      missing_scan: true
    };
  }

  const requestedPages = extractPageNumbers(prompt);
  const historyContext = buildHistoryContext(payload.chat_history || []);
  let systemMessage = [
    "Sen ReylAI adlı bir yapay zeka asistanısın.",
    "MEB ders kitaplarını analiz eder, öğrencilere ve öğretmenlere yardımcı olursun.",
    "Yalnızca verilen hazır tarama metnine dayan; kitapta olmayan bilgiyi uydurma.",
    "Bağlam yeterli değilse bunu açıkça söyle ve kullanıcıdan sayfa, soru numarası veya konu adı iste.",
    "Yanıtı Türkçe, sade ve öğrenciye yardımcı olacak biçimde ver.",
    "Soru çözüyorsan önce yöntemi, sonra sonucu ver.",
    "Mümkünse kaynak sayfayı [Sayfa X] formatında belirt.",
    "Matematiksel ifadeleri gerekiyorsa LaTeX ile yaz."
  ].join("\n");

  if (requestedPages.length) {
    systemMessage += `\n\nKullanıcı özellikle şu sayfa(lar)a odaklanıyor: ${requestedPages.join(", ")}.`;
  }
  if (historyContext) {
    systemMessage += "\n\nÖnceki konuşma özeti:\n" + historyContext;
  }
  systemMessage += "\n\nKitabın ilgili bölümleri:\n\n" + contextText;

  const messages: MistralMessage[] = [
    { role: "system", content: systemMessage },
    {
      role: "user",
      content: `Kitap adı: ${book?.title || book?.name || bookName}\nİstenen sayfalar: ${requestedPages.join(", ") || "belirtilmedi"}\n\nKullanıcı sorusu: ${prompt}`
    }
  ];

  try {
    const mistralResponse = await mistralChat(env, messages, { temperature: 0.2 });
    const result = mistralResponseText(mistralResponse);
    if (!result) return { error: "Mistral boş yanıt döndürdü." };

    let chatTitle = "";
    if (payload.title_requested) {
      chatTitle = await generateChatTitle(env, book?.title || book?.name || bookName, prompt, result);
    }
    return { result, chat_title: chatTitle };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const lower = message.toLowerCase();
    return {
      error: message,
      rate_limit: lower.includes("429") || lower.includes("quota") || lower.includes("rate limit"),
      temporary_unavailable: lower.includes("503") || lower.includes("unavailable") || lower.includes("high demand")
    };
  }
}

async function handleServePdf(id: string, env: Env): Promise<Response> {
  const library = await fetchLibrary(env);
  const book = findBook(library, id);
  const explicitUrl = firstValidUrl(book?.pdf_url, book?.source_url, book?.remote_url);
  if (explicitUrl) return Response.redirect(explicitUrl, 302);

  if (UUID_RE.test(id) && env.BOOKS_REMOTE_BASE_URL) {
    return Response.redirect(new URL(`${encodeURIComponent(id)}.pdf`, ensureSlash(env.BOOKS_REMOTE_BASE_URL)).toString(), 302);
  }

  if (book?.drive_id) {
    const driveUrl = `https://drive.google.com/uc?export=download&id=${encodeURIComponent(book.drive_id)}`;
    return Response.redirect(driveUrl, 302);
  }

  return text("PDF bulunamadı", 404);
}

async function enrichBook(book: Book, env: Env): Promise<Book> {
  const publicBook: Book = { ...book };
  const key = publicBook.book_id || publicBook.drive_id || "";
  const remotePdf = firstValidUrl(publicBook.pdf_url, publicBook.source_url, publicBook.remote_url);
  if (!publicBook.pdf_url && remotePdf) publicBook.pdf_url = remotePdf;
  if (!publicBook.pdf_url && publicBook.book_id && UUID_RE.test(publicBook.book_id) && env.BOOKS_REMOTE_BASE_URL) {
    publicBook.pdf_url = new URL(`${encodeURIComponent(publicBook.book_id)}.pdf`, ensureSlash(env.BOOKS_REMOTE_BASE_URL)).toString();
    publicBook.pdf_source ||= "book_archive";
  }
  if (key && publicBook.cover_data_url) {
    publicBook.cover_url = `/api/cover/${encodeURIComponent(key)}${publicBook.cover_updated_at ? `?v=${encodeURIComponent(publicBook.cover_updated_at)}` : ""}`;
  } else if (key && !publicBook.cover_url && await staticExists(env, `/reylai_assets/covers/${encodeURIComponent(key)}.jpg`)) {
    publicBook.cover_url = `/api/cover/${encodeURIComponent(key)}`;
  }
  if (key) {
    const scan = await fetchScanData(env, [key]);
    if (scan) {
      publicBook.scan_status = "done";
      publicBook.scan_pages = scan.total_pages || scan.pages?.length || 0;
      publicBook.scan_extractor = publicScanExtractor(scan.extractor || "");
    }
  }
  return publicBook;
}

async function fetchLibrary(env: Env): Promise<Book[]> {
  return applyBookAdminChanges(env, await fetchBaseLibrary(env));
}

async function fetchBaseLibrary(env: Env): Promise<Book[]> {
  const data = await fetchStaticJson<unknown>(env, "/reylai_library.json");
  const registeredBooks = Array.isArray(data) ? data.filter(isBook) : [];
  const archiveIds = await fetchBookArchiveIds(env);
  if (!archiveIds.length) return registeredBooks;

  const registeredById = new Map<string, Book>();
  for (const book of registeredBooks) {
    const key = safeId(book.book_id || book.drive_id || "");
    if (key && !registeredById.has(key)) registeredById.set(key, book);
  }

  const seen = new Set<string>();
  const archiveBooks = archiveIds.map((id) => {
    seen.add(id);
    const registered = registeredById.get(id) || {};
    const title = String(registered.title || registered.name || "").trim();
    return {
      ...registered,
      book_id: id,
      drive_id: registered.drive_id || "",
      local_path: registered.local_path || "",
      grade: registered.grade || "9",
      name: registered.name || title || `${id}.pdf`,
      title: title || registered.name || `${id}.pdf`,
      pdf_url: archivePdfUrl(env, id),
      pdf_source: "book_archive"
    };
  });

  const extraBooks = registeredBooks.filter((book) => {
    const key = safeId(book.book_id || book.drive_id || "");
    return !key || !seen.has(key);
  });
  return [...archiveBooks, ...extraBooks];
}

async function applyBookAdminChanges(env: Env, books: Book[]): Promise<Book[]> {
  const result = await env.DB.prepare("SELECT * FROM book_admin_changes").all<BookAdminChange>();
  const changes = new Map<string, BookAdminChange>();
  for (const row of result.results || []) {
    if (row.book_key) changes.set(String(row.book_key), row);
  }
  if (!changes.size) return books;

  const output: Book[] = [];
  for (const book of books) {
    const key = bookKey(book);
    const change = key ? changes.get(key) : null;
    if (change?.deleted_at) continue;
    if (!change) {
      output.push(book);
      continue;
    }
    const next: Book = { ...book };
    const title = String(change.title || "").trim();
    const name = String(change.name || "").trim();
    if (title) next.title = title;
    if (name) next.name = name;
    if (change.cover_data_url) {
      next.cover_data_url = change.cover_data_url;
      next.cover_updated_at = change.cover_updated_at || change.updated_at || next.cover_updated_at;
      next.cover_url = `/api/cover/${encodeURIComponent(key)}${next.cover_updated_at ? `?v=${encodeURIComponent(next.cover_updated_at)}` : ""}`;
    }
    if (change.updated_at) next.updated_at = change.updated_at;
    output.push(next);
  }
  return output;
}

async function getBookAdminChange(env: Env, key: string): Promise<BookAdminChange | null> {
  if (!key) return null;
  return await env.DB.prepare("SELECT * FROM book_admin_changes WHERE book_key = ?")
    .bind(key)
    .first<BookAdminChange>();
}

async function fetchBookArchiveIds(env: Env): Promise<string[]> {
  if (!env.BOOKS_REMOTE_BASE_URL) return [];
  const response = await fetch(ensureSlash(env.BOOKS_REMOTE_BASE_URL), {
    headers: { "accept": "text/html" },
    cf: { cacheTtl: 300, cacheEverything: true }
  });
  if (!response.ok) return [];
  const html = await readTextSnippet(response, 50000);
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const match of html.matchAll(BOOK_ARCHIVE_PDF_RE)) {
    const id = safeId(match[1] || "");
    if (id && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  }
  return ids;
}

function archivePdfUrl(env: Env, id: string): string {
  return new URL(`${encodeURIComponent(id)}.pdf`, ensureSlash(env.BOOKS_REMOTE_BASE_URL || "https://thejinx1.github.io/blupblupreylai-books/")).toString();
}

async function fetchScanData(env: Env, keys: string[]): Promise<ScanData | null> {
  for (const rawKey of keys) {
    const key = safeId(rawKey);
    if (!key) continue;
    const data = await fetchStaticJson<ScanData>(env, `/reylai_assets/scans/${encodeURIComponent(key)}.json`);
    if (data?.pages?.length) return data;
  }
  return null;
}

async function fetchStaticJson<T>(env: Env, path: string): Promise<T | null> {
  const response = await fetch(staticUrl(env, path), {
    headers: { "accept": "application/json" },
    cf: { cacheTtl: 60, cacheEverything: true }
  });
  if (!response.ok) return null;
  return await response.json() as T;
}

async function redirectIfExists(env: Env, path: string): Promise<Response> {
  if (!await staticExists(env, path)) return text("", 404);
  return Response.redirect(staticUrl(env, path), 302);
}

async function staticExists(env: Env, path: string): Promise<boolean> {
  const response = await fetch(staticUrl(env, path), {
    method: "HEAD",
    cf: { cacheTtl: 300, cacheEverything: true }
  });
  return response.ok;
}

async function mistralChat(
  env: Env,
  messages: MistralMessage[],
  options: { temperature?: number; maxTokens?: number } = {}
): Promise<unknown> {
  const payload: Record<string, unknown> = {
    model: env.MISTRAL_MODEL || "mistral-small-latest",
    messages,
    stream: false,
    temperature: options.temperature ?? 0.2,
    top_p: 0.9
  };
  if (options.maxTokens) payload.max_tokens = options.maxTokens;

  const response = await fetch(env.MISTRAL_CHAT_URL || "https://api.mistral.ai/v1/chat/completions", {
    method: "POST",
    headers: {
      "authorization": `Bearer ${env.MISTRAL_API_KEY}`,
      "content-type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const snippet = await readTextSnippet(response, 500);
    throw new Error(`Mistral API hatası (${response.status}): ${snippet || response.statusText}`);
  }

  return await response.json();
}

function mistralResponseText(payload: unknown): string {
  if (!isRecord(payload) || !Array.isArray(payload.choices) || !isRecord(payload.choices[0])) return "";
  const message = payload.choices[0].message;
  if (!isRecord(message)) return "";
  const content = message.content;
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (typeof part === "string") return part;
      if (isRecord(part) && typeof part.text === "string") return part.text;
      return "";
    }).join("").trim();
  }
  return "";
}

async function generateChatTitle(env: Env, bookName: string, prompt: string, answer: string): Promise<string> {
  const fallback = fallbackChatTitle(prompt);
  try {
    const titlePrompt = [
      "Aşağıdaki ders kitabı sohbeti için Türkçe, kısa ve doğal bir başlık yaz.",
      "Sadece başlığı döndür; tırnak, açıklama veya madde işareti kullanma.",
      "En fazla 6 kelime olsun.",
      "",
      `Kitap: ${bookName}`,
      `Kullanıcı sorusu: ${prompt}`,
      `Cevap özeti: ${answer.slice(0, 700)}`
    ].join("\n");
    const response = await mistralChat(env, [{ role: "user", content: titlePrompt }], {
      maxTokens: 32,
      temperature: 0.1
    });
    return cleanChatTitle(mistralResponseText(response)) || fallback;
  } catch {
    return fallback;
  }
}

function buildContextExcerpt(pages: ScanPage[], prompt: string): string {
  const selectedPages = pickContextPages(pages, prompt);
  const sourcePages = selectedPages.length ? selectedPages : pages.filter((page) => cleanPageText(page).length).slice(0, 3);
  const charLimit = selectedPages.length ? CONTEXT_CHAR_LIMIT : FALLBACK_CHAR_LIMIT;
  const parts: string[] = [];
  let total = 0;

  for (const page of sourcePages) {
    const pageNo = Number(page.page || 0);
    const text = cleanPageText(page);
    if (!pageNo || !text) continue;
    const part = `[Sayfa ${pageNo}]\n${text}`;
    if (total + part.length > charLimit) {
      const remaining = charLimit - total;
      if (remaining > 200) parts.push(part.slice(0, remaining));
      break;
    }
    parts.push(part);
    total += part.length;
  }

  return parts.join("\n\n");
}

function pickContextPages(pages: ScanPage[], prompt: string): ScanPage[] {
  const promptLower = normalizeText(prompt);
  const byPage = new Map<number, string>();
  for (const page of pages) {
    const pageNo = Number(page.page || 0);
    const text = cleanPageText(page);
    if (pageNo && text) byPage.set(pageNo, text);
  }

  const requested = extractPageNumbers(prompt);
  if (requested.length) {
    const selected: ScanPage[] = [];
    const seen = new Set<number>();
    const radius = requested.length === 1 && /(civar|yakın|yaklasik|yaklaşık)/i.test(prompt) ? 2 : (requested.length === 1 ? 1 : 0);
    for (const pageNo of requested) appendPageWindow(selected, seen, byPage, pageNo, radius);
    return selected.slice(0, MAX_CONTEXT_PAGES);
  }

  const terms = queryTerms(prompt).slice(0, 12);
  const scored: Array<{ score: number; page: number; text: string }> = [];
  for (const [pageNo, text] of byPage) {
    const lower = normalizeText(text);
    let score = 0;
    if (promptLower && lower.includes(promptLower)) score += 20;
    for (const term of terms) {
      const hits = lower.split(term).length - 1;
      if (hits > 0) score += Math.min(hits, 5) * 3;
    }
    if (score > 0) scored.push({ score, page: pageNo, text });
  }

  scored.sort((a, b) => b.score - a.score || a.page - b.page);
  return scored.slice(0, MAX_CONTEXT_PAGES).sort((a, b) => a.page - b.page).map((item) => ({
    page: item.page,
    text: item.text
  }));
}

function appendPageWindow(target: ScanPage[], seen: Set<number>, byPage: Map<number, string>, center: number, radius: number): void {
  for (let pageNo = center - radius; pageNo <= center + radius; pageNo += 1) {
    const text = byPage.get(pageNo);
    if (text && !seen.has(pageNo)) {
      target.push({ page: pageNo, text });
      seen.add(pageNo);
    }
  }
}

function extractPageNumbers(prompt: string): number[] {
  const textValue = prompt.toLowerCase();
  const found: number[] = [];
  for (const match of textValue.matchAll(/sayfa\s*(\d{1,4})\s*[-–]\s*(\d{1,4})/g)) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    for (let page = Math.min(start, end); page <= Math.max(start, end); page += 1) found.push(page);
  }
  for (const match of textValue.matchAll(/(?:sayfa|sf)\s*(\d{1,4})/g)) found.push(Number(match[1]));
  for (const match of textValue.matchAll(/(\d{1,4})\.?\s*(?:sayfa|sf)\w*/g)) found.push(Number(match[1]));
  return [...new Set(found)].filter((page) => page > 0 && page < 2000);
}

function queryTerms(prompt: string): string[] {
  const stop = new Set(["için", "icin", "olan", "bana", "şunu", "sunu", "bunu", "nedir", "nasıl", "nasil", "sayfa", "soru", "cevap", "lütfen", "lutfen"]);
  return normalizeText(prompt)
    .split(/[^a-z0-9ığüşöçİĞÜŞÖÇ]+/i)
    .map((term) => term.trim())
    .filter((term) => term.length >= 3 && !stop.has(term));
}

function smallTalkResponse(prompt: string): string {
  const clean = normalizeText(prompt);
  if (/^(selam|merhaba|mrb|slm|sa|hey|hi|hello)\b/.test(clean)) {
    return "Merhaba, buradayım. Kitaptaki bir soru, sayfa veya konuyu yaz; hemen yardımcı olayım.";
  }
  if (clean.includes("teşekkür") || clean.includes("tesekkur") || clean.includes("sağ ol") || clean.includes("sag ol")) {
    return "Rica ederim. Buradayım; kitapla ilgili bir soru, sayfa veya konu yazarsan hemen yardımcı olurum.";
  }
  if (clean.includes("kimsin") || clean.includes("sen nesin") || clean.includes("adın ne") || clean.includes("adin ne")) {
    return "Ben ReylAI. Ders kitaplarındaki sayfa, soru ve konuları hızlıca açıklamak için buradayım.";
  }
  return "";
}

function buildHistoryContext(history: Array<{ role?: string; text?: string }>): string {
  return history.slice(-10).map((item) => {
    const role = item.role === "user" ? "Kullanıcı" : "ReylAI";
    const textValue = String(item.text || "").replace(/\s+/g, " ").trim().slice(0, 1800);
    return textValue ? `${role}: ${textValue}` : "";
  }).filter(Boolean).join("\n");
}

function fallbackChatTitle(prompt: string): string {
  return cleanChatTitle(prompt) || "Yeni sohbet";
}

function cleanChatTitle(title: string): string {
  let clean = title.replace(/[`*_>#[\]()"“”‘’]+/g, " ").replace(/\s+/g, " ").trim().replace(/[.:-]+$/g, "");
  if (clean.length > 64) clean = clean.slice(0, 61).trimEnd() + "...";
  return clean;
}

function findBook(library: Book[], selectedId: string): Book | undefined {
  return library.find((book) => book.book_id === selectedId) || library.find((book) => book.drive_id === selectedId);
}

function bookKey(book: Book | undefined): string {
  return safeId(book?.book_id || book?.drive_id || "");
}

async function findVisibleBook(env: Env, selectedId: string): Promise<Book | undefined> {
  return findBook(await fetchLibrary(env), selectedId);
}

function scanKeysForBook(book: Book | undefined, selectedId: string): string[] {
  return [selectedId, book?.book_id || "", book?.drive_id || ""].filter((value, index, arr) => value && arr.indexOf(value) === index);
}

function isBook(value: unknown): value is Book {
  return isRecord(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function cleanPageText(page: ScanPage): string {
  return String(page.text || "").trim();
}

function normalizeText(value: string): string {
  return value.toLocaleLowerCase("tr-TR").replace(/\s+/g, " ").trim();
}

function normalizeBookName(value: string): string {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 180);
}

function publicScanExtractor(extractor: string): string {
  return extractor.toLowerCase() === "adobe" ? "pypdf" : extractor;
}

function firstValidUrl(...values: Array<string | undefined>): string {
  for (const value of values) {
    const url = String(value || "").trim();
    if (/^https?:\/\/.+\.pdf($|[?#])/i.test(url)) return url;
  }
  return "";
}

function staticUrl(env: Env, path: string): string {
  return new URL(path, ensureSlash(env.STATIC_ORIGIN || "https://ai.reyliar.xyz")).toString();
}

function ensureSlash(url: string): string {
  return url.endsWith("/") ? url : `${url}/`;
}

function safeId(value: string): string {
  const trimmed = String(value || "").trim();
  return VALID_ID.test(trimmed) ? trimmed : "";
}

function normalizeImageMime(value: string): string {
  const mime = String(value || "").trim().toLowerCase();
  if (mime === "image/jpg") return "image/jpeg";
  return ["image/jpeg", "image/png", "image/webp"].includes(mime) ? mime : "";
}

function validateCoverDataUrl(value: string): string {
  const dataUrl = String(value || "").trim();
  if (!dataUrl) return "Kapak görseli boş.";
  if (dataUrl.length > BOOK_COVER_DATA_URL_LIMIT) return "Kapak görseli çok büyük. Daha küçük bir görsel seçin.";
  if (!/^data:image\/(?:png|jpeg|jpg|webp);base64,[a-z0-9+/=]+$/i.test(dataUrl)) {
    return "Kapak görseli PNG, JPG veya WEBP olmalı.";
  }
  return "";
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function dataUrlImageResponse(dataUrl: string, updatedAt = ""): Response | null {
  const match = String(dataUrl || "").match(/^data:(image\/(?:png|jpeg|jpg|webp));base64,([a-z0-9+/=]+)$/i);
  if (!match) return null;
  const mime = normalizeImageMime(match[1]);
  if (!mime) return null;
  const bytes = base64ToBytes(match[2]);
  const body = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return new Response(body, {
    headers: {
      "content-type": mime,
      "cache-control": "public, max-age=3600",
      ...(updatedAt ? { "etag": `"${awaitlessHash(updatedAt)}"` } : {})
    }
  });
}

function awaitlessHash(value: string): string {
  return String(value || "").replace(/[^a-z0-9_-]+/gi, "").slice(0, 80) || "cover";
}

async function readJson<T>(request: Request): Promise<T> {
  try {
    return await request.json() as T;
  } catch {
    return {} as T;
  }
}

async function readTextSnippet(response: Response, limit: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let output = "";
  try {
    while (output.length < limit) {
      const chunk = await reader.read();
      if (chunk.done) break;
      output += decoder.decode(chunk.value, { stream: true });
      if (output.length >= limit) {
        await reader.cancel();
        break;
      }
    }
    output += decoder.decode();
  } finally {
    reader.releaseLock();
  }
  return output.slice(0, limit);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: JSON_HEADERS
  });
}

function text(data: string, status = 200): Response {
  return new Response(data, {
    status,
    headers: TEXT_HEADERS
  });
}

function corsHeaders(request: Request): Headers {
  const headers = new Headers();
  const origin = request.headers.get("origin");
  headers.set("access-control-allow-origin", origin || "*");
  headers.set("access-control-allow-methods", "GET,POST,PUT,DELETE,OPTIONS");
  headers.set("access-control-allow-headers", "content-type,x-auth-token,authorization");
  headers.set("access-control-max-age", "86400");
  return headers;
}

