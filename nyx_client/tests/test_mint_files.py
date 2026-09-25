from pathlib import Path
from nyx_client.config.settings import *  # TokenSettings included
from nyx_client.core.app import NyxApp
from nyx_client.crypto.aead import generate_key
from nyx_client.crypto import Identity
from nyx_client.core.mint import issue_mint_voucher, save_voucher_file, load_voucher_file


def test_mint_claim_and_double_claim(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
        token=TokenSettings(allow_local_mine=True),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    before = app.wallet.balance_nyx()
    r = app.issue_test_mint(12.5, memo="block-reward")
    assert r["amount_nyx"] == 12.5
    assert app.wallet.balance_nyx() == before + 12.5
    # double claim same voucher file fails
    path = r["voucher_path"]
    try:
        app.claim_mint_voucher(path)
        assert False
    except ValueError as e:
        assert "already" in str(e).lower()
    # pay still works
    bob = Identity.create()
    app.transfer_nyx(bob.id, 1.0, memo="x")
    assert abs(app.wallet.balance_nyx() - (before + 12.5 - 1.0)) < 1e-6
    app.stop()


def test_attach_file(tmp_path: Path) -> None:
    s = Settings(
        storage=StorageSettings(data_dir=str(tmp_path), db_filename="t.db"),
        data_dir=tmp_path,
        network=NetworkSettings(),
        logging=LoggingSettings(level="ERROR"),
        updates=UpdateSettings(),
        token=TokenSettings(allow_local_mine=True),
    )
    app = NyxApp.from_settings(settings=s, profile_key=generate_key())
    app.start()
    peer = Identity.create()
    f = tmp_path / "doc.txt"
    f.write_text("hello file")
    res = app.dispatch(f"/attach {peer.id} {f}")
    assert res.ok, res.message
    convs = app.messages.list_conversations(limit=10)
    assert convs
    c0 = convs[0]
    cid = c0["conversation_id"] if isinstance(c0, dict) else c0.conversation_id
    files = app.attachments.list_for_conversation(cid)
    assert files and files[0].filename == "doc.txt"
    app.stop()
