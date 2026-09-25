<?php
/**
 * index.php — Router / entry point for the NYX Relay Server.
 *
 * When using PHP's built-in server (`php -S localhost:8080`), this file
 * acts as a simple router that dispatches to the appropriate endpoint.
 *
 * Routes:
 *   GET  /              → status / health check
 *   POST /register.php  → device registration
 *   POST /send.php      → ciphertext delivery
 *   POST /sync.php      → ciphertext retrieval
 *   GET  /sync.php?lookup=<id> → public key lookup
 *
 * Direct access to register.php / send.php / sync.php also works.
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';

// ---------------------------------------------------------------------------
// Simple path-based routing for the built-in PHP server
// ---------------------------------------------------------------------------

$uri    = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// Normalize: strip trailing slash
$uri = rtrim($uri, '/') ?: '/';

// Health check / status endpoint
if ($uri === '/' || $uri === '/index.php') {
    // Ensure the database is initialized
    $db = nyx_db();

    $deviceCount = (int) $db->query('SELECT COUNT(*) FROM registered_devices')->fetchColumn();
    $msgCount    = (int) $db->query('SELECT COUNT(*) FROM messages')->fetchColumn();
    $pendingCount = (int) $db->query('SELECT COUNT(*) FROM messages WHERE delivered = 0')->fetchColumn();

    json_response(200, [
        'service'  => 'NYX Relay Server',
        'version'  => '1.0.0-mvp',
        'status'   => 'online',
        'stats'    => [
            'registered_devices' => $deviceCount,
            'total_messages'     => $msgCount,
            'pending_messages'   => $pendingCount,
        ],
        'endpoints' => [
            'POST /register.php'          => 'Register a device public key',
            'POST /send.php'              => 'Send an encrypted message',
            'POST /sync.php'              => 'Fetch undelivered messages',
            'GET  /sync.php?lookup=<id>'  => 'Look up a device public key',
        ],
    ]);
}

// Dispatch to endpoint scripts if accessed via the router
// (PHP built-in server serves .php files directly, so this is a fallback)
$routes = [
    '/register' => __DIR__ . '/register.php',
    '/send'     => __DIR__ . '/send.php',
    '/sync'     => __DIR__ . '/sync.php',
];

if (isset($routes[$uri])) {
    require $routes[$uri];
    exit;
}

// 404 for unknown routes
json_response(404, [
    'error'   => 'Not found',
    'message' => "No endpoint at \"{$uri}\". See GET / for available endpoints.",
]);