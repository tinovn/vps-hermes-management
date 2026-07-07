/**
 * Hermes WhatsApp pairing sidecar — dashboard QR login helper.
 *
 * The Hermes WhatsApp bridge (scripts/whatsapp-bridge/bridge.js) only renders
 * the pairing QR as ASCII art to stdout, and in its pair-only mode it opens no
 * HTTP server at all — so a web dashboard cannot show a scannable QR the way the
 * Zalo sidecar does. This tiny sidecar fills that gap: it drives the SAME
 * Baileys multi-file auth state the gateway bridge uses, captures the RAW QR
 * string, and exposes it over a loopback HTTP API so the Management API can
 * render it as a PNG for the Tino dashboard.
 *
 * It is copied into the bridge directory at spawn time so that a bare
 * `import '@whiskeysockets/baileys'` resolves against the bridge's own
 * node_modules (ESM walks up node_modules from the importing file) — no second
 * heavy Baileys install.
 *
 * Handover: once the phone scans, Baileys writes creds.json into the shared
 * session dir and we exit (mirroring the bridge's pair-only mode). The gateway
 * bridge then reconnects with those creds — a single WhatsApp socket at a time,
 * so no stream:conflict.
 *
 * CLI: node whatsapp_pair.mjs --session <dir> --port <p> [--pidfile <path>]
 *
 *   GET  /health  -> { status, qr, paired, error }
 *   POST /logout  -> clear session dir, exit (unlink creds → forces re-pair)
 *   POST /shutdown-> exit without touching creds (free the socket for gateway)
 */

import { makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } from '@whiskeysockets/baileys';
import express from 'express';
import pino from 'pino';
import { rmSync, existsSync, writeFileSync, unlinkSync } from 'fs';
import path from 'path';

const args = process.argv.slice(2);
function getArg(name, fallback) {
  const i = args.indexOf(`--${name}`);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
}

const SESSION_DIR = getArg('session', path.join(process.env.HOME || '/root', '.hermes', 'platforms', 'whatsapp', 'session'));
const PORT = parseInt(getArg('port', '3999'), 10);
const PIDFILE = getArg('pidfile', '');

const logger = pino({ level: process.env.WHATSAPP_DEBUG ? 'debug' : 'silent' });

let latestQR = null;         // raw QR payload string (render to image on the client)
let connectionState = 'connecting'; // connecting | pending | connected | disconnected | error
let lastError = null;
let paired = false;
let sock = null;

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ['Hermes Agent', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      connectionState = 'pending';
    }

    if (connection === 'open') {
      connectionState = 'connected';
      paired = true;
      latestQR = null;
      // Pairing done. Give Baileys a moment to flush creds, then exit so the
      // gateway bridge can take over the (now free) WhatsApp session.
      setTimeout(() => cleanupAndExit(0), 2500);
    } else if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (paired) return; // intentional close after pairing — ignore
      if (code === 401) {
        // Logged out / creds invalid — clear so a fresh QR is issued.
        connectionState = 'disconnected';
      } else {
        // 515 (restart required, common) and transient drops: reconnect.
        setTimeout(() => startSocket().catch(onFatal), code === 515 ? 1000 : 3000);
      }
    }
  });
}

function onFatal(err) {
  lastError = String(err?.message || err);
  connectionState = 'error';
}

function cleanupAndExit(code) {
  try { if (PIDFILE && existsSync(PIDFILE)) unlinkSync(PIDFILE); } catch { /* ignore */ }
  process.exit(code);
}

// ── HTTP API (loopback only) ────────────────────────────────────────────────
const app = express();

app.get('/health', (_req, res) => {
  res.json({ status: connectionState, qr: latestQR, paired, error: lastError });
});

app.post('/logout', async (_req, res) => {
  try { await sock?.logout(); } catch { /* socket may already be gone */ }
  try { rmSync(SESSION_DIR, { recursive: true, force: true }); } catch { /* ignore */ }
  res.json({ ok: true });
  setTimeout(() => cleanupAndExit(0), 200);
});

app.post('/shutdown', (_req, res) => {
  // Free the socket without deleting creds (used right before the gateway
  // bridge takes over a freshly paired session).
  res.json({ ok: true });
  setTimeout(() => cleanupAndExit(0), 200);
});

app.listen(PORT, '127.0.0.1', () => {
  if (PIDFILE) {
    try { writeFileSync(PIDFILE, String(process.pid)); } catch { /* ignore */ }
  }
  startSocket().catch(onFatal);
});
