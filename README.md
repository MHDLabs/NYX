# NYX Client 0.2.0

**Terminal-native secure messaging client** implementing the NYX Whitepaper v3.0 MVP + auto-update + multi-relay selection.

## Features

| Area | Status |
|------|--------|
| Ed25519 identity (`nyx1…`) + BIP39 recovery | Done |
| Encrypted local profile / SQLite storage | Done |
| Direct messages with **Double Ratchet** + signed envelopes | Done |
| Contacts + message history (survives restart) | Done |
| Session auth + reconnect backoff | Done |
| **HTTP(S) transport** to relays | Done |
| **Multi-server directory**, latency probe, composite score | Done |
| Server discovery from relays | Done |
| **Auto-update** (relay + GitHub), signed manifest, hash verify | Done |
| REPL + **curses TUI** | Done |
| Commands: `/dm` `/servers` `/update` `/connect` `/whois` `/search` … | Done |
| Groups / channels (local) + room settings | Done |
| User profiles (name + bio) in chat & search | Done |

## Requirements

- Python **3.11+**
- `cryptography`
- `pytest` (tests only)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install cryptography pytest
export PYTHONPATH=.
```

## Quick start (group testing)

```bash
# Version
python3 -m nyx_client.main --version

# First run creates identity + recovery mnemonic (SAVE IT OFFLINE)
python3 -m nyx_client.main --data-dir /tmp/nyx-alice

# Interactive
python3 -m nyx_client.main --data-dir /tmp/nyx-alice --repl
python3 -m nyx_client.main --data-dir /tmp/nyx-alice --tui

# Automated checks
python3 scripts/smoke_test.py
python3 scripts/demo_dm.py
python3 -m pytest nyx_client/tests/ -q
```

### REPL commands

```
/help
/status
/identity
/contacts
/addcontact <nyx1...> [name]
/dm <nyx1...> [message]
/servers
/servers refresh
/connect [endpoint]
/update
/update install
/exit
```

## Configuration

Copy `config.example.toml` → `~/.config/nyx/config.toml`

Important sections:

```toml
[network]
default_server = "nyx://YOUR_RELAY"
# bootstrap_servers = ["nyx://relay2...", "nyx://relay3..."]

[updates]
channel = "stable"
github_manifest_url = "https://raw.githubusercontent.com/ORG/REPO/main/manifest.json"
release_keys_file = "~/.config/nyx/release_keys.json"
auto_install = false
```

`release_keys.example.json` shows the format for trusted Ed25519 release public keys.

## Multi-server selection

1. Bootstrap + config servers load into `servers.json`
2. `/servers refresh` probes latency and asks reachable relays for more servers
3. Composite score (whitepaper §14): latency, reputation, uptime, trust, capacity
4. `/connect` uses the highest-scoring reachable relay

## Auto-update trust model

1. Fetch manifest from **relay** and/or **GitHub**
2. Verify **Ed25519 signature** with configured release keys
3. Download artifact and verify **hash**
4. Stage → health check → commit (rollback on failure)
5. Never installs an unsigned or hash-mismatched build

## Security notes for testers

- This is a **test build**. Treat it as experimental.
- Profile key is stored at `data_dir/.profile_key` (mode 0600). Production should use passphrase unlock only.
- X3DH prekey bundles over the network are not fully deployed; SK is derived via static ECDH then Double Ratchet runs.
- Only install updates when `release_keys_file` is configured with keys you trust.
- Do not share recovery mnemonics.

## Project layout

```
nyx_client/
  config/     settings, logging
  crypto/     keys, identity, BIP39, AEAD, Double Ratchet
  protocol/   envelope, session, connection, HTTP, discovery
  storage/    SQLite + encrypted profile
  core/       messaging, commands, app facade
  update/     signed auto-update
  ui/         REPL + curses TUI
```

## License

MIT — Copyright 2025 Mr.A
