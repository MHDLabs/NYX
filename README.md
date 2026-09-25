# NYX - Terminal-Native Secure Communication

NYX is a secure, terminal-native distributed communication protocol with end-to-end encryption. This implementation provides both a PHP relay server and a Python client for secure messaging.

**Version**: 0.0.7

## Project Structure

```
NYX/
├── server/              # PHP Relay Server
│   ├── index.php       # Entry point
│   ├── router.php      # Router for built-in server
│   ├── db.php          # PDO database connection
│   ├── helpers.php     # Shared utilities
│   ├── register.php    # Legacy registration endpoint
│   ├── send.php        # Legacy message send endpoint (updated for E2EE)
│   ├── sync.php        # Legacy message sync endpoint (updated for E2EE)
│   └── api/            # v3 API endpoints
│       ├── health.php
│       ├── session.php
│       ├── messages.php
│       ├── keys.php
│       ├── discovery.php
│       ├── profile.php
│       └── manifest.php
├── client/             # Python Client
│   ├── main.py         # Entry point (with password-based DB encryption)
│   ├── config.py       # Configuration management
│   ├── crypto.py       # X25519 + Ed25519 + ChaCha20-Poly1305 (PyNaCl)
│   ├── db.py           # Local SQLite storage (with optional encryption)
│   ├── commands.py     # Command handlers (E2EE message send/sync)
│   ├── ui.py           # TUI and REPL interface
│   ├── requirements.txt
│   └── pyproject.toml
├── Dockerfile          # Server container config
├── railway.json        # Railway deployment config
└── .gitignore          # Git exclusion rules
```

## Features

### Server (PHP)
- **RESTful API**: Clean API for message relay
- **Relay Mechanism**: Securely relay encrypted messages between clients
- **Ed25519 Signature Verification**: Proper authentication via libsodium
- **Health Checks**: Built-in monitoring endpoints
- **Flexible Deployment**: Supports Docker and Railway
- **Portability**: Uses PHP built-in server for easy setup

### Client (Python)
- **End-to-End Encryption**: X25519 key exchange + ChaCha20-Poly1305 AEAD (via PyNaCl)
- **Identity Management**: Ed25519-based identities (nyx1...)
- **Dual UI**: Interactive REPL and rich TUI (Textual)
- **Encrypted Local Storage**: SQLite database with optional Fernet encryption
- **Asynchronous**: Non-blocking message synchronization
- **Local profile**: Display name + avatar stored in SQLite
- **Groups**: Create / join / list groups with local messaging
- **v3 API client**: Session auth, envelope send/sync with legacy fallback

## Installation

### Prerequisites
- Python 3.11+
- PHP 8.1+ with **sodium extension** (required for Ed25519 verification)
- SQLite3

### Client Setup

1. Clone the repository and navigate to `client/`:
```bash
git clone https://github.com/openclaw02221/NYX.git
cd NYX/client
```

2. Setup virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Run the client:
```bash
python main.py        # Starts TUI (prompts for encryption password)
python main.py --repl # Starts REPL
python main.py --no-encrypt  # Disable database encryption
```

### Server Setup

1. Navigate to the server directory:
```bash
cd NYX/server
```

2. Run using PHP's built-in server (ensure sodium extension is enabled):
```bash
php -S localhost:8000 router.php
```

3. Or deploy using Docker:
```bash
docker build -t nyx-server .
docker run -p 8000:8000 nyx-server
```

## Usage

### Client Commands (REPL/TUI)

```
/help                  - Show all commands
/status                - Show connection and identity status
/identity              - Display your identity
/contacts              - List all contacts
/add <name> <id> [--public-key <hex>]  - Add a contact with their X25519 public key
/send <id> <msg>       - Send an encrypted message
/conversations         - List all conversations
/messages <id>         - View messages in a conversation
/sync                  - Sync and decrypt messages from server
/groups                - List groups
/create_group <name>   - Create a group
/join_group <id>       - Join a group
/gsend <id> <msg>      - Send group message
/profile [name] [avatar]  - Show/set profile
/exit                  - Exit the client
```

### Adding Contacts

To send encrypted messages, you need the recipient's X25519 public key:

```bash
/add "Alice" nyx1abc123... --public-key a1b2c3d4...
```

The public key is the 32-byte X25519 public key in hex format (64 hex characters).

### Encryption Password

On first run, the client will prompt for an encryption password. This password is used to encrypt the local SQLite database using Fernet (AES-128). If you leave it empty, the database will not be encrypted.

## Security

- **End-to-End Encryption**: Messages are encrypted with X25519 + ChaCha20-Poly1305 before leaving your device
- **Authentication**: Ed25519 signatures verify message integrity and sender identity
- **Encrypted Local Storage**: Database encryption with password-derived keys (PBKDF2)
- **Blind Relay**: Server never sees unencrypted message content
- **Signature Verification**: Server validates Ed25519 signatures using libsodium

## Deployment

### Railway (Server)
Deploy the server to Railway using the provided `railway.json` and `Dockerfile`.

### Docker (Server)
```bash
docker build -t nyx-server .
docker run -p 8000:8000 -e PORT=8000 nyx-server
```

## License
Open Source