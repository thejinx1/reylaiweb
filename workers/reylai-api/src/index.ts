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
const ADMIN_PANEL_PASSWORD = "benbugunpilavyedim";
const VERIFY_CODE_TTL_MINUTES = 10;
const VERIFY_CODE_COOLDOWN_SECONDS = 60;
const AVATAR_DATA_URL_LIMIT = 360_000;
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
  current_password?: string;
  new_password?: string;
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
  "email_verification_sent_at"
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
      return json({ error: "Sunucu hatasÄ±." }, 500);
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

  if (path === "/api/auth/verification/send" && request.method === "POST") {
    return handleVerificationSend(request, env);
  }

  if (path === "/api/auth/verification/confirm" && request.method === "POST") {
    return handleVerificationConfirm(request, env);
  }

  if (path === "/api/admin/accounts" && request.method === "GET") {
    return handleAdminAccounts(request, env);
  }

  if (path === "/api/admin/accounts/sensitive" && request.method === "POST") {
    return handleAdminAccountsSensitive(request, env);
  }

  if (request.method === "GET" && path === "/api/library") {
    return handleLibrary(url, env);
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
    if (request.method === "POST") return json({ success: false, error: "Statik yayÄ±nda ayar kaydetme kapalÄ±." }, 405);
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
    return handleAnalyze(request, env);
  }

  if (request.method === "POST" && path === "/api/analyze_start") {
    const auth = await requireAuth(request, env);
    if (auth instanceof Response) return auth;
    const data = await readJson<AnalyzePayload>(request);
    const response = await analyzePayload(data, env);
    return json({ success: !response.error, analysis_id: crypto.randomUUID(), ...response });
  }

  if (request.method === "GET" && path.startsWith("/api/analyze_status/")) {
    return json({ done: true, message: "HazÄ±r" });
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
    return redirectIfExists(env, `/reylai_assets/covers/${encodeURIComponent(id)}.jpg`);
  }

  if (request.method === "GET" && path.startsWith("/api/serve_pdf/")) {
    const id = safeId(path.split("/").pop() || "");
    if (!id) return text("PDF bulunamadÄ±", 404);
    return handleServePdf(id, env);
  }

  if (request.method === "GET" && path.startsWith("/api/page_image/")) {
    return text("Statik yayÄ±nda sayfa gÃ¶rseli Ã¼retimi desteklenmiyor.", 404);
  }

  if (
    ["POST", "PUT", "DELETE"].includes(request.method) &&
    ["/api/upload", "/api/add_book", "/api/delete", "/api/rename_book", "/api/update_cover", "/api/scan_missing_books", "/api/scan_missing_books_cancel"].some((prefix) => path.startsWith(prefix))
  ) {
    return json({ success: false, error: "Bu iÅŸlem statik Cloudflare yayÄ±nda desteklenmiyor." }, 405);
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
      current_message: "Statik yayÄ±nda tarama iÅŸi yok.",
      logs: []
    });
  }

  if (request.method === "POST" && path === "/api/verify_password") {
    return handleAdminPasswordVerify(request, env);
  }

  return json({ error: "API endpoint bulunamadÄ±." }, 404);
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
  if (!user) return json({ success: false, error: "Hesap oluÅŸturuldu ama oturum aÃ§Ä±lamadÄ±." }, 500);
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
    return json({ success: false, error: "E-posta veya ÅŸifre hatalÄ±." }, 401);
  }

  const user = await getUserByEmail(env, email);
  if (!user || !await verifyPassword(password, user.password_hash)) {
    return json({ success: false, error: "E-posta veya ÅŸifre hatalÄ±." }, 401);
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
    if (!user) return json({ success: false, error: "Hesap bulunamadÄ±." }, 404);

    const updates: string[] = [];
    const values: unknown[] = [];
    const now = new Date().toISOString();
    let shouldSendVerification = false;

    if (Object.prototype.hasOwnProperty.call(payload, "display_name")) {
      const displayName = normalizeDisplayName(payload.display_name || "");
      if (!displayName || displayName.length < 2 || displayName.length > 40) {
        return json({ success: false, error: "GÃ¶rÃ¼nen ad 2-40 karakter olmalÄ±." }, 400);
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
    const requestedPassword = Object.prototype.hasOwnProperty.call(payload, "new_password") ? String(payload.new_password || "") : "";
    const changingEmail = Boolean(requestedEmail && requestedEmail !== user.email);
    const changingPassword = Boolean(requestedPassword);

    if (changingEmail || changingPassword) {
      const currentPassword = String(payload.current_password || "");
      if (!currentPassword || !await verifyPassword(currentPassword, user.password_hash)) {
        return json({ success: false, error: "Mevcut ÅŸifre doÄŸrulanamadÄ±." }, 401);
      }
    }

    if (changingEmail) {
      if (!EMAIL_RE.test(requestedEmail) || requestedEmail.length > 254) {
        return json({ success: false, error: "GeÃ§erli bir e-posta girin." }, 400);
      }
      updates.push(
        "email = ?",
        "role = ?",
        "email_verified_at = NULL",
        "email_verification_code_hash = NULL",
        "email_verification_expires_at = NULL",
        "email_verification_sent_at = NULL"
      );
      values.push(requestedEmail, roleForEmail(requestedEmail));
      shouldSendVerification = true;
    }

    if (changingPassword) {
      if (requestedPassword.length < 8 || requestedPassword.length > 128) {
        return json({ success: false, error: "Åifre 8-128 karakter olmalÄ±." }, 400);
      }
      updates.push("password_hash = ?", "password_updated_at = ?");
      values.push(await hashPassword(requestedPassword), now);
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
        return json({ success: false, error: "Bu e-posta baÅŸka bir hesapta kullanÄ±lÄ±yor." }, 409);
      }
      throw error;
    }

    const freshUser = await getUserById(env, auth.user.id);
    const delivery = shouldSendVerification && freshUser ? await sendVerificationCode(env, freshUser) : null;
    return json({
      success: true,
      user: freshUser ? publicUser(freshUser) : auth.user,
      verification_email_sent: delivery?.sent || false,
      email_delivery_configured: delivery ? delivery.configured : undefined
    });
  }
  const authCtx = auth as AuthContext;
  const payload = await readJson<{ display_name?: string }>(request);
  const displayName = normalizeDisplayName(payload.display_name || "");
  if (!displayName || displayName.length < 2 || displayName.length > 40) {
    return json({ success: false, error: "GÃ¶rÃ¼nen ad 2-40 karakter olmalÄ±." }, 400);
  }
  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?")
    .bind(displayName, now, authCtx.user.id)
    .run();
  const user = await getUserById(env, authCtx.user.id);
  if (user !== null) return json({ success: true, user: publicUser(user as UserRow) });
  return json({ success: true, user: { ...authCtx.user, display_name: displayName } });
}

async function handleVerificationSend(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadÄ±." }, 404);
  if (user.email_verified_at) {
    return json({ success: true, already_verified: true, user: publicUser(user) });
  }

  const sentAt = user.email_verification_sent_at ? Date.parse(user.email_verification_sent_at) : 0;
  const waitMs = sentAt ? VERIFY_CODE_COOLDOWN_SECONDS * 1000 - (Date.now() - sentAt) : 0;
  if (waitMs > 0) {
    return json({
      success: false,
      error: `Yeni kod iÃ§in ${Math.ceil(waitMs / 1000)} saniye bekleyin.`,
      retry_after: Math.ceil(waitMs / 1000)
    }, 429);
  }

  const delivery = await sendVerificationCode(env, user);
  return json({
    success: delivery.sent,
    email_delivery_configured: delivery.configured,
    error: delivery.sent ? undefined : delivery.error || "E-posta gÃ¶nderilemedi."
  }, delivery.sent ? 200 : 503);
}

async function handleVerificationConfirm(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  const payload = await readJson<VerificationPayload>(request);
  const code = String(payload.code || "").replace(/\D+/g, "").slice(0, 12);
  if (code.length !== 6) return json({ success: false, error: "6 haneli kodu girin." }, 400);

  const user = await getUserById(env, auth.user.id);
  if (!user) return json({ success: false, error: "Hesap bulunamadÄ±." }, 404);
  if (user.email_verified_at) return json({ success: true, already_verified: true, user: publicUser(user) });
  if (!user.email_verification_code_hash || !user.email_verification_expires_at) {
    return json({ success: false, error: "Ã–nce yeni bir doÄŸrulama kodu isteyin." }, 400);
  }
  if (Date.parse(user.email_verification_expires_at) <= Date.now()) {
    return json({ success: false, error: "Kodun sÃ¼resi doldu. Yeni kod isteyin." }, 400);
  }

  const expected = await verificationHash(user.id, user.email, code);
  if (expected !== user.email_verification_code_hash) {
    return json({ success: false, error: "DoÄŸrulama kodu hatalÄ±." }, 400);
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE users SET email_verified_at = ?, email_verification_code_hash = NULL, email_verification_expires_at = NULL, email_verification_sent_at = NULL, updated_at = ? WHERE id = ?"
  ).bind(now, now, user.id).run();
  const freshUser = await getUserById(env, user.id);
  return json({ success: true, user: freshUser ? publicUser(freshUser) : auth.user });
}

async function handleAdminPasswordVerify(request: Request, env: Env): Promise<Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  if (!auth.user.is_admin) {
    return json({ success: false, auth: false, error: "Bu iÅŸlem sadece yÃ¶netici hesabÄ± ile yapÄ±labilir." }, 403);
  }

  const payload = await readJson<{ password?: string }>(request);
  const password = String(payload.password || "");
  if (!isAdminPanelPassword(password)) {
    return json({ success: false, error: "YÃ¶netici ÅŸifresi hatalÄ±." }, 401);
  }
  return json({ success: true, token: randomToken(16) });
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
  const payload = await readJson<{ password?: string }>(request);
  if (!isAdminPanelPassword(String(payload.password || ""))) {
    return json({ success: false, error: "YÃ¶netici ÅŸifresi hatalÄ±." }, 401);
  }

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
  if (!chatId) return json({ success: false, error: "Sohbet kimliÄŸi eksik." }, 400);
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
    return json({ success: false, auth: false, error: "Oturum sÃ¼resi doldu." }, 401);
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
      error: "Cloudflare bot doÄŸrulamasÄ± henÃ¼z yapÄ±landÄ±rÄ±lmadÄ±."
    }, 503);
  }

  const token = String(tokenValue || "").trim();
  if (!token || token.length > 2048) {
    return json({ success: false, error: "Cloudflare bot doÄŸrulamasÄ±nÄ± tamamlayÄ±n." }, 400);
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
    return json({ success: false, error: "Cloudflare bot doÄŸrulamasÄ± ÅŸu an cevap vermiyor." }, 502);
  }

  const result = await response.json() as TurnstileResponse;
  if (!result.success) {
    return json({
      success: false,
      error: "Cloudflare bot doÄŸrulamasÄ± baÅŸarÄ±sÄ±z oldu.",
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
    avatar_data_url: user.avatar_data_url || ""
  };
}

async function requireAdmin(request: Request, env: Env): Promise<AuthContext | Response> {
  const auth = await requireAuth(request, env);
  if (auth instanceof Response) return auth;
  if (!auth.user.is_admin) {
    return json({ success: false, auth: false, error: "Bu iÅŸlem sadece yÃ¶netici hesabÄ± ile yapÄ±labilir." }, 403);
  }
  return auth;
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

function isAdminPanelPassword(password: string): boolean {
  return String(password || "") === ADMIN_PANEL_PASSWORD;
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
  if (avatar.length > AVATAR_DATA_URL_LIMIT) return "Profil fotoÄŸrafÄ± Ã§ok bÃ¼yÃ¼k. Daha kÃ¼Ã§Ã¼k bir gÃ¶rsel seÃ§in.";
  if (!/^data:image\/(?:png|jpeg|jpg|webp);base64,[a-z0-9+/=]+$/i.test(avatar)) {
    return "Profil fotoÄŸrafÄ± PNG, JPG veya WEBP olmalÄ±.";
  }
  return "";
}

async function sendVerificationCode(env: Env, user: UserRow): Promise<{ sent: boolean; configured: boolean; error?: string }> {
  const binding = getEmailBinding(env);
  if (!binding) {
    return { sent: false, configured: false, error: "Cloudflare Email Sending henÃ¼z baÄŸlÄ± deÄŸil." };
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
      subject: "ReylAI doÄŸrulama kodun",
      html: verificationEmailHtml(user, code),
      text: `ReylAI doÄŸrulama kodun: ${code}. Kod ${VERIFY_CODE_TTL_MINUTES} dakika geÃ§erlidir.`
    });
    return { sent: true, configured: true };
  } catch (error) {
    console.error(JSON.stringify({
      level: "error",
      message: "verification email failed",
      detail: error instanceof Error ? error.message : String(error)
    }));
    return { sent: false, configured: true, error: "DoÄŸrulama e-postasÄ± gÃ¶nderilemedi." };
  }
}

function getEmailBinding(env: Env): EmailBinding | null {
  const value = (env as unknown as Record<string, unknown>).EMAIL;
  return isRecord(value) && typeof value.send === "function" ? value as EmailBinding : null;
}

async function verificationHash(userId: string, email: string, code: string): Promise<string> {
  return await sha256Base64Url(`${userId}:${normalizeEmail(email)}:${code}`);
}

function verificationEmailHtml(user: UserRow, code: string): string {
  const name = escapeHtml(user.display_name || "ReylAI kullanÄ±cÄ±sÄ±");
  const spacedCode = code.split("").join(" ");
  return `<!doctype html>
<html lang="tr">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ReylAI doÄŸrulama kodu</title>
  </head>
  <body style="margin:0;background:#080414;color:#f7f2ff;font-family:Inter,Segoe UI,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:radial-gradient(circle at 20% 0%,rgba(102,232,226,.24),transparent 32%),linear-gradient(135deg,#120725,#080414);padding:32px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;border:1px solid rgba(255,255,255,.16);border-radius:28px;background:rgba(24,18,42,.82);box-shadow:0 30px 90px rgba(0,0,0,.38);overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 18px;">
                <div style="font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#61f1e7;font-weight:900;">ReylAI GÃ¼venlik</div>
                <h1 style="margin:10px 0 8px;font-size:28px;line-height:1.1;color:#fff;">E-postanÄ± doÄŸrula</h1>
                <p style="margin:0;color:#d9d2ea;font-size:15px;line-height:1.65;">Merhaba ${name}, hesabÄ±nÄ± gÃ¼vene almak iÃ§in bu kodu ReylAI ayarlarÄ±nda kullan.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 28px;">
                <div style="border:1px solid rgba(97,241,231,.26);border-radius:22px;background:linear-gradient(135deg,rgba(97,241,231,.18),rgba(157,109,255,.16));padding:24px;text-align:center;">
                  <div style="font-size:12px;color:#bdb2d8;font-weight:800;text-transform:uppercase;letter-spacing:.16em;">GÃ¼venlik kodu</div>
                  <div style="margin-top:10px;font-size:36px;letter-spacing:.22em;font-weight:950;color:#ffffff;">${spacedCode}</div>
                  <div style="margin-top:10px;color:#ffd44d;font-size:13px;font-weight:800;">${VERIFY_CODE_TTL_MINUTES} dakika geÃ§erlidir.</div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:10px 28px 30px;color:#a99ec1;font-size:13px;line-height:1.6;">
                Bu isteÄŸi sen yapmadÄ±ysan bu e-postayÄ± yok sayabilirsin. Åifreni kimseyle paylaÅŸma.
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
  if (!EMAIL_RE.test(email) || email.length > 254) return "GeÃ§erli bir e-posta girin.";
  if (password.length < 8 || password.length > 128) return "Åifre 8-128 karakter olmalÄ±.";
  if (!displayName || displayName.length < 2 || displayName.length > 40) return "GÃ¶rÃ¼nen ad 2-40 karakter olmalÄ±.";
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

async function handleConfig(env: Env): Promise<Response> {
  const config = await fetchStaticJson<Record<string, unknown>>(env, "/reylai_config.json");
  return json(config || { folder_ids: {} });
}

async function handleDebugGas(env: Env): Promise<Response> {
  if (!env.GAS_WEB_APP_URL) {
    return json({ error: "GAS_WEB_APP_URL ayarlanmamÄ±ÅŸ" }, 500);
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

  if (!env.MISTRAL_API_KEY) return { error: "MISTRAL_API_KEY yapÄ±landÄ±rÄ±lmamÄ±ÅŸ." };
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
      error: "SeÃ§ili kitap iÃ§in hazÄ±r tarama metni bulunamadÄ±.",
      missing_scan: true
    };
  }

  const contextText = buildContextExcerpt(scanData.pages, prompt);
  if (!contextText) {
    return {
      error: "SeÃ§ili kitap iÃ§in kullanÄ±labilir tarama metni bulunamadÄ±.",
      missing_scan: true
    };
  }

  const requestedPages = extractPageNumbers(prompt);
  const historyContext = buildHistoryContext(payload.chat_history || []);
  let systemMessage = [
    "Sen ReylAI adlÄ± bir yapay zeka asistanÄ±sÄ±n.",
    "MEB ders kitaplarÄ±nÄ± analiz eder, Ã¶ÄŸrencilere ve Ã¶ÄŸretmenlere yardÄ±mcÄ± olursun.",
    "YalnÄ±zca verilen hazÄ±r tarama metnine dayan; kitapta olmayan bilgiyi uydurma.",
    "BaÄŸlam yeterli deÄŸilse bunu aÃ§Ä±kÃ§a sÃ¶yle ve kullanÄ±cÄ±dan sayfa, soru numarasÄ± veya konu adÄ± iste.",
    "YanÄ±tÄ± TÃ¼rkÃ§e, sade ve Ã¶ÄŸrenciye yardÄ±mcÄ± olacak biÃ§imde ver.",
    "Soru Ã§Ã¶zÃ¼yorsan Ã¶nce yÃ¶ntemi, sonra sonucu ver.",
    "MÃ¼mkÃ¼nse kaynak sayfayÄ± [Sayfa X] formatÄ±nda belirt.",
    "Matematiksel ifadeleri gerekiyorsa LaTeX ile yaz."
  ].join("\n");

  if (requestedPages.length) {
    systemMessage += `\n\nKullanÄ±cÄ± Ã¶zellikle ÅŸu sayfa(lar)a odaklanÄ±yor: ${requestedPages.join(", ")}.`;
  }
  if (historyContext) {
    systemMessage += "\n\nÃ–nceki konuÅŸma Ã¶zeti:\n" + historyContext;
  }
  systemMessage += "\n\nKitabÄ±n ilgili bÃ¶lÃ¼mleri:\n\n" + contextText;

  const messages: MistralMessage[] = [
    { role: "system", content: systemMessage },
    {
      role: "user",
      content: `Kitap adÄ±: ${book?.title || book?.name || bookName}\nÄ°stenen sayfalar: ${requestedPages.join(", ") || "belirtilmedi"}\n\nKullanÄ±cÄ± sorusu: ${prompt}`
    }
  ];

  try {
    const mistralResponse = await mistralChat(env, messages, { temperature: 0.2 });
    const result = mistralResponseText(mistralResponse);
    if (!result) return { error: "Mistral boÅŸ yanÄ±t dÃ¶ndÃ¼rdÃ¼." };

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

  return text("PDF bulunamadÄ±", 404);
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
  if (key && !publicBook.cover_url && await staticExists(env, `/reylai_assets/covers/${encodeURIComponent(key)}.jpg`)) {
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
    throw new Error(`Mistral API hatasÄ± (${response.status}): ${snippet || response.statusText}`);
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
      "AÅŸaÄŸÄ±daki ders kitabÄ± sohbeti iÃ§in TÃ¼rkÃ§e, kÄ±sa ve doÄŸal bir baÅŸlÄ±k yaz.",
      "Sadece baÅŸlÄ±ÄŸÄ± dÃ¶ndÃ¼r; tÄ±rnak, aÃ§Ä±klama veya madde iÅŸareti kullanma.",
      "En fazla 6 kelime olsun.",
      "",
      `Kitap: ${bookName}`,
      `KullanÄ±cÄ± sorusu: ${prompt}`,
      `Cevap Ã¶zeti: ${answer.slice(0, 700)}`
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
    const radius = requested.length === 1 && /(civar|yakÄ±n|yaklasik|yaklaÅŸÄ±k)/i.test(prompt) ? 2 : (requested.length === 1 ? 1 : 0);
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
  for (const match of textValue.matchAll(/sayfa\s*(\d{1,4})\s*[-â€“]\s*(\d{1,4})/g)) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    for (let page = Math.min(start, end); page <= Math.max(start, end); page += 1) found.push(page);
  }
  for (const match of textValue.matchAll(/(?:sayfa|sf)\s*(\d{1,4})/g)) found.push(Number(match[1]));
  for (const match of textValue.matchAll(/(\d{1,4})\.?\s*(?:sayfa|sf)\w*/g)) found.push(Number(match[1]));
  return [...new Set(found)].filter((page) => page > 0 && page < 2000);
}

function queryTerms(prompt: string): string[] {
  const stop = new Set(["iÃ§in", "icin", "olan", "bana", "ÅŸunu", "sunu", "bunu", "nedir", "nasÄ±l", "nasil", "sayfa", "soru", "cevap", "lÃ¼tfen", "lutfen"]);
  return normalizeText(prompt)
    .split(/[^a-z0-9Ä±ÄŸÃ¼ÅŸÃ¶Ã§Ä°ÄÃœÅÃ–Ã‡]+/i)
    .map((term) => term.trim())
    .filter((term) => term.length >= 3 && !stop.has(term));
}

function smallTalkResponse(prompt: string): string {
  const clean = normalizeText(prompt);
  if (/^(selam|merhaba|mrb|slm|sa|hey|hi|hello)\b/.test(clean)) {
    return "Merhaba, buradayÄ±m. Kitaptaki bir soru, sayfa veya konuyu yaz; hemen yardÄ±mcÄ± olayÄ±m.";
  }
  if (clean.includes("teÅŸekkÃ¼r") || clean.includes("tesekkur") || clean.includes("saÄŸ ol") || clean.includes("sag ol")) {
    return "Rica ederim. BuradayÄ±m; kitapla ilgili bir soru, sayfa veya konu yazarsan hemen yardÄ±mcÄ± olurum.";
  }
  if (clean.includes("kimsin") || clean.includes("sen nesin") || clean.includes("adÄ±n ne") || clean.includes("adin ne")) {
    return "Ben ReylAI. Ders kitaplarÄ±ndaki sayfa, soru ve konularÄ± hÄ±zlÄ±ca aÃ§Ä±klamak iÃ§in buradayÄ±m.";
  }
  return "";
}

function buildHistoryContext(history: Array<{ role?: string; text?: string }>): string {
  return history.slice(-10).map((item) => {
    const role = item.role === "user" ? "KullanÄ±cÄ±" : "ReylAI";
    const textValue = String(item.text || "").replace(/\s+/g, " ").trim().slice(0, 1800);
    return textValue ? `${role}: ${textValue}` : "";
  }).filter(Boolean).join("\n");
}

function fallbackChatTitle(prompt: string): string {
  return cleanChatTitle(prompt) || "Yeni sohbet";
}

function cleanChatTitle(title: string): string {
  let clean = title.replace(/[`*_>#[\]()"â€œâ€â€˜â€™]+/g, " ").replace(/\s+/g, " ").trim().replace(/[.:-]+$/g, "");
  if (clean.length > 64) clean = clean.slice(0, 61).trimEnd() + "...";
  return clean;
}

function findBook(library: Book[], selectedId: string): Book | undefined {
  return library.find((book) => book.book_id === selectedId) || library.find((book) => book.drive_id === selectedId);
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

