<?php
/**
 * profile.php — v3 Profile Management Endpoints
 *
 * Handles:
 *   POST /api/v3/profile — Update user profile
 *   GET  /api/v3/profile?device_id=... — Retrieve profile
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle POST /api/v3/profile
 *
 * Body: { "device_id": "...", "name": "...", "bio": "..." }
 */
function handle_v3_profile_set(): void
{
    $db   = nyx_db();
    $body = json_request();

    $deviceId = trim($body['device_id'] ?? '');
    $name     = trim($body['name'] ?? '');
    $bio      = trim($body['bio'] ?? '');

    if ($deviceId === '') {
        json_response(400, ['error' => 'device_id is required.']);
    }

    $nyxAddress = nyx_generate_address($deviceId);

    // Upsert profile
    $stmt = $db->prepare('SELECT device_id FROM profiles WHERE device_id = ?');
    $stmt->execute([$deviceId]);

    if ($stmt->fetch()) {
        $upd = $db->prepare('UPDATE profiles SET name = ?, bio = ?, nyx_address = ? WHERE device_id = ?');
        $upd->execute([$name, $bio, $nyxAddress, $deviceId]);
    } else {
        $ins = $db->prepare('INSERT INTO profiles (device_id, name, bio, nyx_address) VALUES (?, ?, ?, ?)');
        $ins->execute([$deviceId, $name, $bio, $nyxAddress]);
    }

    json_response(200, [
        'status' => 'ok',
    ]);
}

/**
 * Handle GET /api/v3/profile?device_id=...
 */
function handle_v3_profile_get(): void
{
    $db       = nyx_db();
    $deviceId = $_GET['device_id'] ?? '';

    if ($deviceId === '') {
        json_response(400, ['error' => 'device_id query parameter is required.']);
    }

    $stmt = $db->prepare('SELECT * FROM profiles WHERE device_id = ?');
    $stmt->execute([$deviceId]);
    $profile = $stmt->fetch();

    if (!$profile) {
        // Return default empty profile structure with computed address
        json_response(200, [
            'device_id'   => $deviceId,
            'name'        => '',
            'bio'         => '',
            'nyx_address' => nyx_generate_address($deviceId),
        ]);
    }

    json_response(200, [
        'device_id'   => $profile['device_id'],
        'name'        => $profile['name'],
        'bio'         => $profile['bio'],
        'nyx_address' => $profile['nyx_address'] ?: nyx_generate_address($deviceId),
    ]);
}