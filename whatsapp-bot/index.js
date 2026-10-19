const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const fs = require('fs');
require('dotenv').config();

const DJANGO = 'http://127.0.0.1:1500';
const HISTORY_SYNC_ON_DEMAND = 6;
const MAX_ROUNDS = 60;
const BATCH_SIZE = 100;
const BATCH_TIMEOUT_MS = 20000;

let sock = null;
let monitoredGroupJid = process.env.GROUP_JID || '';
let syncFullHistory = process.env.SYNC_FULL_HISTORY === '1';
let lastDetectedGroup = '';
let lastSeenKey = null;
let pulling = false;
const queuedRequests = [];
let syncWatcherStarted = false;
let pendingHistoryResolve = null;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function toSec(ts) {
    if (ts === null || ts === undefined) return 0;
    let v = ts;
    if (typeof v === 'object' && typeof v.toNumber === 'function') {
        try { v = v.toNumber(); } catch (e) { v = 0; }
    }
    const n = Number(v);
    if (!isFinite(n) || n <= 0) return 0;
    return n > 1e12 ? Math.floor(n / 1000) : Math.floor(n);
}

async function fetchGroupJid() {
    try {
        const res = await axios.get(DJANGO + '/api/whatsapp/config/');
        monitoredGroupJid = res.data.group_jid || '';
        syncFullHistory = !!res.data.sync_full_history;
        console.log('📋 المجموعة المراقبة: ' + (monitoredGroupJid || 'الكل') + ' | مزامنة كاملة: ' + (syncFullHistory ? 'مفعلة' : 'معطلة'));
    } catch (e) { }
}

async function reportGroup(jid) {
    if (!jid || !jid.endsWith('@g.us')) return;
    if (jid === lastDetectedGroup) return;
    lastDetectedGroup = jid;
    try {
        await axios.post(DJANGO + '/api/whatsapp/report-group/', { group_jid: jid });
        monitoredGroupJid = jid;
        console.log('📥 تم حفظ المجموعة: ' + jid);
    } catch (e) { }
}

function isAllowed(jid) {
    if (!monitoredGroupJid) return true;
    return jid === monitoredGroupJid;
}

function normalizeContent(msg) {
    if (!msg) return undefined;
    if (msg.ephemeralMessage?.message) return normalizeContent(msg.ephemeralMessage.message);
    if (msg.viewOnceMessage?.message) return normalizeContent(msg.viewOnceMessage.message);
    if (msg.viewOnceMessageV2?.message) return normalizeContent(msg.viewOnceMessageV2.message);
    if (msg.viewOnceMessageV2Extension?.message) return normalizeContent(msg.viewOnceMessageV2Extension.message);
    if (msg.documentWithCaptionMessage?.message) return normalizeContent(msg.documentWithCaptionMessage.message);
    if (msg.editedMessage?.message) return normalizeContent(msg.editedMessage.message);
    return msg;
}

function extractText(message) {
    const m = normalizeContent(message);
    if (!m) return '';
    return m.conversation
        || m.extendedTextMessage?.text
        || m.imageMessage?.caption
        || m.videoMessage?.caption
        || m.documentMessage?.caption
        || m.buttonsResponseMessage?.selectedDisplayText
        || m.listResponseMessage?.title
        || m.templateButtonReplyMessage?.selectedDisplayText
        || '';
}

function noteKey(msg) {
    if (!msg || !msg.key || !msg.key.id) return;
    const ts = toSec(msg.messageTimestamp);
    if (!ts) return;
    if (!lastSeenKey || ts >= lastSeenKey.ts) {
        lastSeenKey = { key: { remoteJid: msg.key.remoteJid, fromMe: !!msg.key.fromMe, id: msg.key.id }, ts };
    }
}

async function sendToDjango(msg, opts) {
    opts = opts || {};
    const jid = msg.key.remoteJid;
    if (!monitoredGroupJid && jid && jid.endsWith('@g.us')) {
        await reportGroup(jid);
    }
    if (!isAllowed(jid)) {
        if (jid && jid.endsWith('@g.us')) {
            console.log('🚫 تجاهل رسالة من مجموعة غير مراقبة: ' + jid);
        }
        return 'skip';
    }
    const text = extractText(msg.message);
    if (!text) return 'skip';
    const sender = msg.key.participant || msg.key.remoteJid;
    noteKey(msg);
    try {
        const res = await axios.post(DJANGO + '/api/whatsapp/webhook/', {
            jid: msg.key.remoteJid,
            sender: sender,
            message: text,
            timestamp: toSec(msg.messageTimestamp) || undefined,
            direction: msg.key.fromMe ? 'صادر' : 'وارد',
            msg_id: msg.key.id || '',
            allow_dup: opts.allowDup !== false
        });
        const data = res.data || {};
        if (data.duplicate) return 'dup';
        if (data.saved) {
            console.log('🚀 ' + text.substring(0, 40));
            return 'saved';
        }
        return 'skip';
    } catch (e) {
        console.log('❌ فشل الإرسال');
        return 'error';
    }
}

function writeSyncResult(result) {
    try {
        fs.writeFileSync('sync_result', JSON.stringify(result));
    } catch (e) { }
}

async function detectGroup() {
    if (monitoredGroupJid) return monitoredGroupJid;
    try {
        const chats = await sock.groupFetchAllParticipating();
        const groups = Object.values(chats || {});
        if (groups.length > 0) {
            await reportGroup(groups[0].id);
        }
    } catch (e) { }
    return monitoredGroupJid;
}

async function fetchAnchor(targetJid, since) {
    try {
        const res = await axios.get(DJANGO + '/api/whatsapp/anchor/', { params: { since: since || 0 } });
        const d = res.data || {};
        if (d.available && d.id && d.ts) {
            return {
                key: { remoteJid: targetJid, fromMe: !!d.from_me, id: d.id },
                ts: toSec(d.ts),
                covers: !!d.covers_since
            };
        }
    } catch (e) { }
    return null;
}

async function pickAnchor(targetJid, since) {
    const anchor = await fetchAnchor(targetJid, since);
    if (anchor && anchor.covers) return anchor;
    const live = lastSeenKey && lastSeenKey.key && lastSeenKey.key.remoteJid === targetJid ? lastSeenKey : null;
    if (live && live.ts >= since) {
        return { key: live.key, ts: live.ts, covers: true };
    }
    return null;
}

function requestHistoryBatch(key, tsSec) {
    if (!sock) return Promise.resolve(null);
    return new Promise((resolve) => {
        let done = false;
        const finish = (value) => {
            if (done) return;
            done = true;
            if (pendingHistoryResolve === resolver) pendingHistoryResolve = null;
            clearTimeout(timer);
            resolve(value);
        };
        const resolver = (data) => finish(data);
        const timer = setTimeout(() => finish(null), BATCH_TIMEOUT_MS);
        pendingHistoryResolve = resolver;
        (async () => {
            try {
                await sock.fetchMessageHistory(
                    BATCH_SIZE,
                    { remoteJid: key.remoteJid, fromMe: !!key.fromMe, id: key.id },
                    Math.floor(tsSec) * 1000
                );
                console.log('📤 تم إرسال طلب سحب دفعة (' + key.id + ')');
            } catch (e) {
                console.log('❌ فشل إرسال طلب السحب: ' + (e && e.message));
                finish(null);
            }
        })();
    });
}

async function pullRange(since, until, allowDup) {
    const result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 0, error: null, since, until, allow_dup: !!allowDup };
    const targetJid = await detectGroup();
    if (!sock || !sock.user) {
        result.error = 'البوت غير متصل بالواتساب';
        return result;
    }
    if (!targetJid) {
        result.error = 'لا توجد مجموعة محددة - احفظ معرف الغرفة أولاً';
        return result;
    }
    const anchor = await pickAnchor(targetJid, since);
    if (!anchor) {
        result.error = 'لا يوجد مرجع أقدم من تاريخ البداية - انتظر وصول رسالة جديدة ثم أعد المحاولة';
        return result;
    }
    console.log('🔄 بدء السحب من ' + new Date(since * 1000).toLocaleString('ar-LY') + ' إلى ' + new Date(until * 1000).toLocaleString('ar-LY'));
    let cur = anchor;
    let reachedStart = false;
    let lastOldest = 0;
    while (result.rounds < MAX_ROUNDS) {
        result.rounds++;
        const batch = await requestHistoryBatch(cur.key, cur.ts);
        if (!batch) {
            if (!result.error) result.error = 'واتساب لم يرد على طلب السحب - قد لا يدعم جهازك السحب عند الطلب، جرب تفعيل مزامنة التاريخ الكامل وإعادة الربط';
            break;
        }
        const msgs = (batch.messages || []).filter(m => m && m.key && m.message && m.key.remoteJid === targetJid);
        if (!msgs.length) {
            reachedStart = true;
            break;
        }
        let oldest = 0;
        let oldestMsg = null;
        for (const m of msgs) {
            const ts = toSec(m.messageTimestamp);
            if (ts && (!oldest || ts < oldest)) {
                oldest = ts;
                oldestMsg = m;
            }
        }
        if (!oldest || !oldestMsg) {
            if (!result.error) result.error = 'تعذر قراءة تواريخ الرسائل المسحوبة';
            break;
        }
        let inRange = 0;
        for (const m of msgs) {
            const ts = toSec(m.messageTimestamp);
            if (!ts || ts < since || ts > until) continue;
            inRange++;
            result.found++;
            const status = await sendToDjango(m, { allowDup });
            if (status === 'dup') result.duplicates++;
            else if (status === 'saved') result.saved++;
            await sleep(150);
        }
        console.log('📥 دفعة ' + result.rounds + ': ' + msgs.length + ' رسالة (' + inRange + ' ضمن المدى)');
        if (oldest >= cur.ts) {
            if (!result.error) result.error = 'توقف السحب: لا يوجد تقدم في الرسائل';
            break;
        }
        cur = {
            key: { remoteJid: oldestMsg.key.remoteJid, fromMe: !!oldestMsg.key.fromMe, id: oldestMsg.key.id },
            ts: oldest
        };
        if (oldest <= since) {
            reachedStart = true;
            break;
        }
        if (oldest === lastOldest) break;
        lastOldest = oldest;
        await sleep(600);
    }
    if (!reachedStart && !result.error && result.rounds >= MAX_ROUNDS) {
        result.error = 'وصلت الحد الأقصى (' + MAX_ROUNDS + ' دفعة) - أعد السحب للمتابعة من نفس النقطة';
    }
    if (!reachedStart && !result.error && result.found === 0) {
        result.error = 'لم يتم العثور على رسائل ضمن هذه الفترة';
    }
    result.ok = !result.error;
    result.reached_start = reachedStart;
    return result;
}

const HISTORY_TYPE_NAMES = { 0: 'أولية', 2: 'كاملة', 3: 'حديثة', 6: 'عند الطلب' };

async function forwardHistorySync(data) {
    const name = HISTORY_TYPE_NAMES[data.syncType] || String(data.syncType);
    const all = data.messages || [];
    const msgs = all.filter(m => m && m.key && m.message && monitoredGroupJid && m.key.remoteJid === monitoredGroupJid);
    if (!msgs.length) {
        console.log('📥 مزامنة تاريخ (' + name + '): لا رسائل للمجموعة المراقبة من أصل ' + all.length);
        return;
    }
    console.log('📥 مزامنة تاريخ (' + name + '): ' + msgs.length + ' رسالة للمجموعة المراقبة');
    let saved = 0;
    let dups = 0;
    for (const m of msgs) {
        const status = await sendToDjango(m, { allowDup: false });
        if (status === 'dup') dups++;
        else if (status === 'saved') saved++;
        await sleep(120);
    }
    console.log('✅ انتهت مزامنة التاريخ (' + name + '): جديد ' + saved + ' | مكرر ' + dups);
    if (!pulling) {
        writeSyncResult({
            ok: true, found: msgs.length, saved: saved, duplicates: dups, rounds: 1,
            error: null, mode: 'history-' + data.syncType
        });
    }
}

async function selfProbe() {
    const result = { ok: false, found: 0, rounds: 1, error: null, mode: 'selfprobe' };
    if (!sock || !sock.user) {
        result.error = 'البوت غير متصل';
        return result;
    }
    const me = (sock.user.id || '').split(':')[0] + '@s.whatsapp.net';
    const nowSec = Math.floor(Date.now() / 1000);
    let pdoId;
    try {
        pdoId = await sock.fetchMessageHistory(
            BATCH_SIZE,
            { remoteJid: me, fromMe: true, id: 'SELFPROBE0' },
            nowSec * 1000
        );
        console.log('📤 selfprobe PDO id=' + pdoId + ' chat=' + me);
    } catch (e) {
        result.error = 'فشل إرسال الطلب: ' + ((e && e.message) || e);
        return result;
    }
    await sleep(1500);
    const batch = await requestHistoryBatch({ remoteJid: me, fromMe: true, id: pdoId }, nowSec);
    if (!batch) {
        result.error = 'لا يوجد رد من الهاتف على طلب السحب (selfprobe)';
        return result;
    }
    result.found = (batch.messages || []).length;
    result.chats = (batch.chats || []).length;
    result.syncType = batch.syncType;
    result.ok = true;
    return result;
}

async function runPull(req) {
    if (pulling) {
        queuedRequests.push(req);
        return;
    }
    pulling = true;
    let result;
    try {
        if (req.mode === 'selfprobe') {
            result = await selfProbe();
        } else {
            result = await pullRange(Number(req.since) || 0, Number(req.until) || Math.floor(Date.now() / 1000), !!req.allow_dup);
        }
    } catch (e) {
        result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 0, error: (e && e.message) || 'خطأ غير معروف' };
    }
    pulling = false;
    writeSyncResult(result);
    console.log(result.ok
        ? '✅ اكتمل السحب: وُجد ' + result.found + ' | جديد ' + result.saved + ' | مكرر ' + result.duplicates
        : '❌ فشل السحب: ' + result.error);
    if (queuedRequests.length) {
        const next = queuedRequests.shift();
        await runPull(next);
    }
}

async function readSyncRequest(path) {
    let raw = '';
    try {
        raw = fs.readFileSync(path, 'utf8').trim();
    } catch (e) {
        return null;
    }
    if (!raw) return null;
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            const parsed = JSON.parse(raw);
            fs.unlinkSync(path);
            if (parsed && typeof parsed === 'object') return parsed;
            return null;
        } catch (e) {
            const n = parseInt(raw, 10);
            if (!isNaN(n)) {
                fs.unlinkSync(path);
                return { mode: 'all', since: n, until: Math.floor(Date.now() / 1000), allow_dup: false };
            }
            await sleep(500);
            try {
                raw = fs.readFileSync(path, 'utf8').trim();
            } catch (e2) {
                return null;
            }
        }
    }
    try { fs.unlinkSync(path); } catch (e) { }
    console.log('⚠️ طلب سحب غير صالح تم تجاهله');
    return null;
}

function watchSyncRequest() {
    if (syncWatcherStarted) return;
    syncWatcherStarted = true;
    setInterval(async () => {
        const path = 'sync_request';
        if (!fs.existsSync(path)) return;
        const req = await readSyncRequest(path);
        if (!req) return;
        const mode = req.mode || 'all';
        console.log('🔄 وصل طلب سحب (' + mode + ')...');
        await runPull({
            mode,
            since: Number(req.since) || 0,
            until: Number(req.until) || Math.floor(Date.now() / 1000),
            allow_dup: req.allow_dup === true || req.allow_dup === 1 || req.allow_dup === 'true'
        });
    }, 3000);
}

async function startBot() {
    await fetchGroupJid();
    setInterval(fetchGroupJid, 10000);

    const { state, saveCreds } = await useMultiFileAuthState('auth_session');
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        browser: syncFullHistory ? ['Windows', 'Chrome', '10.0'] : ['Chrome', 'Desktop', '10.0'],
        syncFullHistory: syncFullHistory,
        defaultQueryTimeoutMs: undefined,
        markOnlineOnConnect: false,
        fireInitQueries: false,
        generateHighQualityLinkPreview: false,
        shouldSyncHistoryMessage: (h) => {
            if (!h) return false;
            if (syncFullHistory) return true;
            return h.syncType === HISTORY_SYNC_ON_DEMAND;
        },
    });

    fs.writeFileSync('whatsapp_bot.pid', process.pid.toString());
    watchSyncRequest();

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('messaging-history.set', async (data) => {
        if (!data) return;
        if (data.syncType === HISTORY_SYNC_ON_DEMAND) {
            if (pendingHistoryResolve) {
                const resolver = pendingHistoryResolve;
                pendingHistoryResolve = null;
                resolver(data);
            }
            return;
        }
        if (syncFullHistory && (data.syncType === 0 || data.syncType === 2 || data.syncType === 3)) {
            await forwardHistorySync(data);
        }
    });

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            qrcode.generate(qr, { small: true });
            axios.post(DJANGO + '/api/whatsapp/qr/', { qr: qr, status: 'waiting' }).catch(() => {});
        }
        if (connection === 'open') {
            console.log('✅ تم الاتصال بالواتساب بنجاح!');
            axios.post(DJANGO + '/api/whatsapp/qr/', { qr: null, status: 'connected' }).catch(() => {});
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
            await sendToDjango(msg, { allowDup: true });
        }
    });
}

startBot();
