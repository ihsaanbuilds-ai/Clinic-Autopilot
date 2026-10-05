const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

const WEBHOOK_URL = 'https://clinic-autopilot.onrender.com/api/doctor/whatsapp-webhook';

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
    
    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: false
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            console.log('\n📲 Scan this QR code with WhatsApp (Linked Devices):\n');
            qrcode.generate(qr, { small: true });
        }
        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection closed. Reconnecting:', shouldReconnect);
            if (shouldReconnect) startBot();
        } else if (connection === 'open') {
            console.log('✅ WhatsApp Bridge Connected & Live!');
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return;
        const msg = messages[0];
        if (!msg.message || msg.key.fromMe) return;

        // Extract clean JID and phone number
        const remoteJid = msg.key.remoteJid || '';
        // If message comes via @lid or alternate JID, fallback to participant or strip non-digits
        let rawPhone = remoteJid.split('@')[0];
        if (msg.key.participant) {
            rawPhone = msg.key.participant.split('@')[0];
        }
        const cleanPhone = rawPhone.replace(/\D/g, '');

        const text = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
        const pushName = msg.pushName || 'Patient';

        if (!text) return;
        console.log(`📩 Inbound from ${pushName} (+${cleanPhone}): "${text}"`);

        try {
            const params = new URLSearchParams();
            params.append('Body', text);
            params.append('From', `whatsapp:+${cleanPhone}`);
            params.append('ProfileName', pushName);

            const res = await axios.post(WEBHOOK_URL, params.toString(), {
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
            });

            let replyText = res.data;
            if (typeof replyText === 'string' && replyText.includes('<Body>')) {
                const match = replyText.match(/<Body>(.*?)<\/Body>/s);
                if (match) replyText = match[1].trim();
            }

            if (replyText && typeof replyText === 'string') {
                await sock.sendMessage(remoteJid, { text: replyText });
                console.log(`📤 Replied successfully to ${pushName}`);
            }
        } catch (err) {
            if (err.response) {
                console.error(`❌ Webhook 500 Details:`, err.response.data);
            } else {
                console.error('Webhook error:', err.message);
            }
        }
    });
}

startBot();
