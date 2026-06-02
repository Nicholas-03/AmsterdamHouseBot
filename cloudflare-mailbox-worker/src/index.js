const MESSAGE_PREFIX = "message:";
const MESSAGE_INDEX_KEY = "message:index";
const DEFAULT_RETENTION_DAYS = 14;
const DEFAULT_LIST_LIMIT = 30;
const DEFAULT_INDEX_LIMIT = 200;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({ ok: true });
    }

    const auth = authorize(request, env);
    if (auth) {
      return auth;
    }

    if (request.method === "GET" && url.pathname === "/messages") {
      return listMessages(env, url);
    }

    if (request.method === "POST" && url.pathname === "/messages/rebuild-index") {
      return rebuildMessageIndex(env);
    }

    const messageMatch = url.pathname.match(/^\/messages\/([^/]+)$/);
    if (request.method === "GET" && messageMatch) {
      return getMessage(env, messageMatch[1]);
    }

    const seenMatch = url.pathname.match(/^\/messages\/([^/]+)\/seen$/);
    if (request.method === "POST" && seenMatch) {
      return markSeen(env, seenMatch[1]);
    }

    if (request.method === "POST" && url.pathname === "/test-message") {
      return createTestMessage(request, env);
    }

    return json({ error: "not_found" }, 404);
  },

  async email(message, env, ctx) {
    const raw = await new Response(message.raw).text();
    const record = buildEmailRecord(message, raw);
    record.forward = {
      to: env.FORWARD_TO || "",
      status: env.FORWARD_TO ? "pending" : "not_configured",
      updatedAt: record.createdAt,
    };
    await saveMessage(env, record);
    if (env.FORWARD_TO) {
      try {
        await message.forward(env.FORWARD_TO);
        record.forward.status = "sent";
      } catch (error) {
        record.forward.status = "failed";
        record.forward.error = error instanceof Error ? error.message : String(error);
      }
      record.forward.updatedAt = new Date().toISOString();
      ctx.waitUntil(saveMessage(env, record));
    }
  },
};

async function listMessages(env, url) {
  const limit = clampLimit(url.searchParams.get("limit"));
  const messages = (await readMessageIndex(env))
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")))
    .slice(0, limit);
  return json({ messages });
}

async function rebuildMessageIndex(env) {
  let listed;
  try {
    listed = await env.MAILBOX.list({ prefix: MESSAGE_PREFIX, limit: 1000 });
  } catch (error) {
    return json(
      {
        error: "kv_list_failed",
        detail: error instanceof Error ? error.message : String(error),
      },
      503
    );
  }

  const results = await Promise.all(listed.keys.map((key) => readSummaryForKey(env, key)));
  const skipped = results.filter((result) => result.skipped).length;
  const messages = results
    .map((result) => result.summary)
    .filter((summary) => summary)
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")))
    .slice(0, indexLimit(env));

  await env.MAILBOX.put(
    MESSAGE_INDEX_KEY,
    JSON.stringify({ updatedAt: new Date().toISOString(), messages })
  );
  return json({ ok: true, indexed: messages.length, skipped });
}

async function getMessage(env, id) {
  const record = await readRecord(env, id);
  if (!record) {
    return json({ error: "not_found" }, 404);
  }
  return json(record);
}

async function markSeen(env, id) {
  const record = await readRecord(env, id);
  if (!record) {
    return json({ error: "not_found" }, 404);
  }
  record.seen = true;
  await saveMessage(env, record);
  return json({ ok: true });
}

async function createTestMessage(request, env) {
  const payload = await request.json().catch(() => ({}));
  const now = new Date().toISOString();
  const from = payload.from || "test@example.com";
  const to = payload.to || env.MAILBOX_ADDRESS || "housing@example.com";
  const subject = payload.subject || "Mailbox test";
  const body = payload.body || "This is a Cloudflare mailbox test message.";
  const raw = [
    `From: ${from}`,
    `To: ${to}`,
    `Subject: ${subject}`,
    `Date: ${now}`,
    "Content-Type: text/plain; charset=utf-8",
    "",
    body,
  ].join("\r\n");
  const record = {
    id: crypto.randomUUID(),
    createdAt: now,
    subject,
    from: { address: from },
    to,
    seen: false,
    raw,
    text: body,
    html: [],
    links: extractLinks(raw),
    headers: {},
  };
  await saveMessage(env, record);
  return json(summaryFor(record), 201);
}

async function readRecord(env, id) {
  const value = await env.MAILBOX.get(MESSAGE_PREFIX + id);
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

async function saveMessage(env, record) {
  const retentionDays = Number(env.MAILBOX_RETENTION_DAYS || DEFAULT_RETENTION_DAYS);
  const expirationTtl = Math.max(1, retentionDays) * 24 * 60 * 60;
  await env.MAILBOX.put(MESSAGE_PREFIX + record.id, JSON.stringify(record), {
    expirationTtl,
    metadata: summaryFor(record),
  });
  await updateMessageIndex(env, summaryFor(record));
}

function buildEmailRecord(message, raw) {
  const headers = headersToObject(message.headers);
  const createdAt = new Date().toISOString();
  const headerFrom = headers.from || "";
  const headerTo = headers.to || "";
  return {
    id: crypto.randomUUID(),
    createdAt,
    subject: headers.subject || rawHeader(raw, "subject") || "",
    from: { address: headerFrom || message.from || "" },
    envelopeFrom: message.from || "",
    to: headerTo || message.to || "",
    envelopeTo: message.to || "",
    seen: false,
    raw,
    text: raw,
    html: [raw],
    links: extractLinks(raw),
    headers,
  };
}

function summaryFor(record) {
  if (!record || typeof record !== "object") {
    return null;
  }
  return {
    id: record.id || "",
    createdAt: record.createdAt,
    subject: record.subject || rawHeader(record.raw, "subject") || "",
    from: record.from || { address: "" },
    to: record.to || "",
    seen: Boolean(record.seen),
    links: Array.isArray(record.links) ? record.links.slice(0, 10) : [],
    forward: record.forward || null,
  };
}

function rawHeader(raw, name) {
  if (!raw || !name) {
    return "";
  }
  const target = String(name).toLowerCase();
  const headerBlock = String(raw).split(/\r?\n\r?\n/, 1)[0] || "";
  let current = "";
  let value = "";
  for (const line of headerBlock.split(/\r?\n/)) {
    if (/^\s/.test(line)) {
      if (current === target) {
        value += " " + line.trim();
      }
      continue;
    }
    const match = line.match(/^([^:]+):(.*)$/);
    if (!match) {
      current = "";
      continue;
    }
    current = match[1].trim().toLowerCase();
    if (current === target) {
      value = match[2].trim();
    }
  }
  return value;
}

async function readMessageIndex(env) {
  try {
    const value = await env.MAILBOX.get(MESSAGE_INDEX_KEY);
    if (!value) {
      return [];
    }
    const parsed = JSON.parse(value);
    const rawMessages = Array.isArray(parsed) ? parsed : parsed.messages;
    if (!Array.isArray(rawMessages)) {
      return [];
    }
    return pruneExpiredSummaries(env, rawMessages.map(summaryFor).filter((summary) => summary));
  } catch {
    return [];
  }
}

async function readSummaryForKey(env, key) {
  try {
    const fallbackId = String(key.name || "").startsWith(MESSAGE_PREFIX)
      ? String(key.name).slice(MESSAGE_PREFIX.length)
      : String(key.name || "");
    if (key.metadata && typeof key.metadata === "object") {
      const summary = summaryFor({ id: fallbackId, ...key.metadata });
      if (summary) {
        return { summary, skipped: false };
      }
    }
    const value = await env.MAILBOX.get(key.name);
    if (!value) {
      return { summary: null, skipped: true };
    }
    const record = JSON.parse(value);
    const summary = summaryFor({ id: fallbackId, ...record });
    return summary ? { summary, skipped: false } : { summary: null, skipped: true };
  } catch {
    return { summary: null, skipped: true };
  }
}

async function updateMessageIndex(env, summary) {
  if (!summary || !summary.id) {
    return;
  }
  const existing = await readMessageIndex(env);
  const messages = [summary, ...existing.filter((message) => message.id !== summary.id)]
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")))
    .slice(0, indexLimit(env));
  await env.MAILBOX.put(
    MESSAGE_INDEX_KEY,
    JSON.stringify({ updatedAt: new Date().toISOString(), messages })
  );
}

function pruneExpiredSummaries(env, messages) {
  const retentionDays = Number(env.MAILBOX_RETENTION_DAYS || DEFAULT_RETENTION_DAYS);
  const cutoff = Date.now() - Math.max(1, retentionDays) * 24 * 60 * 60 * 1000;
  return messages.filter((message) => {
    const createdAt = Date.parse(message.createdAt || "");
    return Number.isFinite(createdAt) && createdAt >= cutoff;
  });
}

function indexLimit(env) {
  const parsed = Number(env.MAILBOX_INDEX_LIMIT || DEFAULT_INDEX_LIMIT);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_INDEX_LIMIT;
  }
  return Math.max(DEFAULT_LIST_LIMIT, Math.min(1000, Math.trunc(parsed)));
}

function headersToObject(headers) {
  const result = {};
  for (const [key, value] of headers.entries()) {
    result[key.toLowerCase()] = value;
  }
  return result;
}

function extractLinks(text) {
  const links = [];
  const pattern = /https?:\/\/[^\s<>"')]+/gi;
  for (const match of text.matchAll(pattern)) {
    links.push(match[0].replace(/[.,;]+$/, ""));
  }
  return [...new Set(links)];
}

function authorize(request, env) {
  if (!env.API_TOKEN) {
    return json({ error: "missing_api_token" }, 500);
  }
  const expected = `Bearer ${env.API_TOKEN}`;
  if (request.headers.get("Authorization") !== expected) {
    return json({ error: "unauthorized" }, 401);
  }
  return null;
}

function clampLimit(value) {
  const parsed = Number(value || DEFAULT_LIST_LIMIT);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_LIST_LIMIT;
  }
  return Math.max(1, Math.min(100, Math.trunc(parsed)));
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
