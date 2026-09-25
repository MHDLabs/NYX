# Unique handles (@username)

## Format
- 3–32 characters, `^[a-z][a-z0-9_]{2,31}$`
- Reserved: admin, nyx, system, …

## Uniqueness
One handle → one target (user identity, group id, or channel id).

## Concurrent registration (race)
When two clients claim the same handle at nearly the same time:

1. Each sends `claimed_at_ms` (client UTC epoch milliseconds at claim time).
2. **Older `claimed_at_ms` wins.**
3. The newer claim is rejected with conflict; the client must pick another id.

### Relay

`POST /api/v3/handles/claim`
```json
{
  "handle": "alice",
  "kind": "user",
  "target_id": "nyx1...",
  "owner_id": "nyx1...",
  "public_key": "<hex>",
  "claimed_at_ms": 1710000000123
}
```

Success: `{"status":"ok","handle":"alice"}`  
Conflict: `{"status":"conflict","handle":"alice","winner_target":"...","winner_claimed_at_ms":1710000000001}`

`GET /api/v3/handles/check?name=alice` → `{available, handle}`

Relay storage MUST keep `claimed_at_ms` and compare on every claim.
