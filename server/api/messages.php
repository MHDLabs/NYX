<?php
/**
 * messages.php — v3 Message Routing Endpoints
 *
 * Handles:
 *   POST /api/v3/messages/send — Queue an encrypted message envelope
 *   GET  /api/v3/messages/sync — Retrieve pending messages (GET with Bearer auth)
 *
 * CRITICAL CONSTRAINT:
 * Server is a BLIND RELAY. Never parse or modify crypto fields in envelopes.
 * Store envelope as opaque JSON string.
 *
 * Conversation Routing:
 *   DM conversations use deterministic conv_dm_{hash(identity_a, identity_b)} IDs.
 *   The server stores conversation participants so it can route messages correctly.
 *   When a device sends to a conversation, the server auto-registers both participants.
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle POST /api/v3/messages/send
 *
 * Body (MessageEnvelope from client — to_wire_dict() format):
 * {
 *   "message_id": "nyx_msg_...",
 *   "sender_id": "nyx1...",
 *   "device_id": "...",
 *   "conversation_id": "conv_dm_...",
 *   "timestamp": 1234567890123,
 *   "sequence": 1,
 *   "ciphertext": "hex_encoded",
 *   "signature": "hex_encoded",
 *   "previous_hash": "optional_hex_or_null",
 *   "protocol_version": 3
 * }
 *
 * Headers: Authorization: Bearer <session_token>
 */
function handle_v3_messages_send(): void
{
    $db   = nyx_db();
    $body = json_request();

    // Validate session via Authorization header
    $deviceId = authenticate_request($db);

    // Extract envelope fields
    $messageId       = trim($body['message_id'] ?? '');
    $senderId        = trim($body['sender_id'] ?? '');
    $conversationId  = trim($body['conversation_id'] ?? '');
    $ciphertext      = trim($body['ciphertext'] ?? '');

    if ($messageId === '' || $senderId === '' || $conversationId === '' || $ciphertext === '') {
        json_response(400, ['error' => 'message_id, sender_id, conversation_id, and ciphertext are required.']);
    }

    // Ensure the sender device matches the authenticated session
    if ($senderId !== $deviceId && $deviceId !== '') {
        // Allow if the sender identity is linked to this device
        $stmt = $db->prepare('SELECT device_id FROM registered_devices WHERE device_id = ?');
        $stmt->execute([$senderId]);
        if (!$stmt->fetch()) {
            // This might be an identity string, not a device_id — allow for now
            // In production, validate identity-to-device binding
        }
    }

    // ----- Conversation participant registration -----
    // Register the sender as a participant in this conversation
    $stmt = $db->prepare('
        INSERT OR IGNORE INTO conversation_participants (conversation_id, device_id, identity_id)
        VALUES (?, ?, ?)
    ');
    $stmt->execute([$conversationId, $deviceId, $senderId]);

    // Store all participant device_ids from the request (client can provide a list)
    $participants = $body['participants'] ?? [];
    if (!is_array($participants)) {
        $participants = [];
    }
    foreach ($participants as $pid) {
        if (is_string($pid) && $pid !== '') {
            $stmt2 = $db->prepare('
                INSERT OR IGNORE INTO conversation_participants (conversation_id, device_id, identity_id)
                VALUES (?, ?, ?)
            ');
            $stmt2->execute([$conversationId, $pid, $pid]);
        }
    }

    // Add relay metadata
    $relayMetadata = [
        'received_at' => intval(microtime(true) * 1000),
        'relay_id'    => 'nyx1relay' . bin2hex(random_bytes(8)),
        'hop_count'   => 0,
    ];
    $body['relay_metadata'] = $relayMetadata;

    // Store the complete envelope as JSON
    $envelopeJson = json_encode($body, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

    // Insert into messages table
    // recipient_id stores conversation_id for routing (participants resolved at sync time)
    $stmt = $db->prepare('
        INSERT INTO messages (message_id, sender_id, recipient_id, ciphertext, nonce, envelope, room_id, delivered)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    ');
    $stmt->execute([
        $messageId,
        $senderId,
        $conversationId,
        $ciphertext,
        '',
        $envelopeJson,
        null,
    ]);

    json_response(200, [
        'status'     => 'ok',
        'message_id' => $messageId,
        'queued'     => true,
        'relay_metadata' => $relayMetadata,
    ]);
}

/**
 * Handle GET /api/v3/messages/sync
 *
 * Retrieves undelivered messages for the authenticated device's conversations.
 *
 * Query params:
 *   ?since=ISO8601_timestamp (optional)
 *   ?limit=100 (optional, default 100, max 1000)
 *
 * Headers: Authorization: Bearer <session_token>
 *
 * Returns: {
 *   "messages": [ {MessageEnvelope}, ... ],
 *   "server_time": "ISO8601",
 *   "has_more": boolean
 * }
 */
function handle_v3_messages_sync(): void
{
    $db = nyx_db();

    // Validate session and get device_id
    $deviceId = authenticate_request($db);

    // Get query parameters
    $since = $_GET['since'] ?? null;
    $limit = isset($_GET['limit']) ? min((int)$_GET['limit'], 1000) : 100;

    // Look up which conversations this device participates in
    $convStmt = $db->prepare('SELECT conversation_id FROM conversation_participants WHERE device_id = ?');
    $convStmt->execute([$deviceId]);
    $conversations = $convStmt->fetchAll(PDO::FETCH_COLUMN, 0);

    if (empty($conversations)) {
        // No conversations yet — return empty
        json_response(200, [
            'messages'    => [],
            'server_time' => nyx_iso8601(),
            'has_more'    => false,
        ]);
        return;
    }

    // Build query: fetch undelivered messages for sender != this device AND
    // recipient_id (which stores conversation_id) matches one of our conversations
    $placeholders = implode(',', array_fill(0, count($conversations), '?'));
    $sql = "SELECT * FROM messages 
            WHERE delivered = 0 
              AND sender_id != ? 
              AND recipient_id IN ($placeholders)";
    $params = [$deviceId];
    $params = array_merge($params, $conversations);

    if ($since !== null && $since !== '') {
        $sql .= ' AND created_at > ?';
        $params[] = $since;
    }

    $sql .= ' ORDER BY created_at ASC LIMIT ' . (int)$limit;

    $stmt = $db->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll();

    $messages = [];
    $messageIdsToMark = [];

    foreach ($rows as $row) {
        $msgId = $row['message_id'];

        // Parse the stored envelope
        $envelope = null;
        if (!empty($row['envelope'])) {
            $envelope = json_decode($row['envelope'], true);
        }

        // If no envelope stored, skip (v3 protocol requires full envelope)
        if ($envelope === null) {
            continue;
        }

        $messageIdsToMark[] = $msgId;
        $messages[] = $envelope;
    }

    // Mark retrieved messages as delivered
    if (!empty($messageIdsToMark)) {
        $inClause = implode(',', array_fill(0, count($messageIdsToMark), '?'));
        $markStmt = $db->prepare("UPDATE messages SET delivered = 1 WHERE message_id IN ($inClause)");
        $markStmt->execute($messageIdsToMark);
    }

    json_response(200, [
        'messages'    => $messages,
        'server_time' => nyx_iso8601(),
        'has_more'    => count($rows) >= $limit,
    ]);
}

/**
 * Authenticate the request by validating the Bearer token from the Authorization header.
 *
 * Returns the device_id from the session.
 */
function authenticate_request(PDO $db): string
{
    // Try Authorization header first
    $authHeader = $_SERVER['HTTP_AUTHORIZATION'] 
        ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] 
        ?? '';

    if ($authHeader === '') {
        // Some servers pass auth via Apache rewrite
        if (function_exists('apache_request_headers')) {
            $headers = apache_request_headers();
            $authHeader = $headers['Authorization'] ?? $headers['authorization'] ?? '';
        }
    }

    if ($authHeader === '') {
        json_response(401, ['error' => 'Authorization header required.']);
    }

    // Parse Bearer token
    if (!preg_match('/^Bearer\s+(.+)$/i', $authHeader, $matches)) {
        json_response(401, ['error' => 'Invalid Authorization header format. Use: Bearer <token>']);
    }
    $sessionToken = $matches[1];

    // Validate session and get device_id
    $stmt = $db->prepare('SELECT device_id, expires_at FROM sessions WHERE token = ?');
    $stmt->execute([$sessionToken]);
    $session = $stmt->fetch();

    if (!$session) {
        json_response(401, ['error' => 'Invalid session token.']);
    }

    // Check expiry
    $now = new DateTimeImmutable();
    $expiresAt = new DateTimeImmutable($session['expires_at']);
    if ($now > $expiresAt) {
        json_response(401, ['error' => 'Session expired.']);
    }

    return $session['device_id'];
}