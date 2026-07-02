import hashlib
import hmac
import random
import string
import time
import uuid


CAPTCHA_ALGORITHMS = [
    'C9qPpZLN8ucRTaTiUMWYS9cQvWOE',
    '+r6CQVxjzJV6LCV',
    'F',
    'pFJRC',
    '9WXYIDGrwTCz2OiVlgZa90qpECPD6olt',
    '/750aCr4lm/Sly/c',
    'RB+DT/gZCrbV',
    '',
    'CyLsf7hdkIRxRm215hl',
    '7xHvLi2tOYP0Y92b',
    'ZGTXXxu8E/MIWaEDB+Sm/',
    '1UI3',
    'E7fP5Pfijd+7K+t6Tg/NhuLq0eEUVChpJSkrKxpO',
    'ihtqpG6FMt65+Xk+tWUH2',
    'NhXXU9rg4XXdzo7u5o',
]

LOCALES = ['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'ko-KR']
COUNTRY_CODES = ['US', 'JP', 'HK', 'SG', 'TW']

DEVICE_SIGN_SECRET_KEY = 'pikpak_device_sign_2024'


class RateLimitError(Exception):
    def __init__(self, data=None, endpoint=''):
        msg = f'频率限制 [{endpoint}]' if endpoint else 'PikPak 服务端限流'
        super().__init__(msg)
        self.data = data
        self.endpoint = endpoint


def random_item(items):
    return random.choice(items)


def md5(value):
    return hashlib.md5(value.encode('utf-8')).hexdigest()


def sha1(value):
    return hashlib.sha1(value.encode('utf-8')).digest()


def random_string(length, alphabet='abcdefghijklmnopqrstuvwxyz0123456789'):
    return ''.join(random.choice(alphabet) for _ in range(length))


def random_password():
    return 'Pp' + random_string(8) + '@1'


def generate_device_id():
    return uuid.uuid4().hex


def generate_device_sign(device_id, client_id='YUMx5nI8ZU8Ap8pm', client_version='2.0.0', package_name='mypikpak.com'):
    ts = str(int(time.time() * 1000))
    raw = device_id + client_id + client_version + package_name + ts
    sign_body = hmac.new(
        DEVICE_SIGN_SECRET_KEY.encode('utf-8'),
        raw.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'wdi10.{device_id}.{sign_body[:32]}'


def calculate_captcha_sign(client_id, client_version, package_name, device_id, timestamp):
    source = client_id + client_version + package_name + device_id + timestamp
    result = source
    for salt in CAPTCHA_ALGORITHMS:
        result = md5(result + salt)
    return '1.' + result


def is_rate_limited(data):
    if not isinstance(data, dict):
        return False
    message_parts = []
    if data.get('error_description'):
        message_parts.append(str(data['error_description']))
    if isinstance(data.get('details'), list):
        for item in data['details']:
            if isinstance(item, dict):
                message_parts.append(str(item.get('reason', '') or item.get('message', '')))
    message = ' '.join(filter(None, message_parts))
    return (
        data.get('error_code') == 10
        or 'too frequent' in message.lower()
        or '操作过于频繁' in message
        or '操作過於頻繁' in message
    )


def solve_pow_single(prefix, target_md5, max_nonce=100_000_000):
    start_time = time.time()
    for nonce in range(max_nonce):
        candidate = prefix + str(nonce)
        h = hashlib.md5(candidate.encode('utf-8')).hexdigest()
        if h == target_md5:
            elapsed = int((time.time() - start_time) * 1000)
            return {'answer': candidate, 'nonce': nonce, 'calc_time': elapsed}
        if nonce > 0 and nonce % 1_000_000 == 0:
            print(f'  [PoW] 进度: {nonce // 1_000_000}M / {max_nonce // 1_000_000}M')
    elapsed = int((time.time() - start_time) * 1000)
    raise RuntimeError(f'PoW 未找到 (max_nonce={max_nonce}, elapsed={elapsed}ms)')