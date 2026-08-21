/**
 * worker.js — webhook Telegram di Cloudflare Workers.
 *
 * Telegram MENDORONG pesan ke sini, jadi tidak ada polling dan tidak ada
 * jadwal. Balasan datang dalam hitungan detik, 24/7, tanpa komputer Anda.
 *
 * Pembagian tugas:
 *   /bantuan /status /laporan  → dijawab Worker langsung (seketika)
 *   /analisa                   → butuh pandas/numpy yang tidak ada di
 *                                Workers, jadi memicu GitHub Actions.
 *                                Balasan "sedang dianalisis" langsung,
 *                                hasilnya menyusul ~40-60 detik.
 *
 * Data dibaca dari branch bot-data lewat raw.githubusercontent.com —
 * satu-satunya sumber kebenaran, sama dengan yang dipakai bot Python.
 *
 * Secrets yang perlu diset (wrangler secret put NAMA):
 *   TELEGRAM_BOT_TOKEN
 *   TELEGRAM_CHAT_ID
 *   GH_TOKEN            PAT dengan scope 'repo'
 *   WEBHOOK_SECRET      string acak, diverifikasi tiap permintaan
 *
 * Variable biasa (wrangler.toml):
 *   GH_REPO             contoh: thesarpandjiux/xausignal
 */

const RAW = (repo, file) =>
  `https://raw.githubusercontent.com/${repo}/bot-data/${file}`;

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("XAUUSD Signal Bot webhook", { status: 200 });
    }

    // Tanpa ini, siapa pun yang menemukan URL Worker bisa mengirim perintah
    // palsu. Telegram menyertakan header ini bila setWebhook dipanggil
    // dengan secret_token.
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");           // jangan buat Telegram mengulang
    }

    const msg = update.message || update.edited_message;
    if (!msg?.text) return new Response("ok");

    const chatId = String(msg.chat?.id ?? "");
    if (chatId !== env.TELEGRAM_CHAT_ID) {
      return new Response("ok");           // abaikan chat asing, diam-diam
    }

    const cmd = msg.text.trim().toLowerCase().split(/\s+/)[0].split("@")[0];

    try {
      await route(cmd, chatId, env);
    } catch (e) {
      await send(env, chatId, `❌ Error: <code>${esc(String(e).slice(0, 300))}</code>`);
    }
    return new Response("ok");
  },
};

async function route(cmd, chatId, env) {
  switch (cmd) {
    case "/analisa":
      return analisa(chatId, env);
    case "/status":
      return send(env, chatId, await status(env));
    case "/laporan":
      return send(env, chatId, await laporan(env));
    case "/bantuan":
    case "/start":
    case "/help":
      return send(env, chatId, bantuan());
    default:
      if (cmd.startsWith("/")) {
        return send(env, chatId, "Perintah tidak dikenal. Ketik /bantuan.");
      }
  }
}

// ─────────────────────────────── Perintah ───────────────────────────────────

function bantuan() {
  return [
    "<b>Perintah tersedia</b>",
    "",
    "/analisa — kondisi pasar sekarang, skor, dan syarat mana yang gagal",
    "/status — bot hidup? sinyal terakhir kapan?",
    "/laporan — statistik hasil sinyal",
    "/bantuan — pesan ini",
    "",
    "<i>Sinyal otomatis tetap dikirim tiap jam tanpa diminta.</i>",
    "<i>/analisa butuh perhitungan berat, jadi hasilnya menyusul ~1 menit.</i>",
  ].join("\n");
}

async function analisa(chatId, env) {
  // Worker tidak bisa menjalankan pandas/numpy — picu GitHub Actions.
  const r = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "xausignal-worker",
      },
      body: JSON.stringify({ event_type: "analisa" }),
    }
  );

  if (!r.ok) {
    const t = await r.text();
    return send(env, chatId,
      `❌ Gagal memicu analisis (HTTP ${r.status})\n<code>${esc(t.slice(0, 200))}</code>`);
  }
  return send(env, chatId,
    "⏳ <b>Menganalisis…</b>\n<i>Hasil menyusul dalam ~1 menit.</i>");
}

async function status(env) {
  const csv = await raw(env, "signals.csv");
  const L = ["<b>Status bot</b>", ""];

  if (!csv) {
    L.push("Riwayat : belum ada (bot belum pernah jalan)");
    return L.join("\n");
  }

  const rows = parseCsv(csv);
  const terkirim = rows.filter((r) => String(r.sent) === "True");
  const last = terkirim[terkirim.length - 1];

  L.push(`Total evaluasi  : ${rows.length}`);
  L.push(`Sinyal terkirim : ${terkirim.length}`);

  if (last) {
    const t = new Date(last.time);
    const jam = (Date.now() - t.getTime()) / 3.6e6;
    L.push("");
    L.push(`Terakhir : ${last.direction} grade ${last.grade}`);
    L.push(`           $${Number(last.entry).toFixed(2)} · ${wib(t)} WIB`);
    L.push(`           ${jam.toFixed(0)} jam lalu`);
  } else {
    L.push("");
    L.push("Belum ada sinyal terkirim.");
  }

  const ev = rows[rows.length - 1];
  if (ev) {
    const t = new Date(ev.time);
    const menit = (Date.now() - t.getTime()) / 6e4;
    L.push("");
    L.push(`Evaluasi terakhir : ${menit.toFixed(0)} menit lalu`);
    if (menit > 150) L.push("⚠️ <i>Lebih dari 2 jam — cek tab Actions.</i>");
  }
  return L.join("\n");
}

async function laporan(env) {
  const csv = await raw(env, "journal.csv");
  if (!csv) {
    return "Jurnal masih kosong.\n<i>Terisi setelah ada sinyal yang selesai.</i>";
  }

  const rows = parseCsv(csv);
  const selesai = rows.filter((r) => r.outcome === "WIN" || r.outcome === "LOSS");
  if (!selesai.length) {
    const pend = rows.filter((r) => r.outcome === "PENDING").length;
    return `Belum ada sinyal yang selesai (${pend} masih berjalan).`;
  }

  const menang = selesai.filter((r) => r.outcome === "WIN");
  const wr = (menang.length / selesai.length) * 100;
  const rSum = selesai.reduce((a, r) => a + (Number(r.r_result) || 0), 0);
  const rrAvg =
    selesai.reduce((a, r) => a + (Number(r.rr1) || 1.5), 0) / selesai.length;
  const impas = 100 / (1 + rrAvg);

  const L = [
    "<b>Hasil nyata sinyal</b>",
    "",
    `Selesai    : ${selesai.length}`,
    `Win rate   : ${wr.toFixed(1)}%`,
    `Ekspektasi : ${(rSum / selesai.length >= 0 ? "+" : "")}${(rSum / selesai.length).toFixed(3)}R`,
    `Total      : ${rSum >= 0 ? "+" : ""}${rSum.toFixed(1)}R`,
    "",
    `R:R rata-rata ${rrAvg.toFixed(2)} → butuh ${impas.toFixed(1)}% untuk impas`,
  ];

  const selisih = wr - impas;
  L.push(
    selisih > 5 ? "✅ di atas titik impas"
      : selisih > -5 ? "⚠️ tipis, belum meyakinkan"
        : "❌ di bawah titik impas"
  );

  if (selesai.length < 30) {
    L.push("");
    L.push(`<i>Baru ${selesai.length} sinyal. Di bawah 30, angka ini masih`);
    L.push(`kebisingan — beruntun menang itu wajar bahkan pada sistem buruk.</i>`);
  }
  return L.join("\n");
}

// ─────────────────────────────── Pembantu ───────────────────────────────────

async function raw(env, file) {
  const r = await fetch(RAW(env.GH_REPO, file), {
    headers: { "User-Agent": "xausignal-worker" },
    cf: { cacheTtl: 30 },
  });
  return r.ok ? r.text() : null;
}

/** Parser CSV sederhana. Data kita tidak punya koma di dalam nilai. */
function parseCsv(text) {
  const lines = text.trim().split("\n").filter(Boolean);
  if (lines.length < 2) return [];
  const head = lines[0].split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    return Object.fromEntries(head.map((h, i) => [h, (cells[i] ?? "").trim()]));
  });
}

function wib(d) {
  return new Intl.DateTimeFormat("id-ID", {
    timeZone: "Asia/Jakarta",
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(d);
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function send(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text: text.slice(0, 4096),
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}
