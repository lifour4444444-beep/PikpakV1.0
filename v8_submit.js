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

const proxyUrl = process.env.SOCKS5_PROXY || '';
let proxyAgent = null;
if (proxyUrl) {
  try {
    const { SocksProxyAgent } = require('socks-proxy-agent');
    proxyAgent = new SocksProxyAgent(proxyUrl);
  } catch (e) {
    console.error('SOCKS5_PROXY init failed:', e.message);
  }
}

const inputPath = process.argv[2];
if (!inputPath) {
  console.error('Usage: node v8_submit.js <input_json_path>');
  process.exit(1);
}

const input = JSON.parse(fs.readFileSync(inputPath, 'utf-8'));

// ============ HTTP Utility ============
function httpGet(url, referer) {
  return new Promise(async (resolve, reject) => {
    try {
      const parsed = new URL(url);
      const client = parsed.protocol === 'https:' ? https : http;
      const reqOptions = {
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname + parsed.search,
        method: 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Accept': '*/*',
          'Accept-Encoding': 'gzip, deflate',
          'Accept-Language': 'zh-CN,zh;q=0.9',
          'Referer': referer || 'https://user.mypikpak.com/',
        },
      };
      if (proxyAgent) {
        reqOptions.agent = proxyAgent;
      }

      const req = client.request(reqOptions, (res) => {
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
      req.on('error', reject);
      req.end();
    } catch (e) {
      reject(e);
    }
  });
}

// ============ V8 Mock Window ============
function createMockWindow() {
  var _chromeBuild = 149 + Math.floor(Math.random() * 3);
  var mockNavigator = {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + _chromeBuild + '.0.0.0 Safari/537.36',
    platform: 'Win32', maxTouchPoints: 0, language: 'zh-CN',
    languages: ['zh-CN', 'zh', 'en'], hardwareConcurrency: 8,
    cookieEnabled: true, doNotTrack: null,
    webdriver: false, vendor: 'Google Inc.',
    appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + _chromeBuild + '.0.0.0 Safari/537.36',
    onLine: true, plugins: { length: 5 }, mimeTypes: { length: 4 },
    getBattery: function() { return Promise.resolve({ charging: true, level: 1 }); },
    sendBeacon: function() { return true; },
    connection: { effectiveType: '4g', rtt: 50 },
    deviceMemory: 8,
  };
  var _screenW = 1920 + Math.floor(Math.random() * 10 - 5);
  var _screenH = 1080 + Math.floor(Math.random() * 10 - 5);
  var mockScreen = {
    width: _screenW, height: _screenH, availWidth: _screenW, availHeight: _screenH - 40,
    colorDepth: 24, pixelDepth: 24,
    orientation: { type: 'landscape-primary', angle: 0 },
  };
  var mockCtx = {
    fillStyle: '#000', strokeStyle: '#000', font: '10px sans-serif',
    textBaseline: 'alphabetic', textAlign: 'start',
    fillRect: function() {}, fillText: function() {}, strokeText: function() {},
    measureText: function(t) { return { width: t.length * 6 }; },
    getImageData: function(x,y,w,h) { return { data: new Uint8Array(w*h*4), width: w, height: h }; },
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
  var mockWebGL = {
    getParameter: function() { return 0; }, getExtension: function() { return null; },
    getSupportedExtensions: function() { return []; },
    getShaderPrecisionFormat: function() { return { precision: 23, rangeMin: 127, rangeMax: 127 }; },
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
    getContextAttributes: function() { return { alpha: true, antialias: true, depth: true, stencil: false }; },
    VERSION: 'WebGL 1.0', RENDERER: 'WebKit WebGL', VENDOR: 'WebKit',
    SHADING_LANGUAGE_VERSION: 'WebGL GLSL ES 1.0',
  };
  var mockCanvas = {
    width: 280, height: 60,
    getContext: function(type) {
      if (type === '2d') return mockCtx;
      if (type === 'webgl' || type === 'experimental-webgl') return mockWebGL;
      return null;
    },
    toDataURL: function() {
      var r = Math.random().toString(36).slice(2, 10);
      return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42m' + r + 'P8/5+hAAAHBAL8B0VFAAAAAElFTkSuQmCC';
    },
    toBlob: function(cb) { cb(null); },
  };
  var mockDocument = {
    cookie: '', referrer: '', title: 'txCaptcha', hidden: false, visibilityState: 'visible',
    documentElement: { clientWidth: 1920, clientHeight: 919 },
    body: { clientWidth: 1920, clientHeight: 919, appendChild: function() {}, removeChild: function() {} },
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
  var mockPerformance = {
    now: function() { return Date.now() - startTime; },
    timing: {
      navigationStart: Date.now() - 1000, loadEventEnd: Date.now() - 500,
      domContentLoadedEventEnd: Date.now() - 600, connectEnd: Date.now() - 900,
      connectStart: Date.now() - 950, domComplete: Date.now() - 500,
      domInteractive: Date.now() - 600, domainLookupEnd: Date.now() - 950,
      domainLookupStart: Date.now() - 980, fetchStart: Date.now() - 980,
      requestStart: Date.now() - 950, responseEnd: Date.now() - 800,
      responseStart: Date.now() - 850, secureConnectionStart: Date.now() - 900,
    },
    getEntriesByType: function() { return []; },
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
    innerWidth: 1920, innerHeight: 919, outerWidth: 1920, outerHeight: 1040,
    devicePixelRatio: 1, screenX: 0, screenY: 0,
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
function runTdcInV8(tdcCode) {
  var mockWindow = createMockWindow();
  var sandbox = vm.createContext(mockWindow);
  vm.runInContext(tdcCode, sandbox, { filename: 'tdc.js' });
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

  var tdcCode = await httpGet(tdcUrl, 'https://user.mypikpak.com/');
  var tdcResult = runTdcInV8(tdcCode);

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