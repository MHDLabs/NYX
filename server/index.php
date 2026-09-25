<?php
/**
 * index.php — Router / entry point for the NYX Relay Server.
 *
 * Supports both legacy endpoints and the v3 API protocol.
 *
 * Routes (Legacy — backward compat):
 *   GET  /              → status / health check (legacy format)
 *   POST /register.php  → device registration
 *   POST /send.php      → ciphertext delivery
 *   POST /sync.php      → ciphertext retrieval
 *   GET  /sync.php?lookup=<id> → public key lookup
 *
 * Routes (v3 API):
 *   GET  /api/v3/health             → health check (v3 format)
 *   POST /api/v3/auth/session       → create session
 *   POST /api/v3/auth/refresh       → refresh session
 *   POST /api/v3/messages/send      → send message
 *   POST /api/v3/messages/sync      → sync messages
 *   GET  /api/v3/keys/lookup        → key lookup
 *   POST /api/v3/keys/prekeys       → upload prekeys
 *   GET  /api/v3/keys/prekeys       → fetch prekeys
 *   POST /api/v3/discovery          → server discovery
 *   POST /api/v3/profile            → set profile
 *   GET  /api/v3/profile            → get profile
 *   GET  /api/v3/manifest           → server manifest
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/helpers.php';

// ---------------------------------------------------------------------------
// CORS preflight handling for all routes
// ---------------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Authorization');
    header('Access-Control-Max-Age: 86400');
    http_response_code(204);
    exit;
}

// ---------------------------------------------------------------------------
// Path-based routing
// ---------------------------------------------------------------------------

$uri    = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// Normalize: strip trailing slash (except root)
$uri = rtrim($uri, '/') ?: '/';

// ---------------------------------------------------------------------------
// Legacy health check / status endpoint
// ---------------------------------------------------------------------------
if (($uri === '/' || $uri === '/index.php') && $method === 'GET') {
    $db = nyx_db();

    $deviceCount  = (int) $db->query('SELECT COUNT(*) FROM registered_devices')->fetchColumn();
    $msgCount     = (int) $db->query('SELECT COUNT(*) FROM messages')->fetchColumn();
    $pendingCount = (int) $db->query('SELECT COUNT(*) FROM messages WHERE delivered = 0')->fetchColumn();

    json_response(200, [
        'service'  => 'NYX Relay Server',
        'version'  => '3.0.0',
        'status'   => 'online',
        'stats'    => [
            'registered_devices' => $deviceCount,
            'total_messages'     => $msgCount,
            'pending_messages'   => $pendingCount,
        ],
        'endpoints' => [
            'GET  /api/v3/health'             => 'v3 health check',
            'POST /api/v3/auth/session'       => 'Create session',
            'POST /api/v3/auth/refresh'       => 'Refresh session',
            'POST /api/v3/messages/send'      => 'Send encrypted message',
            'POST /api/v3/messages/sync'      => 'Sync undelivered messages',
            'GET  /api/v3/keys/lookup'        => 'Look up device public key',
            'POST /api/v3/keys/prekeys'       => 'Upload one-time prekeys',
            'GET  /api/v3/keys/prekeys'       => 'Fetch prekey bundle',
            'POST /api/v3/discovery'          => 'Server discovery',
            'POST /api/v3/profile'            => 'Set profile',
            'GET  /api/v3/profile'            => 'Get profile',
            'GET  /api/v3/manifest'           => 'Server manifest',
            'POST /register.php'              => 'Register a device (legacy)',
            'POST /send.php'                  => 'Send an encrypted message (legacy)',
            'POST /sync.php'                  => 'Fetch undelivered messages (legacy)',
            'GET  /sync.php?lookup=<id>'      => 'Look up a device public key (legacy)',
        ],
    ]);
}

// ---------------------------------------------------------------------------
// v3 API routes
// ---------------------------------------------------------------------------

// Load v3 API handlers
require_once __DIR__ . '/api/health.php';
require_once __DIR__ . '/api/session.php';
require_once __DIR__ . '/api/messages.php';
require_once __DIR__ . '/api/keys.php';
require_once __DIR__ . '/api/discovery.php';
require_once __DIR__ . '/api/profile.php';
require_once __DIR__ . '/api/manifest.php';

// GET /api/v3/health
if ($uri === '/api/v3/health' && $method === 'GET') {
    handle_v3_health();
}

// POST /api/v3/auth/session
if ($uri === '/api/v3/auth/session' && $method === 'POST') {
    handle_v3_session_create();
}

// POST /api/v3/auth/refresh
if ($uri === '/api/v3/auth/refresh' && $method === 'POST') {
    handle_v3_session_refresh();
}

// POST /api/v3/messages/send
if ($uri === '/api/v3/messages/send' && $method === 'POST') {
    handle_v3_messages_send();
}

// GET /api/v3/messages/sync  (v3 protocol: client uses GET with Bearer auth)
if ($uri === '/api/v3/messages/sync' && $method === 'GET') {
    handle_v3_messages_sync();
}

// POST /api/v3/messages/sync (backward compat — some clients may POST)
if ($uri === '/api/v3/messages/sync' && $method === 'POST') {
    handle_v3_messages_sync();
}

// GET /api/v3/keys/lookup
if ($uri === '/api/v3/keys/lookup' && $method === 'GET') {
    handle_v3_keys_lookup();
}

// POST /api/v3/keys/prekeys
if ($uri === '/api/v3/keys/prekeys' && $method === 'POST') {
    handle_v3_keys_prekeys_upload();
}

// GET /api/v3/keys/prekeys
if ($uri === '/api/v3/keys/prekeys' && $method === 'GET') {
    handle_v3_keys_prekeys_fetch();
}

// POST /api/v3/discovery
if ($uri === '/api/v3/discovery' && $method === 'POST') {
    handle_v3_discovery();
}

// POST /api/v3/profile
if ($uri === '/api/v3/profile' && $method === 'POST') {
    handle_v3_profile_set();
}

// GET /api/v3/profile
if ($uri === '/api/v3/profile' && $method === 'GET') {
    handle_v3_profile_get();
}

// GET /api/v3/manifest
if ($uri === '/api/v3/manifest' && $method === 'GET') {
    handle_v3_manifest();
}

// ---------------------------------------------------------------------------
// Legacy routes (backward compatibility)
// ---------------------------------------------------------------------------

// Dispatch to endpoint scripts if accessed via the router
// (PHP built-in server serves .php files directly when they exist on disk,
//  so this is a fallback for clean-URL routing)
$legacyRoutes = [
    '/register'     => __DIR__ . '/register.php',
    '/register.php' => __DIR__ . '/register.php',
    '/send'         => __DIR__ . '/send.php',
    '/send.php'     => __DIR__ . '/send.php',
    '/sync'         => __DIR__ . '/sync.php',
    '/sync.php'     => __DIR__ . '/sync.php',
];

if (isset($legacyRoutes[$uri])) {
    require $legacyRoutes[$uri];
    exit;
}

// ---------------------------------------------------------------------------
// 404 for unknown routes
// ---------------------------------------------------------------------------
json_response(404, [
    'error'   => 'Not found',
    'message' => "No endpoint at \"{$uri}\". See GET / or GET /api/v3/health for available endpoints.",
]);