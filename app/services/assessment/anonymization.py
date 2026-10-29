"""Privacy filtering at the process-log persistence boundary."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import SecretStr


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d().\s-]{7,}\d(?!\w)")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{0,4}(?![\w:])")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

_IDENTITY_KEYS = {
    "studentid",
    "userid",
    "learnerid",
    "personid",
    "externaluserid",
    "accountid",
}
_REMOVED_PII_KEYS = {
    "name",
    "fullname",
    "firstname",
    "lastname",
    "email",
    "emailaddress",
    "phone",
    "phonenumber",
    "mobile",
    "address",
    "dateofbirth",
    "birthdate",
    "dob",
    "governmentid",
    "aadhaar",
    "ssn",
    "rollnumber",
    "registrationnumber",
    "studentnumber",
    "contactdetails",
    "postalcode",
    "zipcode",
}
_DEVICE_KEY_PARTS = (
    "device",
    "useragent",
    "browser",
    "operatingsystem",
    "hardware",
    "screen",
    "resolution",
    "ipaddress",
    "clientip",
    "networktype",
    "networkquality",
    "connectiontype",
    "carrier",
    "platform",
)


class ProcessLogAnonymizer:
    """Remove direct identifiers and device metadata before assessment storage.

    Identity references are replaced with institution-scoped HMAC pseudonyms.
    The HMAC key must be a managed secret and is never written to process logs.
    """

    def __init__(self, pseudonym_key: SecretStr | str | bytes) -> None:
        if isinstance(pseudonym_key, SecretStr):
            key = pseudonym_key.get_secret_value().encode("utf-8")
        elif isinstance(pseudonym_key, str):
            key = pseudonym_key.encode("utf-8")
        else:
            key = pseudonym_key
        if len(key) < 32:
            raise ValueError("pseudonym key must contain at least 32 bytes")
        self._key = key

    def anonymize(self, event: Mapping[str, Any]) -> dict[str, Any]:
        institution_id = event.get("institution_id")
        if not isinstance(institution_id, str) or not institution_id:
            raise ValueError("institution_id is required to pseudonymize process logs")
        pseudonym: str | None = None
        for key, value in event.items():
            if self._normalize_key(str(key)) in _IDENTITY_KEYS and value is not None:
                pseudonym = self._pseudonym(institution_id, str(value))
                break

        result = self._clean_mapping(event)
        # Tenant scope is required downstream; user identity is not.
        result["institution_id"] = institution_id
        if pseudonym:
            result["pseudonymous_id"] = pseudonym
        return result

    def _clean_mapping(self, value: Mapping[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = self._normalize_key(key)
            if normalized in _IDENTITY_KEYS or normalized in _REMOVED_PII_KEYS:
                continue
            if any(part in normalized for part in _DEVICE_KEY_PARTS):
                continue
            if normalized == "pseudonymousid":
                # Do not trust a caller-provided identifier in place of HMAC.
                continue
            cleaned[key] = self._clean_value(raw_value)
        return cleaned

    def _clean_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self._clean_mapping(value)
        if isinstance(value, list):
            return [self._clean_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._clean_value(item) for item in value]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-z0-9]", "", key.lower())

    def _pseudonym(self, institution_id: str, identity: str) -> str:
        message = f"{institution_id}\0{identity}".encode("utf-8")
        digest = hmac.new(self._key, message, hashlib.sha256).hexdigest()[:32]
        return f"stu_{digest}"

    @staticmethod
    def _redact_text(value: str) -> str:
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _SSN.sub("[REDACTED_ID]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)

        def redact_ip(match: re.Match[str]) -> str:
            try:
                ipaddress.ip_address(match.group(0))
                return "[REDACTED_IP]"
            except ValueError:
                return match.group(0)

        value = _IPV4.sub(redact_ip, value)
        return _IPV6.sub(redact_ip, value)


class ProcessLogPrivacyMiddleware:
    """Sanitize process events immediately before invoking a persistence sink.

    Gateway/process-log code should use this wrapper instead of calling the
    database writer directly, so the raw event is never passed to persistence.
    """

    def __init__(
        self,
        anonymizer: ProcessLogAnonymizer,
        persist: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> None:
        self.anonymizer = anonymizer
        self.persist = persist

    async def persist_process_event(self, event: Mapping[str, Any]) -> Any:
        sanitized = self.anonymizer.anonymize(event)
        return await self.persist(sanitized)
