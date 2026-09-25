"""
Database schema for the NYX client local store.

Whitepaper Section 30 / 57 (adapted for client-side SQLite):
  - Identity / keys   : encrypted at rest
  - Private ciphertext: stored as-is (already E2EE)
  - Contacts, conversations, messages

When SQLCipher becomes available the same schema is used inside an
encrypted database file. Until then, sensitive columns are encrypted
at the application layer with the AEAD module.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    identity_id     TEXT    NOT NULL UNIQUE,
    public_key      BLOB    NOT NULL,
    -- encrypted private key material (AEAD blob)
    enc_identity_key BLOB   NOT NULL,
    -- encrypted recovery seed (optional)
    enc_recovery_seed BLOB,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    public_key      BLOB NOT NULL,
    enc_private_key BLOB NOT NULL,
    name            TEXT,
    created_at      INTEGER NOT NULL,
    revoked_at      INTEGER,
    last_seen_at    INTEGER
);

CREATE TABLE IF NOT EXISTS contacts (
    identity_id     TEXT PRIMARY KEY,
    public_key      BLOB,
    display_name    TEXT,
    notes           TEXT,
    trusted         INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    type            TEXT NOT NULL DEFAULT 'dm',
    peer_id         TEXT,
    title           TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    last_sequence   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    sender_id       TEXT NOT NULL,
    device_id       TEXT,
    -- ciphertext blob (E2EE) or plaintext for public (MVP: always treat as opaque)
    payload         BLOB NOT NULL,
    signature       BLOB,
    previous_hash   TEXT,
    sequence        INTEGER NOT NULL,
    timestamp       INTEGER NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    status          TEXT NOT NULL DEFAULT 'sent',
    protocol_version INTEGER NOT NULL DEFAULT 3,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv_seq
    ON messages(conversation_id, sequence);

CREATE INDEX IF NOT EXISTS idx_messages_conv_ts
    ON messages(conversation_id, timestamp);

CREATE TABLE IF NOT EXISTS session_state (
    key   TEXT PRIMARY KEY,
    value BLOB NOT NULL,
    updated_at INTEGER NOT NULL
);
"""
