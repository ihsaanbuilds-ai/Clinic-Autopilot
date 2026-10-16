const { 
    default: makeWASocket, 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestBaileysVersion,
    Browsers,
    delay
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const express = require('express');

const app = express();
app.use(express.json());

const WEBHOOK_URL = 'https://clinic-autopilot.onrender.com/api/doctor/whatsapp-webhook';
const PORT = 3000;

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
                console.log(`⚠️ Connection dropped (code ${statusCode}). Auto-reconnecting: ${shouldReconnect}`);
                if (shouldReconnect) {
                    setTimeout(startBot, 3000);
                } else {
                    console.log('❌ Session invalidated. Run "rm -rf auth_info_baileys" and scan again.');
                }
            } else if (connection === 'open') {
                isConnected = true;
                const rawId = sock.user?.id || '';
                const cleanOwnerPhone = rawId.split(':')[0].replace(/\D/g, '');
                console.log('\n========================================');
                console.log('✅ WhatsApp Bridge Connected & Live!');
                console.log(`👤 Bridge Owner Number: +${cleanOwnerPhone}`);
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
            let rawPhone = remoteJid.split('@')[0];
            if (msg.key.participant) {
                rawPhone = msg.key.participant.split('@')[0];
            }
            const cleanPhone = rawPhone.replace(/\D/g, '');

            const text = msg.message?.conversation || 
                         msg.message?.extendedTextMessage?.text || 
                         msg.message?.imageMessage?.caption || '';
            const pushName = msg.pushName || 'Patient';

            if (!text.trim()) return;

            console.log(`📩 Inbound from ${pushName} (+${cleanPhone}): "${text}"`);

            try {
                const payload = {
                    phone: cleanPhone,
                    name: pushName,
                    message: text.trim()
                };

                const res = await axios.post(WEBHOOK_URL, payload, {
                    headers: { 'Content-Type': 'application/json' },
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
                    console.log(`📤 Replied to ${pushName}: "${replyText.substring(0, 45).replace(/\n/g, ' ')}..."`);
                }
            } catch (err) {
                console.error('❌ Webhook error:', err.message);
            }
        });

    } catch (e) {
        console.error('❌ Fatal bridge startup error:', e);
    }
}

// Helper: timeout wrapper for promises
const withTimeout = (promise, ms) =>
    Promise.race([
        promise,
        new Promise((_, reject) => setTimeout(() => reject(new Error('Query timeout')), ms))
    ]);

// Rock-Solid Outbound Dispatcher
app.post('/send', async (req, res) => {
    const { phone, message } = req.body;
    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'Missing phone or message' });
    }

    if (!globalSock || !isConnected) {
        return res.status(503).json({ success: false, error: 'Bridge socket not yet connected' });
    }

    try {
        let cleanPhone = String(phone).replace(/\D/g, '');
        if (cleanPhone.length === 10) {
            cleanPhone = '91' + cleanPhone;
        }

        const ownerId = globalSock.user?.id || '';
        const ownerPhone = ownerId.split(':')[0].replace(/\D/g, '');

        let targetJid = `${cleanPhone}@s.whatsapp.net`;

        if (ownerPhone && cleanPhone === ownerPhone) {
            targetJid = globalSock.user.id;
            console.log(`ℹ️ Outbound routed as Self-Chat note to host device: ${targetJid}`);
        } else {
            try {
                // Verify existence with a fast 3-second timeout
                const results = await withTimeout(globalSock.onWhatsApp(targetJid), 3000);
                if (results && results.length > 0 && results[0].exists) {
                    targetJid = results[0].jid;
                }
            } catch (e) {
                // If timeout or failure, proceed with standard jid
            }
        }

        await delay(200);
        await globalSock.sendMessage(targetJid, { text: message });
        console.log(`📤 Outbound successfully sent to ${targetJid}: "${message.substring(0, 40).replace(/\n/g, ' ')}..."`);
        return res.json({ success: true, target: targetJid });

    } catch (err) {
        console.error('❌ Outbound delivery error:', err?.message || err);
        return res.status(500).json({ success: false, error: err?.message || String(err) });
    }
});

app.listen(PORT, () => {
    console.log(`🚀 Bridge HTTP outbound server listening on port ${PORT}`);
});

startBot();
