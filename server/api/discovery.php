<?php
/**
 * discovery.php — v3 Server Discovery Endpoint
 *
 * Handles:
 *   POST /api/v3/discovery — Discover relay servers
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle POST /api/v3/discovery
 *
 * Body: { "known_servers": ["url1", "url2"] }
 */
function handle_v3_discovery(): void
{
    $db   = nyx_db();
    $body = json_request();

    $knownServers = $body['known_servers'] ?? [];

    // Always include current server as primary
    $protocol = isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on' ? 'https' : 'http';
    $host = $_SERVER['HTTP_HOST'] ?? 'localhost:8000';
    $currentUrl = "{$protocol}://{$host}";

    // If Railway URL is set, prefer that or default to railway host
    if (getenv('RAILWAY_PUBLIC_DOMAIN')) {
        $currentUrl = 'https://' . getenv('RAILWAY_PUBLIC_DOMAIN');
    }

    $servers = [
        [
            'url'              => 'https://nyx-9router.up.railway.app',
            'name'             => 'NYX Relay',
            'latency_ms'       => 0,
            'trust_level'      => 3,
            'capacity'         => 1000,
            'uptime_percent'   => 99.9,
            'protocol_version' => '3.0',
        ],
    ];

    // Check if known_servers contains any other servers stored in server_directory table
    if (is_array($knownServers) && !empty($knownServers)) {
        $inClause = implode(',', array_fill(0, count($knownServers), '?'));
        $stmt = $db->prepare("SELECT * FROM server_directory WHERE url IN ($inClause)");
        $stmt->execute($knownServers);
        $directoryRows = $stmt->fetchAll();

        foreach ($directoryRows as $row) {
            $servers[] = [
                'url'              => $row['url'],
                'name'             => $row['name'] ?: 'NYX Peer',
                'latency_ms'       => (int) $row['latency_ms'],
                'trust_level'      => (int) $row['trust_level'],
                'capacity'         => (int) $row['capacity'],
                'uptime_percent'   => 99.0,
                'protocol_version' => '3.0',
            ];
        }
    }

    json_response(200, [
        'servers' => $servers,
    ]);
}