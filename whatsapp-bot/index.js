const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadMediaMessage, getContentType } = require('@whiskeysockets/baileys');
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
let monitoredGroupJids = [];
let monitorAll = process.env.MONITOR_ALL === '1';
let syncFullHistory = process.env.SYNC_FULL_HISTORY === '1';
let lastDetectedGroup = '';
let lastSeenKey = null;
let pulling = false;
const queuedRequests = [];
let syncWatcherStarted = false;
let configWatcherStarted = false;
let configReady = false;
let pendingHistoryResolve = null;
let intentionalRestart = false;
let pendingGapfill = false;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// منع موت العملية بأخطاء اتصال غير متوقعة (مثل Connection Closed أثناء sendRetryRequest)
process.on('uncaughtException', (err) => {
    console.error('⚠️ uncaughtException:', err && err.message);
    if (sock) {
        try { sock.end(undefined); } catch (e) {}
        sock = null;
    }
    setTimeout(() => { try { startBot(); } catch (e) { console.error('restart failed:', e && e.message); } }, 3000);
});
process.on('unhandledRejection', (reason) => {
    console.error('⚠️ unhandledRejection:', reason && reason.message ? reason.message : reason);
});

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

let lastConfigLog = '';
async function fetchGroupJid() {
    for (let i = 0; i < 8; i++) {
        try {
            const res = await axios.get(DJANGO + '/api/whatsapp/config/', { timeout: 5000 });
            monitoredGroupJid = normalizeGroupJid(res.data.group_jid || '');
            monitoredGroupJids = (res.data.group_jids || '').split(',').map(s => normalizeGroupJid(s.trim())).filter(Boolean);
            monitorAll = !!res.data.monitor_all;
            syncFullHistory = !!res.data.sync_full_history;
            configReady = true;
            const mode = monitorAll ? 'الكل' : (monitoredGroupJids.length ? monitoredGroupJids.length + ' غرف' : (monitoredGroupJid || 'الكل'));
            const sig = mode + '|' + syncFullHistory;
            if (sig !== lastConfigLog) {
                lastConfigLog = sig;
                console.log('📋 المجموعة المراقبة: ' + mode + ' | مزامنة كاملة: ' + (syncFullHistory ? 'مفعلة' : 'معطلة'));
            }
            return true;
        } catch (e) {
            console.log('⚠️ تعذر جلب الإعدادات من السيرفر (محاولة ' + (i + 1) + '/8)');
            await sleep(2000);
        }
    }
    console.log('⚠️ لم يتم جلب الإعدادات - ستتم تجاهل الرسائل حتى نجاح الجلب');
    return false;
}

function wipeAuth() {
    try {
        if (!fs.existsSync('auth_session')) return;
        for (const f of fs.readdirSync('auth_session')) {
            fs.rmSync('auth_session/' + f, { force: true });
        }
    } catch (e) { }
}

function isGroupJid(jid) {
    if (!jid) return false;
    if (jid.slice(-5) === '@g.us') return true;
    const at = jid.lastIndexOf('@');
    if (at < 0) return false;
    const id = jid.slice(0, at);
    const suffix = jid.slice(at + 1);
    return (suffix === 'id' || suffix === 'lid') && id.slice(0, 5) === '12036';
}

function normalizeGroupJid(jid) {
    if (!isGroupJid(jid)) return jid || '';
    return jid.slice(0, jid.lastIndexOf('@')) + '@g.us';
}

function sameGroup(a, b) {
    const na = normalizeGroupJid(a);
    const nb = normalizeGroupJid(b);
    if (!na || !nb) return false;
    return na === nb;
}

function selfNumerics() {
    if (!sock || !sock.user) return [];
    const out = [];
    for (const v of [sock.user.id, sock.user.lid]) {
        if (!v) continue;
        const num = String(v).split(':')[0].split('@')[0];
        if (num) out.push(num);
    }
    return out;
}

function isSelfJid(jid) {
    if (!jid) return false;
    const at = jid.lastIndexOf('@');
    if (at < 0) return false;
    const id = jid.slice(0, at).split(':')[0];
    if (!id) return false;
    return selfNumerics().includes(id);
}

async function reportGroup(jid) {
    jid = normalizeGroupJid(jid);
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
    if (isSelfJid(jid)) return true;
    if (!isGroupJid(jid)) return false;
    if (monitorAll) return true;
    if (monitoredGroupJids.length) {
        return monitoredGroupJids.some(g => sameGroup(jid, g));
    }
    if (!monitoredGroupJid) return jid.slice(-5) === '@g.us';
    return sameGroup(jid, monitoredGroupJid);
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

const GEMINI_KEY = process.env.GEMINI_API_KEY || '';
const GEMINI_MODELS = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash'];

async function analyzeImageWithGemini(msg) {
    if (!GEMINI_KEY || !sock) return null;
    try {
        const m = normalizeContent(msg.message);
        const imgNode = m && (m.imageMessage || m.stickerMessage);
        if (!imgNode) return null;
        const buffer = await downloadMediaMessage(msg, 'buffer', {}, {
            logger: undefined,
            reuploadRequest: sock.updateMediaMessage,
        });
        if (!buffer || !buffer.length) return null;
        const b64 = buffer.toString('base64');
        const mime = imgNode.mimetype || 'image/jpeg';
        const prompt = 'هذه صورة من غرفة واتساب لشركة تحويلات مالية. اقرأ كل الأرقام والنصوص المالية الظاهرة فيها. أعد JSON فقط بدون أي نص آخر بالشكل: {"rate": number|null, "post_rate": number|null, "currency": "د ل"|"ج م"|null, "amount": number|null, "phone": string|null, "transfer_type": "تحويل"|"محفظة"|"فودافون كاش"|"انستا كاش"|null, "summary": "ملخص عربي قصير لمحتوى الصورة"}. rate هو سعر التحويل الظاهر في الصورة (مثال: 5.30 أو 13.25). currency استنتجها من سياق الصورة (مصر=ج م، ليبيا/تونس/الجزائر=د ل). إذا الصورة إعلان سعر فقط بدون معاملة فاترك amount و phone و transfer_type = null. إذا ظهر سعران في الصورة (سعر فودافون كاش/انستا باي/الحوالات العامة وسعر البريد/التسليم اليد) فضع سعر فودافون/انستا في rate والسعر الأدنى في post_rate، وإلا post_rate = null.';
        for (const model of GEMINI_MODELS) {
            for (let attempt = 0; attempt < 2; attempt++) {
                try {
                    const res = await axios.post(
                        'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent?key=' + GEMINI_KEY,
                        {
                            contents: [{
                                parts: [
                                    { text: prompt },
                                    { inline_data: { mime_type: mime, data: b64 } }
                                ]
                            }],
                            generationConfig: { temperature: 0.1, responseMimeType: 'application/json' }
                        },
                        { timeout: 45000 }
                    );
                    const txt = res.data?.candidates?.[0]?.content?.parts?.[0]?.text || '';
                    const jsonMatch = txt.match(/\{[\s\S]*\}/);
                    if (!jsonMatch) return null;
                    const parsed = JSON.parse(jsonMatch[0]);
                    console.log('👁️ صورة محللة (' + model + '): rate=' + parsed.rate + ' cur=' + parsed.currency + ' — ' + (parsed.summary || '').substring(0, 60));
                    return parsed;
                } catch (e) {
                    const code = e && e.response && e.response.status;
                    if (code === 404) break;
                    if ((code === 503 || code === 429) && attempt === 0) {
                        console.log('⚠️ ' + model + ' مشغول (HTTP ' + code + ') — إعادة محاولة...');
                        await sleep(3000);
                        continue;
                    }
                    console.log('⚠️ فشل تحليل الصورة عبر ' + model + ': HTTP ' + code + ' ' + (e && e.message));
                    break;
                }
            }
        }
        return null;
    } catch (e) {
        console.log('⚠️ فشل تحليل الصورة: ' + (e && e.message));
        return null;
    }
}

async function sendToDjango(msg, opts) {
    opts = opts || {};
    if (!configReady) return 'skip';
    const jid = msg.key.remoteJid;
    if (!monitorAll && !monitoredGroupJid && isGroupJid(jid)) {
        await reportGroup(jid);
    }
    if (!isAllowed(jid)) {
        if (jid && jid.endsWith('@g.us')) {
            console.log('🚫 تجاهل رسالة من مجموعة غير مراقبة: ' + jid);
        }
        return 'skip';
    }
    if (isSelfJid(jid)) {
        console.log('🫧 رسالة شات ذاتية مقبولة: ' + jid);
    }
    const text = extractText(msg.message);
    const mContent = normalizeContent(msg.message);
    const isImage = !!(mContent && mContent.imageMessage);
    if (!text && !isImage) return 'skip';
    const sender = msg.key.participant || msg.key.remoteJid;
    noteKey(msg);
    let imageAnalysis = null;
    if (isImage) {
        imageAnalysis = await analyzeImageWithGemini(msg);
    }
    let finalText = text || '';
    if (!finalText && imageAnalysis && imageAnalysis.summary) {
        finalText = '[صورة] ' + imageAnalysis.summary;
    } else if (!finalText && isImage) {
        finalText = '[صورة] بانتظار التحليل';
    }
    if (!finalText) return 'skip';
    // الرقم الحقيقي للمرسل (يُخفي واتساب المعرّف @lid خلفه) — أساس تصنيف صادر/وارد
    const senderPn = String(msg.key.participantAlt || msg.key.remoteJidAlt || '').split('@')[0].replace(/\D/g, '');
    rememberName(sender, sender && sender.endsWith('@lid') ? sender : '', msg.pushName);
    try {
        const payload = {
            jid: msg.key.remoteJid,
            sender: sender,
            sender_pn: senderPn,
            from_me: !!msg.key.fromMe,
            message: finalText,
            timestamp: toSec(msg.messageTimestamp) || undefined,
            direction: msg.key.fromMe ? 'صادر' : 'وارد',
            msg_id: msg.key.id || '',
            allow_dup: opts.allowDup !== false
        };
        if (isImage) payload.from_image = true;
        if (imageAnalysis) {
            payload.image_rate = imageAnalysis.rate;
            payload.image_post_rate = imageAnalysis.post_rate;
            payload.image_currency = imageAnalysis.currency;
            payload.image_amount = imageAnalysis.amount;
            payload.image_phone = imageAnalysis.phone;
            payload.image_ttype = imageAnalysis.transfer_type;
        }
        const res = await axios.post(DJANGO + '/api/whatsapp/webhook/', payload);
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
    if (monitoredGroupJids.length) return monitoredGroupJids[0];
    if (monitoredGroupJid) return monitoredGroupJid;
    if (lastSeenKey && lastSeenKey.key && lastSeenKey.key.remoteJid) {
        return normalizeGroupJid(lastSeenKey.key.remoteJid);
    }
    if (monitorAll) {
        try {
            const chats = await sock.groupFetchAllParticipating();
            const groups = Object.values(chats || {});
            if (groups.length > 0) return normalizeGroupJid(groups[0].id);
        } catch (e) { }
        return null;
    }
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
    const msgs = all.filter(m => m && m.key && m.message && isAllowed(m.key.remoteJid));
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

let pushNames = {};
try {
    pushNames = JSON.parse(fs.readFileSync('names_cache.json', 'utf8')) || {};
} catch (e) {
    pushNames = {};
}
let namesTimer = null;
function saveNamesSoon() {
    if (namesTimer) return;
    namesTimer = setTimeout(() => {
        namesTimer = null;
        try { fs.writeFileSync('names_cache.json', JSON.stringify(pushNames)); } catch (e) { }
    }, 5000);
}
function rememberName(id, lid, name) {
    name = String(name || '').trim();
    if (!name) return;
    let changed = false;
    for (const v of [id, lid]) {
        const raw = String(v || '').trim();
        if (!raw) continue;
        const keys = [raw];
        const base = raw.split('@')[0].split(':')[0];
        if (base) keys.push(base);
        const digits = base.replace(/\D/g, '');
        if (digits) keys.push(digits);
        for (const k of keys) {
            if (k && pushNames[k] !== name) {
                pushNames[k] = name;
                changed = true;
            }
        }
    }
    if (changed) saveNamesSoon();
}
function lookupName(phone, lid, jid) {
    for (const v of [lid, jid, phone]) {
        const raw = String(v || '').trim();
        if (!raw) continue;
        const keys = [raw];
        const base = raw.split('@')[0].split(':')[0];
        if (base) keys.push(base);
        const digits = base.replace(/\D/g, '');
        if (digits) keys.push(digits);
        for (const k of keys) {
            if (k && pushNames[k]) return pushNames[k];
        }
    }
    return '';
}

async function postParticipants() {
    if (!sock || !sock.user) return { ok: false, error: 'البوت غير متصل' };
    const jids = monitoredGroupJids.length ? monitoredGroupJids.slice() : (monitoredGroupJid ? [monitoredGroupJid] : []);
    if (!jids.length) return { ok: false, error: 'لا توجد غرفة محددة - احفظ معرف الغرفة أولاً' };
    const out = [];
    for (const jid of jids) {
        try {
            const meta = await sock.groupMetadata(jid);
            const parts = (meta.participants || []).map(p => {
                let phoneJid = p.jid || '';
                let lidJid = p.lid || '';
                if (p.id && p.id.endsWith('@s.whatsapp.net')) phoneJid = phoneJid || p.id;
                if (p.id && p.id.endsWith('@lid')) lidJid = lidJid || p.id;
                const phone = (phoneJid || '').split('@')[0].split(':')[0].replace(/\D/g, '');
                return {
                    jid: phoneJid || p.id || '',
                    lid: lidJid || '',
                    phone: phone,
                    name: (p.name || p.notify || p.verifiedName || lookupName(phone, lidJid, p.id) || '').trim(),
                    admin: p.admin || '',
                };
            });
            out.push({ group_jid: jid, group_name: (meta.subject || '').trim(), participants: parts });
        } catch (e) {
            console.log('❌ فشل جلب مشتركي ' + jid + ': ' + (e && e.message));
        }
    }
    if (!out.length) return { ok: false, error: 'تعذر جلب المشتركين من واتساب' };
    const total = out.reduce((n, g) => n + g.participants.length, 0);
    try {
        await axios.post(DJANGO + '/api/whatsapp/participants/', { groups: out });
    } catch (e) {
        return { ok: false, error: 'فشل إرسال قائمة المشتركين للسيرفر' };
    }
    return { ok: true, groups: out.length, participants: total };
}

async function runParticipants() {
    const result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 1, error: null, mode: 'participants' };
    const r = await postParticipants();
    if (r.ok) {
        result.ok = true;
        result.found = r.participants;
        result.saved = r.participants;
        result.note = 'تم تحديث بيانات المشتركين: ' + r.participants + ' مشترك في ' + r.groups + ' غرفة';
    } else {
        result.error = r.error;
    }
    return result;
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

async function runCatchup(req) {
    const result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 1, error: null, mode: 'catchup' };
    if (!sock || !sock.user) {
        result.error = 'البوت غير متصل — شغّل البوت ثم أعد المحاولة';
        return result;
    }
    // إعادة اتصال مقصودة: عند إعادة الاتصال يرسل واتساب الرسائل الفائتة (offline buffer) تلقائيًا
    console.log('⏩ متابعة من آخر رسالة: إعادة اتصال الواتساب لاستقبال الرسائل الفائتة...');
    intentionalRestart = true;
    pendingGapfill = true; // بعد الاتصال: سحب عكسي ذكي حتى أول رسالة محفوظة محليًا
    try { sock.end(undefined); } catch (e) { }
    result.ok = true;
    result.note = 'تمت إعادة اتصال الواتساب — الرسائل الفائتة تصل تلقائيًا ثم يبدأ السحب العكسي الذكي';
    // سلامة: إذا لم يُغلق/يُعاد فتح الاتصال خلال 12 ثانية أعد التشغيل يدويًا
    setTimeout(() => {
        if (intentionalRestart) {
            intentionalRestart = false;
            console.log('⏱ لم يغلق الاتصال تلقائيًا — إعادة تشغيل البوت');
            try { sock.end(undefined); } catch (e) { }
            sock = null;
            startBot();
        }
    }, 12000);
    return result;
}

// سحب عكسي ذكي: من آخر رسالة وصلت فعليًا نسحب للخلف دفعة-بدفعة حتى نصل
// لرسالة معرفها موجود بالجدول المحلي (أول رسالة مكررة = الحد الفاصل) ثم نتوقف.
async function runGapfill() {
    const result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 0, error: null, mode: 'gapfill' };
    if (!sock || !sock.user) {
        result.error = 'البوت غير متصل';
        return result;
    }
    const targetJid = await detectGroup();
    if (!targetJid) {
        result.error = 'لا توجد مجموعة محددة';
        return result;
    }
    // المرساة: آخر رسالة وصلت فعليًا من واتساب (ليست محفوظة محليًا بالضرورة)
    let cur = lastSeenKey && lastSeenKey.key && lastSeenKey.key.remoteJid === targetJid
        ? { key: lastSeenKey.key, ts: lastSeenKey.ts } : null;
    if (!cur) {
        // لا توجد رسالة حية — جلب أحدث رسالة محفوظة محليًا كمرساة
        cur = await fetchAnchor(targetJid, 0);
    }
    if (!cur) {
        result.error = 'لا توجد نقطة بداية — انتظر وصول رسالة جديدة ثم أعد المحاولة';
        return result;
    }
    console.log('🧠 سحب عكسي ذكي من رسالة ' + new Date(cur.ts * 1000).toLocaleString('ar-LY') + '...');
    let lastOldest = 0;
    while (result.rounds < MAX_ROUNDS) {
        result.rounds++;
        let batch = await requestHistoryBatch(cur.key, cur.ts);
        if (!batch && result.rounds === 1) {
            console.log('🔁 لم يرد الهاتف على الدفعة الأولى — إعادة المحاولة بعد 3 ثوانٍ...');
            await sleep(3000);
            batch = await requestHistoryBatch(cur.key, cur.ts);
        }
        if (!batch) {
            if (!result.error) result.error = 'واتساب لم يرد على طلب السحب العكسي';
            break;
        }
        const msgs = (batch.messages || []).filter(m => m && m.key && m.message && m.key.remoteJid === targetJid);
        if (!msgs.length) {
            console.log('🧠 دفعة ' + result.rounds + ': فارغة — نهاية السجل (تم)');
            break;
        }
        let oldest = 0;
        let oldestMsg = null;
        let hitKnown = false;
        for (const m of msgs) {
            const ts = toSec(m.messageTimestamp);
            if (ts && (!oldest || ts < oldest)) {
                oldest = ts;
                oldestMsg = m;
            }
            // كل رسالة تُدفع للسيرفر — الديدب يكشف المحفوظ محليًا
            result.found++;
            const status = await sendToDjango(m, { allowDup: false });
            if (status === 'dup') {
                hitKnown = true; // وصلنا لرسالة مدخلة مسبقاً — الحد الفاصل
                result.duplicates++;
            } else if (status === 'saved') {
                result.saved++;
            }
            await sleep(120);
        }
        console.log('🧠 دفعة ' + result.rounds + ': ' + msgs.length + ' | جديد ' + result.saved + ' | مكرر ' + result.duplicates);
        if (hitKnown) {
            console.log('✅ السحب العكسي توقف عند رسالة محفوظة مسبقاً — اكتملت الفجوة');
            break;
        }
        if (!oldest || !oldestMsg) break;
        if (oldest >= cur.ts) {
            console.log('🧠 لا يوجد تقدم في السحب — توقف');
            break;
        }
        cur = {
            key: { remoteJid: oldestMsg.key.remoteJid, fromMe: !!oldestMsg.key.fromMe, id: oldestMsg.key.id },
            ts: oldest
        };
        if (oldest === lastOldest) break;
        lastOldest = oldest;
        await sleep(400);
    }
    result.ok = !result.error;
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
        } else if (req.mode === 'catchup') {
            result = await runCatchup(req);
        } else if (req.mode === 'gapfill') {
            result = await runGapfill();
        } else if (req.mode === 'participants') {
            result = await runParticipants();
        } else {
            result = await pullRange(Number(req.since) || 0, Number(req.until) || Math.floor(Date.now() / 1000), !!req.allow_dup);
        }
    } catch (e) {
        result = { ok: false, found: 0, saved: 0, duplicates: 0, rounds: 0, error: (e && e.message) || 'خطأ غير معروف' };
    }
    pulling = false;
    writeSyncResult(result);
    if (result.mode === 'catchup') {
        console.log(result.ok
            ? '✅ جاهز لاستقبال الرسائل الفائتة بعد إعادة الاتصال'
            : '❌ فشلت المتابعة: ' + result.error);
    } else if (result.mode === 'gapfill') {
        console.log(result.ok
            ? '✅ اكتمل السحب العكسي الذكي: وُجد ' + result.found + ' | جديد ' + result.saved + ' | مكرر ' + result.duplicates
            : '❌ فشل السحب العكسي: ' + result.error);
    } else if (result.mode === 'participants') {
        console.log(result.ok
            ? '✅ تم تحديث المشتركين: ' + result.found
            : '❌ فشل تحديث المشتركين: ' + result.error);
    } else {
        console.log(result.ok
            ? '✅ اكتمل السحب: وُجد ' + result.found + ' | جديد ' + result.saved + ' | مكرر ' + result.duplicates
            : '❌ فشل السحب: ' + result.error);
    }
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
    if (!configWatcherStarted) {
        configWatcherStarted = true;
        setInterval(fetchGroupJid, 10000);
    }

    const { state, saveCreds } = await useMultiFileAuthState('auth_session');
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        browser: ['Chrome', 'Desktop', '10.0'],
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
        for (const ct of (data.contacts || [])) {
            if (!ct) continue;
            rememberName(ct.id, ct.lid, ct.name || ct.notify || ct.verifiedName);
        }
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

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            qrcode.generate(qr, { small: true });
            axios.post(DJANGO + '/api/whatsapp/qr/', { qr: qr, status: 'waiting' }).catch(() => {});
        }
        if (connection === 'open') {
            console.log('✅ تم الاتصال بالواتساب بنجاح!');
            axios.post(DJANGO + '/api/whatsapp/qr/', { qr: null, status: 'connected' }).catch(() => {});
            try {
                const chats = await sock.groupFetchAllParticipating();
                const groups = Object.values(chats || {}).map(g => ({
                    id: normalizeGroupJid(g.id),
                    name: g.subject || '',
                    members: g.participants ? g.participants.length : 0,
                }));
                axios.post(DJANGO + '/api/whatsapp/groups/', { groups }).catch(() => {});
                console.log('📋 تم إرسال ' + groups.length + ' غرفة للسيرفر');
            } catch (e) {
                console.log('❌ فشل جلب قائمة الغرف: ' + (e && e.message));
            }
            postParticipants().then(r => {
                if (r.ok) console.log('👥 تم تحديث مشتركي الغرف: ' + r.participants);
            }).catch(() => {});
            // بعد «متابعة من آخر رسالة»: انتظر وصول الرسائل الفائتة ثم اسحب عكسيًا حتى أول رسالة محفوظة
            if (pendingGapfill) {
                pendingGapfill = false;
                console.log('🧠 انتظار الرسائل الفائتة (8 ثوانٍ) ثم بدء السحب العكسي الذكي...');
                setTimeout(async () => {
                    await runPull({ mode: 'gapfill' });
                }, 8000);
            }
        }
        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            if (intentionalRestart) {
                intentionalRestart = false;
                console.log('🔄 إعادة اتصال مقصودة (متابعة من آخر رسالة)...');
                setTimeout(startBot, 2000);
                return;
            }
            if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
                console.log('❌ تم تسجيل الخروج (device_removed) - مسح الجلسة القديمة وعرض رمز QR جديد');
                wipeAuth();
                axios.post(DJANGO + '/api/whatsapp/qr/', { qr: null, status: 'waiting' }).catch(() => {});
                setTimeout(startBot, 2000);
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

    sock.ev.on('contacts.upsert', (contacts) => {
        for (const ct of (contacts || [])) {
            if (!ct) continue;
            rememberName(ct.id, ct.lid, ct.name || ct.notify || ct.verifiedName);
        }
    });
    sock.ev.on('contacts.update', (contacts) => {
        for (const ct of (contacts || [])) {
            if (!ct) continue;
            rememberName(ct.id, ct.lid, ct.name || ct.notify || ct.verifiedName);
        }
    });
}

startBot();
