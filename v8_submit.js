/**
 * Tencent Tianyu V2 Captcha - V8 Sandbox TDC Runner
 * 
 * Downloads and executes tdc.js in a V8 sandbox, then solves PoW.
 * 
 * Usage: node v8_submit.js <input_json_path>
 * 
 * Input JSON format:
 * {
 *   "sess": "...",
 *   "sid": "...",
 *   "subcapclass": "...",
 *   "tdc_path": "/tdc.js?app_data=...",
 *   "pow_cfg": {"prefix": "...", "md5": "..."},
 *   "ans": "..."  (optional)
 * }
 * 
 * Output (stdout): JSON with {collect, info, tokenid} or {error}
 */

const https = require('https');
const http = require('http');
const vm = require('vm');
const fs = require('fs');
const zlib = require('zlib');
const crypto = require('crypto');

var nodeVersion = process.versions.node.split('.').map(Number);
if (nodeVersion[0] < 12) {
  console.error(JSON.stringify({ error: '需要 Node.js 12+，当前版本: ' + process.version }));
  process.exit(1);
}

const proxyUrl = process.env.SOCKS5_PROXY || '';
let proxyAgent = null;
if (proxyUrl) {
  try {
    const { SocksProxyAgent } = require('socks-proxy-agent');
    proxyAgent = new SocksProxyAgent(proxyUrl);
  } catch (e) {
    console.error('SOCKS5_PROXY init failed:', e.message);
    console.error('请运行: npm install');
  }
}

const inputPath = process.argv[2];
if (!inputPath) {
  console.error('Usage: node v8_submit.js <input_json_path>');
  process.exit(1);
}

if (!fs.existsSync(inputPath)) {
  console.error(JSON.stringify({ error: '输入文件不存在: ' + inputPath }));
  process.exit(1);
}

const input = JSON.parse(fs.readFileSync(inputPath, 'utf-8'));

// ============ HTTP Utility ============
function httpGet(url, referer, timeoutMs) {
  timeoutMs = timeoutMs || 15000;
  return new Promise(function(resolve, reject) {
    var timer = setTimeout(function() {
      req && req.destroy();
      reject(new Error('HTTP timeout after ' + timeoutMs + 'ms: ' + url));
    }, timeoutMs);
    var req;
    try {
      const parsed = new URL(url);
      const client = parsed.protocol === 'https:' ? https : http;
      const reqOptions = {
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname + parsed.search,
        method: 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
          'Accept': '*/*',
          'Accept-Encoding': 'gzip, deflate',
          'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
          'Referer': referer || 'https://user.mypikpak.com/',
          'Sec-Fetch-Dest': 'script',
          'Sec-Fetch-Mode': 'no-cors',
          'Sec-Fetch-Site': 'cross-site',
          'Cache-Control': 'no-cache',
        },
      };
      if (proxyAgent) {
        reqOptions.agent = proxyAgent;
      }

      req = client.request(reqOptions, (res) => {
        clearTimeout(timer);
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          let buf = Buffer.concat(chunks);
          const enc = res.headers['content-encoding'];
          if (enc === 'gzip') {
            zlib.gunzip(buf, (err, d) => err ? reject(err) : resolve(d.toString('utf-8')));
          } else if (enc === 'deflate') {
            zlib.inflate(buf, (err, d) => err ? reject(err) : resolve(d.toString('utf-8')));
          } else {
            resolve(buf.toString('utf-8'));
          }
        });
      });
      req.on('error', function(err) {
        clearTimeout(timer);
        reject(err);
      });
      req.end();
    } catch (e) {
      clearTimeout(timer);
      reject(e);
    }
  });
}

// ============ V8 Mock Window ============
function createMockWindow() {
  var _chromeBuild = 130 + Math.floor(Math.random() * 20);
  var _localeSet = ['en-US', 'zh-CN', 'ja-JP', 'ko-KR'];
  var _lang = _localeSet[Math.floor(Math.random() * _localeSet.length)];
  var _hwConcurrency = [4, 6, 8, 12, 16][Math.floor(Math.random() * 5)];
  var _deviceMem = [4, 8, 16, 32][Math.floor(Math.random() * 4)];
  var _canvasFpSeed = Date.now() + Math.random();
  var mockNavigator = {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + _chromeBuild + '.0.0.0 Safari/537.36',
    platform: 'Win32', maxTouchPoints: 0, language: _lang,
    languages: [_lang, _lang.split('-')[0], 'en-US', 'en'], hardwareConcurrency: _hwConcurrency,
    cookieEnabled: true, doNotTrack: null,
    webdriver: false, vendor: 'Google Inc.',
    appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + _chromeBuild + '.0.0.0 Safari/537.36',
    onLine: true,
    plugins: [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ],
    mimeTypes: [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
      { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ],
    getBattery: function() { return Promise.resolve({ charging: true, level: 0.85 + Math.random() * 0.15 }); },
    sendBeacon: function() { return true; },
    connection: { effectiveType: '4g', rtt: 20 + Math.floor(Math.random() * 60), downlink: 5 + Math.random() * 15 },
    deviceMemory: _deviceMem,
    javaEnabled: function() { return false; },
  };
  var _screenResolutions = [[1366, 768], [1440, 900], [1536, 864], [1600, 900], [1680, 1050], [1920, 1080], [2560, 1440]];
  var _resIdx = Math.floor(Math.random() * _screenResolutions.length);
  var _screenW = _screenResolutions[_resIdx][0];
  var _screenH = _screenResolutions[_resIdx][1];
  var _taskbarH = [30, 40, 48][Math.floor(Math.random() * 3)];
  var mockScreen = {
    width: _screenW, height: _screenH, availWidth: _screenW, availHeight: _screenH - _taskbarH,
    colorDepth: 24, pixelDepth: 24,
    orientation: { type: 'landscape-primary', angle: 0 },
  };
  var mockCtx = {
    fillStyle: '#000', strokeStyle: '#000', font: '10px sans-serif',
    textBaseline: 'alphabetic', textAlign: 'start',
    fillRect: function() {}, fillText: function() {}, strokeText: function() {},
    measureText: function(t) { return { width: t.length * 6 }; },
    getImageData: function(x,y,w,h) {
      var totalPixels = w * h;
      var data = new Uint8ClampedArray(totalPixels * 4);
      var fpHash = crypto.createHash('md5').update(_canvasFpSeed.toString()).digest('hex');
      for (var i = 0; i < totalPixels && i < fpHash.length; i++) {
        var idx = i * 4;
        var val = fpHash.charCodeAt(i % fpHash.length);
        data[idx] = val & 0xff;
        data[idx + 1] = (val * 3) & 0xff;
        data[idx + 2] = (val * 7) & 0xff;
        data[idx + 3] = 255;
      }
      return { data: data, width: w, height: h };
    },
    beginPath: function() {}, moveTo: function() {}, lineTo: function() {}, stroke: function() {},
    arc: function() {}, fill: function() {}, closePath: function() {},
    save: function() {}, restore: function() {}, translate: function() {}, scale: function() {},
    rotate: function() {}, transform: function() {}, setTransform: function() {},
    createLinearGradient: function() { return { addColorStop: function() {} }; },
    createRadialGradient: function() { return { addColorStop: function() {} }; },
    createPattern: function() { return {}; }, drawImage: function() {}, putImageData: function() {},
    clearRect: function() {}, clip: function() {},
    quadraticCurveTo: function() {}, bezierCurveTo: function() {},
    isPointInPath: function() { return false; },
    getLineDash: function() { return []; }, setLineDash: function() {},
    lineDashOffset: 0, globalAlpha: 1, globalCompositeOperation: 'source-over',
    lineWidth: 1, lineCap: 'butt', lineJoin: 'miter', miterLimit: 10,
    shadowBlur: 0, shadowColor: 'rgba(0,0,0,0)', shadowOffsetX: 0, shadowOffsetY: 0,
  };
  var _gpuModels = ['GeForce GTX 1060', 'GeForce GTX 1650', 'GeForce RTX 2060', 'GeForce RTX 3060', 'GeForce GTX 970'];
  var _gpuModel = _gpuModels[Math.floor(Math.random() * _gpuModels.length)];
  var _webglExtensions = [
    'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
    'EXT_float_blend', 'EXT_frag_depth', 'EXT_shader_texture_lod',
    'EXT_texture_compression_bptc', 'EXT_texture_compression_rgtc',
    'EXT_texture_filter_anisotropic', 'EXT_sRGB', 'OES_element_index_uint',
    'OES_fbo_render_mipmap', 'OES_standard_derivatives', 'OES_texture_float',
    'OES_texture_float_linear', 'OES_texture_half_float', 'OES_texture_half_float_linear',
    'OES_vertex_array_object', 'WEBGL_color_buffer_float', 'WEBGL_compressed_texture_s3tc',
    'WEBGL_compressed_texture_s3tc_srgb', 'WEBGL_debug_renderer_info',
    'WEBGL_debug_shaders', 'WEBGL_depth_texture', 'WEBGL_draw_buffers',
    'WEBGL_lose_context', 'WEBGL_multi_draw'
  ];
  var mockWebGL = {
    getParameter: function(pname) {
      var map = {};
      map[0x1B00] = 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
      map[0x1B01] = 'ANGLE (' + _gpuModel + ' Direct3D11 vs_5_0 ps_5_0)';
      map[0x1B02] = 'Google Inc. (NVIDIA)';
      map[0x1B03] = 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
      map[0x0D33] = 16384;
      map[0x0D3A] = [16384, 16384];
      map[0x8872] = 16;
      map[0x8876] = _webglExtensions.length;
      map[0x8824] = 16;
      map[0x0A22] = [1, 1];
      map[0x0A23] = [1, 1023];
      map[0x84FF] = 16;
      return map[pname] !== undefined ? map[pname] : 0;
    },
    getExtension: function(name) {
      if (!_webglExtensions.includes(name)) return null;
      return { MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84FF, TEXTURE_MAX_ANISOTROPY_EXT: 0x84FE, UNMASKED_RENDERER_WEBGL: 0x9246, UNMASKED_VENDOR_WEBGL: 0x9245 };
    },
    getSupportedExtensions: function() { return _webglExtensions; },
    getShaderPrecisionFormat: function(sType, pType) {
      if (sType === 35633 || sType === 35632) {
        if (pType === 35639) return { precision: 127, rangeMin: 127, rangeMax: 127 };
        if (pType === 35641) return { precision: 23, rangeMin: 127, rangeMax: 127 };
      }
      return { precision: 23, rangeMin: 127, rangeMax: 127 };
    },
    createBuffer: function() { return {}; }, bindBuffer: function() {}, bufferData: function() {},
    createProgram: function() { return {}; }, createShader: function() { return {}; },
    shaderSource: function() {}, compileShader: function() {},
    attachShader: function() {}, linkProgram: function() {},
    getProgramParameter: function() { return true; },
    getShaderParameter: function() { return true; },
    getProgramInfoLog: function() { return ''; },
    getShaderInfoLog: function() { return ''; },
    useProgram: function() {}, getAttribLocation: function() { return 0; },
    getUniformLocation: function() { return {}; },
    enableVertexAttribArray: function() {}, vertexAttribPointer: function() {},
    drawArrays: function() {}, clear: function() {}, clearColor: function() {},
    viewport: function() {},
    getContextAttributes: function() { return { alpha: true, antialias: true, depth: true, stencil: false, premultipliedAlpha: true, preserveDrawingBuffer: false }; },
    VERSION: 'WebGL 1.0 (OpenGL ES 2.0 Chromium)',
    RENDERER: 'ANGLE (' + _gpuModel + ' Direct3D11 vs_5_0 ps_5_0)',
    VENDOR: 'Google Inc. (NVIDIA)',
    SHADING_LANGUAGE_VERSION: 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)',
  };
  var _canvasFpHash = crypto.createHash('md5').update(_canvasFpSeed.toString()).digest('hex');
  var mockCanvas = {
    width: 220, height: 30,
    getContext: function(type) {
      if (type === '2d') return mockCtx;
      if (type === 'webgl' || type === 'experimental-webgl') return mockWebGL;
      return null;
    },
    toDataURL: function(mimeType) {
      mimeType = mimeType || 'image/png';
      var body = _canvasFpHash.substring(0, 200);
      return 'data:' + mimeType + ';base64,' + (mimeType === 'image/jpeg' ? '/9j/4AAQSkZJRgABAQEASABIAAD' : 'iVBORw0KGgoAAAANSUhEUg') + body;
    },
    toBlob: function(cb) { cb(null); },
  };
  var mockDocument = {
    cookie: '', referrer: 'https://mypikpak.com/drive/login?redirect=/all', title: 'txCaptcha', hidden: false, visibilityState: 'visible',
    documentElement: { clientWidth: _screenW, clientHeight: _screenH - _taskbarH },
    body: { clientWidth: _screenW, clientHeight: _screenH - _taskbarH, appendChild: function() {}, removeChild: function() {} },
    head: { appendChild: function() {} },
    createElement: function(tag) {
      if (tag === 'canvas') return mockCanvas;
      return { style: {}, appendChild: function() {}, getAttribute: function() { return null; }, setAttribute: function() {} };
    },
    getElementById: function() { return null; },
    getElementsByTagName: function() { return []; },
    querySelector: function() { return null; },
    querySelectorAll: function() { return []; },
    addEventListener: function() {}, removeEventListener: function() {},
    createEvent: function() { return { initEvent: function() {} }; },
    hasFocus: function() { return true; },
  };
  var startTime = Date.now();
  var _navStart = startTime - 2000 - Math.floor(Math.random() * 3000);
  var _dnsTime = 5 + Math.floor(Math.random() * 20);
  var _tcpTime = 20 + Math.floor(Math.random() * 40);
  var _tlsTime = 30 + Math.floor(Math.random() * 50);
  var _ttfbTime = 50 + Math.floor(Math.random() * 100);
  var _downloadTime = 20 + Math.floor(Math.random() * 80);
  var _domParseTime = 100 + Math.floor(Math.random() * 200);
  var _domReadyTime = 50 + Math.floor(Math.random() * 150);
  var mockPerformance = {
    now: function() { return Date.now() - startTime; },
    timing: {
      navigationStart: _navStart,
      fetchStart: _navStart + 1,
      domainLookupStart: _navStart + 2,
      domainLookupEnd: _navStart + 2 + _dnsTime,
      connectStart: _navStart + 2 + _dnsTime,
      connectEnd: _navStart + 2 + _dnsTime + _tcpTime,
      secureConnectionStart: _navStart + 2 + _dnsTime + Math.floor(_tcpTime / 2),
      requestStart: _navStart + 2 + _dnsTime + _tcpTime,
      responseStart: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime,
      responseEnd: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime + _downloadTime,
      domInteractive: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime + _downloadTime + _domParseTime,
      domContentLoadedEventEnd: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime + _downloadTime + _domParseTime + _domReadyTime,
      domComplete: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime + _downloadTime + _domParseTime + _domReadyTime + 50,
      loadEventEnd: _navStart + 2 + _dnsTime + _tcpTime + _ttfbTime + _downloadTime + _domParseTime + _domReadyTime + 60,
      unloadEventStart: 0,
      unloadEventEnd: 0,
      redirectStart: 0,
      redirectEnd: 0,
    },
    getEntriesByType: function(type) {
      if (type === 'navigation') {
        return [{ name: 'https://user.mypikpak.com/captcha/v2/txCaptcha.html', entryType: 'navigation', startTime: 0, duration: _dnsTime + _tcpTime + _ttfbTime + _downloadTime + _domParseTime + _domReadyTime }];
      }
      return [];
    },
    memory: { usedJSHeapSize: 10000000, totalJSHeapSize: 20000000, jsHeapSizeLimit: 40000000 },
  };
  var mockLocation = {
    href: 'https://user.mypikpak.com/captcha/v2/txCaptcha.html',
    protocol: 'https:', host: 'user.mypikpak.com', hostname: 'user.mypikpak.com',
    port: '', pathname: '/captcha/v2/txCaptcha.html', search: '', hash: '',
    origin: 'https://user.mypikpak.com',
  };
  var mockStorage = {
    _data: {},
    getItem: function(k) { return this._data[k] || null; },
    setItem: function(k, v) { this._data[k] = v; },
    removeItem: function(k) { delete this._data[k]; },
    clear: function() { this._data = {}; },
    get length() { return Object.keys(this._data).length; },
    key: function(i) { return Object.keys(this._data)[i] || null; },
  };
  var baseWindow = {
    Object: Object, Array: Array, String: String, Number: Number,
    Boolean: Boolean, Function: Function, Date: Date, Math: Math, RegExp: RegExp,
    Error: Error, TypeError: TypeError, SyntaxError: SyntaxError,
    ReferenceError: ReferenceError, RangeError: RangeError,
    JSON: JSON, Promise: Promise, Symbol: Symbol,
    Map: Map, Set: Set, WeakMap: WeakMap, WeakSet: WeakSet,
    Proxy: Proxy, Reflect: Reflect,
    ArrayBuffer: ArrayBuffer, Uint8Array: Uint8Array, Uint8ClampedArray: Uint8ClampedArray,
    Uint16Array: Uint16Array, Uint32Array: Uint32Array,
    Int8Array: Int8Array, Int16Array: Int16Array, Int32Array: Int32Array,
    Float32Array: Float32Array, Float64Array: Float64Array, DataView: DataView,
    parseInt: parseInt, parseFloat: parseFloat, isNaN: isNaN, isFinite: isFinite,
    encodeURIComponent: encodeURIComponent, decodeURIComponent: decodeURIComponent,
    encodeURI: encodeURI, decodeURI: decodeURI,
    escape: escape, unescape: unescape, btoa: btoa, atob: atob,
    setTimeout: setTimeout, setInterval: setInterval,
    clearTimeout: clearTimeout, clearInterval: clearInterval,
    console: console, undefined: undefined, null: null, NaN: NaN, Infinity: Infinity,
    eval: undefined,
    navigator: mockNavigator, screen: mockScreen, document: mockDocument,
    location: mockLocation, performance: mockPerformance,
    localStorage: Object.assign({}, mockStorage, { _data: {} }),
    sessionStorage: Object.assign({}, mockStorage, { _data: {} }),
    innerWidth: _screenW, innerHeight: _screenH - _taskbarH, outerWidth: _screenW, outerHeight: _screenH,
    devicePixelRatio: [1, 1, 1.25][Math.floor(Math.random() * 3)], screenX: 0, screenY: 0,
    screenLeft: 0, screenTop: 0,
    scrollX: 0, scrollY: 0, pageXOffset: 0, pageYOffset: 0,
    CSS: { escape: function(s) { return s; }, supports: function() { return false; } },
    Image: function() { this.src = ''; this.onload = null; this.onerror = null; this.width = 0; this.height = 0; },
    Blob: function(parts, opts) { this.size = 0; this.type = (opts && opts.type) || ''; this.slice = function() { return this; }; },
    URL: { createObjectURL: function() { return 'blob:null'; }, revokeObjectURL: function() {} },
    getComputedStyle: function() { return { getPropertyValue: function() { return ''; }, fontFamily: 'Arial', fontSize: '16px', color: 'rgb(0,0,0)' }; },
    matchMedia: function() { return { matches: false, media: '', addListener: function() {}, removeListener: function() {} }; },
    requestAnimationFrame: function(cb) { return setTimeout(cb, 16); },
    cancelAnimationFrame: function(id) { clearTimeout(id); },
    crypto: {
      getRandomValues: function(arr) { for (var i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256); return arr; },
      subtle: undefined,
      randomUUID: function() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) { var r = Math.random()*16|0; return (c==='x'?r:r&0x3|0x8).toString(16); }); },
    },
    addEventListener: function() {}, removeEventListener: function() {},
    dispatchEvent: function() {},
    Event: function() { this.type = ''; this.initEvent = function() {}; },
  };

  var handler = {
    get: function(target, prop) { return prop in target ? target[prop] : undefined; },
    has: function() { return true; },
  };
  var windowProxy = new Proxy(baseWindow, handler);
  windowProxy.self = windowProxy;
  windowProxy.window = windowProxy;
  windowProxy.globalThis = windowProxy;
  windowProxy.top = windowProxy;
  windowProxy.parent = windowProxy;
  return windowProxy;
}

// ============ V8 Execute TDC ============
function runTdcInV8(tdcCode, timeoutMs) {
  timeoutMs = timeoutMs || 30000;
  var mockWindow = createMockWindow();
  var sandbox = vm.createContext(mockWindow);
  vm.runInContext(tdcCode, sandbox, { filename: 'tdc.js', timeout: timeoutMs });
  if (!sandbox.TDC) throw new Error('TDC not found after executing tdc.js');
  var info = sandbox.TDC.getInfo();
  var collect = sandbox.TDC.getData(true);
  return { info: info, collect: collect };
}

// ============ PoW Solver ============
function solvePow(prefix, targetMd5) {
  var nonce = 0;
  while (true) {
    var hash = crypto.createHash('md5').update(prefix + nonce).digest('hex');
    if (hash === targetMd5) return { answer: prefix + nonce, calcTime: 0 };
    nonce++;
  }
}

// ============ Main ============
async function main() {
  var tdcUrl = 'https://turing.captcha.qcloud.com' + input.tdc_path;

  var tdcCode = null;
  var lastErr = null;
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      tdcCode = await httpGet(tdcUrl, 'https://user.mypikpak.com/', 15000);
      break;
    } catch (e) {
      lastErr = e;
      if (attempt < 2) {
        await new Promise(function(r) { setTimeout(r, 2000); });
      }
    }
  }
  if (!tdcCode) {
    throw new Error('tdc.js \u4E0B\u8F7D\u5931\u8D25(3\u6B21): ' + (lastErr ? lastErr.message : 'unknown'));
  }

  var tdcResult = runTdcInV8(tdcCode, 30000);

  var output = {
    collect: tdcResult.collect,
    info: tdcResult.info.info,
    tokenid: tdcResult.info.tokenid || '',
  };
  console.log(JSON.stringify(output));
}

main().catch(function(err) {
  console.error(JSON.stringify({ error: err.message || String(err) }));
  process.exit(1);
});