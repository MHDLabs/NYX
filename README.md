# NYX Client 0.2.x

Terminal-native **secure messaging** client aligned with the **NYX Whitepaper v3.0**.

This is a production-oriented MVP foundation: identity, E2EE DMs, local groups/channels,
relay register/session, wallet, marketplace, moderation, media sessions, and a professional TUI.

> Architecture is designed to grow past 100k LOC without rewrites. Extension points are
> documented in code comments (`# Extension point`).

---

## Features

| Area | Status |
|------|--------|
| Ed25519 identity (`nyx1…`) + BIP39 recovery | Done |
| Encrypted local profile / SQLite | Done |
| DM **Double Ratchet** + signed envelopes | Done |
| Contacts, history, profiles (name/bio) | Done |
| Groups / channels + **owner-only settings** | Done |
| Roles: owner / admin / poster / member | Done |
| **Mute / unmute / kick** (admin+) | Done |
| Post policy: owner_only / posters / members | Done |
| Relay: register → session → discovery → profile push | Done |
| Multi-server directory + scoring | Done |
| Signed auto-update manifests | Done |
| **NYX wallet** (address, ledger, export/import keystore, fund) | Done |
| Marketplace (NYX-only payments) | Done |
| Attachments + **voice notes** | Done |
| **Online meetings** (join codes; media stack extension point) | Done |
| Recovery email (synced on connect) | Done |
| Themes + custom background preference | Done |
| REPL + animated curses TUI | Done |

### Explicit non-goals (yet)

- Full WebRTC voice/video path (meeting registry is ready for SFU plug-in)
- On-chain token settlement (wallet is local custody + keystore)
- Federated moderation gossip

---

## Requirements

- Python **3.11+**
- `cryptography`
- `pytest` (tests)
- Windows TUI: `pip install windows-curses`

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install cryptography pytest
export PYTHONPATH=.         # Windows: set PYTHONPATH=.
```

---

## Quick start

```bash
python3 -m nyx_client.main --version
python3 -m nyx_client.main --data-dir /tmp/nyx-alice
python3 -m nyx_client.main --data-dir /tmp/nyx-alice --tui
python3 -m nyx_client.main --data-dir /tmp/nyx-alice --repl

python3 -m pytest nyx_client/tests/ -q
python3 scripts/smoke_test.py
python3 scripts/demo_dm.py
```

First run prints a **BIP39 mnemonic** — store offline; never share.

---

## Relay connection

Configure `default_server` in config, then:

```
/connect nyx://YOUR_RELAY
```

Client flow: health → **register** (identity + pubkey + device) → **session** (signed) →
Bearer token → discovery servers → profile + recovery email push → local persist.

---

## Commands (REPL)

### Identity & social
```
/identity  /register  /whois <id>  /setname  /setbio  /setemail
/contacts  /addcontact  /search  /dm
```

### Rooms & moderation
```
/newgroup <title>
/newchannel <title>
/roomrole <room> <id> <owner|admin|poster|member>   # owner only
/roompolicy <room> <owner_only|posters|members>     # owner only
/mute <room> <id> [seconds] [reason]                # admin+
/unmute <room> <id>
/kick <room> <id> [reason]
```

### Wallet (Bitcoin-style local custody)
```
/wallet                          # address + balance + history
/fund <amount_nyx>               # deposit / charge
/walletexport <dir> <passphrase> # encrypted .nks into a folder
/walletimport <file.nks> <pass> [--replace]
```

Address format: `nyxw1…` (derived from identity).  
Keystore is AEAD-encrypted with PBKDF2-stretched passphrase.  
**Identity private keys are not inside the keystore** — use BIP39 for account recovery.

### Marketplace
```
/market [category]
/sell <price_nyx> <title>
/buy <listing_id>
```

### Media
```
/attach <target> <path>
/voice <target> <audio_file> [duration_sec]
/emoji
/meeting create <title>
/meeting list | start <id> | join <code> | end <id>
```

### Network
```
/connect [endpoint]  /servers  /servers refresh  /status  /update
```

---

## TUI keys

| Key | Action |
|-----|--------|
| ↑↓ Enter | Navigate / open chat |
| 1–4 | Filter all / DM / group / channel |
| n | Create group/channel |
| / or f | Search |
| i | User profile |
| o | Room settings (owner) |
| m | Compose |
| s | Settings |
| t | Themes |
| q | Quit |

Unread badge: `(N new)` next to chat titles.

---

## Data layout (all local state)

Everything is stored under the **client working tree**, not on another drive:

```
./nyx_data/
  db/           SQLite databases
  media/        attachments & voice files
  wallet/       wallet working files
  keystore/     exported .nks backups (you choose path; prefer here)
  cache/
  logs/
  config/       config.toml
```

Override only if needed: `storage.data_dir` in config or `--data-dir`.

## NYX token (UTXO) — anti-scam

Public clients **cannot** free-mint coins (`token.allow_local_mine = false`).

Coins enter wallets only via:
1. Protocol genesis UTXO
2. **Server-signed mint voucher** (`/claimmint file.json`)
3. `/claimrelay` after connect (relay `GET /api/v3/token/vouchers/pending`)

See `docs/TOKEN_POLICY.md`.

## NYX token (UTXO)

Spendable coins are **Ed25519-signed UTXO transactions** (`nyx-utxo-v1`).
Spent outpoints cannot be reused. `/pay` builds+applies locally and broadcasts
to `POST /api/v3/token/broadcast` when connected. Relays must validate and
consensus the UTXO set (see `docs/RELAY_API.md`).

## Configuration

`config.example.toml` → `~/.config/nyx/config.toml`

```toml
[network]
default_server = "nyx://YOUR_RELAY"

[updates]
channel = "stable"
```

---

## Architecture (layers)

```
ui/          TUI + REPL
core/        app facade, messaging, wallet, market, meetings
protocol/    envelopes, session, transport, discovery
crypto/      keys, AEAD, ratchet, BIP39
storage/     sqlite, rooms, roles, attachments, prefs
update/      signed manifests
```

Each module is independently testable. Prefer extension over breaking APIs.

---

## Development notes

- **Never** leave broken code between milestones; keep tests green.
- Room **settings** mutations must call `room_roles.require_owner`.
- Admins may mute/kick **lower ranks only**.
- Meeting media: implement SFU client behind `MediaSessionStore` without changing message schema.
- Wallet on-chain: replace `fund()` path with relay/mint proofs; keep address + ledger API stable.

---

## License / whitepaper

Protocol semantics: **NYX Whitepaper v3.0** (project specification).
