const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
require('dotenv').config();

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_session');
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        browser: ['Chrome', 'Desktop', '10.0'],
        syncFullHistory: false,
        defaultQueryTimeoutMs: undefined,
        markOnlineOnConnect: false,
        fireInitQueries: false,
        generateHighQualityLinkPreview: false,
        msgRetryCounterCache: undefined,
        deviceMetrics: undefined,
        userDevicesCache: undefined,
        shouldSyncHistoryMessage: () => false,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            qrcode.generate(qr, { small: true });
            axios.post('http://127.0.0.1:1500/api/whatsapp/qr/', { qr: qr, status: 'waiting' }).catch(() => {});
        }
        if (connection === 'open') {
            console.log('✅ تم الاتصال بالواتساب بنجاح!');
            axios.post('http://127.0.0.1:1500/api/whatsapp/qr/', { qr: null, status: 'connected' }).catch(() => {});
        }
        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            if (statusCode !== DisconnectReason.loggedOut) {
                startBot();
            }
        }
    });

    sock.ev.on('messages.upsert', async ({ messages }) => {
        const msg = messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const targetGroupJid = process.env.GROUP_JID || '';

        if (targetGroupJid && msg.key.remoteJid !== targetGroupJid) return;

        const text = msg.message.conversation || msg.message.extendedTextMessage?.text;
        const sender = msg.key.participant || msg.key.remoteJid;

        if (!text) return;

        console.log(`📩 رسالة جديدة: ${text}`);

        try {
            await axios.post('http://127.0.0.1:1500/api/whatsapp/webhook/', {
                jid: msg.key.remoteJid,
                sender: sender,
                message: text,
                timestamp: msg.messageTimestamp
            });
            console.log('🚀 تم إرسال البيانات بنجاح!');
        } catch (error) {
            console.log('❌ فشل الإرسال للمنظومة');
        }
    });
}

startBot();
