<?php
/**
 * health.php — GET /api/v3/health
 *
 * Returns server health status, version info, and stats.
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

function handle_v3_health(): void
{
    $db = nyx_db();

    $deviceCount  = (int) $db->query('SELECT COUNT(*) FROM registered_devices')->fetchColumn();
    $pendingCount = (int) $db->query('SELECT COUNT(*) FROM messages WHERE delivered = 0')->fetchColumn();

    json_response(200, [
        'status'           => 'ok',
        'version'          => '3.0.0',
        'protocol_version' => '3.0',
        'server_time'      => nyx_iso8601(),
        'stats'            => [
            'registered_devices' => $deviceCount,
            'pending_messages'   => $pendingCount,
        ],
    ]);
}