import random
import re
import string
import time

from .http_client import make_request

_MAILTM_URL = "https://api.mail.tm"

_TEMPMĀILIO_URL = "https://api.internal.temp-mail.io"
_TEMPMĀILIO_DOMAINS_CACHE = None
_TEMPMĀILIO_DOMAINS_CACHE_TIME = 0

_TEMPMĀILLOL_URL = "https://api.tempmail.lol"
_TEMPMĀILLOL_DOMAINS_CACHE = None
_TEMPMĀILLOL_DOMAINS_CACHE_TIME = 0

_FORCE_DOMAIN = None
_BLOCKED_DOMAINS = {"gmeenramy.com"}

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
                            timeout=30, use_proxy=True)
        data = _safe_data(resp)
        domains = data.get("domains", []) if isinstance(data, dict) else []
        _TEMPMĀILIO_DOMAINS_CACHE = domains
        _TEMPMĀILIO_DOMAINS_CACHE_TIME = now
        return domains
    except Exception:
        if _TEMPMĀILIO_DOMAINS_CACHE:
            return _TEMPMĀILIO_DOMAINS_CACHE
        return []


def _get_tempmailol_domains():
    global _TEMPMĀILLOL_DOMAINS_CACHE, _TEMPMĀILLOL_DOMAINS_CACHE_TIME
    now = time.time()
    if _TEMPMĀILLOL_DOMAINS_CACHE and (now - _TEMPMĀILLOL_DOMAINS_CACHE_TIME) < 600:
        return _TEMPMĀILLOL_DOMAINS_CACHE
    try:
        domains = []
        resp = make_request("GET", _TEMPMĀILLOL_URL, "/generate",
                            timeout=15, use_proxy=True)
        data = _safe_data(resp)
        addr = data.get("address", "") if isinstance(data, dict) else ""
        if "@" in addr:
            domain = addr.split("@")[1]
            domains.append(domain)
        _TEMPMĀILLOL_DOMAINS_CACHE = domains
        _TEMPMĀILLOL_DOMAINS_CACHE_TIME = now
        return domains
    except Exception:
        if _TEMPMĀILLOL_DOMAINS_CACHE:
            return _TEMPMĀILLOL_DOMAINS_CACHE
        return []


def _get_mailtm_domains():
    resp = make_request("GET", _MAILTM_URL, "/domains",
                        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                        timeout=15, use_proxy=True)
    data = _safe_data(resp)
    if isinstance(data, list):
        return [d["domain"] for d in data if isinstance(d, dict) and d.get("isActive")]
    return []


def get_available_domains():
    domains = set()

    try:
        resp = make_request("GET", _MAILTM_URL, "/domains",
                            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                            timeout=15, use_proxy=True)
        data = _safe_data(resp)
        if isinstance(data, list):
            for d in data:
                if isinstance(d, dict) and d.get("isActive"):
                    domain = d["domain"]
                    if domain not in _BLOCKED_DOMAINS:
                        domains.add(domain)
    except Exception:
        pass

    try:
        tempmailio_domains = _get_tempmailio_domains()
        for d in tempmailio_domains:
            if d not in _BLOCKED_DOMAINS:
                domains.add(d)
    except Exception:
        pass

    try:
        tempmailol_domains = _get_tempmailol_domains()
        for d in tempmailol_domains:
            if d not in _BLOCKED_DOMAINS:
                domains.add(d)
    except Exception:
        pass

    if domains:
        return sorted(domains)
    return sorted([d for d in _FALLBACK_DOMAINS if d not in _BLOCKED_DOMAINS])


def _try_create_mailtm(local, password, domain):
    email = f"{local}@{domain}"
    resp = make_request("POST", _MAILTM_URL, "/accounts", headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
    }, body={"address": email, "password": password}, timeout=15, use_proxy=True)
    data = _safe_data(resp)
    if isinstance(data, dict) and resp["status_code"] == 201 and data.get("id"):
        token_resp = make_request("POST", _MAILTM_URL, "/token", headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        }, body={"address": email, "password": password}, timeout=15, use_proxy=True)
        token_data = _safe_data(token_resp)
        token = token_data.get("token") if isinstance(token_data, dict) else None
        if not token:
            raise RuntimeError(f"获取token失败: {token_resp['data']}")
        return {"email": email, "token": token, "base_url": _MAILTM_URL, "type": "mailtm"}
    return None


def _try_create_tempmailio(local, domain):
    resp = make_request("POST", _TEMPMĀILIO_URL, "/api/v2/email/new",
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        body={"local_part": local, "domain": domain},
                        timeout=30, use_proxy=True)
    data = _safe_data(resp)
    if not isinstance(data, dict):
        raise RuntimeError(f"Temp-Mail创建失败: {data}")
    email = data.get("email")
    token = data.get("token")
    if not email or not token:
        raise RuntimeError(f"Temp-Mail创建失败: {data}")
    return {"email": email, "token": email, "base_url": _TEMPMĀILIO_URL, "type": "tempmailio"}


def _try_create_tempmailol(local, domain):
    resp = make_request("GET", _TEMPMĀILLOL_URL, "/generate",
                        timeout=15, use_proxy=True)
    data = _safe_data(resp)
    if not isinstance(data, dict):
        raise RuntimeError(f"TempMail.lol创建失败: {data}")
    email = data.get("address")
    token = data.get("token")
    if not email or not token:
        raise RuntimeError(f"TempMail.lol创建失败: {data}")
    return {"email": email, "token": token, "base_url": _TEMPMĀILLOL_URL, "type": "tempmailol"}


def create_mail_account(force_domain=None):
    local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    password = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    if force_domain:
        try:
            mailtm_domains = _get_mailtm_domains()
            if force_domain in mailtm_domains:
                result = _try_create_mailtm(local, password, force_domain)
                if result:
                    return result
        except Exception:
            pass
        try:
            tempmailio_domains = _get_tempmailio_domains()
            if force_domain in tempmailio_domains:
                result = _try_create_tempmailio(local, force_domain)
                if result:
                    return result
        except Exception:
            pass
        raise RuntimeError(f"指定域名不可用: {force_domain}")

    candidates = []

    try:
        tempmailio_domains = [d for d in _get_tempmailio_domains() if d not in _BLOCKED_DOMAINS]
        if tempmailio_domains:
            candidates.append(("tempmailio", tempmailio_domains))
    except Exception:
        pass

    try:
        mailtm_domains = [d for d in _get_mailtm_domains() if d not in _BLOCKED_DOMAINS]
        if mailtm_domains:
            candidates.append(("mailtm", mailtm_domains))
    except Exception:
        pass

    try:
        tempmailol_domains = [d for d in _get_tempmailol_domains() if d not in _BLOCKED_DOMAINS]
        if tempmailol_domains:
            candidates.append(("tempmailol", tempmailol_domains))
    except Exception:
        pass

    for ptype, domains in candidates:
        try:
            domain = random.choice(domains)
            if ptype == "tempmailio":
                result = _try_create_tempmailio(local, domain)
            elif ptype == "mailtm":
                result = _try_create_mailtm(local, password, domain)
            elif ptype == "tempmailol":
                result = _try_create_tempmailol(local, domain)
            else:
                continue
            if result:
                return result
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


def _find_code(value):
    if not value:
        return None
    match = re.search(r"\b(\d{6})\b", str(value))
    return match.group(1) if match else None


def _fetch_code_mailtm(email, token, stop_check=None):
    last_error = None
    poll_count = 0

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            resp = make_request("GET", _MAILTM_URL, "/messages", headers={
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
                        "GET", _MAILTM_URL, f"/messages/{msg_id}",
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

            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} 已轮询{poll_count}次未收到验证码")
            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} {last_error}")


def _fetch_code_tempmailio(email, stop_check=None):
    last_error = None
    poll_count = 0
    seen_ids = set()

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            resp = make_request("GET", _TEMPMĀILIO_URL,
                                f"/api/v2/email/{email}/messages",
                                headers={"Accept": "application/json"},
                                timeout=30, use_proxy=True)
            messages = _safe_data(resp)

            if not isinstance(messages, list):
                continue

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                subject = msg.get("subject", "")
                body = msg.get("body_text", "") or msg.get("body_html", "")
                snippet = msg.get("snippet", "")
                code = _find_code(f"{subject} {snippet} {body}")
                if code:
                    return code

                try:
                    detail = make_request("GET", _TEMPMĀILIO_URL,
                                          f"/api/v2/email/{email}/messages/{msg_id}",
                                          headers={"Accept": "application/json"},
                                          timeout=30, use_proxy=True)
                    detail_data = _safe_data(detail)
                    full_body = detail_data.get("body_text", "") or detail_data.get("body_html", "")
                    full_code = _find_code(full_body)
                    if full_code:
                        return full_code
                except Exception as e:
                    last_error = e

            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} 已轮询{poll_count}次未收到验证码")
            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} {last_error}")


def _fetch_code_tempmailol(email, token, stop_check=None):
    last_error = None
    poll_count = 0
    seen_ids = set()

    while True:
        if stop_check and stop_check():
            raise RuntimeError('用户停止')

        poll_count += 1
        try:
            resp = make_request("GET", _TEMPMĀILLOL_URL, f"/auth/{token}",
                                headers={"Accept": "application/json"},
                                timeout=15, use_proxy=True)
            data = _safe_data(resp)
            emails = data.get("email", []) if isinstance(data, dict) else []

            if not isinstance(emails, list):
                continue

            for msg in emails:
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("unique_id") or msg.get("id")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                subject = msg.get("subject", "")
                body = msg.get("body", "") or msg.get("html", "")
                code = _find_code(f"{subject} {body}")
                if code:
                    return code

            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} 已轮询{poll_count}次未收到验证码")
            time.sleep(3)
        except Exception as e:
            last_error = e
            time.sleep(5)
            if poll_count > 30:
                raise RuntimeError(f"收取验证码超时: {email} {last_error}")


def fetch_verification_code(email, token, base_url=None, stop_check=None, provider_type=None):
    if provider_type == "tempmailio":
        return _fetch_code_tempmailio(email, stop_check=stop_check)

    if provider_type == "tempmailol":
        return _fetch_code_tempmailol(email, token, stop_check=stop_check)

    return _fetch_code_mailtm(email, token, stop_check=stop_check)


def configure_mail(force_domain=None):
    global _FORCE_DOMAIN
    _FORCE_DOMAIN = force_domain