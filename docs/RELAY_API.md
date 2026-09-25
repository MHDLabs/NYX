# NYX Relay API contract (client v0.2.x)

Base URL: `https://HOST` (client maps `nyx://HOST` → HTTPS).

All authenticated routes: `Authorization: Bearer <session_token>`.

## Health

`GET /api/v3/health`

```json
{"status":"ok","protocol_version":3,"server_identity":"nyx1...","uptime":123}
```

## Register

`POST /api/v3/auth/register`

```json
{
  "identity": "nyx1...",
  "identity_public_key": "<hex ed25519>",
  "device_id": "...",
  "device_public_key": "<hex>",
  "timestamp": 1710000000000,
  "protocol_version": 3,
  "signature": "<hex over identity|device_id|device_public_key|timestamp|protocol_version>"
}
```

Response: `{"status":"ok","registered":true,"server_identity":"nyx1..."}`

## Session

`POST /api/v3/auth/session` — same signed body as register without identity_public_key required.

Response:

```json
{"status":"ok","session_token":"...","server_identity":"nyx1...","expires_at":1710003600}
```

## Messages

### Delivery model (store + pull — no online queue)

Keep the relay simple. **Do not** maintain per-user online presence or push queues.

1. Client `POST /api/v3/messages/send` → relay **stores** the signed envelope
   (indexed by `conversation_id` and `timestamp` / sequence).
2. For rooms, storage is enough; membership is checked only to authorize write.
3. When a client comes **online**, it calls once:
   `GET /api/v3/messages/sync?since=<cursor>&limit=…`
   Relay returns all stored messages for conversations the identity may read
   with `timestamp > since` (or server-side mailbox index by recipient if you prefer).
4. Optional: `POST /api/v3/messages/ack` only if you want the server to mark
   “client has cursor ≥ X” for metrics — **not required** for correctness.
5. No continuous client polling required; one automatic sync on `/connect` is enough.

This avoids server-side queue workers and online/offline branching.

## Messages

`POST /api/v3/messages/send` — MessageEnvelope wire dict (opaque ciphertext).

`GET /api/v3/messages/sync?since=&limit=`

`POST /api/v3/messages/ack` `{"message_ids":["nyx_msg_..."]}`

## Discovery

`GET /api/v3/discovery/servers`

```json
{"servers":[{"id":"r1","endpoint":"nyx://host","trust_level":2,"reputation":0.9,"uptime":0.99,"capacity":0.7}]}
```

## Profile

`PUT /api/v3/profile`

```json
{"identity":"nyx1...","display_name":"...","bio":"...","public_key":"<hex>","recovery_email":"..."}
```

`GET /api/v3/profile/{identity}`

## Token (UTXO)

`POST /api/v3/token/broadcast`

Body = signed transaction:

```json
{
  "protocol": "nyx-utxo-v1",
  "version": 1,
  "txid": "<hex>",
  "inputs": [{"txid":"...","vout":0,"amount_micro":100000000,"owner_id":"nyx1..."}],
  "outputs": [{"owner_id":"nyx1...","amount_micro":50000000}],
  "timestamp": 1710000000000,
  "memo": "",
  "signer": "nyx1...",
  "signatures": [{"owner_id":"nyx1...","signature":"<hex>"}]
}
```

Relay **must**:

1. Verify Ed25519 signatures over canonical JSON body (keys sorted, without `signatures`/`txid`).
2. Reject if any input outpoint already spent.
3. Apply UTXO updates; gossip to peers.
4. Return `{"status":"ok","txid":"...","confirmed":true}`.

`GET /api/v3/token/utxos/{identity}` → list unspent for wallet sync.

`GET /api/v3/token/tx/{txid}` → transaction status.

## Handles (unique @usernames)

`GET /api/v3/handles/check?name=alice`
```json
{"available": true, "handle": "alice"}
```

`POST /api/v3/handles/claim` (include `claimed_at_ms`; **older timestamp wins** on race)
```json
{"handle":"alice","kind":"user|group|channel","target_id":"nyx1...|room_id","owner_id":"nyx1...","public_key":"<hex>"}
```
Response `409` if taken. Relay MUST enforce global uniqueness.

`GET /api/v3/handles/{name}` → `{handle, kind, target_id, public_key}`

## Updates

`GET /api/v3/updates/manifest` — signed client release manifest.

## Sample curl

```bash
curl -s https://RELAY/api/v3/health
curl -s -X POST https://RELAY/api/v3/auth/session -H 'Content-Type: application/json' -d @auth.json
curl -s -X POST https://RELAY/api/v3/token/broadcast -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @tx.json
```

