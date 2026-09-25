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
 * Tables:
 *   registered_devices(device_id, public_key, registered_at)
 *   messages(message_id, sender_id, recipient_id, ciphertext, nonce, created_at, delivered)
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
        // Railway provides postgres:// or postgresql:// URLs.
        // PDO requires pgsql:host=...;port=...;dbname=... format.
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
                delivered    INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_messages_recipient
                ON messages (recipient_id, delivered);
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
                delivered    INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_messages_recipient
                ON messages (recipient_id, delivered);
        ");
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