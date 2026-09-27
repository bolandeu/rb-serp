from __future__ import annotations

import json
import logging
import os
import re

import keyring
import keyring.errors
from keyring.backends import fail

from ..core.errors import CredentialStoreUnavailable
from ..core.version import CLI_NAME as _PACKAGE_NAME  # serp: keychain serp:*, env SERP_*

log = logging.getLogger(__name__)

# A "credential" is a small dict of named fields stored as one JSON blob per
# provider in the OS keychain. `provider` is whatever identifies the
# credential in this CLI's domain: a service name ("openai"), a target
# site/host for a deploy/publish tool ("example.com"), a machine/profile
# name, etc. One field ("api_key") covers the common case; multiple fields
# cover session-based auth (cookies + csrf token) or host credentials
# (username + password) — nothing is special-cased for either shape.


def _service_name(provider: str) -> str:
    return f"{_PACKAGE_NAME}:{provider}"


def _env_var_name(provider: str, field: str) -> str:
    # Anything that isn't a letter or digit becomes "_", so per-target names
    # ("deploy:example.com") still give a settable name: MYCLI_DEPLOY_EXAMPLE_COM_API_KEY.
    return re.sub(r"[^A-Z0-9]", "_", f"{_PACKAGE_NAME}_{provider}_{field}".upper())


# keyring can't list what it holds, so the names of stored credentials are
# kept in one more keychain entry. `auth status` reads it to find every
# stored target of a per-target provider ("deploy:example.com", ...).
_INDEX_USER = "__stored_credentials__"


def stored_names() -> list[str]:
    """Names of credentials stored in the keychain by this CLI (env-only ones aren't listed)."""
    try:
        raw = keyring.get_password(_PACKAGE_NAME, _INDEX_USER)
    except keyring.errors.KeyringError:
        return []
    return sorted(json.loads(raw)) if raw else []


def _update_index(provider: str, *, present: bool) -> None:
    names = set(stored_names())
    names = names | {provider} if present else names - {provider}
    try:
        keyring.set_password(_PACKAGE_NAME, _INDEX_USER, json.dumps(sorted(names)))
    except keyring.errors.KeyringError as exc:  # the credential itself is stored; only listing suffers
        log.warning("could not update the stored-credentials index (%s)", exc)


def read_fields_from_env(provider: str, fields: list[str]) -> dict[str, str]:
    """Read whichever of `fields` are set as env vars for this provider.

    Env var name: <PACKAGE>_<PROVIDER>_<FIELD>, e.g. MYCLI_OPENAI_API_KEY.
    """
    result: dict[str, str] = {}
    for field in fields:
        value = os.environ.get(_env_var_name(provider, field))
        if value is not None:
            result[field] = value
    return result


def _stored(provider: str) -> dict[str, str]:
    try:
        stored_raw = keyring.get_password(_service_name(provider), provider)
    except keyring.errors.KeyringError as exc:
        # A missing/locked keychain (headless Linux, CI) must not block the
        # env-var path — that's the whole point of having a fallback.
        log.warning("OS credential store unavailable (%s); using environment variables only", exc)
        return {}
    return json.loads(stored_raw) if stored_raw else {}


def get_credential(provider: str, *, env_fields: list[str] | None = None) -> dict[str, str] | None:
    """Resolve the full credential bag for a provider.

    Env vars win field-by-field over what's stored in the keychain, so CI
    can override a single field (e.g. a rotated token) without touching the
    rest. `env_fields` lets a multi-field provider (session cookies, host
    credentials, ...) declare its field names explicitly, so env vars are
    checked even before anything has ever been stored.
    """
    stored = _stored(provider)
    fields_to_check = env_fields or list(stored.keys()) or ["api_key"]
    merged = {**stored, **read_fields_from_env(provider, fields_to_check)}
    return merged or None


def locate_fields(provider: str, fields: list[str]) -> dict[str, tuple[str, str]]:
    """Map each present field to (value, where it came from: "env" | "keychain").

    Same precedence as `get_credential`. For `auth status`, which reports the
    source and must never print the value.
    """
    located = {field: (value, "keychain") for field, value in _stored(provider).items() if field in fields}
    located.update({field: (value, "env") for field, value in read_fields_from_env(provider, fields).items()})
    return located


def env_var_name(provider: str, field: str) -> str:
    return _env_var_name(provider, field)


def keychain_service(provider: str) -> str:
    return _service_name(provider)


def store_backend() -> str | None:
    """Name of the active keyring backend, or None when there's no usable one.

    `keyring` falls back to its `fail` backend on a machine without a
    secret service (headless Linux, some containers) — reads and writes
    then raise, and only env vars can supply credentials. `doctor` reports
    this so it isn't discovered from a confusing `auth login` failure.
    """
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        return None
    return f"{type(backend).__module__}.{type(backend).__name__}"


def store_credential(provider: str, fields: dict[str, str]) -> None:
    try:
        keyring.set_password(_service_name(provider), provider, json.dumps(fields))
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreUnavailable(provider, str(exc)) from exc
    _update_index(provider, present=True)


def delete_credential(provider: str) -> None:
    try:
        keyring.delete_password(_service_name(provider), provider)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent — deleting a missing credential is not an error
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreUnavailable(provider, str(exc)) from exc
    _update_index(provider, present=False)


# Convenience wrappers for the common single-secret case (most providers
# only ever need one API key). Multi-field providers should use
# get_credential/store_credential/read_fields_from_env directly instead, in
# their own commands/auth flow — see architecture.md.


def get_secret(provider: str, field: str = "api_key") -> str | None:
    credential = get_credential(provider, env_fields=[field])
    return credential.get(field) if credential else None


def store_secret(provider: str, secret: str, field: str = "api_key") -> None:
    store_credential(provider, {field: secret})


def read_secret_from_env(provider: str, field: str = "api_key") -> str | None:
    return read_fields_from_env(provider, [field]).get(field)


def delete_secret(provider: str) -> None:
    delete_credential(provider)
