import json
import random
import re
import string
import threading
import time
import urllib.parse

import requests
from urllib3.util.retry import Retry

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'


class ProxyManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._proxies = []
        self._index = 0
        self._gateway = ''
        self._rotate_every = 1
        self._request_count = 0
        self._tls = threading.local()

    def configure(self, proxy_list=None, gateway='', rotate_every=1):
        with self._lock:
            self._proxies = proxy_list or []
            self._gateway = gateway
            self._rotate_every = rotate_every
            self._index = 0
            self._request_count = 0

    @property
    def enabled(self):
        return bool(self._proxies or self._gateway)

    def pin(self):
        with self._lock:
            if self._gateway:
                proxy = {'http': self._gateway, 'https': self._gateway}
            elif self._proxies:
                used = set()
                seen = 0
                while True:
                    candidate = random.choice(self._proxies)
                    seen += 1
                    if candidate not in used or seen >= len(self._proxies):
                        break
                    used.add(candidate)
                proxy = {'http': candidate, 'https': candidate}
            else:
                proxy = None
        self._tls.pinned = proxy
        return proxy

    def unpin(self):
        self._tls.pinned = None

    def force_rotate(self):
        with self._lock:
            if self._gateway:
                proxy = {'http': self._gateway, 'https': self._gateway}
                self._tls.pinned = proxy
                return proxy
            if not self._proxies:
                return None
            self._index = (self._index + 1) % len(self._proxies)
            proxy_url = self._proxies[self._index]
            self._tls.pinned = {'http': proxy_url, 'https': proxy_url}
            return self._tls.pinned

    @property
    def pinned_url(self):
        p = getattr(self._tls, 'pinned', None)
        if p:
            return p.get('http', '')
        return ''

    def get(self):
        pinned = getattr(self._tls, 'pinned', None)
        if pinned is not None:
            return pinned

        with self._lock:
            if self._gateway:
                return {'http': self._gateway, 'https': self._gateway}

            if not self._proxies:
                return None

            if self._rotate_every > 1:
                self._request_count += 1
                if self._request_count % self._rotate_every == 0:
                    self._index = (self._index + 1) % len(self._proxies)
            else:
                self._index = (self._index + 1) % len(self._proxies)

            proxy_url = self._proxies[self._index]
            return {'http': proxy_url, 'https': proxy_url}

    def random(self):
        with self._lock:
            if self._gateway:
                return {'http': self._gateway, 'https': self._gateway}
            if not self._proxies:
                return None
            proxy_url = random.choice(self._proxies)
            return {'http': proxy_url, 'https': proxy_url}


_proxy_manager = ProxyManager()


def configure_proxy(proxy_list=None, gateway='', rotate_every=1):
    _proxy_manager.configure(proxy_list=proxy_list, gateway=gateway, rotate_every=rotate_every)


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
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=retry)
        s.mount('https://', adapter)
        s.mount('http://', adapter)
        _session_tls.session = s
    return s


def make_request(method, base_url, path, headers=None, body=None, timeout=30, use_proxy=True, retries=3):
    url = base_url + path
    request_headers = {
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
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

        try:
            if method == 'GET':
                resp = session.get(url, headers=request_headers, timeout=timeout, proxies=proxies)
            elif method == 'POST':
                resp = session.post(url, headers=request_headers, data=body_bytes, timeout=timeout, proxies=proxies)
            else:
                raise ValueError(f'Unsupported HTTP method: {method}')

            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                data = resp.text

            return {'status_code': resp.status_code, 'data': data}
        except requests.exceptions.SSLError as e:
            last_error = e
            _proxy_manager.force_rotate()
            need_new_session = True
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.ConnectionError as e:
            last_error = e
            err_str = str(e).lower()
            if 'ssl' in err_str or 'wrong_version' in err_str:
                _proxy_manager.force_rotate()
                need_new_session = True
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f'请求失败: {e}')

    raise RuntimeError(f'请求失败(重试{retries}次): {last_error}')


_IP_SERVICES = [
    ('https://api.ipify.org?format=json', lambda r: r.json().get('ip', '')),
    ('https://httpbin.org/ip', lambda r: r.json().get('origin', '').split(',')[0].strip()),
    ('https://api.ip.sb/ip', lambda r: r.text.strip()),
    ('https://ifconfig.me/ip', lambda r: r.text.strip()),
    ('https://icanhazip.com', lambda r: r.text.strip()),
]


def get_current_ip():
    proxies = _proxy_manager.get() if _proxy_manager.enabled else None
    for url, parser in _IP_SERVICES:
        try:
            resp = requests.get(url, headers={'Connection': 'close'},
                               timeout=8, proxies=proxies)
            ip = parser(resp)
            if ip and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                return ip
        except Exception:
            continue
    return '获取失败'


def http_get_raw(url_text, referer=None, timeout=15, use_proxy=False, retries=3):
    request_headers = {
        'User-Agent': USER_AGENT,
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': referer or 'https://user.mypikpak.com/',
    }

    last_error = None
    for attempt in range(retries + 1):
        proxies = _proxy_manager.get() if (use_proxy and _proxy_manager.enabled) else None
        try:
            resp = requests.get(url_text, headers=request_headers, timeout=timeout, proxies=proxies)
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
        except requests.exceptions.SSLError:
            last_error = 'SSL握手失败'
            if use_proxy and _proxy_manager.enabled:
                _proxy_manager.force_rotate()
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.Timeout:
            last_error = '超时'
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.ConnectionError as e:
            last_error = '连接失败'
            err_str = str(e).lower()
            if 'ssl' in err_str or 'wrong_version' in err_str:
                if use_proxy and _proxy_manager.enabled:
                    _proxy_manager.force_rotate()
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f'下载图片失败: {e}')

    raise RuntimeError(f'下载图片失败(重试{retries}次): {last_error}')