# Project NYX v0.0.3 — Terminal-Native Encrypted Communication Protocol

A privacy-oriented, end-to-end encrypted messaging system with a Python terminal client (interactive REPL) and a PHP relay server. All encryption/decryption happens client-side; the server acts only as a blind relay for ciphertext.

Supports dual-database deployment: **SQLite** (development) or **PostgreSQL** (production on Railway).

## Architecture

```
┌──────────────┐    ciphertext    ┌──────────────┐    ciphertext    ┌──────────────┐
│   Client A   │ ──────────────► │  PHP Relay   │ ──────────────► │   Client B   │
│  (Python)    │                  │   Server     │                  │  (Python)    │
│              │                  │  (blind)     │                  │              │
│ X25519 + Cha │                  │  Postgres/   │                  │ X25519 + Cha │
│  REPL mode   │                  │  SQLite      │                  │  REPL mode   │
└──────────────┘                  └──────────────┘                  └──────────────┘
```

## Directory Structure

```
nyx/
├── README.md
├── server/
│   ├── index.php          # Router / entry point
│   ├── register.php       # Public key registration
│   ├── send.php           # Ciphertext delivery
│   ├── sync.php           # Ciphertext retrieval
│   └── db.php             # PDO helper (SQLite + PostgreSQL)
├── client/
│   ├── main.py            # Interactive REPL (prompt_toolkit)
│   ├── config.py          # Config file management (~/.nyx/config.json)
│   ├── crypto.py          # X25519 + ChaCha20Poly1305 E2EE
│   ├── db.py              # Local SQLite (NYXDatabase class)
│   ├── ui.py              # Minimal shim (display in commands.py)
│   ├── commands.py        # Command implementations (register, send, sync …)
│   └── requirements.txt   # Python dependencies
├── Dockerfile             # Server container (PHP + Apache)
└── railway.json           # Railway deployment config
```

## Prerequisites

- **Python 3.10+**
- **PHP 8.0+** with PDO and SQLite extensions
- **pip** (Python package manager)
- **Docker** (optional, for containerised deployment)

## Setup

### 1. Install Python Dependencies

```bash
cd nyx/client
pip install -r requirements.txt
```

### 2. Start the PHP Relay Server

For local development with SQLite:

```bash
cd nyx/server
php -S localhost:8080
```

The server will be available at `http://localhost:8080`. By default it stores data in `nyx_relay.db`.

For PostgreSQL, set `DATABASE_URL` and `DRIVER=postgres`:

```bash
export DATABASE_URL="pgsql://user:pass@host:5432/nyx"
export DRIVER=postgres
php -S localhost:8080
```

Docker one‑liner:

```bash
cd nyx
docker build -t nyx-server .
docker run -p 8080:80 nyx-server
```

### 3. Set the Server URL

```bash
export NYX_SERVER="http://localhost:8080"
```

Or pass `--server` when starting the REPL:

```bash
python main.py --server http://localhost:8080
```

## Usage

### Start the Interactive REPL

```bash
cd nyx/client
python main.py
```

On first launch, NYX automatically generates a new identity and registers it with the relay server.

### Available Commands

Inside the REPL, type `help` to see all commands:

| Command | Description |
|---|---|
| `help` | Show this help message |
| `register` | Register your identity with the relay server |
| `myid` | Show your device ID and public key |
| `sync` | Pull new messages from the server |
| `send <contact> <message>` | Send an encrypted message |
| `contacts` | List known contacts (device IDs) |
| `import <public_key>` | Import a contact's public key |
| `decrypt <ciphertext> <nonce>` | Decrypt a message manually |
| `config [key] [value]` | View or set configuration |
| `server [url]` | View or set the relay server URL |
| `clear` | Clear the terminal screen |
| `debug` | Show debug information |
| `quit / exit` | Exit NYX |

### Example Session

```
nyx> register
[INFO] Registering device a1b2c3d4...
[OK] Registered successfully.

nyx> myid
Device Identity
  Device ID:    a1b2c3d4e5f67890
  Public Key:   AQIDBAUGBwgJCgsMDQ4PEBESExQ...

nyx> sync
[INFO] No new messages.

nyx> send 99887766 "Hello from NYX!"
[INFO] Encrypting and sending to 99887766...
[OK] Message sent to 99887766...

nyx> quit
Goodbye. Stay encrypted.
```

## Security Notes

- The PHP server **never** sees plaintext — only base64-encoded ciphertext.
- Private keys are stored unencrypted locally in `~/.nyx/keys` (file‑system permissions protect them).
- Each message uses a fresh random ephemeral X25519 key for forward secrecy.
- ChaCha20-Poly1305 AEAD includes associated data (sender identity) to prevent replay.
- The server can be hosted behind Tor or any reverse proxy for additional privacy.

## Deployment (Railway)

The included `railway.json` and `Dockerfile` are pre-configured for [Railway](https://railway.app). Set the following environment variables in your Railway dashboard:

| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL` | `pgsql://…` | Supplied by Railway PostgreSQL plugin |
| `DRIVER` | `postgres` | Tells db.php to use PostgreSQL |

No changes to the PHP code are needed — `db.php` auto-selects the database driver based on the `DRIVER` environment variable.

## License

MIT