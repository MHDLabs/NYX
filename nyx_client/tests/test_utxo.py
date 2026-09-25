
from pathlib import Path
from nyx_client.crypto import Identity
from nyx_client.storage import Database
from nyx_client.core.utxo_ledger import UTXOLedger, MICRO


def test_utxo_transfer_and_double_spend(tmp_path: Path) -> None:
    db = Database(tmp_path / "u.db")
    db.connect()
    alice = Identity.create()
    bob = Identity.create()
    la = UTXOLedger(db, alice)
    start = la.balance_micro()
    assert start >= 100 * MICRO
    stx = la.transfer(bob.id, 10 * MICRO, memo="pay bob")
    assert la.balance_micro() == start - 10 * MICRO
    # double-spend same inputs must fail — build raw reuse would need spent inputs
    try:
        # try apply same tx again
        la.apply_tx(stx.raw)
        ok = False
    except ValueError as e:
        ok = "double-spend" in str(e).lower() or "spent" in str(e).lower()
    assert ok
    db.close()
