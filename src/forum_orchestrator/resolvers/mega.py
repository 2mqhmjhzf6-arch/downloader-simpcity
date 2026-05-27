"""mega.nz file / folder / password-link resolver.

Mega URLs come in several shapes::

    https://mega.nz/file/<handle>#<base64-key>
    https://mega.nz/folder/<handle>#<base64-key>
    https://mega.nz/folder/<handle>#<base64-key>/file/<sub-handle>
    https://mega.nz/#!<handle>!<base64-key>           (legacy)
    https://mega.nz/#F!<handle>!<base64-key>          (legacy folder)
    https://mega.nz/#P!<blob>                         (password-protected)

The API is a JSON-RPC over POSTs to ``https://g.api.mega.co.nz/cs``. The
downloader does the actual AES-CTR decryption of the ciphertext stream — we
just stash the key + IV on each :class:`Resource`.

We deliberately do NOT verify Mega's optional file MAC: matching the
userscript behaviour and saving one full re-read of every download.

This module pulls in :mod:`pycryptodome` lazily so the rest of the project
still imports cleanly when that dependency is missing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import struct
import time
from typing import Any
from urllib.parse import urlparse

from ..errors import DeadLink, ResolverError
from ..models import Kind, Resource
from .base import Resolver, ResolveContext, guess_kind, register

log = logging.getLogger(__name__)

_API = "https://g.api.mega.co.nz/cs"

# URL patterns covered by this resolver. Order matters only for `find_resolver`
# matching — both legacy and modern hashes are caught.
_NEW_FILE_RE = re.compile(
    r"https?://mega\.nz/file/(?P<id>[A-Za-z0-9_-]+)#(?P<key>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_NEW_FOLDER_RE = re.compile(
    r"https?://mega\.nz/folder/(?P<id>[A-Za-z0-9_-]+)#(?P<key>[A-Za-z0-9_-]+)"
    r"(?:/file/(?P<sub>[A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)
_LEGACY_FILE_RE = re.compile(
    r"https?://mega\.nz/#!(?P<id>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_LEGACY_FOLDER_RE = re.compile(
    r"https?://mega\.nz/#F!(?P<id>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)"
    r"(?:!(?P<sub>[A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)
_PASSWORD_RE = re.compile(
    r"https?://mega\.nz/#P!(?P<blob>[A-Za-z0-9_-]+)", re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# base64 helpers (Mega's URL-safe, no padding)
# ---------------------------------------------------------------------------


def _b64d(s: str) -> bytes:
    """Decode Mega's URL-safe base64 (no padding)."""
    s = s.replace("-", "+").replace("_", "/").replace(",", "")
    pad = "=" * (-len(s) % 4)
    return base64.b64decode(s + pad)


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii").rstrip("=").replace("+", "-").replace("/", "_")


# ---------------------------------------------------------------------------
# crypto primitives — lazy pycryptodome import so the module file is still
# safe to import when the optional dependency is missing.
# ---------------------------------------------------------------------------


def _aes_cbc_decrypt(key: bytes, ciphertext: bytes, iv: bytes = b"\0" * 16) -> bytes:
    from Crypto.Cipher import AES
    return AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)


def _aes_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    from Crypto.Cipher import AES
    return AES.new(key, AES.MODE_ECB).decrypt(ciphertext)


def _aes_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    from Crypto.Cipher import AES
    return AES.new(key, AES.MODE_ECB).encrypt(plaintext)


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _decrypt_node_key(encoded_key: str, master_key: bytes) -> bytes:
    """Folder node key payload: ``<sharer>:<base64-blob>``.

    The blob is AES-ECB encrypted with the folder master key. Files yield a
    32-byte key, sub-folders a 16-byte one.
    """
    payload = encoded_key.split(":", 1)[-1]
    cipher = _b64d(payload)
    # ECB decrypt in 16-byte blocks.
    pt = b""
    for i in range(0, len(cipher), 16):
        pt += _aes_ecb_decrypt(master_key, cipher[i : i + 16])
    return pt


def _file_aes_params(file_key_32: bytes) -> tuple[bytes, bytes]:
    """Given a 32-byte Mega file key, return (aes_key, ctr_iv_16).

    AES-CTR for the ciphertext stream uses ``key[0:16] XOR key[16:32]`` as
    the cipher key and ``key[16:24]`` as the 8-byte nonce (high half of a
    16-byte IV).
    """
    if len(file_key_32) != 32:
        raise ResolverError(f"mega: unexpected file key length {len(file_key_32)}")
    aes_key = _xor_bytes(file_key_32[:16], file_key_32[16:])
    iv = file_key_32[16:24] + b"\x00" * 8
    return aes_key, iv


def _decrypt_attributes(encrypted_attrs: bytes, aes_key: bytes) -> dict[str, Any]:
    """Decode Mega's encrypted node attributes (filename, mime, ...).

    Plaintext starts with the literal ``MEGA{`` magic and is null-padded to
    a 16-byte boundary.
    """
    pt = _aes_cbc_decrypt(aes_key, encrypted_attrs)
    pt = pt.rstrip(b"\x00")
    if not pt.startswith(b"MEGA"):
        raise ResolverError("mega: attribute decryption failed (bad magic)")
    try:
        return json.loads(pt[4:].decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise ResolverError(f"mega: bad attribute JSON: {e}") from e


# ---------------------------------------------------------------------------
# API wrapper
# ---------------------------------------------------------------------------


class _MegaApi:
    """Tiny stateful client for the public Mega CS endpoint.

    Auto-increments the request sequence id and surfaces non-zero numeric
    error replies as :class:`DeadLink`.
    """

    def __init__(self, ctx: ResolveContext):
        self.ctx = ctx
        self._seq = int(time.time() * 1000) & 0xFFFFFFFF

    async def call(self, payload: list[dict], *, folder: str | None = None) -> Any:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        url = f"{_API}?id={self._seq}"
        if folder:
            url += f"&n={folder}"
        try:
            r = await self.ctx.http.request(
                "POST", url, json=payload, referer="https://mega.nz/",
                headers={"Content-Type": "text/plain;charset=UTF-8"},
            )
        except Exception as e:
            raise ResolverError(f"mega: api request failed: {e}") from e
        # Mega returns either a JSON array (one entry per command) or a bare
        # integer when something went wrong.
        try:
            data = r.json()
        except Exception as e:
            raise ResolverError(f"mega: bad api response: {e}") from e
        if isinstance(data, int):
            raise DeadLink(f"mega: api error {data}")
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], int):
            raise DeadLink(f"mega: api error {data[0]}")
        return data


# ---------------------------------------------------------------------------
# Password-protected link unwrap
# ---------------------------------------------------------------------------


def _unwrap_password_link(blob_b64: str, passwords: list[str]) -> str | None:
    """Decode a ``#P!`` link by trying each candidate password.

    Layout (after base64 decode):
        byte 0    algorithm (1 = PBKDF2-SHA512)
        byte 1    type      (0 = file, 1 = folder)
        bytes 2-5 iterations (big-endian uint32)
        bytes 6-21 salt (16 bytes)
        rest      AES-ECB(handle || key || mac) under the derived key
                  handle = 6 bytes (file) or 8 bytes (folder) – stored as
                  base64 in the resulting URL with `_-` charset
                  key = 32 bytes (file) or 16 bytes (folder)
                  mac = 32 bytes; verified by sha256(derived) == mac
    """
    try:
        from Crypto.Hash import SHA512
        from Crypto.Protocol.KDF import PBKDF2
    except ImportError:
        return None
    blob = _b64d(blob_b64)
    if len(blob) < 22:
        return None
    algo = blob[0]
    is_folder = blob[1] == 1
    iterations = struct.unpack(">I", blob[2:6])[0]
    salt = blob[6:22]
    encrypted = blob[22:]
    if algo != 1 or iterations == 0 or len(encrypted) < 48:
        return None

    for pw in passwords:
        try:
            derived = PBKDF2(
                pw, salt, dkLen=64, count=iterations, hmac_hash_module=SHA512,
            )
        except Exception:
            continue
        cipher_key = derived[:32]
        mac_expected = derived[32:]
        # ECB-decrypt in 16-byte chunks (key is 32 bytes -> AES-256-ECB).
        try:
            from Crypto.Cipher import AES
            pt = b""
            ecb = AES.new(cipher_key, AES.MODE_ECB)
            for i in range(0, len(encrypted), 16):
                pt += ecb.decrypt(encrypted[i : i + 16])
        except Exception:
            continue
        # File: handle(6) + key(32) + mac(32). Folder: handle(8) + key(16) + mac(32).
        if is_folder:
            handle_len, key_len = 8, 16
        else:
            handle_len, key_len = 6, 32
        if len(pt) < handle_len + key_len + 32:
            continue
        handle = pt[:handle_len]
        node_key = pt[handle_len : handle_len + key_len]
        mac_got = pt[handle_len + key_len : handle_len + key_len + 32]
        if mac_got != mac_expected:
            continue
        kind = "folder" if is_folder else "file"
        return f"https://mega.nz/{kind}/{_b64e(handle)}#{_b64e(node_key)}"
    return None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@register
class Mega(Resolver):
    name = "mega"
    patterns = [
        r"mega\.nz/file/[A-Za-z0-9_-]+#",
        r"mega\.nz/#![A-Za-z0-9_-]+!",
        r"mega\.nz/#P![A-Za-z0-9_-]+",
    ]
    album_patterns = [
        r"mega\.nz/folder/[A-Za-z0-9_-]+#",
        r"mega\.nz/#F![A-Za-z0-9_-]+!",
    ]

    async def resolve(self, url: str, ctx: ResolveContext) -> list[Resource]:
        # Password-protected envelope first; unwrap into a real file/folder URL.
        m = _PASSWORD_RE.search(url)
        if m:
            unwrapped = _unwrap_password_link(m.group("blob"), ctx.passwords)
            if not unwrapped:
                raise DeadLink("mega: password-protected (no matching password)")
            return await self.resolve(unwrapped, ctx)

        api = _MegaApi(ctx)

        m = _NEW_FILE_RE.search(url) or _LEGACY_FILE_RE.search(url)
        if m:
            return [await self._single_file(api, m.group("id"), m.group("key"))]

        m = _NEW_FOLDER_RE.search(url) or _LEGACY_FOLDER_RE.search(url)
        if m:
            return await self._folder(api, m.group("id"), m.group("key"), m.groupdict().get("sub"))

        raise DeadLink(f"mega: unrecognised URL shape: {url}")

    # ---- single ------------------------------------------------------------

    async def _single_file(self, api: _MegaApi, file_id: str, key_b64: str) -> Resource:
        file_key = _b64d(key_b64)
        aes_key, iv = _file_aes_params(file_key)
        try:
            resp = await api.call([{"a": "g", "g": 1, "p": file_id, "ssm": 1}])
        except Exception as e:
            if isinstance(e, DeadLink):
                raise
            raise ResolverError(f"mega: file metadata: {e}") from e
        if not resp or not isinstance(resp, list):
            raise DeadLink("mega: empty file metadata")
        info = resp[0]
        if not isinstance(info, dict) or "g" not in info:
            raise DeadLink(f"mega: no download URL ({info})")
        attrs = _decrypt_attributes(_b64d(info["at"]), aes_key)
        name = (attrs.get("n") or file_id).strip() or file_id
        return Resource(
            url=info["g"],
            filename=name,
            kind=guess_kind(name),
            referer="https://mega.nz/",
            dedup_key=f"mega:file:{file_id}",
            stream_decrypt_key=aes_key,
            stream_decrypt_iv=iv,
        )

    # ---- folder ------------------------------------------------------------

    async def _folder(
        self, api: _MegaApi, folder_id: str, master_key_b64: str, sub_id: str | None,
    ) -> list[Resource]:
        master_key = _b64d(master_key_b64)
        if len(master_key) != 16:
            raise ResolverError(f"mega: folder master key must be 16 bytes, got {len(master_key)}")

        try:
            resp = await api.call(
                [{"a": "f", "c": 1, "r": 1, "ca": 1}], folder=folder_id,
            )
        except Exception as e:
            if isinstance(e, DeadLink):
                raise
            raise ResolverError(f"mega: folder listing: {e}") from e
        if not resp or not isinstance(resp, list) or not isinstance(resp[0], dict):
            raise DeadLink(f"mega: empty folder listing for {folder_id}")
        nodes = resp[0].get("f") or []

        # Build out the tree: each node has `h` (handle), `t` (0=file 1=folder),
        # `p` (parent handle), `k` (encrypted key payload), `a` (attrs), `s` (size).
        out: list[Resource] = []
        # Process files only; sub-folders are walked just to keep their path
        # context (Mega's userscript flattens, so we do too).
        for node in nodes:
            if node.get("t") != 0:  # not a file
                continue
            if sub_id and node.get("h") != sub_id:
                continue
            try:
                file_key_32 = _decrypt_node_key(node.get("k") or "", master_key)
            except Exception as e:
                log.debug("mega: skip node %s (key decrypt failed: %s)", node.get("h"), e)
                continue
            try:
                aes_key, iv = _file_aes_params(file_key_32)
                attrs = _decrypt_attributes(_b64d(node.get("a") or ""), aes_key)
            except Exception as e:
                log.debug("mega: skip node %s (attr decrypt failed: %s)", node.get("h"), e)
                continue
            name = (attrs.get("n") or node.get("h") or "file").strip()

            # Fetch the per-file download URL.
            try:
                g_resp = await api.call(
                    [{"a": "g", "g": 1, "n": node["h"], "ssm": 1}], folder=folder_id,
                )
            except Exception as e:
                if isinstance(e, DeadLink):
                    log.debug("mega: dead file %s: %s", node.get("h"), e)
                    continue
                raise ResolverError(f"mega: file URL fetch: {e}") from e
            if not g_resp or not isinstance(g_resp, list) or "g" not in (g_resp[0] or {}):
                continue
            out.append(Resource(
                url=g_resp[0]["g"],
                filename=name,
                kind=guess_kind(name),
                referer="https://mega.nz/",
                dedup_key=f"mega:{folder_id}/{node['h']}",
                stream_decrypt_key=aes_key,
                stream_decrypt_iv=iv,
            ))
            # If the caller asked for a single sub-file, stop after the first match.
            if sub_id:
                break
        return out
