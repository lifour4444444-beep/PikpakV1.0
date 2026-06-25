/**
 * PikPak 全协议注册 + 邀请 (协议版)
 * 
 * 纯 HTTP 请求完成验证码识别与提交，无需浏览器。
 * 
 * 协议流程:
 *   1. prehandle: 请求 cap_union_prehandle 获取验证码配置
 *   2. 识别: 下载背景图 → 图图打码平台 → 获取点击坐标
 *   3. TDC: 调用 v8_submit.js 在 V8 沙箱中执行 tdc.js
 *   4. PoW: 在 Node.js 中求解 MD5 哈希碰撞
 *   5. submit: 提交 cap_union_new_verify 获取 ticket
 * 
 * 用法: node batch_register_protocol.js
 */

const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execSync, spawnSync } = require('child_process');
const os = require('os');
const { USER_AGENT, httpGetRaw, makeRequest } = require('./lib/http');
const { createMailAccount, fetchVerificationCode } = require('./lib/mail');
const { fetchProxy } = require('./lib/proxy');
const {
    RateLimitError,
    calculateCaptchaSign,
    generateDeviceId,
    isRateLimited,
    randomItem,
    randomPassword,
    sleep,
} = require('./lib/utils');

// ==================== 配置 ====================

const BASE_URL = 'https://user.mypikpak.com';
const DRIVE_BASE_URL = 'https://api-drive.mypikpak.com';
const DEFAULT_CLIENT_ID = 'YUMx5nI8ZU8Ap8pm';
const RESULT_FILE = path.join(__dirname, 'batch_result_protocol.txt');

const PROXY_API_URL = '';

const DELAY_MINUTES = 10;

// 图图打码平台配置
const TTSHITU_CONFIG = {
    username: 'tbag',
    password: 'Xg666666',
    captchaType: '20',
};

// 设备指纹 Token (从浏览器捕获，格式 v3:base64)
// 该 token 已通过 fetch_captcha_images.py 验证可用
const DEVICE_TOKEN = 'v3:AsGIU9zBiFPc68uJjyTi8YGjMbl6A9b4SYipWU17opDVAZ1lCalHP1jW8pDPu+mCRBZ/7WV0YxfBtreY0ci1/M81peZzGvcOaQYFtdhgwJiImMIBZZTdCPLCY2xI1ME3NsVXBhhWDvKiJyDnOBktbjcIgcJD5OwJiaf2Cf69pzvCfd2X4Et/W9yYfuVIbfeFkUQasWe0jqCW24VYPJx9WBN0kcxoP0gWpRP4feGK8ZJhbz22y8r66ZHQ98sMtY0G5ZZx5BfsuQURZG2GIiWXr9W1ix1X/KseM2baSUhK4lE0tSbuZ/McZNIdt54KCGKCtIQKpdfTHngziAkS+6ujcDRBrQXlPt/HqyDv1RUbELbsosZSlHSKnFyoQ+LBlirGyrZ38cO6KxS+QmhxPBMSGRNydjyO6DQeHFq6RItVmZBW//LkM2Z9irjkcSfHr/k16Ggn+05fouB9M2kyjWhy3PA=';

// Prehandle API 参数
const PREHANDLE_URL = 'https://ca.turing.captcha.qcloud.com/cap_union_prehandle';
const VERIFY_URL = 'https://turing.captcha.qcloud.com/cap_union_new_verify';

// v8_submit.js 路径
const V8_SUBMIT_JS = path.join(__dirname, 'v8_submit.js');
const NODE_PATH = 'node';

// PoW 配置
const POW_MAX_NONCE = 100_000_000;
const POW_WORKERS = 4;
const PROTOCOL_BUILD = '2026-06-14-single-attempt-mail-poll-v3';

// ==================== 工具函数 ====================

const COUNTRY_CODES = ['US', 'JP', 'HK', 'SG', 'TW'];
const LOCALES = ['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'ko-KR'];

// ==================== 图片工具 ====================

function getImageDimensions(buffer) {
    if (buffer.length < 24) return null;

    // PNG: 前8字节是签名，之后是IHDR chunk
    if (buffer[0] === 0x89 && buffer[1] === 0x50 && buffer[2] === 0x4E && buffer[3] === 0x47) {
        return {
            width: buffer.readUInt32BE(16),
            height: buffer.readUInt32BE(20),
            type: 'png',
        };
    }

    // JPEG: 查找 SOF0 标记 (0xFF 0xC0)
    if (buffer[0] === 0xFF && buffer[1] === 0xD8) {
        let i = 2;
        while (i < buffer.length - 9) {
            if (buffer[i] !== 0xFF) { i++; continue; }
            const marker = buffer[i + 1];
            if (marker === 0xC0 || marker === 0xC1 || marker === 0xC2) {
                return {
                    width: buffer.readUInt16BE(i + 7),
                    height: buffer.readUInt16BE(i + 5),
                    type: 'jpeg',
                };
            }
            const segLen = buffer.readUInt16BE(i + 2);
            i += 2 + segLen;
        }
    }

    // WebP
    if (buffer[0] === 0x52 && buffer[1] === 0x49 && buffer[2] === 0x46 && buffer[3] === 0x46) {
        if (buffer.toString('ascii', 8, 15) === 'WEBPVP8') {
            const w = buffer.readUInt16LE(26);
            const h = buffer.readUInt16LE(28);
            return { width: w & 0x3FFF, height: h & 0x3FFF, type: 'webp' };
        }
        if (buffer.toString('ascii', 8, 15) === 'WEBPVP8L') {
            const bits = buffer.readUInt32LE(21);
            return { width: (bits & 0x3FFF) + 1, height: ((bits >> 14) & 0x3FFF) + 1, type: 'webp' };
        }
        if (buffer.toString('ascii', 8, 15) === 'WEBPVP8X') {
            return {
                width: buffer.readUInt32LE(24) & 0xFFFFFF + 1,
                height: buffer.readUInt32LE(27) & 0xFFFFFF + 1,
                type: 'webp',
            };
        }
    }

    // GIF
    if (buffer[0] === 0x47 && buffer[1] === 0x49 && buffer[2] === 0x46) {
        return {
            width: buffer.readUInt16LE(6),
            height: buffer.readUInt16LE(8),
            type: 'gif',
        };
    }

    // BMP
    if (buffer[0] === 0x42 && buffer[1] === 0x4D) {
        return {
            width: buffer.readUInt32LE(18),
            height: Math.abs(buffer.readInt32LE(22)),
            type: 'bmp',
        };
    }

    return null;
}

async function downloadImageAsBase64(url, referer, proxy) {
    const buf = await httpGetRaw(url, referer, 15000, proxy);
    const dims = getImageDimensions(buf);
    const ext = dims ? dims.type : 'png';
    const mimeMap = { png: 'image/png', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', bmp: 'image/bmp' };
    const mime = mimeMap[ext] || 'image/png';
    const base64 = buf.toString('base64');
    return {
        base64,
        dataUrl: `data:${mime};base64,${base64}`,
        width: dims ? dims.width : 0,
        height: dims ? dims.height : 0,
        size: buf.length,
        type: ext,
    };
}

// ==================== 图图打码平台 ====================

function ttshituPost(path, body) {
    return new Promise((resolve, reject) => {
        const bodyStr = JSON.stringify(body);
        const req = https.request({
            hostname: 'api.ttshitu.com',
            path,
            method: 'POST',
            timeout: 30000,
            headers: {
                'Content-Type': 'application/json;charset=UTF-8',
                'Content-Length': Buffer.byteLength(bodyStr),
            },
        }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch (e) { reject(new Error('响应解析失败: ' + data.substring(0, 200))); }
            });
        });
        req.on('error', (e) => reject(new Error('网络错误: ' + e.message)));
        req.on('timeout', () => { req.destroy(); reject(new Error('请求超时')); });
        req.write(bodyStr);
        req.end();
    });
}

function ttshituGet(path) {
    return new Promise((resolve, reject) => {
        const req = https.request({
            hostname: 'api.ttshitu.com', path, method: 'GET', timeout: 10000,
        }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch (e) { reject(new Error('响应解析失败: ' + data.substring(0, 200))); }
            });
        });
        req.on('error', (e) => reject(new Error('网络错误: ' + e.message)));
        req.on('timeout', () => { req.destroy(); reject(new Error('请求超时')); });
        req.end();
    });
}

async function ttshituQueryBalance() {
    try {
        const json = await ttshituGet(
            '/queryAccountInfo.json?username=' + encodeURIComponent(TTSHITU_CONFIG.username) +
            '&password=' + encodeURIComponent(TTSHITU_CONFIG.password)
        );
        if (json.success) {
            console.log('  [图图] 余额:', json.data.balance, '总消费:', json.data.consumed);
            return json;
        }
        console.log('  [图图] 查询失败:', json.message);
        return null;
    } catch (e) {
        console.log('  [图图] 查询余额失败:', e.message);
        return null;
    }
}

async function ttshituRecognize(base64Image) {
    const json = await ttshituPost('/predict', {
        username: TTSHITU_CONFIG.username,
        password: TTSHITU_CONFIG.password,
        typeid: TTSHITU_CONFIG.captchaType,
        image: base64Image,
    });
    if (json.success) {
        console.log('  [图图] 识别成功, ID:', json.data.id, '结果:', json.data.result);
        return json.data;
    }
    throw new Error('识别失败: ' + (json.message || JSON.stringify(json)));
}

async function ttshituReportError(taskId) {
    try {
        const json = await ttshituPost('/reporterror.json', { id: taskId });
        console.log('  [图图] 已上报错误:', json.data.result || json.message);
    } catch (e) {
        console.log('  [图图] 上报失败:', e.message);
    }
}

// ==================== 协议核心: Prehandle ====================

async function fetchPrehandle(deviceToken, sess, subsid, proxy) {
    const params = new URLSearchParams({
        aid: '189981187',
        protocol: 'https',
        accver: '1',
        showtype: 'popup',
        ua: Buffer.from(USER_AGENT).toString('base64'),
        noheader: '1',
        fb: '0',
        deviceToken: deviceToken,
        isJsVersion: '3',
        aged: '0',
        enableAged: '0',
        enableDarkMode: '0',
        grayscale: '1',
        clientype: '2',
        userLanguage: 'zh-cn',
        cap_cd: '',
        uid: '',
        lang: 'zh-cn',
        entry_url: 'https://user.mypikpak.com/captcha/v2/txCaptcha.html',
        elder_captcha: '0',
        js: 'https://global.turing.captcha.gtimg.com/tgJNCap-global.203d0ca0.js',
        login_appid: '',
        wb: '2',
        subsid: String(subsid || 1),
        sess: sess || '',
    });

    const url = PREHANDLE_URL + '?' + params.toString();

    return new Promise(async (resolve, reject) => {
        try {
            const parsed = new URL(PREHANDLE_URL);
            const reqOptions = {
                hostname: parsed.hostname,
                port: 443,
                path: parsed.pathname + '?' + params.toString(),
                method: 'GET',
                timeout: 15000,
                headers: {
                    'User-Agent': USER_AGENT,
                    'Referer': 'https://user.mypikpak.com/',
                    'Accept': '*/*',
                },
            };

            if (proxy) {
                const { connectViaProxy } = require('./lib/proxy');
                const [proxyHost, proxyPort] = proxy.split(':');
                const socket = await connectViaProxy(parsed.hostname, 443, proxyHost, parseInt(proxyPort, 10));
                reqOptions.socket = socket;
                reqOptions.agent = false;
            }

            const req = https.request(reqOptions, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    try {
                        const info = parsePrehandleResponse(data);
                        resolve(info);
                    } catch (e) {
                        reject(e);
                    }
                });
            });
            req.on('error', reject);
            req.on('timeout', () => { req.destroy(); reject(new Error('Prehandle 请求超时')); });
            req.end();
        } catch (e) {
            reject(e);
        }
    });
}

function parsePrehandleResponse(body) {
    body = body.trim();
    let jsonStr = body;

    if (body.startsWith('(')) {
        jsonStr = body.slice(1, -1);
    } else {
        const m = body.match(/^\w+\((.*)\)$/s);
        if (!m) {
            throw new Error('无法解析 JSONP 响应: ' + body.substring(0, 200));
        }
        jsonStr = m[1];
    }

    const raw = JSON.parse(jsonStr);
    const data = raw.data || {};
    const dynInfo = data.dyn_show_info || {};
    const bgCfg = dynInfo.bg_elem_cfg || {};
    const commCfg = data.comm_captcha_cfg || {};

    const baseImg = 'https://ca.turing.captcha.qcloud.com';
    let bgUrl = bgCfg.img_url || '';
    let spriteUrl = dynInfo.sprite_url || '';
    let tdcPath = commCfg.tdc_path || '';

    if (bgUrl && !bgUrl.startsWith('http')) bgUrl = baseImg + bgUrl;
    if (spriteUrl && !spriteUrl.startsWith('http')) spriteUrl = baseImg + spriteUrl;
    // tdcPath 不拼接域名 - v8_submit.js 会自动拼接 'https://turing.captcha.qcloud.com'

    return {
        sess: raw.sess || '',
        sid: String(raw.sid || ''),
        subcapclass: String(raw.subcapclass || ''),
        tdcPath,
        powCfg: commCfg.pow_cfg || {},
        bgUrl,
        spriteUrl,
        instruction: dynInfo.instruction || '',
        insElemCfg: dynInfo.ins_elem_cfg || [],
        bgSize: bgCfg.size_2d || [672, 480],
        markStyle: (bgCfg.click_cfg || {}).mark_style || '',
        lang: dynInfo.lang || '',
        raw,
    };
}

// ==================== 协议核心: TDC 执行 ====================

function runTDC(sess, sid, subcapclass, tdcPath, powCfg, ans, proxy) {
    const inputData = {
        sess,
        sid,
        subcapclass,
        tdc_path: tdcPath,
        pow_cfg: powCfg,
        ans: ans || '',
        proxy: proxy || '',
    };

    const tmpFile = path.join(os.tmpdir(), 'tdc_input_' + Date.now() + '.json');
    fs.writeFileSync(tmpFile, JSON.stringify(inputData), 'utf-8');

    try {
        const result = spawnSync(NODE_PATH, [V8_SUBMIT_JS, tmpFile], {
            timeout: 60000,
            encoding: 'utf-8',
            maxBuffer: 10 * 1024 * 1024,
        });

        if (result.error) {
            throw new Error('Node.js 执行失败: ' + result.error.message);
        }

        if (result.status !== 0) {
            const stderr = (result.stderr || '').trim();
            const stdout = (result.stdout || '').trim();
            throw new Error(
                'v8_submit.js 退出码: ' + result.status + '\n' +
                'STDOUT: ' + stdout.slice(-500) + '\n' +
                'STDERR: ' + stderr.slice(-500)
            );
        }

        const output = JSON.parse(result.stdout.trim());
        if (output.error) {
            throw new Error('TDC 执行错误: ' + output.error);
        }

        return {
            collect: output.collect || '',
            info: output.info || '',
            tokenid: output.tokenid || '',
        };
    } finally {
        try { fs.unlinkSync(tmpFile); } catch (e) { /* ignore */ }
    }
}

// ==================== 协议核心: PoW 求解 ====================

function solvePoW(prefix, targetMd5, maxNonce, workers) {
    maxNonce = maxNonce || POW_MAX_NONCE;
    workers = workers || POW_WORKERS;

    const startTime = Date.now();

    if (workers <= 1) {
        for (let nonce = 0; nonce < maxNonce; nonce++) {
            const candidate = prefix + nonce;
            const hash = crypto.createHash('md5').update(candidate).digest('hex');
            if (hash === targetMd5) {
                const elapsed = Date.now() - startTime;
                return { answer: candidate, nonce, calcTime: elapsed };
            }
        }
        const elapsed = Date.now() - startTime;
        throw new Error('PoW 未找到 (max_nonce=' + maxNonce + ', elapsed=' + elapsed + 'ms)');
    }

    // 多线程 PoW 求解
    const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');

    if (!isMainThread) {
        const { prefix, targetMd5, start, end } = workerData;
        for (let nonce = start; nonce < end; nonce++) {
            const candidate = prefix + nonce;
            const hash = crypto.createHash('md5').update(candidate).digest('hex');
            if (hash === targetMd5) {
                parentPort.postMessage({ found: true, answer: candidate, nonce });
                return;
            }
        }
        parentPort.postMessage({ found: false });
        return;
    }

    return new Promise((resolve, reject) => {
        const chunkSize = Math.ceil(maxNonce / workers);
        let completed = 0;
        let resolved = false;

        for (let i = 0; i < workers; i++) {
            const start = i * chunkSize;
            const end = i === workers - 1 ? maxNonce : (i + 1) * chunkSize;

            const worker = new Worker(__filename, {
                workerData: { prefix, targetMd5, start, end },
            });

            worker.on('message', (msg) => {
                if (msg.found && !resolved) {
                    resolved = true;
                    const elapsed = Date.now() - startTime;
                    resolve({ answer: msg.answer, nonce: msg.nonce, calcTime: elapsed });
                }
            });

            worker.on('error', reject);
            worker.on('exit', () => {
                completed++;
                if (completed >= workers && !resolved) {
                    const elapsed = Date.now() - startTime;
                    reject(new Error('PoW 未找到 (max_nonce=' + maxNonce + ', elapsed=' + elapsed + 'ms)'));
                }
            });
        }
    });
}

// 单线程 PoW (更简单可靠)
function solvePoWSingle(prefix, targetMd5, maxNonce) {
    maxNonce = maxNonce || POW_MAX_NONCE;
    const startTime = Date.now();

    for (let nonce = 0; nonce < maxNonce; nonce++) {
        const candidate = prefix + nonce;
        const hash = crypto.createHash('md5').update(candidate).digest('hex');
        if (hash === targetMd5) {
            const elapsed = Date.now() - startTime;
            return { answer: candidate, nonce, calcTime: elapsed };
        }
        // 每100万次打印进度
        if (nonce > 0 && nonce % 1000000 === 0) {
            console.log('  [PoW] 进度: ' + (nonce / 1000000).toFixed(0) + 'M / ' + (maxNonce / 1000000).toFixed(0) + 'M');
        }
    }

    const elapsed = Date.now() - startTime;
    throw new Error('PoW 未找到 (max_nonce=' + maxNonce + ', elapsed=' + elapsed + 'ms)');
}

// ==================== 协议核心: 提交验证 ====================

async function submitVerify(sess, sid, subcapclass, collect, eks, ans, powAnswer, powCalcTime, proxy) {
    const enc = encodeURIComponent;
    const body = [
        'sess=' + enc(sess),
        'sid=' + enc(sid),
        'collect=' + collect,
        'eks=' + enc(eks),
        'ans=' + enc(ans),
        'pow_answer=' + enc(powAnswer),
        'pow_calc_time=' + enc(String(powCalcTime)),
        'subcapclass=' + enc(subcapclass),
        'tlg=' + enc(String(collect.length)),
        'data=' + enc(''),
        'aini=' + enc('1'),
        'uid=' + enc(''),
        'track=' + enc(''),
        'char_c=' + enc(''),
        'crypted_char_c=' + enc(''),
        'extra_net_req=' + enc(''),
        'poll_captcha=' + enc(''),
        'webg_p=' + enc(''),
        'ele_ans=' + enc(''),
        'ans_enc=' + enc(''),
        'e_ans=' + enc(''),
        'aesKey=' + enc(''),
        'crypto=' + enc('1'),
        'cap_cd=' + enc(''),
    ].join('&');

    return new Promise(async (resolve, reject) => {
        try {
            const parsed = new URL(VERIFY_URL);
            const reqOptions = {
                hostname: parsed.hostname,
                port: 443,
                path: parsed.pathname,
                method: 'POST',
                timeout: 15000,
                headers: {
                    'User-Agent': USER_AGENT,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(body),
                    'Referer': 'https://user.mypikpak.com/',
                    'Origin': 'https://user.mypikpak.com',
                },
            };

            if (proxy) {
                const { connectViaProxy } = require('./lib/proxy');
                const [proxyHost, proxyPort] = proxy.split(':');
                const socket = await connectViaProxy(parsed.hostname, 443, proxyHost, parseInt(proxyPort, 10));
                reqOptions.socket = socket;
                reqOptions.agent = false;
            }

            const req = https.request(reqOptions, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    try {
                        const json = JSON.parse(data);
                        resolve({
                            success: json.errorCode == 0 && !!json.ticket,
                            errorCode: json.errorCode,
                            errorMessage: json.errorMessage || '',
                            ticket: json.ticket || '',
                            randstr: json.randstr || '',
                            sess: json.sess || '',
                            raw: json,
                        });
                    } catch (e) {
                        resolve({
                            success: false,
                            errorCode: -1,
                            errorMessage: '非JSON响应: ' + data.substring(0, 200),
                            ticket: '',
                            randstr: '',
                            sess: '',
                            raw: data,
                        });
                    }
                });
            });
            req.on('error', reject);
            req.on('timeout', () => { req.destroy(); reject(new Error('提交验证超时')); });
            req.write(body);
            req.end();
        } catch (e) {
            reject(e);
        }
    });
}

// ==================== 协议版验证码求解 (主函数) ====================

async function protocolSolveCaptcha() {
    console.log('  [协议] 开始协议版验证码求解...');

    // 获取代理 IP (每次验证码求解换一个 IP)
    let proxy;
    try {
        proxy = await fetchProxy();
        console.log('  [协议] 代理: ' + proxy);
    } catch (e) {
        console.log('  [协议] 获取代理失败: ' + e.message + '，使用直连');
    }

    // Step 1: 查询图图余额
    await ttshituQueryBalance();

    // Step 2: 请求 prehandle 获取验证码配置
    console.log('  [协议] Step 1: 请求 prehandle...');
    let prehandleInfo;
    try {
        prehandleInfo = await fetchPrehandle(DEVICE_TOKEN, '', 1);
    } catch (e) {
        console.log('  [协议] Prehandle 失败:', e.message);
        return null;
    }

    console.log('  [协议] sess:', prehandleInfo.sess.substring(0, 40) + '...');
    console.log('  [协议] sid:', prehandleInfo.sid);
    console.log('  [协议] subcapclass:', prehandleInfo.subcapclass);
    console.log('  [协议] bg_size:', prehandleInfo.bgSize);
    console.log('  [协议] instruction:', prehandleInfo.instruction);
    console.log('  [协议] tdc_path:', prehandleInfo.tdcPath.substring(0, 60) + '...');
    console.log('  [协议] pow_cfg:', JSON.stringify(prehandleInfo.powCfg));

    if (!prehandleInfo.bgUrl) {
        console.log('  [协议] 无背景图URL，跳过');
        return null;
    }

    // Step 3: 下载背景图和参考条
    console.log('  [协议] Step 2: 下载背景图和参考条...');
    let imageData, spriteData;
    try {
        imageData = await downloadImageAsBase64(prehandleInfo.bgUrl, 'https://user.mypikpak.com/', proxy);
        if (prehandleInfo.spriteUrl) {
            spriteData = await downloadImageAsBase64(prehandleInfo.spriteUrl, 'https://user.mypikpak.com/', proxy);
        }
    } catch (e) {
        console.log('  [协议] 下载图片失败:', e.message);
        return null;
    }
    console.log('  [协议] 背景图:', imageData.width + 'x' + imageData.height,
        '类型:', imageData.type, '大小:', (imageData.size / 1024).toFixed(1) + 'KB');
    if (spriteData) console.log('  [协议] 参考条:', spriteData.width + 'x' + spriteData.height,
        '类型:', spriteData.type);

    // Step 3.5: 拼接图片 (参考条在上 + 背景图在下，模拟原网页)
    let combinedBase64 = imageData.base64;
    let combinedWidth = imageData.width;
    let combinedHeight = imageData.height;
    let spriteHeight = 0;

    if (spriteData) {
        const tmpDir = os.tmpdir();
        const bgTmp = path.join(tmpDir, 'captcha_bg_' + Date.now() + '.jpg');
        const spriteTmp = path.join(tmpDir, 'captcha_sprite_' + Date.now() + '.png');
        const combinedTmp = path.join(tmpDir, 'captcha_combined_' + Date.now() + '.jpg');

        fs.writeFileSync(bgTmp, Buffer.from(imageData.base64, 'base64'));
        fs.writeFileSync(spriteTmp, Buffer.from(spriteData.base64, 'base64'));

        try {
            const imageTool = path.join(__dirname, 'image_tools.py');
            const imageResult = spawnSync(
                'python',
                [imageTool, 'combine', bgTmp, spriteTmp, combinedTmp],
                { timeout: 10000, encoding: 'utf-8' }
            );
            if (imageResult.error) throw imageResult.error;
            if (imageResult.status !== 0) throw new Error((imageResult.stderr || '').trim());

            const result = JSON.parse(imageResult.stdout.trim());
            if (result.ok) {
                combinedWidth = result.width;
                combinedHeight = result.height;
                spriteHeight = result.sprite_height;

                const combinedBuf = fs.readFileSync(combinedTmp);
                combinedBase64 = combinedBuf.toString('base64');
                console.log('  [协议] 拼接后图片:', combinedWidth + 'x' + combinedHeight,
                    'sprite偏移:', spriteHeight + 'px');
            }
        } catch (e) {
            console.log('  [协议] 图片拼接失败:', e.message, '使用原始图片');
        }

        try { fs.unlinkSync(bgTmp); } catch (e) { }
        try { fs.unlinkSync(spriteTmp); } catch (e) { }
        try { fs.unlinkSync(combinedTmp); } catch (e) { }
    }

    // Step 4: 发送图图识别
    console.log('  [协议] Step 3: 图图识别...');
    let recResult;
    let taskId;
    try {
        recResult = await ttshituRecognize(combinedBase64);
        taskId = recResult.id;
    } catch (e) {
        console.log('  [协议] 图图识别失败:', e.message);
        return null;
    }

    // 解析坐标: 格式 "x1,y1|x2,y2|..." 或 "x1,y1 x2,y2"
    const coords = recResult.result.split(/[| ]+/).map(p => {
        const parts = p.split(',').map(Number);
        return (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1]))
            ? { x: parts[0], y: parts[1] } : null;
    }).filter(Boolean);

    if (coords.length === 0) {
        console.log('  [协议] 坐标解析为空');
        if (taskId) await ttshituReportError(taskId);
        return null;
    }
    console.log('  [协议] 图图返回', coords.length, '个坐标:', JSON.stringify(coords));

    // Step 5: 坐标映射 (拼接图坐标 → 背景图坐标 → bg_size 坐标)
    // 先减去 sprite 偏移（参考条在顶部占用的高度）
    const bgCoords = coords.map(c => ({
        x: c.x,
        y: c.y - spriteHeight,
    })).filter(c => c.y >= 0);

    if (bgCoords.length === 0) {
        console.log('  [协议] 坐标偏移后为空 (spriteHeight=' + spriteHeight + ')');
        if (taskId) await ttshituReportError(taskId);
        return null;
    }

    const bgW = prehandleInfo.bgSize[0];
    const bgH = prehandleInfo.bgSize[1];
    const imgW = combinedWidth;
    const imgH = combinedHeight - spriteHeight;

    const scaleX = imgW > 0 ? bgW / imgW : 1;
    const scaleY = imgH > 0 ? bgH / imgH : 1;

    console.log('  [协议] 坐标缩放: scaleX=' + scaleX.toFixed(4) + ', scaleY=' + scaleY.toFixed(4));

    const mappedCoords = bgCoords.map((c, i) => ({
        x: Math.round(c.x * scaleX),
        y: Math.round(c.y * scaleY),
    }));

    console.log('  [协议] 映射后坐标:', JSON.stringify(mappedCoords));

    // Step 6: 构建答案 JSON
    const invalidCoords = mappedCoords.filter(c =>
        c.x < 0 || c.x >= bgW || c.y < 0 || c.y >= bgH
    );
    if (mappedCoords.length !== 3 || invalidCoords.length > 0) {
        console.log('  [协议] 坐标检查失败: expected=3, actual=' + mappedCoords.length +
            ', outOfBounds=' + invalidCoords.length);
        if (taskId) await ttshituReportError(taskId);
        return null;
    }

    const ans = JSON.stringify(mappedCoords.map((c, i) => ({
        elem_id: i + 1,
        type: 'DynAnswerType_POS',
        data: c.x + ',' + c.y,
    })));
    console.log('  [协议] 答案:', ans);

    // Step 7: 执行 TDC
    console.log('  [协议] Step 4: 执行 TDC (V8 沙箱)...');
    let tdcResult;
    try {
        tdcResult = runTDC(
            prehandleInfo.sess,
            prehandleInfo.sid,
            prehandleInfo.subcapclass,
            prehandleInfo.tdcPath,
            prehandleInfo.powCfg,
            ans,
            proxy
        );
    } catch (e) {
        console.log('  [协议] TDC 执行失败:', e.message);
        if (taskId) await ttshituReportError(taskId);
        return null;
    }
    console.log('  [协议] collect:', tdcResult.collect.length, 'chars');
    console.log('  [协议] eks:', tdcResult.info.length, 'chars');

    // Step 8: 求解 PoW
    console.log('  [协议] Step 5: 求解 PoW...');
    const powCfg = prehandleInfo.powCfg;
    if (!powCfg || !powCfg.prefix || !powCfg.md5) {
        console.log('  [协议] 无 PoW 配置，跳过');
        if (taskId) await ttshituReportError(taskId);
        return null;
    }

    let powResult;
    try {
        powResult = solvePoWSingle(powCfg.prefix, powCfg.md5);
    } catch (e) {
        console.log('  [协议] PoW 求解失败:', e.message);
        if (taskId) await ttshituReportError(taskId);
        return null;
    }
    console.log('  [协议] PoW 答案:', powResult.answer.substring(0, 40) + '...');
    console.log('  [协议] nonce:', powResult.nonce, '耗时:', powResult.calcTime + 'ms');

    // Step 9: 提交验证
    console.log('  [协议] Step 6: 提交验证...');
    let verifyResult;
    try {
        verifyResult = await submitVerify(
            prehandleInfo.sess,
            prehandleInfo.sid,
            prehandleInfo.subcapclass,
            tdcResult.collect,
            tdcResult.info,
            ans,
            powResult.answer,
            powResult.calcTime,
            proxy
        );
    } catch (e) {
        console.log('  [协议] 提交验证失败:', e.message);
        if (taskId) await ttshituReportError(taskId);
        return null;
    }

    if (!verifyResult.success) {
        console.log('  [协议] 验证失败: errorCode=' + verifyResult.errorCode +
            ', msg=' + verifyResult.errorMessage);
        console.log('  [协议] raw:', JSON.stringify(verifyResult.raw));
        if (taskId) await ttshituReportError(taskId);
        return null;
    }

    console.log('  [协议] ✅ 验证成功!');
    console.log('  [协议] ticket:', verifyResult.ticket.substring(0, 30) + '...');
    console.log('  [协议] randstr:', verifyResult.randstr);

    return { ticket: verifyResult.ticket, randstr: verifyResult.randstr };
}

// ==================== Ticket 兑换 JWT ====================

async function exchangeTicketForJWT(deviceId, ticket, randstr, step2Jwt, locale) {
    // 浏览器实际流程:
    //   GET /credit/v1/report?deviceid=XXX&captcha_token=JWT_B
    //     &type=txCaptcha&result=0&data=<ticket>&rand_str=<randstr>
    //     &request_id=<UUID>&sign=<签名>
    //   返回 200 JSON: {"code":200,"captcha_token":"JWT_C","expires_in":579}
    //   JWT_C 就是 sendVerification 要用的 token

    const requestId = crypto.randomUUID();
    const sign = crypto.createHash('sha1').update(ticket + randstr).digest('base64');

    const params = [
        'deviceid=' + encodeURIComponent(deviceId),
        'captcha_token=' + encodeURIComponent(step2Jwt),
        'type=txCaptcha',
        'result=0',
        'data=' + encodeURIComponent(ticket),
        'rand_str=' + encodeURIComponent(randstr),
        'request_id=' + encodeURIComponent(requestId),
        'sign=' + encodeURIComponent(sign),
    ].join('&');

    console.log('  [兑换] GET /credit/v1/report (通知服务器验证码已解决)...');
    try {
        const reportResp = await makeRequest({
            method: 'GET',
            baseUrl: BASE_URL,
            path: '/credit/v1/report?' + params,
            headers: {
                'accept-language': locale,
                'x-captcha-token': step2Jwt,
                'x-device-id': deviceId,
                'Origin': 'https://mypikpak.com',
                'Referer': 'https://mypikpak.com/',
            },
        });
        console.log('  [兑换] 响应: status=' + reportResp.statusCode +
            ', data=' + JSON.stringify(reportResp.data).substring(0, 200));

        if (reportResp.statusCode === 200 && reportResp.data && reportResp.data.captcha_token && reportResp.data.captcha_token !== step2Jwt) {
            console.log('  [兑换] ✅ 获取到 JWT_C: ' + reportResp.data.captcha_token.substring(0, 30) + '...');
            return reportResp.data.captcha_token;
        }
    } catch (e) {
        console.log('  [兑换] /credit/v1/report 异常: ' + e.message);
    }

    console.log('  [兑换] 回退使用 JWT_B');
    return step2Jwt;
}

// ==================== PikPak API 函数 ====================

function getInitialCaptchaToken(deviceId, locale) {
    const clientVersion = 'undefined';
    const packageName = 'drive.mypikpak.com';
    const timestamp = String(Date.now());
    const captchaSign = calculateCaptchaSign(DEFAULT_CLIENT_ID, clientVersion, packageName, deviceId, timestamp);

    return makeRequest({
        method: 'POST',
        baseUrl: BASE_URL,
        path: '/v1/shield/captcha/init',
        headers: {
            'accept-language': locale,
            'x-device-id': deviceId,
            'Origin': 'https://mypikpak.com',
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            client_id: DEFAULT_CLIENT_ID,
            action: 'POST:/v1/auth/verification',
            device_id: deviceId,
            meta: {
                captcha_sign: captchaSign,
                client_version: clientVersion,
                package_name: packageName,
                user_id: '',
                timestamp,
            },
        },
    });
}

function initCaptchaToken(deviceId, action, meta, locale, captchaToken) {
    return makeRequest({
        method: 'POST',
        baseUrl: BASE_URL,
        path: '/v1/shield/captcha/init',
        headers: {
            'accept-language': locale,
            'x-device-id': deviceId,
            'Origin': 'https://mypikpak.com',
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            client_id: DEFAULT_CLIENT_ID,
            action,
            device_id: deviceId,
            captcha_token: captchaToken,
            meta,
        },
    });
}

function sendVerification(deviceId, captchaToken, email, locale) {
    return makeRequest({
        method: 'POST',
        baseUrl: BASE_URL,
        path: '/v1/auth/verification',
        headers: {
            'accept-language': locale,
            'x-captcha-token': captchaToken,
            'x-device-id': deviceId,
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            email,
            target: 'ANY',
            usage: 'REGISTER',
            locale,
            client_id: DEFAULT_CLIENT_ID,
        },
    });
}

function verifyCode(deviceId, verificationId, verificationCode) {
    return makeRequest({
        method: 'POST',
        baseUrl: BASE_URL,
        path: '/v1/auth/verification/verify',
        headers: {
            'x-device-id': deviceId,
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            verification_id: verificationId,
            verification_code: verificationCode,
            client_id: DEFAULT_CLIENT_ID,
        },
    });
}

function signup(deviceId, email, verificationCode, verificationToken, password) {
    return makeRequest({
        method: 'POST',
        baseUrl: BASE_URL,
        path: '/v1/auth/signup',
        headers: {
            'x-device-id': deviceId,
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            email,
            verification_code: verificationCode,
            verification_token: verificationToken,
            password,
            client_id: DEFAULT_CLIENT_ID,
        },
    });
}

function bindInvite(accessToken, userId, captchaToken, deviceId) {
    return makeRequest({
        method: 'POST',
        baseUrl: DRIVE_BASE_URL,
        path: '/drive/v1/share/restore',
        headers: {
            'authorization': 'Bearer ' + accessToken,
            'x-captcha-token': captchaToken,
            'x-device-id': deviceId,
            'x-user-id': userId,
            'Referer': 'https://mypikpak.com/',
        },
        body: {
            share_id: 'VOsUJsdSUTA42SDTkvJEwqSDo2',
            pass_code_token: 'VzjUMqF+wUWvUjYbIKpfEz8tBpEdY/169l4lOq7jmbMdT4wFKA3qhc56XVaJkwoM02mBCNy0yhugnq9jUb9ZdSNw9hDl0fOt5xzDJcUCkhs=',
            params: { trace_file_ids: 'VOsUJkelWqOdIgEu6ZmmY1gJo2' },
        },
    });
}

async function sendVerificationWithRetry(deviceId, captchaToken, email, locale, maxRetries = 3) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        console.log('  发送验证码 (尝试 ' + attempt + '/' + maxRetries + ')...');
        const resp = await sendVerification(deviceId, captchaToken, email, locale);
        console.log('  响应: status=' + resp.statusCode + ', data=' + JSON.stringify(resp.data).substring(0, 300));
        if (resp.statusCode === 200 && resp.data && resp.data.verification_id) {
            return resp;
        }
        // 频率限制: 等待更长时间
        if (resp.data && (String(resp.data).includes('too frequent') || String(resp.data).includes('try again later'))) {
            const delay = 10000 + attempt * 5000; // 10s, 15s, 20s
            console.log('  频率限制，' + delay / 1000 + '秒后重试...');
            await sleep(delay);
            continue;
        }
        if (attempt < maxRetries) {
            const delay = 3000 * attempt;
            console.log('  发送失败，' + delay / 1000 + '秒后重试...');
            await sleep(delay);
        }
    }
    throw new Error('发送验证码失败，已重试' + maxRetries + '次');
}

function appendResult(email, password, signupData) {
    const line = email + ' | ' + password + ' | ' + new Date().toISOString() + '\n';
    fs.appendFileSync(RESULT_FILE, line);
    console.log('\n  结果已保存到: ' + RESULT_FILE);
}

// ==================== 主注册流程 ====================

async function runBatchRound(round) {
    console.log('\n' + '='.repeat(50));
    console.log('第 ' + round + ' 轮注册 (协议版)');
    console.log('='.repeat(50));

    const deviceId = generateDeviceId();
    const locale = randomItem(LOCALES);
    const password = randomPassword();

    console.log('device_id   :', deviceId);
    console.log('locale      :', locale);

    // Step 0: 创建临时邮箱
    console.log('\nStep 0: 创建临时邮箱...');
    const mailAccount = await createMailAccount();
    console.log('  邮箱: ' + mailAccount.email);

    // Step 1: 获取初始 captcha_token
    console.log('\nStep 1: 获取初始 captcha_token...');
    const initResp = await getInitialCaptchaToken(deviceId, locale);
    if (initResp.statusCode !== 200 || !initResp.data || !initResp.data.captcha_token) {
        console.log('  失败: ' + JSON.stringify(initResp.data));
        if (isRateLimited(initResp.data)) throw new RateLimitError(initResp.data);
        return false;
    }
    const initialToken = initResp.data.captcha_token;
    console.log('  成功');

    // Step 2: 请求人机验证
    console.log('\nStep 2: 请求人机验证...');
    const captchaResp = await initCaptchaToken(deviceId, 'POST:/v1/auth/verification',
        { email: mailAccount.email }, locale, initialToken);

    if (captchaResp.statusCode !== 200 || !captchaResp.data || !captchaResp.data.captcha_token) {
        console.log('  失败: ' + JSON.stringify(captchaResp.data));
        if (isRateLimited(captchaResp.data)) throw new RateLimitError(captchaResp.data);
        return false;
    }

    console.log('  成功');

    // Step 3: ★ 协议版验证码求解 ★
    console.log('\nStep 3: 协议版验证码求解...');
    const captchaResult = await protocolSolveCaptcha();

    if (!captchaResult) {
        console.log('  验证码求解失败');
        return false;
    }

    console.log('  ticket:', captchaResult.ticket.substring(0, 30) + '...');
    console.log('  randstr:', captchaResult.randstr);

    // Step 3.5: 用 ticket/randstr 兑换 PikPak JWT token
    console.log('\nStep 3.5: 兑换 captcha_token...');
    const step2Jwt = captchaResp.data.captcha_token;
    const captchaToken = await exchangeTicketForJWT(deviceId, captchaResult.ticket, captchaResult.randstr, step2Jwt, locale);
    console.log('  captcha_token:', captchaToken.substring(0, 30) + '...');

    // Step 4: 发送验证码
    console.log('\nStep 4: 发送验证码到 ' + mailAccount.email + '...');
    let verifyResp;
    try {
        verifyResp = await sendVerificationWithRetry(deviceId, captchaToken, mailAccount.email, locale);
    } catch (e) {
        console.log('  ' + e.message);
        return false;
    }

    const verificationId = verifyResp.data.verification_id;
    console.log('  已发送，等待收信...');

    // Step 5: 轮询验证码
    console.log('\nStep 5: 轮询验证码');
    let code;
    try {
        code = await fetchVerificationCode(mailAccount.sidToken);
    } catch (e) {
        console.log('\n  ' + e.message);
        return false;
    }
    console.log('\n  验证码: ' + code + ' ✓');

    // Step 6: 注册账号
    console.log('\nStep 6: 注册账号...');
    const verifyResult = await verifyCode(deviceId, verificationId, code);

    if (verifyResult.statusCode !== 200 || !verifyResult.data.verification_token) {
        console.log('  验证码错误: ' + JSON.stringify(verifyResult.data));
        return false;
    }

    const signupResp = await signup(deviceId, mailAccount.email, code,
        verifyResult.data.verification_token, password);

    if (signupResp.statusCode !== 200) {
        console.log('  注册失败: ' + JSON.stringify(signupResp.data));
        return false;
    }

    console.log('  注册成功 ✓');

    const accessToken = signupResp.data.access_token || signupResp.data.token || '';
    const userId = signupResp.data.sub || '';

    if (accessToken && userId) {
        console.log('\nStep 7: 绑定邀请码...');
        try {
            const bindResp = await bindInvite(accessToken, userId, captchaToken, deviceId);
            console.log('  绑定结果: ' + (bindResp.statusCode === 200 ? '成功 ✓' : JSON.stringify(bindResp.data)));
        } catch (e) {
            console.log('  绑定出错: ' + e.message);
        }
    }

    appendResult(mailAccount.email, password, signupResp.data);
    console.log('\n  账号: ' + mailAccount.email);
    console.log('  密码: ' + password);

    return true;
}

// ==================== 主循环 ====================

async function main() {
    console.log('========================================');
    console.log('  PikPak 批量注册工具 (协议版)');
    console.log('========================================');
    console.log('  纯协议: 无需浏览器，纯 HTTP 请求');
    console.log('  流程: 创建邮箱 / 协议验证码 / 查验证码 / 注册 / 绑定');
    console.log('  间隔: 每 ' + DELAY_MINUTES + ' 分钟注册一个');
    console.log('  device_token: ' + DEVICE_TOKEN.substring(0, 20) + '...');
    console.log('  v8_submit.js: ' + V8_SUBMIT_JS);
    console.log('  protocol_build: ' + PROTOCOL_BUILD);
    console.log('========================================\n');

    // 检查 v8_submit.js 是否存在
    if (!fs.existsSync(V8_SUBMIT_JS)) {
        console.error('错误: v8_submit.js 不存在: ' + V8_SUBMIT_JS);
        console.error('请确保 tianyu_captcha/js/v8_submit.js 文件存在');
        process.exit(1);
    }

    // 检查 Node.js 是否可用
    try {
        const nodeVer = execSync(NODE_PATH + ' --version', { encoding: 'utf-8' }).trim();
        console.log('Node.js 版本:', nodeVer);
    } catch (e) {
        console.error('错误: Node.js 不可用，请安装 Node.js >= 16');
        process.exit(1);
    }

    let round = 0;
    let successCount = 0;
    let failCount = 0;

    try {
        while (true) {
            round++;
            const ok = await runBatchRound(round);
            if (ok) successCount++;
            else failCount++;

            console.log('\n累计: 成功 ' + successCount + ' 个, 失败 ' + failCount + ' 个');
            console.log('下一轮将在 ' + DELAY_MINUTES + ' 分钟后开始...');
            await sleep(DELAY_MINUTES * 60 * 1000);
        }
    } catch (e) {
        if (e instanceof RateLimitError) {
            console.error('触发服务端频率限制，已停止全部后续轮次。');
            console.error('服务端响应:', JSON.stringify(e.data));
        } else {
            console.error('运行出错:', e.message);
        }
    }

    console.log('\n========================================');
    console.log('  批量注册结束');
    console.log('  成功: ' + successCount + ' 个');
    console.log('  失败: ' + failCount + ' 个');
    console.log('  结果文件: ' + RESULT_FILE);
    console.log('========================================');
}

main().catch(console.error);