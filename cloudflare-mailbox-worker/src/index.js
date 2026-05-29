const MESSAGE_PREFIX = "message:";
const DEFAULT_RETENTION_DAYS = 14;
const DEFAULT_LIST_LIMIT = 30;

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
  const listed = await env.MAILBOX.list({ prefix: MESSAGE_PREFIX, limit: 1000 });
  const messages = (
    await Promise.all(
      listed.keys.map(async (key) => {
        if (key.metadata) {
          return key.metadata;
        }
        const value = await env.MAILBOX.get(key.name);
        return value ? summaryFor(JSON.parse(value)) : null;
      })
    )
  )
    .filter((metadata) => metadata)
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")))
    .slice(0, limit);
  return json({ messages });
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
  return JSON.parse(value);
}

async function saveMessage(env, record) {
  const retentionDays = Number(env.MAILBOX_RETENTION_DAYS || DEFAULT_RETENTION_DAYS);
  const expirationTtl = Math.max(1, retentionDays) * 24 * 60 * 60;
  await env.MAILBOX.put(MESSAGE_PREFIX + record.id, JSON.stringify(record), {
    expirationTtl,
    metadata: summaryFor(record),
  });
}

function buildEmailRecord(message, raw) {
  const headers = headersToObject(message.headers);
  const createdAt = new Date().toISOString();
  const headerFrom = headers.from || "";
  const headerTo = headers.to || "";
  return {
    id: crypto.randomUUID(),
    createdAt,
    subject: headers.subject || "",
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
  return {
    id: record.id,
    createdAt: record.createdAt,
    subject: record.subject || "",
    from: record.from || { address: "" },
    to: record.to || "",
    seen: Boolean(record.seen),
    links: Array.isArray(record.links) ? record.links.slice(0, 10) : [],
    forward: record.forward || null,
  };
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
