const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const fs = require('fs');
require('dotenv').config();

let sock = null;

async function sendToDjango(msg) {
    const text = msg.message.conversation || msg.message.extendedTextMessage?.text;
    if (!text) return;
    const sender = msg.key.participant || msg.key.remoteJid;
    try {
        await axios.post('http://127.0.0.1:1500/api/whatsapp/webhook/', {
            jid: msg.key.remoteJid,
            sender: sender,
            message: text,
            timestamp: msg.messageTimestamp,
            direction: msg.key.fromMe ? 'صادر' : 'وارد'
        });
        console.log('🚀 ' + text.substring(0, 40));
    } catch (e) {
        console.log('❌ فشل الإرسال');
    }
}

async function syncMissedMessages(sinceTs) {
    if (!sock) return;
    const chats = await sock.store?.chats || {};
    const jids = Object.keys(chats);
    if (jids.length === 0) {
        jids.push(...Object.keys(sock.user?.lid ? {} : {}));
    }
    const allJids = [...new Set([
        ...jids,
        ...(sock.store?.messages ? Object.keys(sock.store.messages) : [])
    ])];
    for (const jid of allJids) {
        try {
            const msgs = await sock.messages.fetch(jid, { limit: 100 });
            const arr = msgs.messages || msgs;
            for (const m of arr) {
                if (!m.message) continue;
                const ts = m.messageTimestamp;
                if (typeof ts === 'number' && ts > sinceTs) {
                    await sendToDjango(m);
                    await new Promise(r => setTimeout(r, 300));
                }
            }
        } catch (e) { }
    }
}

function watchSyncRequest() {
    setInterval(async () => {
        const path = 'sync_request';
        if (fs.existsSync(path)) {
            try {
                const sinceTs = parseInt(fs.readFileSync(path, 'utf8').trim());
                fs.unlinkSync(path);
                console.log('🔄 سحب الرسائل المفقودة...');
                await syncMissedMessages(sinceTs);
                console.log('✅ تم السحب');
            } catch (e) {
                console.log('❌ خطأ في السحب');
            }
        }
    }, 3000);
}

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_session');
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        browser: ['Chrome', 'Desktop', '10.0'],
        syncFullHistory: false,
        defaultQueryTimeoutMs: undefined,
        markOnlineOnConnect: false,
        fireInitQueries: false,
        generateHighQualityLinkPreview: false,
        shouldSyncHistoryMessage: () => false,
    });

    fs.writeFileSync('whatsapp_bot.pid', process.pid.toString());
    watchSyncRequest();

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
            if (statusCode === DisconnectReason.loggedOut) {
                console.log('❌ تم تسجيل الخروج');
                return;
            }
            if (statusCode === 409) {
                setTimeout(startBot, 5000);
                return;
            }
            setTimeout(startBot, 3000);
        }
    });

    sock.ev.on('messages.upsert', async ({ messages }) => {
        for (const msg of messages) {
            if (!msg.message) continue;
            await sendToDjango(msg);
        }
    });
}

startBot();
