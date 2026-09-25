<?php
/**
 * router.php — Router script for PHP built-in server
 * 
 * Usage: php -S localhost:8000 router.php
 * 
 * This script routes all requests to index.php for API endpoints.
 */

// If the request is for a static file that exists, serve it directly
if (php_sapi_name() === 'cli-server') {
    $file = __DIR__ . $_SERVER['REQUEST_URI'];
    
    // Check if it's a file request (has extension) and exists
    if (is_file($file) && pathinfo($file, PATHINFO_EXTENSION) !== '') {
        return false; // Let PHP serve the file
    }
}

// Otherwise, route through index.php
require_once __DIR__ . '/index.php';