/**
 * Free WhatsApp Channel bridge (best-effort, unofficial)
 * ---------------------------------------------------------
 * Uses Baileys (open-source, free, connects like WhatsApp Web) to post
 * text messages to your WhatsApp Channel. Posting to Channels is NOT
 * officially supported by Baileys — this works on many setups but isn't
 * guaranteed. If it silently fails, see the README in this folder for a
 * fallback (manual copy-paste or a paid provider).
 *
 * Run once to link:  npm install && npm start   -> scan the QR code
 * Runs alongside your Flask backend on its own port (default 4001).
 */

const express = require("express");
const P = require("pino");
const qrcode = require("qrcode-terminal");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
} = require("baileys");

const PORT = process.env.BRIDGE_PORT || 4001;
const BRIDGE_TOKEN = process.env.WHATSAPP_API_TOKEN || "change-me-shared-secret";

let sock = null;

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState("auth");
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger: P({ level: "silent" }),
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      console.log("\nScan this QR code with WhatsApp (Linked Devices > Link a device):\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      console.log("✅ WhatsApp linked. Bridge is ready to send.");
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      console.log("Connection closed.", loggedOut ? "Logged out — delete the auth/ folder and re-scan." : "Reconnecting...");
      if (!loggedOut) startSocket();
    }
  });
}

startSocket();

const app = express();
app.use(express.json());

function checkAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.replace(/^Bearer\s+/i, "");
  if (token !== BRIDGE_TOKEN) return res.status(401).json({ error: "Unauthorized" });
  next();
}

// Same shape as the generic webhook the Flask backend already calls:
// POST { "to": "<channel-jid>@newsletter", "body": "<text>" }
app.post("/send", checkAuth, async (req, res) => {
  const { to, body } = req.body || {};
  if (!to || !body) return res.status(422).json({ error: "Missing 'to' or 'body'." });
  if (!sock) return res.status(503).json({ error: "WhatsApp not connected yet." });

  try {
    await sock.sendMessage(to, { text: body });
    return res.json({ message: "Sent (best-effort)." });
  } catch (e) {
    console.error("Send to channel failed:", e);
    return res.status(500).json({ error: String(e) });
  }
});

// One-time helper: turn a channel invite link's code into its JID.
// Visit: http://localhost:4001/resolve-channel?code=XXXXXXXXXXXXXXXXXXXX
// (the code is the part after https://whatsapp.com/channel/ in your invite link)
app.get("/resolve-channel", checkAuth, async (req, res) => {
  const { code } = req.query;
  if (!code) return res.status(422).json({ error: "Missing ?code=" });
  if (!sock) return res.status(503).json({ error: "WhatsApp not connected yet." });
  try {
    const meta = await sock.newsletterMetadata("invite", code);
    return res.json(meta);
  } catch (e) {
    console.error("Resolve channel failed:", e);
    return res.status(500).json({ error: String(e) });
  }
});

app.listen(PORT, () => console.log(`Bridge HTTP API listening on http://localhost:${PORT}`));
