<?php
/**
 * session.php — v3 Authentication & Session Endpoints
 *
 * Handles:
 *   POST /api/v3/auth/session  — Create a session with Ed25519 signature verification
 *   POST /api/v3/auth/refresh  — Refresh/extend an existing session
 *
 * Client sends signed authentication payload per protocol v3 spec.
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle POST /api/v3/auth/session
 *
 * Body: {
 *   "identity": "nyx1...",
 *   "device_id": "...",
 *   "device_public_key": "hex_encoded_32_bytes",
 *   "timestamp": 1234567890123,
 *   "protocol_version": 3,
 *   "signature": "hex_encoded_ed25519_signature"
 * }
 *
 * The signature covers: identity|device_id|device_public_key|timestamp|protocol_version
 */
function handle_v3_session_create(): void
{
    $db   = nyx_db();
    $body = json_request();

    $identity         = trim($body['identity'] ?? '');
    $deviceId         = trim($body['device_id'] ?? '');
    $devicePublicKey  = trim($body['device_public_key'] ?? '');
    $timestamp        = $body['timestamp'] ?? 0;
    $protocolVersion  = $body['protocol_version'] ?? 3;
    $signature        = trim($body['signature'] ?? '');

    if ($identity === '' || $deviceId === '' || $devicePublicKey === '') {
        json_response(400, ['error' => 'identity, device_id, and device_public_key are required.']);
    }

    if ($signature === '') {
        json_response(400, ['error' => 'signature is required.']);
    }

    // Verify timestamp is within acceptable window (±10 minutes)
    $now = intval(microtime(true) * 1000);
    $timeDiff = abs($now - intval($timestamp));
    if ($timeDiff > 600000) { // 10 minutes in milliseconds
        json_response(401, ['error' => 'timestamp out of acceptable range.']);
    }

    // Build canonical string that was signed
    $canonical = "{$identity}|{$deviceId}|{$devicePublicKey}|{$timestamp}|{$protocolVersion}";

    // Verify Ed25519 signature using the identity key
    // Identity format: nyx1 + 32 hex chars (first 16 bytes of SHA256 of Ed25519 verify key)
    // We need to extract the verify key from the identity - for now, we can't verify without
    // the full verify key. The client identity is derived from the verify key but truncated.
    // In a full implementation, we'd store the full verify key or use a different identity format.
    // Accept identity_key (full 32-byte Ed25519 verify key in hex or base64) if provided
    $identityKeyHex = trim($body['identity_key'] ?? '');
    
    if (strncmp($identity, 'nyx1', 4) !== 0) {
        json_response(400, ['error' => 'Invalid identity format. Must start with nyx1']);
    }
    
    // Extract verify key bytes
    $verifyKeyBin = null;
    if ($identityKeyHex !== '') {
        $verifyKeyBin = @hex2bin($identityKeyHex);
        if ($verifyKeyBin === false || strlen($verifyKeyBin) !== 32) {
            // Try base64
            $verifyKeyBin = @base64_decode($identityKeyHex, true);
        }
    }
    
    // If identity_key not passed separately, check if identity has 64 hex chars (32 bytes)
    if ((!$verifyKeyBin || strlen($verifyKeyBin) !== 32) && strlen($identity) == 68) {
        $verifyKeyBin = @hex2bin(substr($identity, 4));
    }
    
    if (!$verifyKeyBin || strlen($verifyKeyBin) !== 32) {
        json_response(400, ['error' => 'Valid 32-byte identity_key (hex or base64) is required for signature verification.']);
    }

    // Check if sodium extension is available
    if (!function_exists('sodium_crypto_sign_verify_detached')) {
        json_response(503, [
            'error' => 'Server missing required sodium extension. Please install php-sodium.'
        ]);
    }

    // Verify signature with sodium
    $signatureBin = @hex2bin($signature);
    if ($signatureBin === false || strlen($signatureBin) !== 64) {
        json_response(400, ['error' => 'Invalid signature format. Must be 64-byte hex string.']);
    }
    
    $valid = sodium_crypto_sign_verify_detached($signatureBin, $canonical, $verifyKeyBin);

    if (!$valid) {
        json_response(401, ['error' => 'Invalid signature. Verification failed.']);
    }

    // Auto-register device if not already registered
    $stmt = $db->prepare('SELECT device_id FROM registered_devices WHERE device_id = ?');
    $stmt->execute([$deviceId]);
    $existing = $stmt->fetch();

    if (!$existing) {
        $ins = $db->prepare('INSERT INTO registered_devices (device_id, public_key) VALUES (?, ?)');
        $ins->execute([$deviceId, $devicePublicKey]);
    } else {
        // Update public key if changed
        $upd = $db->prepare('UPDATE registered_devices SET public_key = ? WHERE device_id = ?');
        $upd->execute([$devicePublicKey, $deviceId]);
    }

    // Generate session credentials
    $sessionId    = nyx_random_hex(32); // 64 hex chars
    $sessionToken = nyx_random_hex(32); // 64 hex chars
    $expiresAt    = nyx_iso8601(86400);  // 24 hours from now

    // Store session (include identity for sync routing)
    $ins = $db->prepare('INSERT INTO sessions (session_id, device_id, identity, token, expires_at) VALUES (?, ?, ?, ?, ?)');
    $ins->execute([$sessionId, $deviceId, $identity, $sessionToken, $expiresAt]);

    // Auto-create profile entry if missing
    $stmt = $db->prepare('SELECT device_id FROM profiles WHERE device_id = ?');
    $stmt->execute([$deviceId]);
    if (!$stmt->fetch()) {
        $address = nyx_generate_address($deviceId);
        $insProf = $db->prepare('INSERT INTO profiles (device_id, nyx_address) VALUES (?, ?)');
        $insProf->execute([$deviceId, $address]);
    }

    // Get persistent server identity
    $serverIdentity = nyx_get_server_identity($db);

    // Return session response (client expects session_token, not session_id+token pair)
    json_response(200, [
        'status'         => 'ok',
        'session_token'  => $sessionToken,
        'device_id'      => $deviceId,
        'server_identity'=> $serverIdentity['identity'],
        'server_verify_key' => $serverIdentity['verify_key'],
        'expires_at'     => $expiresAt,
        'server_version' => '3.0.0',
    ]);
}

/**
 * Handle POST /api/v3/auth/refresh
 *
 * Body: { "session_id": "...", "session_token": "..." }
 */
function handle_v3_session_refresh(): void
{
    $db   = nyx_db();
    $body = json_request();

    // Validate current session credentials
    $session   = nyx_validate_session($db, $body);
    $sessionId = $session['session_id'];

    // Extend expiry by 24h
    $newExpiresAt = nyx_extend_session($db, $sessionId);

    json_response(200, [
        'status'     => 'ok',
        'session_id' => $sessionId,
        'expires_at' => $newExpiresAt,
    ]);
}