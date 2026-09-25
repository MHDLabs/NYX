<?php
/**
 * send.php — Send a message to another user on the NYX Relay Server.
 *
 * POST /send.php
 * Body (new format): {
 *   "message_id":   "uuid",
 *   "sender_id":    "sender_identity_id",
 *   "recipient_id": "recipient_identity_id",
 *   "ciphertext":   "hex encoded full ciphertext (nonce + ciphertext + tag)",
 *   "nonce":        "hex encoded nonce (optional, for backward compatibility)"
 * }
 *
 * Note: New clients send full ciphertext in 'ciphertext' field (nonce prepended).
 * Legacy clients may send nonce and ciphertext separately.
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/helpers.php';

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    nyx_cors_headers();
    http_response_code(204);
    exit;
}

// Require authentication for legacy send endpoint
$db = nyx_db();
$deviceId = nyx_legacy_auth($db);

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(405, ['error' => 'Method not allowed. Use POST.']);
}

$body = json_request();

// Validate required fields (at least message_id, sender_id, recipient_id)
$required = ['message_id', 'sender_id', 'recipient_id'];
foreach ($required as $field) {
    if (!isset($body[$field]) || !is_string($body[$field]) || trim($body[$field]) === '') {
        json_response(400, ['error' => "Missing or empty \"{$field}\" field."]);
    }
}

$messageId   = trim($body['message_id']);
$senderId    = trim($body['sender_id']);
$recipientId = trim($body['recipient_id']);

// Support both new format (single ciphertext) and legacy format (separate nonce + ciphertext)
if (isset($body['ciphertext']) && is_string($body['ciphertext']) && trim($body['ciphertext']) !== '') {
    // New format: full ciphertext with nonce prepended
    $ciphertext = trim($body['ciphertext']);
    $nonce = ''; // Not used in new format
} elseif (isset($body['nonce']) && is_string($body['nonce']) && trim($body['nonce']) !== '') {
    // Legacy format: separate nonce and ciphertext
    $nonce = trim($body['nonce']);
    $ciphertext = $nonce . trim($body['ciphertext']); // Combine for storage
} else {
    json_response(400, ['error' => 'Missing ciphertext.']);
}

if (strlen($ciphertext) > 1048576) {
    json_response(400, ['error' => 'Ciphertext too large (max 1 MB).']);
}

$db  = nyx_db();
$now = nyx_now_sql($db);

// Verify that the sender is a registered device
$check = $db->prepare("SELECT device_id FROM registered_devices WHERE device_id = :sid");
$check->execute([':sid' => $senderId]);
if ($check->fetch() === false) {
    json_response(403, ['error' => 'Sender not registered. Please register first.']);
}

// Store the message — ON CONFLICT DO NOTHING prevents duplicates
$stmt = $db->prepare("
    INSERT INTO messages (message_id, sender_id, recipient_id, ciphertext, nonce, created_at, delivered)
    VALUES (:mid, :sid, :rid, :ct, :nonce, {$now}, 0)
    ON CONFLICT(message_id) DO NOTHING
");

$stmt->execute([
    ':mid'   => $messageId,
    ':sid'   => $senderId,
    ':rid'   => $recipientId,
    ':ct'    => $ciphertext,
    ':nonce' => $nonce, // Empty for new format
]);

json_response(200, [
    'status'  => 'ok',
    'message' => 'Message queued successfully.',
]);
