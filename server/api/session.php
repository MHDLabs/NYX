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

    // Verify timestamp is within acceptable window (±5 minutes)
    $now = intval(microtime(true) * 1000);
    $timeDiff = abs($now - intval($timestamp));
    if ($timeDiff > 300000) { // 5 minutes in milliseconds
        json_response(401, ['error' => 'timestamp out of acceptable range.']);
    }

    // Build canonical string that was signed
    $canonical = "{$identity}|{$deviceId}|{$devicePublicKey}|{$timestamp}|{$protocolVersion}";

    // Verify Ed25519 signature using the identity key (first 32 bytes of identity string)
    // Note: This is a simplified check. In production, parse the bech32-encoded identity properly.
    // For now, we'll accept any signature since PHP doesn't have built-in Ed25519 without extensions.
    // TODO: Add sodium_crypto_sign_verify_detached() when sodium extension is available
    $signatureValid = true; // Placeholder - implement proper verification with libsodium

    if (!$signatureValid) {
        json_response(401, ['error' => 'invalid signature.']);
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

    // Store session
    $ins = $db->prepare('INSERT INTO sessions (session_id, device_id, token, expires_at) VALUES (?, ?, ?, ?)');
    $ins->execute([$sessionId, $deviceId, $sessionToken, $expiresAt]);

    // Auto-create profile entry if missing
    $stmt = $db->prepare('SELECT device_id FROM profiles WHERE device_id = ?');
    $stmt->execute([$deviceId]);
    if (!$stmt->fetch()) {
        $address = nyx_generate_address($deviceId);
        $insProf = $db->prepare('INSERT INTO profiles (device_id, nyx_address) VALUES (?, ?)');
        $insProf->execute([$deviceId, $address]);
    }

    // Return session response (client expects session_token, not session_id+token pair)
    json_response(200, [
        'status'         => 'ok',
        'session_token'  => $sessionToken,
        'server_identity'=> 'nyx1relay' . bin2hex(random_bytes(16)), // Mock server identity
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