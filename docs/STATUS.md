# NYX Client — Implementation Status

Legend: **Complete** · **Partial** · **Not implemented**

## Complete (usable now)

| Area | Notes |
|------|--------|
| Identity Ed25519 + BIP39 | Local create/load, recovery mnemonic |
| Encrypted profile store | AEAD at rest under `nyx_data/` |
| DM Double Ratchet + history | Local E2EE; relay send when connected |
| Contacts / profiles / search | Local directory |
| Groups & channels | Local rooms + conversation list |
| Roles + mute/kick + post policy | Enforced on local send |
| Owner-only room settings | `require_owner` |
| Relay register + session + discovery | Client flow implemented |
| Profile + recovery email push | On connect |
| UTXO NYX ledger | Signed spends, double-spend reject locally |
| Marketplace listings | Purchase spends UTXOs |
| Wallet keystore export/import | Encrypted `.nks` under keystore dir |
| Attachments + voice note metadata | Local media files |
| Meeting registry | Join codes; no realtime media path |
| TUI + REPL | Full navigation |
| Auto-update client | Manifest verify logic |
| Multi-server scoring | Local probes + relay list fetch |

## Partial (needs live relay behaviour)

| Area | Client | Relay must provide |
|------|--------|-------------------|
| Message delivery to other devices | send + auto sync on connect | store messages; sync?since= pull (no push queue) |
| Token consensus | sign + broadcast | validate, gossip UTXO set, reject double-spend |
| Marketplace settlement | local UTXO pay | optional escrow APIs |
| Recovery email | sent on profile PUT | store + recovery workflow |
| Server discovery | fetch list | publish peers |
| Genesis coin distribution | per-identity bootstrap UTXO | shared genesis + faucet/anti-sybil |

## Not implemented (honest)

| Area | Reason |
|------|--------|
| Full blockchain / mining | Out of scope for messaging MVP; UTXO model is the token layer |
| WebRTC voice/video path | Meeting records only; media stack is extension point |
| Federated moderation gossip | Local mod log only |
| SQLCipher full DB | App-layer AEAD used |
| Plugin runtime / AI | Stubs folders only |

