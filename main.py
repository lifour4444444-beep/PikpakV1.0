"""
PikPak 全协议注册 + 腾讯天御V2验证码 (Python 版本)

纯 HTTP 请求完成验证码识别与提交，无需浏览器。

架构: Python (主控) + Node.js (V8沙箱子进程)

用法: python main.py
"""

import sys
if sys.version_info < (3, 9):
    print(f"❌ 需要 Python 3.9+，当前版本: {sys.version}")
    sys.exit(1)

import base64
import hashlib
import io
import json
import os
import random
import re
import subprocess
import struct
import tempfile
import threading
import time
import urllib.parse
import uuid

import requests

from lib.utils import (
    RateLimitError,
    calculate_captcha_sign,
    generate_device_id,
    generate_device_sign,
    is_rate_limited,
    random_item,
    random_password,
    solve_pow_single,
    LOCALES,
)
from lib.http_client import make_request, http_get_raw, get_user_agent, refresh_user_agent, get_chrome_version, configure_proxy, get_current_ip, get_proxy_dict, pin_proxy, unpin_proxy, force_rotate_proxy, acquire_proxy, release_proxy
from lib.mail import create_mail_account, fetch_verification_code
import lib.mail

from PIL import Image, ImageDraw
from models.yolov5_detector import YOLOv5
from models.siamese_compare import ONNXSiamese

_stop_event = threading.Event()

_result_lock = threading.Lock()

_on_captcha_image = None

_captcha_init_lock = threading.Lock()
_captcha_init_last_time = 0.0
_CAPTCHA_INIT_MIN_INTERVAL = 5.0


class _GlobalRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._paused.set()
        self._global_rate_limit_count = 0

    def pause_all(self, wait_seconds):
        with self._lock:
            self._global_rate_limit_count += 1
            self._paused.clear()
        return self._global_rate_limit_count

    def resume_all(self):
        with self._lock:
            self._paused.set()

    def wait_if_paused(self, timeout=1):
        return self._paused.wait(timeout=timeout)

    @property
    def is_paused(self):
        return not self._paused.is_set()

    def reset_count(self):
        with self._lock:
            self._global_rate_limit_count = 0


_global_rate_limiter = _GlobalRateLimiter()


def request_stop():
    _stop_event.set()


def _is_stopped():
    return _stop_event.is_set()


def set_captcha_callback(cb):
    global _on_captcha_image
    _on_captcha_image = cb


BASE_URL = 'https://user.mypikpak.com'
DRIVE_BASE_URL = 'https://api-drive.mypikpak.com'
DEFAULT_CLIENT_ID = 'YUMx5nI8ZU8Ap8pm'


def _pikpak_headers(device_id, extra=None):
    sign = generate_device_sign(device_id)

    ua = get_user_agent()
    if 'Windows' in ua:
        os_ver = 'Win32'
        platform = '"Windows"'
        device_name = 'PC-Chrome'
    elif 'Macintosh' in ua:
        os_ver = 'MacIntel'
        platform = '"macOS"'
        device_name = 'Mac-Chrome'
    else:
        os_ver = 'Linux x86_64'
        platform = '"Linux"'
        device_name = 'Linux-Chrome'

    chrome_ver = get_chrome_version()
    h = {
        'x-client-id': DEFAULT_CLIENT_ID,
        'x-protocol-version': '301',
        'x-device-id': device_id,
        'x-device-sign': sign,
        'x-client-version': '1.0.0',
        'x-device-model': f'chrome%2F{chrome_ver}.0.0.0',
        'x-device-name': device_name,
        'x-net-work-type': 'NONE',
        'x-os-version': os_ver,
        'x-platform-version': '1',
        'x-provider-name': 'NONE',
        'x-sdk-version': '8.1.4',
        'sec-ch-ua': f'"Google Chrome";v="{chrome_ver}", "Chromium";v="{chrome_ver}", "Not)A;Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': platform,
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
    }
    if extra:
        h.update(extra)
    return h

PREHANDLE_URL = 'https://ca.turing.captcha.qcloud.com/cap_union_prehandle'
VERIFY_URL = 'https://turing.captcha.qcloud.com/cap_union_new_verify'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
    _DATA_DIR = sys._MEIPASS
else:
    _BASE_DIR = SCRIPT_DIR
    _DATA_DIR = SCRIPT_DIR

RESULT_FILE = os.path.join(_BASE_DIR, 'batch_result_protocol.txt')
os.makedirs(_BASE_DIR, exist_ok=True)
V8_SUBMIT_JS = os.path.join(_DATA_DIR, 'v8_submit.js')

DELAY_MINUTES = 10
POW_MAX_NONCE = 100_000_000
VERBOSE = False
LOG_FILE = os.path.join(_BASE_DIR, 'debug.log')
YOLO_MODEL_PATH = os.path.join(_DATA_DIR, 'YOLO5', 'best.onnx')
SIAMESE_MODEL_PATH = os.path.join(_DATA_DIR, 'Siamese', 'IconCompare.onnx')

PROXY_GATEWAY = ''


def _debug(msg):
    """详细日志写入 debug.log，控制台只在 VERBOSE 模式显示"""
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    if VERBOSE:
        print(f'  {msg}')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


_log_buffer = ''

_worker_tls = threading.local()


def set_worker_id(wid):
    _worker_tls.wid = wid


_STEP_ICONS = {
    'mail': '\u2709', 'captcha_init': '\u26A1', 'captcha_req': '\U0001F6E1',
    'solve': '\U0001F9E9', 'exchange': '\U0001F504', 'send': '\U0001F4E7',
    'wait': '\u23F3', 'verify': '\u2705', 'signup': '\U0001F680', 'invite': '\U0001F517',
    'tdc': '\U0001F916', 'pow': '\u26CF', 'submit': '\U0001F4E4',
    'prehandle': '\U0001F310', 'download': '\U0001F4F7', 'detect': '\U0001F441',
}

import threading
_log_lock = threading.Lock()

def _log(msg, end='\n'):
    global _log_buffer
    with _log_lock:
        wid = getattr(_worker_tls, 'wid', None)
        prefix = f'\u2502W{str(wid).zfill(2)}\u2502 ' if wid is not None else ''
        ts = time.strftime('%H:%M:%S')
        if end == '':
            if _log_buffer:
                _log_buffer += msg
            else:
                _log_buffer = f'\u2502{ts}\u2502 {prefix}{msg}'
        else:
            if _log_buffer:
                print(f'{_log_buffer}{msg}', end='\n')
                _log_buffer = ''
            else:
                print(f'\u2502{ts}\u2502 {prefix}{msg}', end='\n')
        sys.stdout.flush()

def get_image_dimensions(buf):
    if len(buf) < 24:
        return None
    if buf[0:4] == b'\x89PNG':
        return {
            'width': struct.unpack('>I', buf[16:20])[0],
            'height': struct.unpack('>I', buf[20:24])[0],
            'type': 'png',
        }
    if buf[0:2] == b'\xff\xd8':
        i = 2
        while i < len(buf) - 9:
            if buf[i] != 0xff:
                i += 1
                continue
            marker = buf[i + 1]
            if marker in (0xc0, 0xc1, 0xc2):
                return {
                    'width': struct.unpack('>H', buf[i + 7:i + 9])[0],
                    'height': struct.unpack('>H', buf[i + 5:i + 7])[0],
                    'type': 'jpeg',
                }
            seg_len = struct.unpack('>H', buf[i + 2:i + 4])[0]
            i += 2 + seg_len
    if buf[0:4] == b'RIFF' and buf[8:15] == b'WEBPVP8':
        if buf[15] == 0x20:  # VP8
            w = struct.unpack('<H', buf[26:28])[0]
            h = struct.unpack('<H', buf[28:30])[0]
            return {'width': w & 0x3fff, 'height': h & 0x3fff, 'type': 'webp'}
        if buf[15] == 0x4c:  # VP8L
            bits = struct.unpack('<I', buf[21:25])[0]
            return {'width': (bits & 0x3fff) + 1, 'height': ((bits >> 14) & 0x3fff) + 1, 'type': 'webp'}
    if buf[0:3] == b'GIF':
        return {
            'width': struct.unpack('<H', buf[6:8])[0],
            'height': struct.unpack('<H', buf[8:10])[0],
            'type': 'gif',
        }
    if buf[0:2] == b'BM':
        return {
            'width': struct.unpack('<I', buf[18:22])[0],
            'height': abs(struct.unpack('<i', buf[22:26])[0]),
            'type': 'bmp',
        }
    return None


def download_image_as_base64(url, referer=None):
    buf = http_get_raw(url, referer=referer, timeout=15)
    dims = get_image_dimensions(buf)
    if dims is None:
        raise RuntimeError(f'下载的图片数据无效(前{min(50, len(buf))}字节: {buf[:50]!r})')
    ext = dims['type']
    mime_map = {'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif', 'bmp': 'image/bmp'}
    mime = mime_map.get(ext, 'image/png')
    b64 = base64.b64encode(buf).decode('ascii')
    return {
        'base64': b64,
        'data_url': f'data:{mime};base64,{b64}',
        'width': dims['width'] if dims else 0,
        'height': dims['height'] if dims else 0,
        'size': len(buf),
        'type': ext,
    }


def fetch_prehandle(sess='', subsid=1):
    params = {
        'aid': '189981187',
        'protocol': 'https',
        'accver': '1',
        'showtype': 'popup',
        'ua': base64.b64encode(get_user_agent().encode('utf-8')).decode('ascii'),
        'noheader': '1',
        'fb': '0',
        'isJsVersion': '3',
        'aged': '0',
        'enableAged': '0',
        'enableDarkMode': '0',
        'grayscale': '1',
        'clientype': '2',
        'userLanguage': 'zh-cn',
        'cap_cd': '',
        'uid': '',
        'lang': 'zh-cn',
        'entry_url': 'https://user.mypikpak.com/captcha/v2/txCaptcha.html',
        'elder_captcha': '0',
        'js': 'https://global.turing.captcha.gtimg.com/tgJNCap-global.203d0ca0.js',
        'login_appid': '',
        'wb': '2',
        'subsid': str(subsid),
        'sess': sess or '',
    }

    url = PREHANDLE_URL + '?' + urllib.parse.urlencode(params)

    resp = None
    last_error = None
    for attempt in range(4):
        try:
            resp = requests.get(url, headers={
                'User-Agent': get_user_agent(),
                'Referer': 'https://user.mypikpak.com/',
                'Accept': '*/*',
            }, timeout=15, proxies=get_proxy_dict())
            break
        except requests.exceptions.SSLError as e:
            last_error = e
            force_rotate_proxy()
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.ConnectionError as e:
            last_error = e
            err_str = str(e).lower()
            if 'ssl' in err_str or 'wrong_version' in err_str:
                force_rotate_proxy()
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))

    if resp is None:
        raise RuntimeError(f'prehandle失败(重试4次): {last_error}')

    return parse_prehandle_response(resp.text)


def parse_prehandle_response(body):
    body = body.strip()
    json_str = body

    if body.startswith('('):
        json_str = body[1:-1]
    else:
        m = re.match(r'^\w+\((.*)\)$', body, re.DOTALL)
        if not m:
            raise RuntimeError(f'无法解析 JSONP 响应: {body[:200]}')
        json_str = m.group(1)

    raw = json.loads(json_str)
    data = raw.get('data', {})
    dyn_info = data.get('dyn_show_info', {})
    bg_cfg = dyn_info.get('bg_elem_cfg', {})
    comm_cfg = data.get('comm_captcha_cfg', {})

    base_img = 'https://ca.turing.captcha.qcloud.com'
    bg_url = bg_cfg.get('img_url', '')
    sprite_url = dyn_info.get('sprite_url', '')
    tdc_path = comm_cfg.get('tdc_path', '')

    if bg_url and not bg_url.startswith('http'):
        bg_url = base_img + bg_url
    if sprite_url and not sprite_url.startswith('http'):
        sprite_url = base_img + sprite_url

    return {
        'sess': raw.get('sess', ''),
        'sid': str(raw.get('sid', '')),
        'subcapclass': str(raw.get('subcapclass', '')),
        'tdc_path': tdc_path,
        'pow_cfg': comm_cfg.get('pow_cfg', {}),
        'bg_url': bg_url,
        'sprite_url': sprite_url,
        'instruction': dyn_info.get('instruction', ''),
        'ins_elem_cfg': dyn_info.get('ins_elem_cfg', []),
        'bg_size': bg_cfg.get('size_2d', [672, 480]),
        'mark_style': (bg_cfg.get('click_cfg', {}) or {}).get('mark_style', ''),
        'lang': dyn_info.get('lang', ''),
        'raw': raw,
    }


def run_tdc(sess, sid, subcapclass, tdc_path, pow_cfg, ans, max_retries=2):
    input_data = {
        'sess': sess,
        'sid': sid,
        'subcapclass': subcapclass,
        'tdc_path': tdc_path,
        'pow_cfg': pow_cfg,
        'ans': ans or '',
    }

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='tdc_input_')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(input_data, f)

        env = os.environ.copy()
        if PROXY_GATEWAY:
            env['SOCKS5_PROXY'] = PROXY_GATEWAY

        startup_kwargs = {}
        if os.name == 'nt':
            startup_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        last_err = None
        for attempt in range(max_retries + 1):
            try:
                result = subprocess.run(
                    ['node', V8_SUBMIT_JS, tmp_path],
                    capture_output=True, text=True, timeout=60, encoding='utf-8',
                    env=env, **startup_kwargs,
                )

                if result.returncode != 0:
                    stderr = (result.stderr or '').strip()
                    stdout = (result.stdout or '').strip()
                    raise RuntimeError(
                        f'v8_submit.js \u9000\u51FA\u7801: {result.returncode}\n'
                        f'STDOUT: {stdout[-500:]}\n'
                        f'STDERR: {stderr[-500:]}'
                    )

                output = json.loads(result.stdout.strip())
                if output.get('error'):
                    raise RuntimeError(f'TDC \u6267\u884C\u9519\u8BEF: {output["error"]}')

                return {
                    'collect': output.get('collect', ''),
                    'info': output.get('info', ''),
                    'tokenid': output.get('tokenid', ''),
                }
            except subprocess.TimeoutExpired:
                last_err = RuntimeError('TDC \u6267\u884C\u8D85\u65F6(60s)')
                _debug(f'TDC\u8D85\u65F6 \u5C1D\u8BD5{attempt + 1}/{max_retries + 1}')
                if attempt < max_retries:
                    time.sleep(2)
            except json.JSONDecodeError as e:
                last_err = RuntimeError(f'TDC \u8F93\u51FA\u89E3\u6790\u5931\u8D25: {e}')
                if attempt < max_retries:
                    time.sleep(1)
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(2)
        raise last_err
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def submit_verify(sess, sid, subcapclass, collect, eks, ans, pow_answer, pow_calc_time):
    enc = urllib.parse.quote
    body = (
        f'sess={enc(sess, safe="")}&'
        f'sid={enc(sid, safe="")}&'
        f'collect={collect}&'
        f'eks={enc(eks, safe="")}&'
        f'ans={enc(ans, safe="")}&'
        f'pow_answer={enc(pow_answer, safe="")}&'
        f'pow_calc_time={enc(str(pow_calc_time), safe="")}&'
        f'subcapclass={enc(subcapclass, safe="")}&'
        f'tlg={enc(str(len(collect)), safe="")}&'
        f'data={enc("", safe="")}&'
        f'aini={enc("1", safe="")}&'
        f'uid={enc("", safe="")}&'
        f'track={enc("", safe="")}&'
        f'char_c={enc("", safe="")}&'
        f'crypted_char_c={enc("", safe="")}&'
        f'extra_net_req={enc("", safe="")}&'
        f'poll_captcha={enc("", safe="")}&'
        f'webg_p={enc("", safe="")}&'
        f'ele_ans={enc("", safe="")}&'
        f'ans_enc={enc("", safe="")}&'
        f'e_ans={enc("", safe="")}&'
        f'aesKey={enc("", safe="")}&'
        f'crypto={enc("1", safe="")}&'
        f'cap_cd={enc("", safe="")}'
    )

    resp = None
    last_error = None
    for attempt in range(4):
        try:
            resp = requests.post(VERIFY_URL, data=body.encode('utf-8'), headers={
                'User-Agent': get_user_agent(),
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': 'https://user.mypikpak.com/',
                'Origin': 'https://user.mypikpak.com',
            }, timeout=15, proxies=get_proxy_dict())
            break
        except requests.exceptions.SSLError as e:
            last_error = e
            force_rotate_proxy()
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.ConnectionError as e:
            last_error = e
            err_str = str(e).lower()
            if 'ssl' in err_str or 'wrong_version' in err_str:
                force_rotate_proxy()
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))

    if resp is None:
        raise RuntimeError(f'提交验证码失败(重试4次): {last_error}')

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return {
            'success': False, 'error_code': -1,
            'error_message': f'非JSON响应: {resp.text[:200]}',
            'ticket': '', 'randstr': '', 'sess': '', 'raw': resp.text,
        }

    error_code = data.get('errorCode', -1)
    return {
        'success': str(error_code) == '0' and bool(data.get('ticket')),
        'error_code': error_code,
        'error_message': data.get('errorMessage', ''),
        'ticket': data.get('ticket', ''),
        'randstr': data.get('randstr', ''),
        'sess': data.get('sess', ''),
        'raw': data,
    }


def crop_icon(image, box, margin=2):
    x1, y1, x2, y2 = box
    x1 = max(0, x1 - margin)
    y1 = max(0, y1 - margin)
    x2 = min(image.width, x2 + margin)
    y2 = min(image.height, y2 + margin)
    return image.crop((x1, y1, x2, y2))


def local_solve(bg_image, sprite_image, yolo_path, siamese_path):
    detector = YOLOv5(yolo_path, 0.3, 0.3, Resize=(672, 480))
    siamese = ONNXSiamese(siamese_path)

    large_boxes = detector.Detect(bg_image)
    _debug(f'YOLO检测到 {len(large_boxes)} 个图标')

    sw, sh = sprite_image.size
    part_w = sw // 3
    small_boxes = [(i * part_w, 0, (i + 1) * part_w, sh) for i in range(3)]

    all_scores = []
    for i, sbox in enumerate(small_boxes):
        small_icon = crop_icon(sprite_image, sbox)
        for j, lbox in enumerate(large_boxes):
            large_icon = crop_icon(bg_image, lbox)
            si_score = siamese.Compare(small_icon, large_icon)
            all_scores.append((si_score, i, j))

    all_scores.sort(key=lambda x: x[0], reverse=True)
    selected_j = set()
    results = [None] * 3

    for score, i, j in all_scores:
        if results[i] is not None or j in selected_j:
            continue
        results[i] = (large_boxes[j], score)
        selected_j.add(j)
        if len(selected_j) == 3:
            break

    coords = []
    for i in range(3):
        if results[i] is not None:
            box, score = results[i]
            cx = int((box[0] + box[2]) // 2)
            cy = int((box[1] + box[3]) // 2)
            coords.append({'x': cx, 'y': cy})
            _debug(f'目标[{i+1}] 匹配: ({cx}, {cy})  SI={score:.4f}')
        else:
            coords.append(None)
            _debug(f'目标[{i+1}] 未匹配到')

    return coords


def protocol_solve_captcha():
    _log('  \U0001F310 prehandle...', end='')
    _debug('[协议] 开始验证码求解')

    if _is_stopped(): return None

    try:
        prehandle_info = fetch_prehandle()
    except Exception as e:
        _log(f' \u2717 {e}')
        return None

    _debug(f'sess={prehandle_info["sess"][:40]}... sid={prehandle_info["sid"]}')
    _debug(f'subcapclass={prehandle_info["subcapclass"]} bg={prehandle_info["bg_size"]}')
    _debug(f'instruction={prehandle_info["instruction"]}')
    _debug(f'tdc_path={prehandle_info["tdc_path"][:60]}...')
    _debug(f'pow_cfg={json.dumps(prehandle_info["pow_cfg"])}')

    if not prehandle_info['bg_url']:
        _log(' \u2717 无背景图')
        return None

    _log(' \U0001F4F7\u2193\u2193...', end='')
    if _is_stopped(): return None
    try:
        image_data = download_image_as_base64(
            prehandle_info['bg_url'], referer='https://user.mypikpak.com/')
        sprite_data = None
        if prehandle_info['sprite_url']:
            sprite_data = download_image_as_base64(
                prehandle_info['sprite_url'], referer='https://user.mypikpak.com/')
    except Exception as e:
        _log(f' \u2717 {e}')
        return None

    _debug(f'背景图: {image_data["width"]}x{image_data["height"]} '
           f'{image_data["type"]} {image_data["size"]/1024:.1f}KB')
    if sprite_data:
        _debug(f'参考条: {sprite_data["width"]}x{sprite_data["height"]} '
               f'{sprite_data["type"]}')

    if not sprite_data:
        _log(' \u2717 无参考条')
        return None

    _log(' \U0001F441 detect...', end='')
    if _is_stopped(): return None
    bg_image = Image.open(io.BytesIO(base64.b64decode(image_data['base64'])))
    sprite_image = Image.open(io.BytesIO(base64.b64decode(sprite_data['base64'])))

    coords = local_solve(bg_image, sprite_image, YOLO_MODEL_PATH, SIAMESE_MODEL_PATH)
    if not coords or any(c is None for c in coords):
        _log(f' \u2717 ({len(coords) if coords else 0}/3)')
        return None

    _debug(f'模型坐标: {json.dumps(coords)}')

    bg_w, bg_h = prehandle_info['bg_size']
    scale_x = bg_w / 672
    scale_y = bg_h / 480

    mapped_coords = [
        {'x': round(c['x'] * scale_x), 'y': round(c['y'] * scale_y)}
        for c in coords
    ]
    _debug(f'映射坐标: {json.dumps(mapped_coords)}')

    if _on_captcha_image:
        try:
            vis = bg_image.copy().convert('RGBA')
            draw = ImageDraw.Draw(vis)
            colors = ['#ff4444', '#44ff44', '#4488ff']
            labels = ['A', 'B', 'C']
            for i, c in enumerate(mapped_coords):
                r = 18
                x, y = c['x'], c['y']
                color = colors[i]
                draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=3)
                draw.line([x - r - 6, y, x + r + 6, y], fill=color, width=2)
                draw.line([x, y - r - 6, x, y + r + 6], fill=color, width=2)
                draw.text((x + r + 4, y - 8), labels[i], fill=color)
            vis.thumbnail((360, 360), Image.LANCZOS)
            buf = io.BytesIO()
            vis.save(buf, format='PNG')
            _on_captcha_image(buf.getvalue())
        except Exception:
            pass

    invalid = [c for c in mapped_coords if c['x'] < 0 or c['x'] >= bg_w or c['y'] < 0 or c['y'] >= bg_h]
    if len(mapped_coords) != 3 or invalid:
        _log(f' \u2717 坐标异常: {len(mapped_coords)}/3')
        return None

    ans = json.dumps([
        {'elem_id': i + 1, 'type': 'DynAnswerType_POS', 'data': f'{c["x"]},{c["y"]}'}
        for i, c in enumerate(mapped_coords)
    ])

    _log(' \U0001F916 TDC...', end='')
    if _is_stopped(): return None
    try:
        tdc_result = run_tdc(
            prehandle_info['sess'], prehandle_info['sid'],
            prehandle_info['subcapclass'], prehandle_info['tdc_path'],
            prehandle_info['pow_cfg'], ans,
        )
    except Exception as e:
        _log(f' \u2717 {e}')
        return None

    _debug(f'collect={len(tdc_result["collect"])}chars eks={len(tdc_result["info"])}chars')

    pow_cfg = prehandle_info['pow_cfg']
    if not pow_cfg or not pow_cfg.get('prefix') or not pow_cfg.get('md5'):
        _log(' \u2717 无PoW')
        return None

    _log(' \u26CF PoW...', end='')
    if _is_stopped(): return None
    try:
        pow_result = solve_pow_single(pow_cfg['prefix'], pow_cfg['md5'])
    except Exception as e:
        _log(f' \u2717 {e}')
        return None

    _debug(f'PoW nonce={pow_result["nonce"]} {pow_result["calc_time"]}ms')

    _log(' \U0001F4E4 submit...', end='')
    if _is_stopped(): return None
    try:
        verify_result = submit_verify(
            prehandle_info['sess'], prehandle_info['sid'],
            prehandle_info['subcapclass'], tdc_result['collect'],
            tdc_result['info'], ans, pow_result['answer'],
            pow_result['calc_time'],
        )
    except Exception as e:
        _log(f' \u2717 {e}')
        return None

    if not verify_result['success']:
        _log(f' \u2717 code={verify_result["error_code"]} {verify_result["error_message"]}')
        _debug(f'raw={json.dumps(verify_result["raw"])}')
        return None

    _log(' \u2713')
    _log(f'    \u25CF ticket: {verify_result["ticket"][:60]}...')
    _log(f'    \u25CF randstr: {verify_result["randstr"]}')
    _debug(f'验证成功 ticket={verify_result["ticket"][:30]}... randstr={verify_result["randstr"]}')
    return {'ticket': verify_result['ticket'], 'randstr': verify_result['randstr']}


def exchange_ticket_for_jwt(device_id, ticket, randstr, step2_jwt, locale):
    request_id = str(uuid.uuid4())
    sign = base64.b64encode(hashlib.sha1((ticket + randstr).encode('utf-8')).digest()).decode('ascii')

    params = urllib.parse.urlencode({
        'deviceid': device_id,
        'captcha_token': step2_jwt,
        'type': 'txCaptcha',
        'result': '0',
        'data': ticket,
        'rand_str': randstr,
        'request_id': request_id,
        'sign': sign,
    })

    try:
        resp = make_request('GET', BASE_URL, '/credit/v1/report?' + params, headers=_pikpak_headers(device_id, {
            'accept-language': locale,
            'x-captcha-token': step2_jwt,
            'Origin': 'https://mypikpak.com',
            'Referer': 'https://mypikpak.com/',
        }))
        _debug(f'兑换响应: status={resp["status_code"]}')

        if resp['status_code'] == 200 and resp['data'].get('captcha_token') and \
                resp['data']['captcha_token'] != step2_jwt:
            _log(f'    \u25CF JWT_C: {resp["data"]["captcha_token"][:50]}...')
            _debug(f'获取到JWT_C: {resp["data"]["captcha_token"][:30]}...')
            return resp['data']['captcha_token']
    except Exception as e:
        _log(f'    \u2717 \u5151\u6362\u5F02\u5E38: {e}')
        _debug(f'兑换异常: {e}')

    return step2_jwt


def _throttle_captcha_init():
    global _captcha_init_last_time
    with _captcha_init_lock:
        now = time.monotonic()
        wait = _CAPTCHA_INIT_MIN_INTERVAL - (now - _captcha_init_last_time)
        if wait > 0:
            time.sleep(wait)
        _captcha_init_last_time = time.monotonic()


def get_initial_captcha_token(device_id, locale):
    _throttle_captcha_init()
    client_version = '2.0.0'
    package_name = 'mypikpak.com'
    timestamp = str(int(time.time() * 1000))
    captcha_sign = calculate_captcha_sign(
        DEFAULT_CLIENT_ID, client_version, package_name, device_id, timestamp)

    return make_request('POST', BASE_URL, '/v1/shield/captcha/init', headers=_pikpak_headers(device_id, {
        'accept-language': locale,
        'Origin': 'https://mypikpak.com',
        'Referer': 'https://mypikpak.com/',
    }), body={
        'client_id': DEFAULT_CLIENT_ID,
        'action': 'POST:/config/v1/drive',
        'device_id': device_id,
        'meta': {
            'captcha_sign': captcha_sign,
            'client_version': client_version,
            'package_name': package_name,
            'user_id': '',
            'timestamp': timestamp,
        },
    })


def init_captcha_token(device_id, action, meta, locale, captcha_token):
    _throttle_captcha_init()
    body = {
        'client_id': DEFAULT_CLIENT_ID,
        'action': action,
        'device_id': device_id,
        'meta': meta,
    }
    if captcha_token:
        body['captcha_token'] = captcha_token
    return make_request('POST', BASE_URL, '/v1/shield/captcha/init', headers=_pikpak_headers(device_id, {
        'accept-language': locale,
        'Origin': 'https://mypikpak.com',
        'Referer': 'https://mypikpak.com/',
    }), body=body)


def send_verification(device_id, captcha_token, email, locale):
    return make_request('POST', BASE_URL, '/v1/auth/verification', headers=_pikpak_headers(device_id, {
        'accept-language': locale,
        'x-captcha-token': captcha_token,
        'Referer': 'https://mypikpak.com/',
    }), body={
        'email': email,
        'target': 'ANY',
        'usage': 'REGISTER',
        'locale': locale,
        'client_id': DEFAULT_CLIENT_ID,
    })


def send_verification_with_retry(device_id, captcha_token, email, locale, max_retries=3):
    for attempt in range(1, max_retries + 1):
        if _is_stopped():
            raise RuntimeError('用户停止')
        resp = send_verification(device_id, captcha_token, email, locale)
        _debug(f'发送验证码(尝试{attempt}): status={resp["status_code"]}')
        if resp['status_code'] == 200 and resp['data'].get('verification_id'):
            return resp

        data_str = str(resp.get('data', ''))
        if 'too frequent' in data_str.lower() or 'try again later' in data_str.lower():
            delay = 10 + attempt * 5
            _log(f'  \u26A0 \u9891\u7387\u9650\u5236 [/v1/auth/verification], {delay}s\u540E\u91CD\u8BD5...')
            if _is_stopped(): raise RuntimeError('用户停止')
            time.sleep(delay)
            continue

        if attempt < max_retries:
            delay = 3 * attempt
            _log(f'  \u26A0 \u53D1\u9001\u5931\u8D25, {delay}s\u540E\u91CD\u8BD5...')
            if _is_stopped(): raise RuntimeError('用户停止')
            time.sleep(delay)

    raise RuntimeError(f'发送验证码失败，已重试{max_retries}次')


def verify_code_request(device_id, verification_id, verification_code):
    return make_request('POST', BASE_URL, '/v1/auth/verification/verify', headers=_pikpak_headers(device_id, {
        'Referer': 'https://mypikpak.com/',
    }), body={
        'verification_id': verification_id,
        'verification_code': verification_code,
        'client_id': DEFAULT_CLIENT_ID,
    })


def signup(device_id, email, verification_code, verification_token, password):
    return make_request('POST', BASE_URL, '/v1/auth/signup', headers=_pikpak_headers(device_id, {
        'Referer': 'https://mypikpak.com/',
    }), body={
        'email': email,
        'verification_code': verification_code,
        'verification_token': verification_token,
        'password': password,
        'client_id': DEFAULT_CLIENT_ID,
    })


def parse_invite_link(link):
    """从邀请链接解析出 share_id, pass_code_token, trace_file_ids"""
    if not link:
        return None

    link = link.strip()
    if not link:
        return None

    # 提取 share_id 从链接
    # 支持格式:
    # https://mypikpak.com/s/VOvNkbxJh72PORLCNY6BGA37o2
    # https://mypikpak.com/drive/s/VOvNkbxJh72PORLCNY6BGA37o2
    # VOvNkbxJh72PORLCNY6BGA37o2 (只输ID)
    import re
    match = re.search(r'/s/([^/?#]+)', link)
    if match:
        share_id = match.group(1)
    else:
        # 直接就是ID
        if len(link) >= 20 and link.startswith('V'):
            share_id = link
        else:
            raise ValueError('无法提取邀请ID，请输入完整链接或ID')

    # 获取HTML
    url = f'https://mypikpak.com/s/{share_id}'
    last_err = None
    for attempt in range(3):
        try:
            use_proxy = (attempt == 0)
            proxies = get_proxy_dict() if use_proxy else None
            resp = requests.get(url, headers={
                'User-Agent': get_user_agent(),
                'Referer': 'https://mypikpak.com/',
            }, timeout=20, proxies=proxies)
            break
        except requests.exceptions.SSLError as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
        except requests.exceptions.ConnectionError as e:
            last_err = e
            if attempt < 2:
                time.sleep(3)
    else:
        raise RuntimeError(f'邀请链接解析失败(重试3次): {last_err}')
    if resp.status_code != 200:
        raise RuntimeError(f'获取分享页面失败 {resp.status_code}')

    html = resp.text

    # 从NUXT_DATA提取紧凑格式JSON，解析pass_code_token和files
    match = re.search(r'<script[^>]*NUXT_DATA[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        raise RuntimeError('页面结构异常，无法提取邀请信息')

    raw = match.group(1)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f'解析页面JSON失败: {e}')

    # 递归解析紧凑格式
    def resolve(idx):
        if isinstance(idx, int) and 0 <= idx < len(data):
            return data[idx]
        return idx

    # 找到 pass_code_token
    pass_code_token = None
    files = None
    for item in data:
        if isinstance(item, dict) and 'pass_code_token' in item:
            pt_idx = item['pass_code_token']
            pass_code_token = resolve(pt_idx)
            if 'files' in item:
                f_idx = item['files']
                files = resolve(f_idx)
            break

    if not pass_code_token:
        # 再找一遍 - share_data 里
        for i, item in enumerate(data):
            if isinstance(item, dict) and 'pass_code_token' in item:
                pt_idx = item['pass_code_token']
                pass_code_token = resolve(pt_idx)
                if 'files' in item:
                    f_idx = item['files']
                    files = resolve(f_idx)
                break

    if not pass_code_token:
        share_status = None
        for item in data:
            if isinstance(item, dict) and 'share_status' in item:
                s_idx = item['share_status']
                share_status = resolve(s_idx)
                break
        if share_status == 'PROHIBITED':
            if INVITE_PASS_CODE_TOKEN and INVITE_TRACE_FILE_IDS and INVITE_SHARE_ID == share_id:
                print(f'[警告] 分享页被地区限制(PROHIBITED)，使用已保存的邀请信息')
                return {
                    'share_id': share_id,
                    'pass_code_token': INVITE_PASS_CODE_TOKEN,
                    'trace_file_ids': INVITE_TRACE_FILE_IDS,
                    'warning': '分享页被地区限制(PROHIBITED)，使用已保存的邀请信息',
                }
            raise RuntimeError('分享页被地区限制(PROHIBITED)，请开启代理后重试')
        raise RuntimeError('无法找到 pass_code_token')

    # 提取第一个文件 id
    trace_file_ids = None
    if files and isinstance(files, list) and len(files) > 0:
        first_file = files[0]
        if isinstance(first_file, int):
            first_file = resolve(first_file)
        if isinstance(first_file, dict) and 'id' in first_file:
            id_idx = first_file['id']
            trace_file_ids = resolve(id_idx)

    return {
        'share_id': share_id,
        'pass_code_token': pass_code_token,
        'trace_file_ids': trace_file_ids,
    }


INVITE_SHARE_ID = ''
INVITE_PASS_CODE_TOKEN = ''
INVITE_TRACE_FILE_IDS = ''


def bind_invite(access_token, user_id, captcha_token, device_id):
    return make_request('POST', DRIVE_BASE_URL, '/drive/v1/share/restore', headers=_pikpak_headers(device_id, {
        'authorization': f'Bearer {access_token}',
        'x-captcha-token': captcha_token,
        'x-user-id': user_id,
        'Referer': 'https://mypikpak.com/',
    }), body={
        'share_id': INVITE_SHARE_ID,
        'pass_code_token': INVITE_PASS_CODE_TOKEN,
        'params': {'trace_file_ids': INVITE_TRACE_FILE_IDS},
    })


def append_result(email, password, access_token='', user_id='', invite_ok=None):
    reg_time = time.strftime('%Y-%m-%d %H:%M:%S')
    invite_str = '成功' if invite_ok else ('失败' if invite_ok is False else '未绑定')
    line = f'{email} | {password} | {access_token} | {user_id} | {reg_time} | {invite_str}\n'
    with _result_lock:
        with open(RESULT_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
    _debug(f'结果已保存: {RESULT_FILE}')
    return {'email': email, 'password': password, 'access_token': access_token,
            'user_id': user_id, 'reg_time': reg_time, 'invite': invite_str}


def run_batch_round(round_num):
    _debug(f'第 {round_num} 轮注册')

    refresh_user_agent()
    device_id = generate_device_id()
    locale = random_item(LOCALES)
    password = random_password()

    _debug(f'device_id={device_id} locale={locale} ua={get_user_agent()[:40]}...')

    _log('  \u2709 \u521B\u5EFA\u90AE\u7BB1...', end='')
    if _is_stopped(): return False
    mail_account = create_mail_account(force_domain=lib.mail._FORCE_DOMAIN)
    _log(f' \u2713 {mail_account["email"]}')

    time.sleep(random.uniform(1.0, 4.0))

    _log('  \u26A1 \u521D\u59CB\u5316token...', end='')
    if _is_stopped(): return False
    init_resp = get_initial_captcha_token(device_id, locale)

    initial_token = None
    if init_resp['status_code'] == 200 and init_resp['data'].get('captcha_token'):
        initial_token = init_resp['data']['captcha_token']
        _log(' \u2713')
        _log(f'    \u25CF JWT_A: {initial_token[:50]}...')
    elif init_resp['status_code'] == 204:
        _log(' \u2713 (204)')
        _debug(f'\u9996\u6b21init\u8fd4\u56de204\uff0c\u5c06\u4e0d\u5e26captcha_token\u8fdb\u884c\u4e0b\u4e00\u6b65')
    else:
        _debug(f'\u521D\u59CB\u5316token\u5931\u8D25: {json.dumps(init_resp["data"])}')
        if is_rate_limited(init_resp['data']):
            raise RateLimitError(init_resp['data'], endpoint='/v1/shield/captcha/init')
        _log(' \u2717')
        return False

    time.sleep(random.uniform(1.0, 4.0))

    _log('  \U0001F6E1 \u4EBA\u673A\u9A8C\u8BC1...', end='')
    if _is_stopped(): return False
    captcha_resp = init_captcha_token(
        device_id, 'POST:/v1/auth/verification',
        {'email': mail_account['email']}, locale, initial_token)

    if captcha_resp['status_code'] != 200 or not captcha_resp['data'].get('captcha_token'):
        _debug(f'captcha_token失败: {json.dumps(captcha_resp["data"])}')
        if is_rate_limited(captcha_resp['data']):
            raise RateLimitError(captcha_resp['data'], endpoint='/v1/shield/captcha/init(action)')
        _log(' \u2717')
        return False
    _log(' \u2713')
    step2_jwt = captcha_resp['data']['captcha_token']
    _log(f'    \u25CF JWT_B: {step2_jwt[:50]}...')

    captcha_result = None
    for captcha_retry in range(2):
        if _is_stopped(): return False
        captcha_result = protocol_solve_captcha()
        if captcha_result:
            break
        if captcha_retry < 2:
            _log(f'\u21BB \u91CD\u8BD5({captcha_retry + 1}/3)...', end='')
            time.sleep(3)
    if _is_stopped(): return False
    if not captcha_result:
        _log(f'  \u2717 \u9A8C\u8BC1\u7801\u8BC6\u522B\u5931\u8D25')
        return False

    _log('  \U0001F504 \u5151\u6362token...', end='')
    captcha_token = exchange_ticket_for_jwt(
        device_id, captcha_result['ticket'], captcha_result['randstr'],
        step2_jwt, locale)
    _log(' \u2713')
    _log(f'    \u25CF JWT_C: {captcha_token[:50]}...')

    _log('  \U0001F4E7 \u53D1\u9001\u9A8C\u8BC1\u7801...', end='')
    if _is_stopped(): return False
    try:
        verify_resp = send_verification_with_retry(
            device_id, captcha_token, mail_account['email'], locale)
    except RuntimeError as e:
        _log(f' \u2717 {e}')
        return False

    verification_id = verify_resp['data']['verification_id']
    _log(' \u2713')
    _log(f'    \u25CF vid: {verification_id}')

    _log('  \u23F3 \u7B49\u5F85\u9A8C\u8BC1\u7801...', end='')
    if _is_stopped(): return False
    try:
        code = fetch_verification_code(mail_account['email'], mail_account['token'], mail_account.get('base_url'), stop_check=_is_stopped, provider_type=mail_account.get('type'))
    except Exception as e:
        _log(f' \u2717 {e}')
        return False
    _log(f' \u2713 {code}')

    _log('  \u2705 \u6821\u9A8C\u9A8C\u8BC1\u7801...', end='')
    if _is_stopped(): return False
    verify_result = verify_code_request(device_id, verification_id, code)
    if verify_result['status_code'] != 200 or not verify_result['data'].get('verification_token'):
        _debug(f'验证码校验失败: {json.dumps(verify_result["data"])}')
        if is_rate_limited(verify_result['data']):
            raise RateLimitError(verify_result['data'], endpoint='/v1/auth/verification/verify')
        _log(' \u2717')
        return False
    _log(' \u2713')

    _log('  \U0001F680 \u6CE8\u518C\u8D26\u53F7...', end='')
    if _is_stopped(): return False
    signup_resp = signup(
        device_id, mail_account['email'], code,
        verify_result['data']['verification_token'], password)

    if signup_resp['status_code'] != 200:
        _debug(f'注册失败: {json.dumps(signup_resp["data"])}')
        if is_rate_limited(signup_resp['data']):
            raise RateLimitError(signup_resp['data'], endpoint='/v1/auth/signup')
        _log(' \u2717')
        return False
    _log(' \u2713')

    access_token = signup_resp['data'].get('access_token') or signup_resp['data'].get('token', '')
    user_id = signup_resp['data'].get('sub', '')
    _log(f'    \u25CF token: {access_token[:50]}...')
    _log(f'    \u25CF uid: {user_id}')

    invite_ok = None
    if access_token and user_id:
        _log('  \U0001F517 \u7ED1\u5B9A\u9080\u8BF7...', end='')
        if _is_stopped(): return False
        try:
            bind_resp = bind_invite(access_token, user_id, captcha_token, device_id)
            if bind_resp["status_code"] == 200:
                invite_ok = True
                _log(' \u2713')
            else:
                invite_ok = False
                _log(' \u2717')
            _debug(f'绑定结果: {"成功" if invite_ok else json.dumps(bind_resp["data"])}')
        except Exception as e:
            invite_ok = False
            _debug(f'绑定异常: {e}')
            _log(' ✗')

    acct = append_result(mail_account['email'], password, access_token, user_id, invite_ok)
    _log(f'  \u2705 \u6CE8\u518C\u6210\u529F \u2192 {mail_account["email"]}')
    _log(f'     \U0001F511 {password}')

    return acct


def main():
    _log('\u2550' * 40)
    _log('\u25B6 PikPak \u6279\u91CF\u6CE8\u518C\u673A')
    _log(f'\u2502 \u95F4\u9694: {DELAY_MINUTES}min \u2502 \u6A21\u578B: YOLO+Siamese \u2502 \u9A8C\u8BC1\u7801: \u534F\u8BAE')
    if PROXY_GATEWAY:
        _log(f'\u2502 \u4EE3\u7406: \u7F51\u5173 ({PROXY_GATEWAY[:50]}...)')
    else:
        _log('\u2502 \u4EE3\u7406: \u76F4\u8FDE')
    _log(f'\u2502 \u65E5\u5FD7: {LOG_FILE}')
    _log('\u2550' * 40)

    if not os.path.exists(V8_SUBMIT_JS):
        _log(f'\u2717 v8_submit.js \u4E0D\u5B58\u5728: {V8_SUBMIT_JS}')
        sys.exit(1)

    try:
        node_ver = subprocess.run(
            ['node', '--version'], capture_output=True, text=True, timeout=5)
        _log(f'\u2502 Node.js {node_ver.stdout.strip()}')
        major = int(node_ver.stdout.strip().lstrip('v').split('.')[0])
        if major < 12:
            _log('\u2717 Node.js \u7248\u672C\u8FC7\u4F4E\uFF0C\u9700\u8981 v12+')
            sys.exit(1)
    except FileNotFoundError:
        _log('\u2717 Node.js \u672A\u5B89\u88C5\uFF0C\u8BF7\u5B89\u88C5 https://nodejs.org/')
        sys.exit(1)
    except Exception:
        _log('\u2717 Node.js \u4E0D\u53EF\u7528')
        sys.exit(1)

    node_modules = os.path.join(_BASE_DIR, 'node_modules')
    socks_agent = os.path.join(node_modules, 'socks-proxy-agent')
    if not os.path.exists(socks_agent):
        _log('\u2717 Node.js \u4F9D\u8D56\u672A\u5B89\u88C5\uFF0C\u8BF7\u8FD0\u884C: npm install')
        sys.exit(1)

    configure_proxy(gateway=PROXY_GATEWAY)

    round_num = 0
    success_count = 0
    fail_count = 0
    rate_limit_count = 0

    try:
        while True:
            round_num += 1
            try:
                ok = run_batch_round(round_num)
            except RateLimitError as e:
                rate_limit_count += 1
                _log(f'\u26D4 \u89E6\u53D1\u9891\u7387\u9650\u5236 [{e.endpoint}] ({rate_limit_count}/3)\u2192\u91CD\u8BD5')
                _debug(f'频率限制响应: {json.dumps(e.data)}')
                unpin_proxy()
                force_rotate_proxy()
                pin_proxy()
                if rate_limit_count >= 3:
                    _log('\u26A0 \u9891\u7387\u9650\u5236\u91CD\u8BD53\u6B21\u65E0\u6548\uFF0C\u8DF3\u8FC7\u672C\u8F6E')
                    fail_count += 1
                    rate_limit_count = 0
                    ok = False
                    continue
                for retry_i in range(2):
                    try:
                        ok = run_batch_round(round_num)
                        rate_limit_count = 0
                        break
                    except RateLimitError as e2:
                        rate_limit_count += 1
                        _log(f'\u26D4 \u89E6\u53D1\u9891\u7387\u9650\u5236 [{e2.endpoint}] ({rate_limit_count}/3)\u2192\u91CD\u8BD5')
                        _debug(f'频率限制响应: {json.dumps(e2.data)}')
                        unpin_proxy()
                        force_rotate_proxy()
                        pin_proxy()
                        if rate_limit_count >= 3:
                            _log('\u26A0 \u9891\u7387\u9650\u5236\u91CD\u8BD53\u6B21\u65E0\u6548\uFF0C\u8DF3\u8FC7\u672C\u8F6E')
                            fail_count += 1
                            rate_limit_count = 0
                            ok = False
                            break
                else:
                    ok = False
                    continue
                continue
            rate_limit_count = 0
            if ok:
                success_count += 1
            else:
                fail_count += 1

            _log(f'\u2502 \u2191{success_count}\u2713 \u2193{fail_count}\u2717 \u2502 \u4E0B\u4E00\u8F6E {DELAY_MINUTES}min\u540E')
            time.sleep(DELAY_MINUTES * 60)
    except KeyboardInterrupt:
        _log('\n\u23F9 \u7528\u6237\u4E2D\u65AD')
    except Exception as e:
        _log(f'\u2757 {e}')

    _log('\u2550' * 40)
    _log(f'\u25A0 \u7ED3\u675F \u2502 \u2713{success_count} \u2717{fail_count}')
    _log(f'\u25B6 {RESULT_FILE}')


if __name__ == '__main__':
    main()