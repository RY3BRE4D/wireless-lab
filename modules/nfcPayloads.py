"""
NFC Payload Builders.

Convert Natural User Inputs Into Final NDEF Payloads For Writing.
Each Builder Returns {ok, mode, payload, label, error?}.

  mode    — "uri" Or "text" (Selects The Existing Writer In pn532Module)
  payload — Final String To Hand To The Writer
  label   — Human-Readable Payload Type Shown In The UI
"""

import re
from typing import Any, Dict, Optional
from urllib.parse import quote, quote_plus, urlencode


# ---------- Normalizers ----------

def normalizeUrl(value: Any) -> str:
    """
    Trim Whitespace; If No URI Scheme Is Present, Default To https://.
    Preserves http://, ftp://, etc. If Already Set By The User.
    """
    s = (value or "")
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    if not s:
        return ""
    # Match Any RFC 3986 Scheme: [a-zA-Z][a-zA-Z0-9+.-]*:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", s):
        return s
    return "https://" + s


def normalizePhoneNumber(value: Any) -> str:
    """
    Strip Spaces, Dashes, Parentheses, And Dots. Preserve A Leading + If Present.
    Returns Digits-Only With Optional Leading +.
    """
    s = (value or "")
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    if not s:
        return ""
    hasPlus = s.startswith("+")
    digits = re.sub(r"\D+", "", s)
    if not digits:
        return ""
    return ("+" + digits) if hasPlus else digits


def _wifiEscape(value: str) -> str:
    """
    Escape WiFi Provisioning String Per The Common QR/NFC Convention:
    Backslash-Escape Any Of \\ ; , : ".
    """
    out = []
    for ch in value:
        if ch in ("\\", ";", ",", ":", '"'):
            out.append("\\")
        out.append(ch)
    return "".join(out)


# ---------- URI Builders ----------

def buildWebUri(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = (data.get("url") or "").strip()
    if not raw:
        return {"ok": False, "error": "Missing URL Or Host."}
    payload = normalizeUrl(raw)
    return {"ok": True, "mode": "uri", "payload": payload, "label": "NDEF URI"}


def buildTelUri(data: Dict[str, Any]) -> Dict[str, Any]:
    number = normalizePhoneNumber(data.get("number"))
    if not number:
        return {"ok": False, "error": "Missing Phone Number."}
    return {"ok": True, "mode": "uri", "payload": "tel:" + number, "label": "NDEF URI"}


def buildSmsUri(data: Dict[str, Any]) -> Dict[str, Any]:
    number = normalizePhoneNumber(data.get("number"))
    if not number:
        return {"ok": False, "error": "Missing Phone Number."}
    body = (data.get("message") or "")
    if not isinstance(body, str):
        body = str(body)
    body = body.strip()
    if body:
        # smsto:<Number>:<Message> — URL-Encode Just The Message Portion
        payload = "smsto:" + number + ":" + quote(body, safe="")
    else:
        payload = "smsto:" + number
    return {"ok": True, "mode": "uri", "payload": payload, "label": "NDEF URI"}


def buildMailtoUri(data: Dict[str, Any]) -> Dict[str, Any]:
    email = (data.get("email") or "").strip()
    if not email:
        return {"ok": False, "error": "Missing Email Address."}
    if "@" not in email:
        return {"ok": False, "error": "Invalid Email Address."}
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    params = []
    if subject:
        params.append(("subject", subject))
    if body:
        params.append(("body", body))
    payload = "mailto:" + quote(email, safe="@+._-")
    if params:
        payload += "?" + urlencode(params, quote_via=quote)
    return {"ok": True, "mode": "uri", "payload": payload, "label": "NDEF URI"}


def buildMapsUri(data: Dict[str, Any]) -> Dict[str, Any]:
    label = (data.get("place") or "").strip()
    street = (data.get("street") or "").strip()
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    zipCode = (data.get("zip") or "").strip()
    parts = []
    if street:
        parts.append(street)
    cityStateZip = " ".join([p for p in [state, zipCode] if p]).strip()
    cityLine = ", ".join([p for p in [city, cityStateZip] if p])
    if cityLine:
        parts.append(cityLine)
    address = ", ".join(parts).strip(", ").strip()
    if label and address:
        address = label + ", " + address
    elif label and not address:
        address = label
    if not address:
        return {"ok": False, "error": "Missing Address."}
    encoded = quote_plus(address, safe=",")
    payload = "https://maps.google.com/?q=" + encoded
    return {"ok": True, "mode": "uri", "payload": payload, "label": "NDEF URI"}


def buildWifiPayload(data: Dict[str, Any]) -> Dict[str, Any]:
    ssid = (data.get("ssid") or "").strip()
    if not ssid:
        return {"ok": False, "error": "Missing SSID."}
    auth = (data.get("auth") or "WPA").strip().upper()
    if auth not in ("WPA", "WEP", "NOPASS", "OPEN"):
        auth = "WPA"
    if auth == "OPEN":
        auth = "nopass"
    elif auth == "NOPASS":
        auth = "nopass"
    password = (data.get("password") or "")
    if not isinstance(password, str):
        password = str(password)
    hidden = bool(data.get("hidden", False))

    # WiFi Provisioning String: WIFI:T:<Auth>;S:<SSID>;P:<Password>;H:true;;
    parts = ["WIFI:"]
    parts.append("T:" + auth + ";")
    parts.append("S:" + _wifiEscape(ssid) + ";")
    if auth.lower() != "nopass":
        parts.append("P:" + _wifiEscape(password) + ";")
    if hidden:
        parts.append("H:true;")
    parts.append(";")
    payload = "".join(parts)
    return {
        "ok": True,
        "mode": "text",
        "payload": payload,
        "label": "NDEF Text / Compatibility Format",
    }


def buildVcardLinkUri(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = (data.get("url") or "").strip()
    if not raw:
        return {"ok": False, "error": "Missing vCard URL."}
    return {"ok": True, "mode": "uri", "payload": normalizeUrl(raw), "label": "NDEF URI"}


def buildCalendarUri(data: Dict[str, Any]) -> Dict[str, Any]:
    title = (data.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "Missing Event Title."}
    location = (data.get("location") or "").strip()
    details = (data.get("details") or "").strip()
    start = (data.get("start") or "").strip()
    end = (data.get("end") or "").strip()

    params = [("action", "TEMPLATE"), ("text", title)]
    if location:
        params.append(("location", location))
    if details:
        params.append(("details", details))
    if start and end:
        # Google Calendar Expects: YYYYMMDDTHHMMSSZ/YYYYMMDDTHHMMSSZ
        # We Pass Whatever The User Provides Through Verbatim As A "dates" Pair.
        params.append(("dates", start + "/" + end))
    elif start:
        params.append(("dates", start))
    payload = "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)
    return {"ok": True, "mode": "uri", "payload": payload, "label": "NDEF URI"}


def buildDeepLinkUri(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = (data.get("uri") or "").strip()
    if not raw:
        return {"ok": False, "error": "Missing Deep Link URI."}
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
        return {"ok": False, "error": "Deep Link Must Include A Scheme (e.g. youtube://...)."}
    return {"ok": True, "mode": "uri", "payload": raw, "label": "NDEF URI / App Deep Link"}


def buildNavigationUri(data: Dict[str, Any]) -> Dict[str, Any]:
    dest = (data.get("destination") or "").strip()
    if not dest:
        return {"ok": False, "error": "Missing Destination."}
    encoded = quote_plus(dest, safe=",")
    payload = "google.navigation:q=" + encoded
    return {
        "ok": True,
        "mode": "uri",
        "payload": payload,
        "label": "Android-only / Experimental",
    }


def buildPaymentUri(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = (data.get("url") or "").strip()
    if not raw:
        return {"ok": False, "error": "Missing Payment URL."}
    return {"ok": True, "mode": "uri", "payload": normalizeUrl(raw), "label": "NDEF URI"}


def buildPlainTextPayload(data: Dict[str, Any]) -> Dict[str, Any]:
    text = (data.get("text") or "")
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if not text:
        return {"ok": False, "error": "Missing Text."}
    return {"ok": True, "mode": "text", "payload": text, "label": "NDEF Text"}


def buildCustomUri(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = (data.get("payload") or "")
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip()
    if not raw:
        return {"ok": False, "error": "Missing Payload."}
    mode = (data.get("mode") or "uri").strip().lower()
    if mode not in ("uri", "text"):
        mode = "uri"
    label = "NDEF URI" if mode == "uri" else "NDEF Text"
    return {"ok": True, "mode": mode, "payload": raw, "label": label}


# ---------- Dispatch ----------

TASK_BUILDERS = {
    "web":        buildWebUri,
    "tel":        buildTelUri,
    "sms":        buildSmsUri,
    "mailto":     buildMailtoUri,
    "maps":       buildMapsUri,
    "wifi":       buildWifiPayload,
    "vcard":      buildVcardLinkUri,
    "calendar":   buildCalendarUri,
    "deeplink":   buildDeepLinkUri,
    "navigation": buildNavigationUri,
    "payment":    buildPaymentUri,
    "text":       buildPlainTextPayload,
    "custom":     buildCustomUri,
}


def buildTaskPayload(taskType: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Top-Level Dispatch. Returns {ok, mode, payload, label} Or {ok=False, error}.
    """
    key = (taskType or "").strip().lower()
    builder = TASK_BUILDERS.get(key)
    if not builder:
        return {"ok": False, "error": f"Unknown Task Type: {taskType!r}"}
    if not isinstance(fields, dict):
        fields = {}
    return builder(fields)
