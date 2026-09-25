<?php
/**
 * sync.php — Pull pending messages and discover keys for a recipient.
 *
 * GET  /sync.php?user_id=<device_id>&since=<timestamp>
 * POST /sync.php  { "user_id": "...", "since": "..." }
 *
 * Response:
 * {
 *   "messages": [ { "message_id", "sender_id", "ciphertext", "nonce", "created_at" } ],
 *   "keys": { "device_id": "public_key", ... }
 * }
 *
 * The "since" parameter is optional. When provided, only messages created
 * after that timestamp are returned. When omitted, all pending (undelivered)
 * messages for the user are returned.
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

if (!isset($body['user_id']) || !is_string($body['user_id']) || trim($body['user_id']) === '') {
    json_response(400, ['error' => 'Missing or empty "user_id" field.']);
}

$userId = trim($body['user_id']);
$since  = $body['since'] ?? null;

$db = nyx_db();

// ---- Fetch pending messages ----
$sql = "SELECT message_id, sender_id, ciphertext, nonce, created_at
        FROM messages
        WHERE recipient_id = :uid
          AND delivered = 0";

$params = [':uid' => $userId];

if ($since !== null && $since !== '') {
    $sql .= " AND created_at > :since";
    $params[':since'] = $since;
}

$sql .= " ORDER BY created_at ASC";

$stmt = $db->prepare($sql);
$stmt->execute($params);
$messages = $stmt->fetchAll();

// ---- Mark fetched messages as delivered ----
if (!empty($messages)) {
    $ids = array_column($messages, 'message_id');

    if (nyx_is_pgsql($db)) {
        // PostgreSQL: use array of parameters for IN clause
        $placeholders = [];
        $params = [];
        foreach ($ids as $i => $id) {
            $ph = ":id_{$i}";
            $placeholders[] = $ph;
            $params[$ph] = $id;
        }
        $inClause = implode(', ', $placeholders);
        $stmt = $db->prepare("UPDATE messages SET delivered = 1 WHERE message_id IN ({$inClause})");
        $stmt->execute($params);
    } else {
        // SQLite: use comma-separated string with IN()
        $escaped = array_map(function ($id) use ($db) {
            return "'" . $db->quote($id) . "'";
        }, $ids);
        $inClause = implode(',', $escaped);
        $db->exec("UPDATE messages SET delivered = 1 WHERE message_id IN ({$inClause})");
    }
}

// ---- Fetch public keys for all known devices ----
// The client needs other users' public keys to encrypt messages.
// We return all keys so the client can build its contact list.
$keyStmt = $db->query("SELECT device_id, public_key FROM registered_devices");
$keys    = [];
foreach ($keyStmt->fetchAll() as $row) {
    $keys[$row['device_id']] = $row['public_key'];
}

json_response(200, [
    'messages' => $messages,
    'keys'     => $keys,
]);