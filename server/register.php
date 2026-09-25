<?php
/**
 * register.php — Public key registration endpoint for NYX Relay Server.
 *
 * POST /register.php
 * Body: { "device_id": "...", "public_key": "..." }
 *
 * The server stores the device_id → public_key mapping.
 * It does NOT interpret the public key — it's opaque base64 to the relay.
 * If the device_id already exists, the public key is updated (re-registration).
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';

// CORS preflight
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    json_response(204, []);
}

// Only accept POST requests
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(405, ['error' => 'Method not allowed. Use POST.']);
}

// Parse the JSON request body
$body = json_request();

// Validate required fields
if (!isset($body['device_id']) || !is_string($body['device_id']) || trim($body['device_id']) === '') {
    json_response(400, ['error' => 'Missing or empty "device_id" field (string required).']);
}

if (!isset($body['public_key']) || !is_string($body['public_key']) || trim($body['public_key']) === '') {
    json_response(400, ['error' => 'Missing or empty "public_key" field (string required).']);
}

$deviceId  = trim($body['device_id']);
$publicKey = trim($body['public_key']);

// Basic sanity checks on the values
if (strlen($deviceId) > 128) {
    json_response(400, ['error' => '"device_id" must be 128 characters or fewer.']);
}

if (strlen($publicKey) > 8192) {
    json_response(400, ['error' => '"public_key" must be 8192 characters or fewer.']);
}

// Insert or replace the device registration
$db  = nyx_db();
$now = nyx_now_sql($db);

$stmt = $db->prepare("
    INSERT INTO registered_devices (device_id, public_key, registered_at)
    VALUES (:device_id, :public_key, {$now})
    ON CONFLICT(device_id) DO UPDATE SET
        public_key    = excluded.public_key,
        registered_at = excluded.registered_at
");

$stmt->execute([
    ':device_id'  => $deviceId,
    ':public_key' => $publicKey,
]);

json_response(200, [
    'status'    => 'ok',
    'device_id' => $deviceId,
    'message'   => 'Device registered successfully.',
]);