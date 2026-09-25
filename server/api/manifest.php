<?php
/**
 * manifest.php — v3 Server Manifest Endpoint
 *
 * Handles:
 *   GET /api/v3/manifest — Return server version and update channel info
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle GET /api/v3/manifest
 */
function handle_v3_manifest(): void
{
    json_response(200, [
        'version'            => '3.0.0',
        'channel'            => 'stable',
        'released_at'        => '2026-01-01T00:00:00Z',
        'artifacts'          => (object)[],
        'signature'          => '',
        'min_client_version' => '0.1.0',
    ]);
}