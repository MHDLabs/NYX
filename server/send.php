<?php
/**
 * send.php — Send a message to another user on the NYX Relay Server.
 *
 * POST /send.php
 * Body: {
 *   "message_id":   "uuid",
 *   "sender_id":    "sender_device_id",
 *   "recipient_id": "recipient_device_id",
 *   "ciphertext":   "base64 encoded encrypted message",
 *   "nonce":        "base64 encoded nonce"
 * }
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    json_response(204, []);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(405, ['error' => 'Method not allowed. Use POST.']);
}

$body = json_request();

// Validate required fields
$required = ['message_id', 'sender_id', 'recipient_id', 'ciphertext', 'nonce'];
foreach ($required as $field) {
    if (!isset($body[$field]) || !is_string($body[$field]) || trim($body[$field]) === '') {
        json_response(400, ['error' => "Missing or empty \"{$field}\" field."]);
    }
}

$messageId   = trim($body['message_id']);
$senderId    = trim($body['sender_id']);
$recipientId = trim($body['recipient_id']);
$ciphertext  = trim($body['ciphertext']);
$nonce       = trim($body['nonce']);

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
    ':nonce' => $nonce,
]);

json_response(200, [
    'status'  => 'ok',
    'message' => 'Message queued successfully.',
]);