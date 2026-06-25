import json
import random
import re
import threading
import time

import requests
from urllib3.util.retry import Retry

def random_user_agent():
    major = random.randint(100, 149)
    build = random.randint(5000, 7500)
    patch = random.randint(10, 200)
    chrome_ver = f'{major}.0.{build}.{patch}'
    platform = random.choice([
        f'Windows NT 10.0; {"Win64; x64" if random.random() > 0.3 else "WOW64"}',
        f'Macintosh; Intel Mac OS X 10_{random.randint(13, 15)}_{random.randint(0, 7)}',
        'X11; Linux x86_64',
    ])
    return f'Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36'


_ua_tls = threading.local()


def refresh_user_agent():
    _ua_tls.ua = random_user_agent()


def get_user_agent():
    ua = getattr(_ua_tls, 'ua', None)
    if not ua:
        ua = random_user_agent()
        _ua_tls.ua = ua
    return ua


def get_chrome_version():
    ua = get_user_agent()
    m = re.search(r'Chrome/(\d+)\.', ua)
    return m.group(1) if m else '149'


class ProxyManager:
    _GATEWAY_REGIONS = [
        'SG', 'US', 'JP', 'KR', 'DE', 'GB', 'FR', 'CA', 'AU', 'NL',
        'HK', 'TW', 'IN', 'BR', 'IT', 'ES', 'SE', 'CH', 'NO', 'FI',
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._gateway = ''
        self._gateway_original = ''
        self._tls = threading.local()

    def configure(self, gateway='', rotate_every=1):
        with self._lock:
            if gateway.startswith('socks5://') and not gateway.startswith('socks5h://'):
                gateway = 'socks5h://' + gateway[len('socks5://'):]
            self._gateway = gateway
            self._gateway_original = gateway

    @property
    def enabled(self):
        return bool(self._gateway)

    def pin(self):
        with self._lock:
            if self._gateway:
                proxy = {'http': self._gateway, 'https': self._gateway}
            else:
                proxy = None
        self._tls.pinned = proxy
        return proxy

    def unpin(self):
        self._tls.pinned = None

    def force_rotate(self):
        with self._lock:
            if not self._gateway:
                return None
            new_region = random.choice(self._GATEWAY_REGIONS)
            rotated = re.sub(
                r'region-[A-Z]{2}',
                f'region-{new_region}',
                self._gateway_original,
            )
            self._gateway = rotated
            proxy = {'http': rotated, 'https': rotated}
            self._tls.pinned = proxy
            return proxy

    @property
    def pinned_url(self):
        p = getattr(self._tls, 'pinned', None)
        if p:
            return p.get('http', '')
        return ''

    def get(self):
        pinned = getattr(self._tls, 'pinned', None)
        if pinned is not None:
            with self._lock:
                if self._gateway:
                    current_gw = {'http': self._gateway, 'https': self._gateway}
                    if pinned.get('http') != current_gw.get('http'):
                        self._tls.pinned = current_gw
                        return current_gw
            return pinned

        with self._lock:
            if self._gateway:
                return {'http': self._gateway, 'https': self._gateway}
            return None


_proxy_manager = ProxyManager()

_proxy_cooldown_until = 0.0
_proxy_cooldown_lock = threading.Lock()
_proxy_semaphore = threading.Semaphore(3)


def configure_proxy(gateway=''):
    _proxy_manager.configure(gateway=gateway)


def get_proxy_dict():
    return _proxy_manager.get() if _proxy_manager.enabled else None


def pin_proxy():
    result = _proxy_manager.pin()
    _close_session()
    return result


def unpin_proxy():
    _proxy_manager.unpin()
    _close_session()


def force_rotate_proxy():
    result = _proxy_manager.force_rotate()
    _close_session()
    return result


def _wait_proxy_cooldown():
    global _proxy_cooldown_until
    while True:
        with _proxy_cooldown_lock:
            remaining = _proxy_cooldown_until - time.monotonic()
            if remaining <= 0:
                return
        time.sleep(min(remaining, 1.0))


def _trigger_proxy_cooldown(seconds):
    global _proxy_cooldown_until
    with _proxy_cooldown_lock:
        new_until = time.monotonic() + seconds
        _proxy_cooldown_until = max(_proxy_cooldown_until, new_until)


_session_tls = threading.local()


def _close_session():
    s = getattr(_session_tls, 'session', None)
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
        _session_tls.session = None


def _get_session():
    s = getattr(_session_tls, 'session', None)
    if s is None:
        s = requests.Session()
        retry = Retry(total=0, read=False, connect=False, status=False, backoff_factor=0)
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=retry)
        s.mount('https://', adapter)
        s.mount('http://', adapter)
        _session_tls.session = s
    return s


def make_request(method, base_url, path, headers=None, body=None, timeout=30, use_proxy=True, retries=3, params=None):
    url = base_url + path
    request_headers = {
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'User-Agent': get_user_agent(),
    }
    if headers:
        request_headers.update(headers)

    if body is not None:
        body_bytes = json.dumps(body).encode('utf-8')
        request_headers['Content-Length'] = str(len(body_bytes))
    else:
        body_bytes = None

    last_error = None
    need_new_session = False
    session = _get_session()
    for attempt in range(retries + 1):
        if need_new_session:
            _close_session()
            session = _get_session()
            need_new_session = False

        proxies = _proxy_manager.get() if (use_proxy and _proxy_manager.enabled) else None

        if use_proxy and _proxy_manager.enabled:
            _wait_proxy_cooldown()
            _proxy_semaphore.acquire()
            try:
                resp = _do_request(session, method, url, request_headers, body_bytes, timeout, proxies, params)
            except requests.exceptions.SSLError as e:
                last_error = e
                _trigger_proxy_cooldown(5 + attempt * 3)
                _proxy_manager.force_rotate()
                need_new_session = True
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError as e:
                last_error = e
                err_str = str(e).lower()
                if 'ssl' in err_str or 'wrong_version' in err_str:
                    _trigger_proxy_cooldown(5 + attempt * 3)
                    _proxy_manager.force_rotate()
                    need_new_session = True
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                continue
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f'请求失败: {e}')
            finally:
                _proxy_semaphore.release()
        else:
            try:
                resp = _do_request(session, method, url, request_headers, body_bytes, timeout, proxies, params)
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise RuntimeError(f'请求失败(重试{retries}次): {last_error}')
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f'请求失败: {e}')

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            data = resp.text

        return {'status_code': resp.status_code, 'data': data}

    raise RuntimeError(f'请求失败(重试{retries}次): {last_error}')


def _do_request(session, method, url, headers, body, timeout, proxies, params=None):
    if method == 'GET':
        return session.get(url, headers=headers, timeout=timeout, proxies=proxies, params=params)
    elif method == 'POST':
        return session.post(url, headers=headers, data=body, timeout=timeout, proxies=proxies, params=params)
    else:
        raise ValueError(f'Unsupported HTTP method: {method}')


_IP_SERVICES = [
    ('https://api.ipify.org?format=json', lambda r: r.json().get('ip', '')),
    ('https://httpbin.org/ip', lambda r: r.json().get('origin', '').split(',')[0].strip()),
    ('https://api.ip.sb/ip', lambda r: r.text.strip()),
    ('https://ifconfig.me/ip', lambda r: r.text.strip()),
    ('https://icanhazip.com', lambda r: r.text.strip()),
]


def get_current_ip():
    proxies = _proxy_manager.get() if _proxy_manager.enabled else None
    if _proxy_manager.enabled:
        _wait_proxy_cooldown()
        _proxy_semaphore.acquire()
    try:
        for url, parser in _IP_SERVICES:
            try:
                resp = requests.get(url, headers={'Connection': 'close'},
                                   timeout=8, proxies=proxies)
                ip = parser(resp)
                if ip and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                    return ip
            except Exception:
                continue
    finally:
        if _proxy_manager.enabled:
            _proxy_semaphore.release()
    return '获取失败'


def http_get_raw(url_text, referer=None, timeout=15, use_proxy=False, retries=3):
    request_headers = {
        'User-Agent': get_user_agent(),
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': referer or 'https://user.mypikpak.com/',
    }

    last_error = None
    for attempt in range(retries + 1):
        proxies = _proxy_manager.get() if (use_proxy and _proxy_manager.enabled) else None

        if use_proxy and _proxy_manager.enabled:
            _wait_proxy_cooldown()
            _proxy_semaphore.acquire()
            try:
                resp = requests.get(url_text, headers=request_headers, timeout=timeout, proxies=proxies)
            except requests.exceptions.SSLError:
                last_error = 'SSL握手失败'
                _trigger_proxy_cooldown(5 + attempt * 3)
                _proxy_manager.force_rotate()
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError as e:
                last_error = '连接失败'
                err_str = str(e).lower()
                if 'ssl' in err_str or 'wrong_version' in err_str:
                    _trigger_proxy_cooldown(5 + attempt * 3)
                    _proxy_manager.force_rotate()
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                last_error = '超时'
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                continue
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f'下载图片失败: {e}')
            finally:
                _proxy_semaphore.release()
        else:
            try:
                resp = requests.get(url_text, headers=request_headers, timeout=timeout, proxies=proxies)
            except requests.exceptions.SSLError:
                last_error = 'SSL握手失败'
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError as e:
                last_error = '连接失败'
                if attempt < retries:
                    time.sleep(3.0 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                last_error = '超时'
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                continue
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f'下载图片失败: {e}')

        content = resp.content
        if not content or len(content) < 24:
            last_error = f'响应为空或过小({len(content)}字节)'
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f'下载图片失败: {last_error}')
        ct = resp.headers.get('Content-Type', '')
        if 'image' not in ct and 'octet-stream' not in ct:
            if content[:100].strip().startswith(b'<'):
                last_error = '返回HTML而非图片'
            else:
                last_error = f'非图片响应({ct})'
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f'下载图片失败: {last_error}')
        return content

    raise RuntimeError(f'下载图片失败(重试{retries}次): {last_error}')