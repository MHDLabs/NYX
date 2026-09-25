"""
Signal Double Ratchet for NYX DMs.

Whitepaper Section 11: X3DH/PQXDH + Double Ratchet.
This module implements the Double Ratchet Algorithm
(https://signal.org/docs/specifications/doubleratchet/) using:
  - X25519 for DH ratchet
  - HKDF-SHA256 for KDF_RK and KDF_CK
  - AEAD (ChaCha20-Poly1305) for message encryption

Header format (associated data binds header to ciphertext):
  dh_public (32) || pn (4 big-endian) || n (4 big-endian)

Skipped message keys are retained up to MAX_SKIP for out-of-order delivery.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from nyx_client.crypto.aead import encrypt as aead_encrypt, decrypt as aead_decrypt
from nyx_client.crypto.keys import X25519KeyPair
from nyx_client.config.logging import get_logger

log = get_logger(__name__)

MAX_SKIP = 256
_INFO_RK = b"nyx-dr-root-v1"
_INFO_CK = b"nyx-dr-chain-v1"
_INFO_MK = b"nyx-dr-msg-v1"


def _kdf_rk(root_key: bytes, dh_out: bytes) -> Tuple[bytes, bytes]:
    """KDF_RK: root key + DH -> (new_root, chain_key)."""
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=root_key,
        info=_INFO_RK,
    ).derive(dh_out)
    return material[:32], material[32:]


def _kdf_ck(chain_key: bytes) -> Tuple[bytes, bytes]:
    """KDF_CK: chain_key -> (new_chain_key, message_key)."""
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=b"",
        info=_INFO_CK,
    ).derive(chain_key)
    return material[:32], material[32:]


@dataclass
class RatchetHeader:
    dh_public: bytes  # 32 bytes
    pn: int           # previous chain length
    n: int            # message number in current chain

    def pack(self) -> bytes:
        return self.dh_public + struct.pack(">II", self.pn, self.n)

    @classmethod
    def unpack(cls, data: bytes) -> "RatchetHeader":
        if len(data) != 40:
            raise ValueError("invalid ratchet header length")
        return cls(
            dh_public=data[:32],
            pn=struct.unpack(">I", data[32:36])[0],
            n=struct.unpack(">I", data[36:40])[0],
        )


@dataclass
class DoubleRatchetSession:
    """Full Double Ratchet session state."""

    root_key: bytes
    send_chain_key: Optional[bytes] = None
    recv_chain_key: Optional[bytes] = None
    send_n: int = 0
    recv_n: int = 0
    prev_send_n: int = 0
    dh_send: Optional[X25519KeyPair] = None
    dh_recv_public: Optional[bytes] = None
    skipped: Dict[Tuple[bytes, int], bytes] = field(default_factory=dict)

    @classmethod
    def initiate(
        cls,
        shared_secret: bytes,
        remote_dh_public: bytes,
    ) -> "DoubleRatchetSession":
        """
        Alice initiates after X3DH: shared_secret is SK from X3DH,
        remote_dh_public is Bob's signed prekey / ratchet key.
        """
        dh = X25519KeyPair.generate()
        dh_out = dh.exchange(remote_dh_public)
        rk, ck = _kdf_rk(shared_secret, dh_out)
        return cls(
            root_key=rk,
            send_chain_key=ck,
            send_n=0,
            dh_send=dh,
            dh_recv_public=remote_dh_public,
        )

    @classmethod
    def respond(
        cls,
        shared_secret: bytes,
        local_dh: X25519KeyPair,
    ) -> "DoubleRatchetSession":
        """
        Bob responds: holds the DH keypair whose public was used by Alice.
        Receiving chain is established on first inbound message.
        """
        return cls(
            root_key=shared_secret,
            dh_send=local_dh,
            send_chain_key=None,
            recv_chain_key=None,
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Returns: header(40) || aead_blob
        """
        if self.send_chain_key is None or self.dh_send is None:
            raise RuntimeError("sending chain not initialized")
        self.send_chain_key, mk = _kdf_ck(self.send_chain_key)
        header = RatchetHeader(
            dh_public=self.dh_send.public_bytes(),
            pn=self.prev_send_n,
            n=self.send_n,
        )
        self.send_n += 1
        aad = header.pack()
        blob = aead_encrypt(mk, plaintext, associated_data=aad)
        return aad + blob

    def decrypt(self, data: bytes) -> bytes:
        if len(data) < 40:
            raise ValueError("ciphertext too short for ratchet header")
        header = RatchetHeader.unpack(data[:40])
        body = data[40:]
        aad = header.pack()

        # Try skipped keys first
        skip_key = (header.dh_public, header.n)
        if skip_key in self.skipped:
            mk = self.skipped.pop(skip_key)
            return aead_decrypt(mk, body, associated_data=aad)

        # DH ratchet step if remote public changed
        if self.dh_recv_public != header.dh_public:
            self._skip_message_keys(header.pn)
            self._dh_ratchet(header.dh_public)

        self._skip_message_keys(header.n)
        if self.recv_chain_key is None:
            raise RuntimeError("receiving chain not initialized")
        self.recv_chain_key, mk = _kdf_ck(self.recv_chain_key)
        self.recv_n += 1
        return aead_decrypt(mk, body, associated_data=aad)

    def _dh_ratchet(self, remote_public: bytes) -> None:
        self.prev_send_n = self.send_n
        self.send_n = 0
        self.recv_n = 0
        self.dh_recv_public = remote_public

        if self.dh_send is None:
            self.dh_send = X25519KeyPair.generate()

        # Receive chain from remote DH
        dh_out = self.dh_send.exchange(remote_public)
        self.root_key, self.recv_chain_key = _kdf_rk(self.root_key, dh_out)

        # New send chain
        self.dh_send = X25519KeyPair.generate()
        dh_out2 = self.dh_send.exchange(remote_public)
        self.root_key, self.send_chain_key = _kdf_rk(self.root_key, dh_out2)

    def _skip_message_keys(self, until: int) -> None:
        if self.recv_chain_key is None:
            return
        if until - self.recv_n > MAX_SKIP:
            raise ValueError("too many skipped messages")
        while self.recv_n < until:
            self.recv_chain_key, mk = _kdf_ck(self.recv_chain_key)
            if self.dh_recv_public is not None:
                self.skipped[(self.dh_recv_public, self.recv_n)] = mk
            self.recv_n += 1
