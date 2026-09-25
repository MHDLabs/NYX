<?php
/**
 * helpers.php — Shared helper functions for NYX Relay Server v3 API.
 *
 * Provides:
 *   - Session validation / expiry extension
 *   - nyx_address generation
 *   - ISO 8601 date formatting
 *   - Public key bundle encoding
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';

// ---------------------------------------------------------------------------
// Session helpers
// ---------------------------------------------------------------------------

/**
 * Generate a cryptographically secure random hex string.
 *
 * @param int $bytes Number of random bytes (output will be 2x hex chars).
 * @return string
 */
function nyx_random_hex(int $bytes = 32): string
{
    return bin2hex(random_bytes($bytes));
}

/**
 * Validate a session_id + session_token pair.
 *
 * Returns the session row on success, or sends a 401 response and exits.
 *
 * @param PDO   $db
 * @param array $body  The parsed JSON request body (must contain session_id and session_token)
 * @return array  The session row from the database
 */
function nyx_validate_session(PDO $db, array $body): array
{
    $sessionId    = $body['session_id'] ?? '';
    $sessionToken = $body['session_token'] ?? '';

    if ($sessionId === '' || $sessionToken === '') {
        json_response(401, ['error' => 'Missing session credentials.']);
    }

    $stmt = $db->prepare('SELECT * FROM sessions WHERE session_id = ? AND token = ?');
    $stmt->execute([$sessionId, $sessionToken]);
    $session = $stmt->fetch();

    if (!$session) {
        json_response(401, ['error' => 'Invalid session.']);
    }

    // Check expiry
    $now = new DateTimeImmutable();
    $expiresAt = new DateTimeImmutable($session['expires_at']);

    if ($now > $expiresAt) {
        // Clean up expired session
        $del = $db->prepare('DELETE FROM sessions WHERE session_id = ?');
        $del->execute([$sessionId]);
        json_response(401, ['error' => 'Session expired.']);
    }

    return $session;
}

/**
 * Extend a session's expiry by 24 hours.
 *
 * @param PDO    $db
 * @param string $sessionId
 * @return string  The new expiry ISO 8601 string
 */
function nyx_extend_session(PDO $db, string $sessionId): string
{
    $newExpiry = nyx_iso8601(86400); // +24 hours

    if (nyx_is_pgsql($db)) {
        $stmt = $db->prepare('UPDATE sessions SET expires_at = ? WHERE session_id = ?');
        $stmt->execute([$newExpiry, $sessionId]);
    } else {
        $stmt = $db->prepare("UPDATE sessions SET expires_at = ? WHERE session_id = ?");
        $stmt->execute([$newExpiry, $sessionId]);
    }

    return $newExpiry;
}

// ---------------------------------------------------------------------------
// Date/time helpers
// ---------------------------------------------------------------------------

/**
 * Return an ISO 8601 datetime string.
 *
 * @param int $offsetSeconds  Optional offset from now (e.g., 86400 for +24h).
 * @return string
 */
function nyx_iso8601(int $offsetSeconds = 0): string
{
    $ts = time() + $offsetSeconds;
    return gmdate('Y-m-d\TH:i:s\Z', $ts);
}

// ---------------------------------------------------------------------------
// nyx_address generation
// ---------------------------------------------------------------------------

/**
 * Generate a nyx_address from a device_id (or public key).
 * Format: nyx1 + hex-encoded first 20 bytes of SHA-256(device_id)
 *
 * @param string $deviceId
 * @return string
 */
function nyx_generate_address(string $deviceId): string
{
    $hash = hash('sha256', $deviceId, true);
    $bytes = substr($hash, 0, 20);
    return 'nyx1' . bin2hex($bytes);
}

// ---------------------------------------------------------------------------
// Public key bundle encoding
// ---------------------------------------------------------------------------

/**
 * Encode a public key bundle entry. Returns base64(Ed25519_pub[32] + X25519_pub[32]).
 * If only one key is available, pad with empty bytes.
 *
 * @param string $publicKey  The raw base64-encoded public key
 * @return string  The encoded bundle (88 chars base64)
 */
function nyx_encode_bundle(string $publicKey): string
{
    // If public_key is already base64, decode and pad to 64 bytes
    $decoded = base64_decode($publicKey);
    if (strlen($decoded) < 64) {
        $decoded = str_pad($decoded, 64, "\0");
    }
    return base64_encode($decoded);
}

// ---------------------------------------------------------------------------
// Legacy endpoint authentication
// ---------------------------------------------------------------------------

/**
 * Authenticate a legacy request using session token from Authorization header.
 * Returns the device_id on success, or sends 401 response and exits.
 */
function nyx_legacy_auth(PDO $db): string
{
    $authHeader = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (!preg_match('/^Bearer\s+(.+)$/i', $authHeader, $matches)) {
        json_response(401, ['error' => 'Missing or invalid Authorization header. Use Bearer token.']);
    }
    
    $sessionToken = $matches[1];
    
    $stmt = $db->prepare('SELECT device_id, token, expires_at FROM sessions WHERE token = ?');
    $stmt->execute([$sessionToken]);
    $session = $stmt->fetch();
    
    if (!$session) {
        json_response(401, ['error' => 'Invalid session token.']);
    }
    
    $now = new DateTimeImmutable();
    $expiresAt = new DateTimeImmutable($session['expires_at']);
    
    if ($now > $expiresAt) {
        $del = $db->prepare('DELETE FROM sessions WHERE token = ?');
        $del->execute([$sessionToken]);
        json_response(401, ['error' => 'Session expired.']);
    }
    
    return $session['device_id'];
}

// ---------------------------------------------------------------------------
// CORS headers (reusable)
// ---------------------------------------------------------------------------

/**
 * Set CORS headers for the API response.
 * Authorization header needed for v3 Bearer token auth.
 */
function nyx_cors_headers(): void
{
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');
}

/**
 * Handle CORS preflight (OPTIONS) request. Returns true if it was an OPTIONS request.
 */
function nyx_handle_preflight(): bool
{
    if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
        nyx_cors_headers();
        http_response_code(204);
        exit;
    }
    return false;
}

/**
 * Get or create the persistent server identity.
 * Returns array with 'identity', 'verify_key' (hex), 'signing_key' (hex).
 */
function nyx_get_server_identity(PDO $db): array
{
    $stmt = $db->prepare('SELECT identity, verify_key, signing_key FROM server_identity WHERE id = 1');
    $stmt->execute();
    $row = $stmt->fetch();
    
    if ($row) {
        return [
            'identity'    => $row['identity'],
            'verify_key'  => $row['verify_key'],
            'signing_key' => $row['signing_key'],
        ];
    }
    
    // Generate new Ed25519 keypair for server identity
    if (!function_exists('sodium_crypto_sign_keypair')) {
        // Fallback if sodium not available (should not happen as session requires it)
        $identity    = 'nyx1relay' . bin2hex(random_bytes(16));
        $verifyKey   = bin2hex(random_bytes(32));
        $signingKey  = bin2hex(random_bytes(32));
    } else {
        $keypair = sodium_crypto_sign_keypair();
        $verifyKey  = sodium_crypto_sign_publickey($keypair);
        $signingKey = sodium_crypto_sign_secretkey($keypair);
        $identity   = 'nyx1relay' . bin2hex(substr($verifyKey, 0, 16));
    }
    
    $verifyKeyHex = bin2hex($verifyKey);
    $signingKeyHex = bin2hex($signingKey);
    
    $ins = $db->prepare('INSERT INTO server_identity (id, identity, verify_key, signing_key) VALUES (1, ?, ?, ?)');
    $ins->execute([$identity, $verifyKeyHex, $signingKeyHex]);
    
    return [
        'identity'    => $identity,
        'verify_key'  => $verifyKeyHex,
        'signing_key' => $signingKeyHex,
    ];
}