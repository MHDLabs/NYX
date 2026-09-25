<?php
/**
 * db.php — Database abstraction for the NYX Relay Server.
 *
 * Supports two backends:
 *   1. PostgreSQL — when the DATABASE_URL environment variable is set (Railway, production).
 *   2. SQLite     — fallback for local development (no DATABASE_URL).
 *
 * The schema is auto-created on first connection.
 *
 * Tables (Legacy):
 *   registered_devices(device_id, public_key, registered_at)
 *   messages(message_id, sender_id, recipient_id, ciphertext, nonce, created_at, delivered, envelope, room_id)
 *
 * Tables (v3 API):
 *   sessions(session_id, device_id, token, expires_at, created_at)
 *   profiles(device_id, name, bio, nyx_address, updated_at)
 *   prekey_bundles(device_id, identity_key, signed_prekey, signed_prekey_signature, created_at)
 *   one_time_prekeys(id, device_id, prekey, used, created_at)
 *   server_directory(url, name, trust_level, capacity, latency_ms, last_probed_at)
 *   conversation_participants(conversation_id, device_id, identity_id, joined_at)
 */

declare(strict_types=1);

// ---------------------------------------------------------------------------
// Singleton DB connection
// ---------------------------------------------------------------------------

function nyx_db(): PDO
{
    static $pdo = null;

    if ($pdo !== null) {
        return $pdo;
    }

    $databaseUrl = getenv('DATABASE_URL');

    if ($databaseUrl && $databaseUrl !== '') {
        // ---- PostgreSQL (Railway / production) ----
        $dsn = parse_database_url($databaseUrl);
        $pdo = new PDO($dsn['dsn'], $dsn['user'], $dsn['pass'], [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
        ]);
        $pdo->exec("SET client_encoding TO 'UTF8'");
    } else {
        // ---- SQLite (local development) ----
        $dbPath = __DIR__ . '/nyx_relay.sqlite';
        $pdo = new PDO("sqlite:{$dbPath}", null, null, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
        ]);
        $pdo->exec('PRAGMA journal_mode=WAL');
        $pdo->exec('PRAGMA foreign_keys=ON');
    }

    init_schema($pdo);

    return $pdo;
}


/**
 * Parse a DATABASE_URL (postgres://user:pass@host:port/dbname) into a PDO DSN.
 *
 * @return array{dsn: string, user: string, pass: string}
 */
function parse_database_url(string $url): array
{
    // Convert postgres:// / postgresql:// to something parse_url understands
    $url = preg_replace('#^(postgres(ql)?)://#', 'pgsql://', $url);

    $parts = parse_url($url);
    if ($parts === false || !isset($parts['host'])) {
        // Already a pgsql: DSN — use as-is
        return ['dsn' => $url, 'user' => null, 'pass' => null];
    }

    $host = $parts['host'];
    $port = $parts['port'] ?? 5432;
    $db   = ltrim($parts['path'] ?? '/railway', '/');
    $user = $parts['user'] ?? null;
    $pass = $parts['pass'] ?? null;

    // Query string may contain sslmode etc.
    $query = [];
    if (isset($parts['query'])) {
        parse_str($parts['query'], $query);
    }
    $sslmode = $query['sslmode'] ?? 'require';

    $dsn = "pgsql:host={$host};port={$port};dbname={$db};sslmode={$sslmode}";

    return ['dsn' => $dsn, 'user' => $user, 'pass' => $pass];
}


/**
 * Return a SQL expression for the current timestamp that works on both backends.
 */
function nyx_now_sql(PDO $db): string
{
    $driver = $db->getAttribute(PDO::ATTR_DRIVER_NAME);
    if ($driver === 'pgsql') {
        return 'NOW()';
    }
    return "datetime('now')";
}


/**
 * Return true if the connected driver is PostgreSQL.
 */
function nyx_is_pgsql(PDO $db): bool
{
    return $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'pgsql';
}


// ---------------------------------------------------------------------------
// Schema initialisation (handles both SQLite and PostgreSQL dialects)
// ---------------------------------------------------------------------------

function init_schema(PDO $db): void
{
    if (nyx_is_pgsql($db)) {
        $db->exec("
            CREATE TABLE IF NOT EXISTS registered_devices (
                device_id     VARCHAR(128) PRIMARY KEY,
                public_key    TEXT NOT NULL,
                registered_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id   VARCHAR(128) PRIMARY KEY,
                sender_id    VARCHAR(128) NOT NULL,
                recipient_id VARCHAR(128) NOT NULL,
                ciphertext   TEXT NOT NULL,
                nonce        TEXT NOT NULL,
                created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                delivered    INTEGER NOT NULL DEFAULT 0,
                envelope     TEXT,
                room_id      VARCHAR(128)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_recipient
                ON messages (recipient_id, delivered);

            CREATE TABLE IF NOT EXISTS sessions (
                session_id VARCHAR(128) PRIMARY KEY,
                device_id  VARCHAR(128) NOT NULL,
                token      VARCHAR(256) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_device
                ON sessions (device_id);

            CREATE TABLE IF NOT EXISTS profiles (
                device_id   VARCHAR(128) PRIMARY KEY,
                name        VARCHAR(256) NOT NULL DEFAULT '',
                bio         TEXT NOT NULL DEFAULT '',
                nyx_address VARCHAR(128) NOT NULL DEFAULT '',
                updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS prekey_bundles (
                device_id                VARCHAR(128) PRIMARY KEY,
                identity_key             TEXT NOT NULL DEFAULT '',
                signed_prekey            TEXT NOT NULL DEFAULT '',
                signed_prekey_signature  TEXT NOT NULL DEFAULT '',
                created_at               TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS one_time_prekeys (
                id         SERIAL PRIMARY KEY,
                device_id  VARCHAR(128) NOT NULL,
                prekey     TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_one_time_prekeys_device
                ON one_time_prekeys (device_id, used);

            CREATE TABLE IF NOT EXISTS server_directory (
                url             TEXT PRIMARY KEY,
                name            VARCHAR(256) NOT NULL DEFAULT '',
                trust_level     INTEGER NOT NULL DEFAULT 1,
                capacity        INTEGER NOT NULL DEFAULT 100,
                latency_ms      INTEGER NOT NULL DEFAULT 0,
                last_probed_at  TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversation_participants (
                conversation_id VARCHAR(256) NOT NULL,
                device_id       VARCHAR(128) NOT NULL,
                identity_id     VARCHAR(256) NOT NULL DEFAULT '',
                joined_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (conversation_id, device_id)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_participant_device
                ON conversation_participants (device_id);
        ");
    } else {
        $db->exec("
            CREATE TABLE IF NOT EXISTS registered_devices (
                device_id     TEXT PRIMARY KEY,
                public_key    TEXT NOT NULL,
                registered_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id   TEXT PRIMARY KEY,
                sender_id    TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                ciphertext   TEXT NOT NULL,
                nonce        TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                delivered    INTEGER NOT NULL DEFAULT 0,
                envelope     TEXT,
                room_id      TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_recipient
                ON messages (recipient_id, delivered);

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                device_id  TEXT NOT NULL,
                token      TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_device
                ON sessions (device_id);

            CREATE TABLE IF NOT EXISTS profiles (
                device_id   TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                bio         TEXT NOT NULL DEFAULT '',
                nyx_address TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS prekey_bundles (
                device_id                TEXT PRIMARY KEY,
                identity_key             TEXT NOT NULL DEFAULT '',
                signed_prekey            TEXT NOT NULL DEFAULT '',
                signed_prekey_signature  TEXT NOT NULL DEFAULT '',
                created_at               TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS one_time_prekeys (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id  TEXT NOT NULL,
                prekey     TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_one_time_prekeys_device
                ON one_time_prekeys (device_id, used);

            CREATE TABLE IF NOT EXISTS server_directory (
                url             TEXT PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT '',
                trust_level     INTEGER NOT NULL DEFAULT 1,
                capacity        INTEGER NOT NULL DEFAULT 100,
                latency_ms      INTEGER NOT NULL DEFAULT 0,
                last_probed_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS conversation_participants (
                conversation_id TEXT NOT NULL,
                device_id       TEXT NOT NULL,
                identity_id     TEXT NOT NULL DEFAULT '',
                joined_at       TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (conversation_id, device_id)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_participant_device
                ON conversation_participants (device_id);
        ");
    }

    // Migrate: add envelope/room_id columns if missing (safe re-run)
    migrate_messages_columns($db);
}

/**
 * Add envelope and room_id columns to messages table if they do not already exist.
 * Safe to call repeatedly (checks information_schema / PRAGMA).
 */
function migrate_messages_columns(PDO $db): void
{
    if (nyx_is_pgsql($db)) {
        $check = $db->query("
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'messages' AND column_name = 'envelope'
        ")->fetch();
        if (!$check) {
            $db->exec('ALTER TABLE messages ADD COLUMN envelope TEXT');
        }
        $check = $db->query("
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'messages' AND column_name = 'room_id'
        ")->fetch();
        if (!$check) {
            $db->exec('ALTER TABLE messages ADD COLUMN room_id VARCHAR(128)');
        }
    } else {
        $cols = $db->query("PRAGMA table_info(messages)")->fetchAll();
        $colNames = array_column($cols, 'name');
        if (!in_array('envelope', $colNames, true)) {
            $db->exec('ALTER TABLE messages ADD COLUMN envelope TEXT');
        }
        if (!in_array('room_id', $colNames, true)) {
            $db->exec('ALTER TABLE messages ADD COLUMN room_id TEXT');
        }
    }
}


// ---------------------------------------------------------------------------
// JSON request / response helpers
// ---------------------------------------------------------------------------

/**
 * Parse a JSON request body and return the decoded associative array.
 * Exits with HTTP 400 on malformed JSON.
 */
function json_request(): array
{
    $raw = file_get_contents('php://input');
    $data = json_decode($raw, true);

    if (!is_array($data)) {
        json_response(400, ['error' => 'Invalid or missing JSON body.']);
    }

    return $data;
}

/**
 * Send a JSON response with the given HTTP status code and data.
 * Exits immediately.
 */
function json_response(int $statusCode, array $data): void
{
    http_response_code($statusCode);
    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}