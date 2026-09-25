
"""Double Ratchet unit tests."""
from __future__ import annotations
import os
from nyx_client.crypto.keys import X25519KeyPair
from nyx_client.crypto.ratchet import DoubleRatchetSession

def test_double_ratchet_bidirectional() -> None:
    sk = os.urandom(32)
    bob_dh = X25519KeyPair.generate()
    alice = DoubleRatchetSession.initiate(sk, bob_dh.public_bytes())
    bob = DoubleRatchetSession.respond(sk, bob_dh)
    assert bob.decrypt(alice.encrypt(b"ping")) == b"ping"
    assert alice.decrypt(bob.encrypt(b"pong")) == b"pong"

def test_double_ratchet_out_of_order() -> None:
    sk = os.urandom(32)
    bob_dh = X25519KeyPair.generate()
    alice = DoubleRatchetSession.initiate(sk, bob_dh.public_bytes())
    bob = DoubleRatchetSession.respond(sk, bob_dh)
    msgs = [alice.encrypt(f"m{i}".encode()) for i in range(4)]
    assert bob.decrypt(msgs[3]) == b"m3"
    assert bob.decrypt(msgs[1]) == b"m1"
    assert bob.decrypt(msgs[0]) == b"m0"
    assert bob.decrypt(msgs[2]) == b"m2"
