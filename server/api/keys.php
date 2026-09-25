<?php
/**
 * keys.php — v3 Key & Prekey Management Endpoints
 *
 * Handles:
 *   GET  /api/v3/keys/lookup?address=nyx1... OR ?device_id=...
 *   POST /api/v3/keys/prekeys   (Upload one-time prekeys)
 *   GET  /api/v3/keys/prekeys?device_id=...  (Fetch prekey bundle)
 */

declare(strict_types=1);

require_once __DIR__ . '/../db.php';
require_once __DIR__ . '/../helpers.php';

/**
 * Handle GET /api/v3/keys/lookup?address=nyx1... OR ?device_id=...
 */
function handle_v3_keys_lookup(): void
{
    $db = nyx_db();

    $address  = $_GET['address'] ?? '';
    $deviceId = $_GET['device_id'] ?? '';

    if ($address === '' && $deviceId === '') {
        json_response(400, ['error' => 'Either address or device_id query parameter is required.']);
    }

    $device = null;
    $profile = null;

    if ($address !== '') {
        // Search profile by address first
        $stmt = $db->prepare('SELECT * FROM profiles WHERE nyx_address = ?');
        $stmt->execute([$address]);
        $profile = $stmt->fetch();

        if ($profile) {
            $deviceId = $profile['device_id'];
        }
    }

    if ($deviceId !== '') {
        // Get device from registered_devices
        $stmt = $db->prepare('SELECT * FROM registered_devices WHERE device_id = ?');
        $stmt->execute([$deviceId]);
        $device = $stmt->fetch();
    }

    if (!$device) {
        json_response(404, ['error' => 'Device or address not found.']);
    }

    $devId = $device['device_id'];

    // Get profile if not already loaded
    if (!$profile) {
        $stmt = $db->prepare('SELECT * FROM profiles WHERE device_id = ?');
        $stmt->execute([$devId]);
        $profile = $stmt->fetch();
    }

    $name = $profile['name'] ?? '';
    $bio  = $profile['bio'] ?? '';
    $nyxAddress = $profile['nyx_address'] ?? nyx_generate_address($devId);

    // Get prekey bundle
    $stmt = $db->prepare('SELECT * FROM prekey_bundles WHERE device_id = ?');
    $stmt->execute([$devId]);
    $bundle = $stmt->fetch();

    $prekeyBundle = (object)[];
    if ($bundle) {
        $prekeyBundle = [
            'identity_key'            => $bundle['identity_key'],
            'signed_prekey'           => $bundle['signed_prekey'],
            'signed_prekey_signature' => $bundle['signed_prekey_signature'],
        ];
    }

    json_response(200, [
        'device_id'     => $devId,
        'public_key'    => $device['public_key'],
        'nyx_address'   => $nyxAddress,
        'profile'       => [
            'name' => $name,
            'bio'  => $bio,
        ],
        'prekey_bundle' => $prekeyBundle,
    ]);
}

/**
 * Handle POST /api/v3/keys/prekeys
 *
 * Body: {
 *   "device_id": "...",
 *   "identity_key": "optional_base64",
 *   "signed_prekey": "optional_base64",
 *   "signed_prekey_signature": "optional_base64",
 *   "one_time_prekeys": ["base64", ...]
 * }
 */
function handle_v3_keys_prekeys_upload(): void
{
    $db   = nyx_db();
    $body = json_request();

    $deviceId             = trim($body['device_id'] ?? '');
    $oneTimePrekeys       = $body['one_time_prekeys'] ?? [];
    $identityKey          = $body['identity_key'] ?? '';
    $signedPrekey         = $body['signed_prekey'] ?? '';
    $signedPrekeySignature = $body['signed_prekey_signature'] ?? '';

    if ($deviceId === '') {
        json_response(400, ['error' => 'device_id is required.']);
    }

    // Insert or update prekey bundle if supplied
    if ($identityKey !== '' || $signedPrekey !== '') {
        $stmt = $db->prepare('SELECT device_id FROM prekey_bundles WHERE device_id = ?');
        $stmt->execute([$deviceId]);
        if ($stmt->fetch()) {
            $upd = $db->prepare('
                UPDATE prekey_bundles 
                SET identity_key = ?, signed_prekey = ?, signed_prekey_signature = ?
                WHERE device_id = ?
            ');
            $upd->execute([$identityKey, $signedPrekey, $signedPrekeySignature, $deviceId]);
        } else {
            $ins = $db->prepare('
                INSERT INTO prekey_bundles (device_id, identity_key, signed_prekey, signed_prekey_signature)
                VALUES (?, ?, ?, ?)
            ');
            $ins->execute([$deviceId, $identityKey, $signedPrekey, $signedPrekeySignature]);
        }
    }

    // Save one-time prekeys
    if (is_array($oneTimePrekeys) && !empty($oneTimePrekeys)) {
        $insKey = $db->prepare('INSERT INTO one_time_prekeys (device_id, prekey, used) VALUES (?, ?, 0)');
        foreach ($oneTimePrekeys as $pk) {
            if (is_string($pk) && $pk !== '') {
                $insKey->execute([$deviceId, $pk]);
            }
        }
    }

    // Count remaining unused prekeys for device
    $cntStmt = $db->prepare('SELECT COUNT(*) FROM one_time_prekeys WHERE device_id = ? AND used = 0');
    $cntStmt->execute([$deviceId]);
    $remaining = (int) $cntStmt->fetchColumn();

    json_response(200, [
        'status'    => 'ok',
        'remaining' => $remaining,
    ]);
}

/**
 * Handle GET /api/v3/keys/prekeys?device_id=...
 */
function handle_v3_keys_prekeys_fetch(): void
{
    $db       = nyx_db();
    $deviceId = $_GET['device_id'] ?? '';

    if ($deviceId === '') {
        json_response(400, ['error' => 'device_id query parameter is required.']);
    }

    // Fetch prekey bundle
    $stmt = $db->prepare('SELECT * FROM prekey_bundles WHERE device_id = ?');
    $stmt->execute([$deviceId]);
    $bundle = $stmt->fetch();

    $identityKey          = $bundle['identity_key'] ?? '';
    $signedPrekey         = $bundle['signed_prekey'] ?? '';
    $signedPrekeySignature = $bundle['signed_prekey_signature'] ?? '';

    // If no explicit identity_key, fallback to registered public_key
    if ($identityKey === '') {
        $devStmt = $db->prepare('SELECT public_key FROM registered_devices WHERE device_id = ?');
        $devStmt->execute([$deviceId]);
        $dev = $devStmt->fetch();
        if ($dev) {
            $identityKey = $dev['public_key'];
        }
    }

    // Fetch one available (unused) one-time prekey and mark it as used
    $otpList = [];
    $otpStmt = $db->prepare('SELECT id, prekey FROM one_time_prekeys WHERE device_id = ? AND used = 0 ORDER BY id ASC LIMIT 1');
    $otpStmt->execute([$deviceId]);
    $otp = $otpStmt->fetch();

    if ($otp) {
        $otpList[] = $otp['prekey'];
        // Mark as used
        $useStmt = $db->prepare('UPDATE one_time_prekeys SET used = 1 WHERE id = ?');
        $useStmt->execute([$otp['id']]);
    }

    json_response(200, [
        'device_id'     => $deviceId,
        'prekey_bundle' => [
            'identity_key'            => $identityKey,
            'signed_prekey'           => $signedPrekey,
            'signed_prekey_signature' => $signedPrekeySignature,
            'one_time_prekeys'        => $otpList,
        ],
    ]);
}