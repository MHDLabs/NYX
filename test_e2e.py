#!/usr/bin/env python3
"""
End-to-end test for NYX Messenger.

Tests the full workflow:
1. Register two devices (Alice and Bob)
2. Alice sends an encrypted message to Bob
3. Bob syncs and decrypts Alice's message
4. Bob sends a reply
5. Alice syncs and decrypts Bob's reply
"""

import sys
import os
import json
import requests

# Add client directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'client'))

from crypto import (
    generate_identity, public_key_bundle_b64, parse_public_key_bundle,
    encrypt_message, decrypt_message, NYXCrypto,
)

SERVER = "http://localhost:8000"


def test_register(device_id, public_key):
    resp = requests.post(f"{SERVER}/register.php", json={
        "device_id": device_id,
        "public_key": public_key,
    }, timeout=5)
    data = resp.json()
    print(f"  Register {device_id[:16]}... -> {data}")
    assert data.get("status") == "ok", f"Registration failed: {data}"
    return data


def test_send(message_id, sender_id, recipient_id, ciphertext, nonce):
    resp = requests.post(f"{SERVER}/send.php", json={
        "message_id": message_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "ciphertext": ciphertext,
        "nonce": nonce,
    }, timeout=5)
    data = resp.json()
    print(f"  Send {message_id[:16]}... -> {data}")
    assert data.get("status") == "ok", f"Send failed: {data}"
    return data


def test_sync(user_id):
    resp = requests.post(f"{SERVER}/sync.php", json={
        "user_id": user_id,
    }, timeout=5)
    data = resp.json()
    print(f"  Sync {user_id[:16]}... -> {len(data.get('messages', []))} messages, {len(data.get('keys', {}))} keys")
    return data


def test_health():
    resp = requests.get(f"{SERVER}/", timeout=5)
    data = resp.json()
    print(f"  Health: {data['status']}, {data['stats']}")
    return data


def main():
    print("=" * 60)
    print("NYX End-to-End Test")
    print("=" * 60)

    # Step 0: Health check
    print("\n[Step 0] Health check...")
    test_health()

    # Step 1: Generate identities for Alice and Bob
    print("\n[Step 1] Generating identities...")
    alice = generate_identity()
    bob = generate_identity()
    alice_pub = public_key_bundle_b64(alice)
    bob_pub = public_key_bundle_b64(bob)

    print(f"  Alice: device_id={alice.device_id[:16]}...")
    print(f"  Bob:   device_id={bob.device_id[:16]}...")

    # Step 2: Register both devices
    print("\n[Step 2] Registering devices...")
    test_register(alice.device_id, alice_pub)
    test_register(bob.device_id, bob_pub)

    # Step 3: Alice sends encrypted message to Bob
    print("\n[Step 3] Alice sends encrypted message to Bob...")
    _, bob_x25519_pub = parse_public_key_bundle(bob_pub)
    alice_msg = encrypt_message("Hello Bob! This is Alice.", bob_x25519_pub, alice.device_id)
    print(f"  Encrypted: message_id={alice_msg.message_id[:16]}...")

    test_send(
        alice_msg.message_id,
        alice.device_id,
        bob.device_id,
        alice_msg.ciphertext_b64,
        alice_msg.nonce_b64,
    )

    # Step 4: Bob syncs and decrypts
    print("\n[Step 4] Bob syncs messages...")
    sync_data = test_sync(bob.device_id)
    messages = sync_data.get("messages", [])
    assert len(messages) == 1, f"Expected 1 message, got {len(messages)}"

    msg = messages[0]
    print(f"  Received from {msg['sender_id'][:16]}...")

    plaintext = decrypt_message(msg["ciphertext"], msg["nonce"], bob.x25519_private, msg["sender_id"])
    print(f"  Decrypted: '{plaintext}'")
    assert plaintext == "Hello Bob! This is Alice.", f"Wrong plaintext: {plaintext}"

    # Step 5: Bob replies to Alice
    print("\n[Step 5] Bob sends reply to Alice...")
    _, alice_x25519_pub = parse_public_key_bundle(alice_pub)
    bob_msg = encrypt_message("Hey Alice! Got your message.", alice_x25519_pub, bob.device_id)
    print(f"  Encrypted: message_id={bob_msg.message_id[:16]}...")

    test_send(
        bob_msg.message_id,
        bob.device_id,
        alice.device_id,
        bob_msg.ciphertext_b64,
        bob_msg.nonce_b64,
    )

    # Step 6: Alice syncs and decrypts Bob's reply
    print("\n[Step 6] Alice syncs messages...")
    sync_data = test_sync(alice.device_id)
    messages = sync_data.get("messages", [])
    assert len(messages) == 1, f"Expected 1 message, got {len(messages)}"

    msg = messages[0]
    print(f"  Received from {msg['sender_id'][:16]}...")

    plaintext = decrypt_message(msg["ciphertext"], msg["nonce"], alice.x25519_private, msg['sender_id'])
    print(f"  Decrypted: '{plaintext}'")
    assert plaintext == "Hey Alice! Got your message.", f"Wrong plaintext: {plaintext}"

    # Step 7: Verify sync returns empty on second call
    print("\n[Step 7] Verify no more pending messages...")
    sync_data = test_sync(alice.device_id)
    assert len(sync_data.get("messages", [])) == 0, "Should have 0 pending messages"
    sync_data = test_sync(bob.device_id)
    assert len(sync_data.get("messages", [])) == 0, "Should have 0 pending messages"

    # Step 8: Verify key discovery
    print("\n[Step 8] Verify key discovery...")
    sync_data = test_sync(alice.device_id)
    keys = sync_data.get("keys", {})
    assert bob.device_id in keys, "Bob's key should be in Alice's sync response"
    assert alice.device_id in keys, "Alice's key should be in Alice's sync response"
    print(f"  Keys discovered: {list(keys.keys())}")

    # Step 9: Test error handling
    print("\n[Step 9] Test error handling...")
    resp = requests.post(f"{SERVER}/register.php", json={}, timeout=5)
    data = resp.json()
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print(f"  Empty register -> {resp.status_code}: {data['error'][:50]}...")

    resp = requests.post(f"{SERVER}/send.php", json={}, timeout=5)
    data = resp.json()
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print(f"  Empty send -> {resp.status_code}: {data['error'][:50]}...")

    resp = requests.post(f"{SERVER}/sync.php", json={}, timeout=5)
    data = resp.json()
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print(f"  Empty sync -> {resp.status_code}: {data['error'][:50]}...")

    # Step 10: Test NYXCrypto wrapper
    print("\n[Step 10] Test NYXCrypto wrapper class...")
    import tempfile
    tmpdir = tempfile.mkdtemp()

    crypto_alice = NYXCrypto(
        device_id_path=os.path.join(tmpdir, "alice_id"),
        keys_path=os.path.join(tmpdir, "alice_keys"),
    )
    crypto_alice.generate_identity()

    # Encrypt for Bob using NYXCrypto, decrypt with Bob's raw private key
    _, bob_x25519_pub_raw = parse_public_key_bundle(bob_pub)
    ct, nonce = crypto_alice.encrypt("Hello from NYXCrypto wrapper!", bob_x25519_pub_raw)
    pt = decrypt_message(ct, nonce, bob.x25519_private, crypto_alice.device_id)
    assert pt == "Hello from NYXCrypto wrapper!", f"Got: {pt}"
    print(f"  NYXCrypto encrypt -> raw decrypt OK")

    # Wrong AAD should raise InvalidTag
    try:
        pt = decrypt_message(ct, nonce, bob.x25519_private, "wrong_sender_id")
        assert False, "Should have raised InvalidTag for wrong sender_device_id"
    except Exception:
        print(f"  Wrong AAD correctly rejected OK")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()