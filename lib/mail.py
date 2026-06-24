import random
import re
import string
import time

from .http_client import make_request

_MAILTM_PROVIDERS = [
    "https://api.mail.tm",
]

_GUERRILLA_URL = "https://api.guerrillamail.com"

_GUERRILLA_DOMAINS = [
    "guerrillamail.com",
    "guerrillamailblock.com",
    "sharklasers.com",
    "guerrillamail.info",
    "guerrillamail.biz",
    "guerrillamail.de",
    "guerrillamail.net",
    "guerrillamail.org",
    "pokemail.net",
    "spam4.me",
]

_TEMPMĀILIO_URL = "https://api.internal.temp-mail.io"
_TEMPMĀILIO_DOMAINS_CACHE = None
_TEMPMĀILIO_DOMAINS_CACHE_TIME = 0

_FORCE_DOMAIN = None

_FALLBACK_DOMAINS = [
    "oakon.com", "teihu.com", "raleigh-construction.com",
    "pastryofistanbul.com", "questtechsystems.com",
]


def _get_tempmailio_domains():
    global _TEMPMĀILIO_DOMAINS_CACHE, _TEMPMĀILIO_DOMAINS_CACHE_TIME
    now = time.time()
    if _TEMPMĀILIO_DOMAINS_CACHE and (now - _TEMPMĀILIO_DOMAINS_CACHE_TIME) < 600:
        return _TEMPMĀILIO_DOMAINS_CACHE
    try:
        resp = make_request("GET", _TEMPMĀILIO_URL, "/api/v2/domains",
                            headers={"Accept": "application/json"},
                            timeout=15, use_proxy=True)
        data = _safe_data(resp)
        domains = data.get("domains", []) if isinstance(data, dict) else []
        _TEMPMĀILIO_DOMAINS_CACHE = domains
        _TEMPMĀILIO_DOMAINS_CACHE_TIME = now
        return domains
    except Exception:
        if _TEMPMĀILIO_DOMAINS_CACHE:
            return _TEMPMĀILIO_DOMAINS_CACHE
        return []


def get_available_domains():
    domains = set()

    for base_url in _MAILTM_PROVIDERS:
        try:
            resp = make_request("GET", base_url, "/domains",
                                headers={"Accept": "application/json"},
                                timeout=15, use_proxy=True)
            data = _safe_data(resp)
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, dict) and d.get("isActive"):
                        domains.add(d["domain"])
        except Exception:
            continue

    domains.update(_GUERRILLA_DOMAINS)
    domains.update(_get_tempmailio_domains())

    if domains:
        return sorted(domains)
    return sorted(_FALLBACK_DOMAINS)


def _get_domains_for_provider(base_url):
    resp = make_request("GET", base_url, "/domains",
                        headers={"Accept": "application/json"},
                        timeout=15, use_proxy=True)
    data = _safe_data(resp)
    if not isinstance(data, list):
        return []
    return [d["domain"] for d in data if isinstance(d, dict) and d.get("isActive")]


def _try_create_mailtm(base_url, local, password, domain):
    email = f"{local}@{domain}"
    resp = make_request("POST", base_url, "/accounts", headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
    }, body={"address": email, "password": password}, timeout=15, use_proxy=True)
    data = _safe_data(resp)
    if isinstance(data, dict) and resp["status_code"] == 201 and data.get("id"):
        token_resp = make_request("POST", base_url, "/token", headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        }, body={"address": email, "password": password}, timeout=15, use_proxy=True)
        token_data = _safe_data(token_resp)
        token = token_data.get("token") if isinstance(token_data, dict) else None
        if not token:
            raise RuntimeError(f"获取token失败: {token_resp['data']}")
        return {"email": email, "token": token, "base_url": base_url, "type": "mailtm"}
    return None


def _try_create_guerrilla():
    import requests
    from .http_client import get_proxy_dict

    proxies = get_proxy_dict()
    resp = requests.get(
        _GUERRILLA_URL + "/ajax.php",
        params={"f": "get_email_address"},
        headers={"Accept": "application/json"},
        timeout=15,
        proxies=proxies,
    )
    data = _safe_json(resp)
    if not isinstance(data, dict):
        raise RuntimeError(f"Guerrilla Mail创建失败: {data}")
    email = data.get("email_addr")
    sid_token = data.get("sid_token")
    if not email or not sid_token:
        raise RuntimeError(f"Guerrilla Mail创建失败: {data}")
    return {"email": email, "token": sid_token, "base_url": _GUERRILLA_URL, "type": "guerrilla"}


def _try_create_tempmailio(local, domain):
    import requests
    from .http_client import get_proxy_dict

    proxies = get_proxy_dict()
    resp = requests.post(
        _TEMPMĀILIO_URL + "/api/v2/email/new",
        json={"local_part": local, "domain": domain},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=15,
        proxies=proxies,
    )
    data = _safe_json(resp)
    if not isinstance(data, dict):
        raise RuntimeError(f"Temp-Mail创建失败: {data}")
    email = data.get("email")
    token = data.get("token")
    if not email or not token:
        raise RuntimeError(f"Temp-Mail创建失败: {data}")
    return {"email": email, "token": token, "base_url": _TEMPMĀILIO_URL, "type": "tempmailio"}


def create_mail_account(force_domain=None):
    local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    password = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    is_guerrilla = force_domain and force_domain in _GUERRILLA_DOMAINS
    is_tempmailio = force_domain and force_domain in _get_tempmailio_domains()

    if force_domain and not is_guerrilla and not is_tempmailio:
        providers = list(_MAILTM_PROVIDERS)
        random.shuffle(providers)
        for base_url in providers:
            try:
                domains = _get_domains_for_provider(base_url)
                if force_domain in domains:
                    result = _try_create_mailtm(base_url, local, password, force_domain)
                    if result:
                        return result
            except Exception:
                continue

    if force_domain and is_guerrilla:
        try:
            return _try_create_guerrilla()
        except Exception:
            pass

    if force_domain and is_tempmailio:
        try:
            return _try_create_tempmailio(local, force_domain)
        except Exception:
            pass

    candidates = []

    for base_url in _MAILTM_PROVIDERS:
        try:
            domains = _get_domains_for_provider(base_url)
            if domains:
                candidates.append(("mailtm", base_url, domains))
        except Exception:
            continue

    candidates.append(("guerrilla", _GUERRILLA_URL, _GUERRILLA_DOMAINS))

    tempmailio_domains = _get_tempmailio_domains()
    if tempmailio_domains:
        candidates.append(("tempmailio", _TEMPMĀILIO_URL, tempmailio_domains))

    random.shuffle(candidates)

    for ptype, base_url, domains in candidates:
        try:
            if ptype == "mailtm":
                domain = random.choice(domains)
                result = _try_create_mailtm(base_url, local, password, domain)
                if result:
                    return result
            elif ptype == "guerrilla":
                return _try_create_guerrilla()
            elif ptype == "tempmailio":
                domain = random.choice(domains)
                return _try_create_tempmailio(local, domain)
        except Exception:
            continue

    raise RuntimeError("所有邮箱服务商均不可用")


def _safe_data(resp):
    data = resp["data"]
    if isinstance(data, str):
        return {}
    if isinstance(data, (dict, list)):
        return data
    return {}


def _safe_json(resp):
    try:
        data = resp.json()
    except Exception:
        return {}
    if isinstance(data, str):
        return {}
    if isinstance(data, (dict, list)):
        return data
    return {}


def _find_code(value):
    if not value:
        return None
    match = re.search(r"\b(\d{6})\b", str(value))
    return match.group(1) if match else None


def _fetch_code_mailtm(token, base_url, stop_check=None):
    last_error = None
    poll_count = 0

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            resp = make_request("GET", base_url, "/messages", headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            }, timeout=15, use_proxy=True)

            messages = _safe_data(resp)
            if isinstance(messages, dict):
                messages = messages.get("hydra:member", [])
            if not isinstance(messages, list):
                messages = []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                subject = msg.get("subject", "")
                intro = msg.get("intro", "")
                summary_code = _find_code(f"{subject} {intro}")
                if summary_code:
                    return summary_code

                try:
                    msg_id = msg.get("id")
                    if not msg_id:
                        continue
                    detail = make_request(
                        "GET", base_url, f"/messages/{msg_id}",
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Bearer {token}",
                        }, timeout=15, use_proxy=True,
                    )
                    detail_data = _safe_data(detail)
                    body = detail_data.get("text", "") or detail_data.get("html", "")
                    body_code = _find_code(body)
                    if body_code:
                        return body_code
                except Exception as e:
                    last_error = e

            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 20:
                raise RuntimeError(f"收取验证码超时: {last_error}")


def _fetch_code_guerrilla(sid_token, stop_check=None):
    import requests
    from .http_client import get_proxy_dict

    last_error = None
    poll_count = 0
    seen_ids = set()

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            proxies = get_proxy_dict()
            resp = requests.get(
                _GUERRILLA_URL + "/ajax.php",
                params={"f": "check_email", "seq": 0, "sid_token": sid_token},
                headers={"Accept": "application/json"},
                timeout=15,
                proxies=proxies,
            )
            data = _safe_json(resp)
            messages = data.get("list", []) if isinstance(data, dict) else []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("mail_id")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                subject = msg.get("mail_subject", "")
                excerpt = msg.get("mail_excerpt", "")
                summary_code = _find_code(f"{subject} {excerpt}")
                if summary_code:
                    return summary_code

                body = msg.get("mail_body", "")
                body_code = _find_code(body)
                if body_code:
                    return body_code

                try:
                    detail_resp = requests.get(
                        _GUERRILLA_URL + "/ajax.php",
                        params={"f": "fetch_email", "email_id": msg_id, "sid_token": sid_token},
                        headers={"Accept": "application/json"},
                        timeout=15,
                        proxies=proxies,
                    )
                    detail = _safe_json(detail_resp)
                    full_body = detail.get("mail_body", "") if isinstance(detail, dict) else ""
                    full_code = _find_code(full_body)
                    if full_code:
                        return full_code
                except Exception as e:
                    last_error = e

            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 20:
                raise RuntimeError(f"收取验证码超时: {last_error}")


def _fetch_code_tempmailio(email, stop_check=None):
    import requests
    from .http_client import get_proxy_dict

    last_error = None
    poll_count = 0
    seen_ids = set()

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            proxies = get_proxy_dict()
            resp = requests.get(
                f"{_TEMPMĀILIO_URL}/api/v2/email/{email}/messages",
                headers={"Accept": "application/json"},
                timeout=15,
                proxies=proxies,
            )
            messages = _safe_json(resp)

            if not isinstance(messages, list):
                messages = []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                subject = msg.get("subject", "")
                body_text = msg.get("body_text", "") or msg.get("body_html", "") or msg.get("body", "")
                code = _find_code(f"{subject} {body_text}")
                if code:
                    return code

            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 20:
                raise RuntimeError(f"收取验证码超时: {last_error}")


def fetch_verification_code(token, base_url=None, stop_check=None, provider_type=None):
    if provider_type == "guerrilla":
        return _fetch_code_guerrilla(token, stop_check=stop_check)

    if provider_type == "tempmailio":
        return _fetch_code_tempmailio(token, stop_check=stop_check)

    if base_url is None:
        base_url = _MAILTM_PROVIDERS[0]
    return _fetch_code_mailtm(token, base_url, stop_check=stop_check)