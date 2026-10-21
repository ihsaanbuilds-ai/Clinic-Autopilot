const { 
    default: makeWASocket, 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestBaileysVersion,
    Browsers,
    delay,
    downloadMediaMessage
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const express = require('express');

const app = express();
app.use(express.json());

const WEBHOOK_URL = process.env.WEBHOOK_URL || 'http://127.0.0.1:8001/api/doctor/whatsapp-webhook';
const VOICE_WEBHOOK_URL = process.env.VOICE_WEBHOOK_URL || 'http://127.0.0.1:8001/api/doctor/whatsapp-voice-webhook';
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || 'prod_shared_secret_clinic_autopilot_2026';
const PORT = 3000;

// ==========================================
// 🛡️ STRICT TESTING WHITELIST
// Only this exact number will trigger bot replies or receive outbound messages.
// ==========================================
const ALLOWED_TEST_PHONE = '919567826305';

let globalSock = null;
let isConnected = false;

async function startBot() {
    try {
        const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
        const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1015901307] }));

        console.log(`\nInitializing WhatsApp Bridge with Baileys v${version.join('.')}...`);

        const sock = makeWASocket({
            version,
            auth: state,
            logger: pino({ level: 'silent' }),
            printQRInTerminal: false,
            browser: Browsers.ubuntu('Chrome'),
            syncFullHistory: false,
            shouldIgnoreJid: (jid) => jid.includes('@broadcast') || jid.includes('status@broadcast'),
            markOnlineOnConnect: true,
            connectTimeoutMs: 60000,
            defaultQueryTimeoutMs: 60000,
            keepAliveIntervalMs: 25000,
            generateHighQualityLinkPreview: false
        });

        globalSock = sock;
        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;
            if (qr) {
                console.log('\n📲 Scan this QR code with WhatsApp:\n');
                qrcode.generate(qr, { small: true });
            }
            if (connection === 'close') {
                isConnected = false;
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
                console.log(`⚠️ Connection dropped. Auto-reconnecting: ${shouldReconnect}`);
                if (shouldReconnect) {
                    setTimeout(startBot, 3000);
                } else {
                    console.log('❌ Session invalidated.');
                }
            } else if (connection === 'open') {
                isConnected = true;
                const rawId = sock.user?.id || '';
                const cleanOwnerPhone = rawId.split(':')[0].replace(/\D/g, '');
                console.log('\n========================================');
                console.log('✅ WhatsApp Bridge Connected & Live!');
                console.log(`👤 Bridge Owner Number: +${cleanOwnerPhone}`);
                console.log(`🔒 Test Mode Restricted Strictly To: +${ALLOWED_TEST_PHONE}`);
                console.log('========================================\n');
                try {
                    await sock.sendPresenceUpdate('available');
                } catch (e) {}
            }
        });

        sock.ev.on('messages.upsert', async (m) => {
            if (!m.messages || m.messages.length === 0) return;
            const msg = m.messages[0];

            if (msg.key.fromMe || msg.key.remoteJid === 'status@broadcast') return;

            const remoteJid = msg.key.remoteJid;
            
            // Ignore group chats entirely
            if (remoteJid.endsWith('@g.us')) return;

            let rawPhone = remoteJid.split('@')[0];
            if (msg.key.participant) {
                rawPhone = msg.key.participant.split('@')[0];
            }
            const cleanPhone = rawPhone.replace(/\D/g, '');

            // 🛡️ CRITICAL CHECK: Ignore ANY phone number not in your test whitelist
            if (cleanPhone !== ALLOWED_TEST_PHONE) {
                // Silently skip non-test contacts so they never get an AI reply
                return;
            }

            
            // Check for WhatsApp voice note / audio message
            if (msg.message?.audioMessage) {
                console.log(`🎙️ Received audio voice note from ${pushName} (+${cleanPhone})`);
                try {
                    const audioBuffer = await downloadMediaMessage(
                        msg,
                        "buffer",
                        {},
                        { logger: pino({ level: "silent" }), reuploadRequest: sock.updateMediaMessage }
                    );

                    const voicePayload = {
                        phone: cleanPhone,
                        name: pushName,
                        audio_base64: audioBuffer.toString("base64"),
                        mime_type: msg.message.audioMessage.mimetype || "audio/ogg"
                    };

                    const res = await axios.post(VOICE_WEBHOOK_URL, voicePayload, {
                        headers: { "Content-Type": "application/json", "X-Webhook-Secret": WEBHOOK_SECRET },
                        timeout: 45000
                    });

                    const replyText = res.data?.reply || "";
                    if (replyText) {
                        await sock.sendMessage(remoteJid, { text: replyText });
                        console.log(`📤 Replied to voice note from ${pushName}: "${replyText.substring(0, 50).replace(/\n/g, " ")}..."`);
                    }
                } catch (audioErr) {
                    console.error("❌ Error processing audio note:", audioErr.message);
                }
                return;
            }

            const text = msg.message?.conversation || 
                         msg.message?.extendedTextMessage?.text || 
                         msg.message?.imageMessage?.caption || '';
            const pushName = msg.pushName || 'Friend';

            if (!text.trim()) return;

            console.log(`📩 Whitelisted Inbound from ${pushName} (+${cleanPhone}): "${text}"`);

            try {
                const payload = {
                    phone: cleanPhone,
                    name: pushName,
                    message: text.trim()
                };

                const res = await axios.post(WEBHOOK_URL, payload, {
                    headers: { 'Content-Type': 'application/json', 'X-Webhook-Secret': WEBHOOK_SECRET },
                    timeout: 30000
                });

                let replyText = '';
                if (res.data && res.data.reply) {
                    replyText = res.data.reply;
                } else if (typeof res.data === 'string') {
                    replyText = res.data;
                }

                if (replyText) {
                    await sock.sendMessage(remoteJid, { text: replyText });
                    console.log(`📤 Replied to test user ${pushName}: "${replyText.substring(0, 45).replace(/\n/g, ' ')}..."`);
                }
            } catch (err) {
                console.error('❌ Webhook error:', err.message);
            }
        });

    } catch (e) {
        console.error('❌ Fatal bridge startup error:', e);
    }
}

const withTimeout = (promise, ms) =>
    Promise.race([
        promise,
        new Promise((_, reject) => setTimeout(() => reject(new Error('Query timeout')), ms))
    ]);

// Outbound Dispatcher restricted to test phone
app.post('/send', async (req, res) => {
    const { phone, message } = req.body;
    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'Missing phone or message' });
    }

    let cleanPhone = String(phone).replace(/\D/g, '');
    if (cleanPhone.length === 10) {
        cleanPhone = '91' + cleanPhone;
    }

    // 🛡️ CRITICAL OUTBOUND CHECK
    if (cleanPhone !== ALLOWED_TEST_PHONE) {
        console.log(`🚫 Blocked outbound attempt to non-whitelisted number: +${cleanPhone}`);
        return res.status(403).json({ success: false, error: 'Number not whitelisted for testing' });
    }

    if (!globalSock || !isConnected) {
        return res.status(503).json({ success: false, error: 'Bridge socket not yet connected' });
    }

    try {
        let targetJid = `${cleanPhone}@s.whatsapp.net`;
        try {
            const results = await withTimeout(globalSock.onWhatsApp(targetJid), 3000);
            if (results && results.length > 0 && results[0].exists) {
                targetJid = results[0].jid;
            }
        } catch (e) {}

        await delay(200);
        await globalSock.sendMessage(targetJid, { text: message });
        console.log(`📤 Outbound sent to +${cleanPhone}`);
        return res.json({ success: true, target: targetJid });

    } catch (err) {
        return res.status(500).json({ success: false, error: err?.message || String(err) });
    }
});

app.listen(PORT, () => {
    console.log(`🚀 Bridge listening on port ${PORT}`);
});

startBot();
