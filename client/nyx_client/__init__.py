"""
NYX Client - Terminal-Native Distributed Communication Protocol

Version: 0.1.0 (MVP)
Whitepaper reference: NYX Whitepaper v3.0

This package implements the Python client as specified in the whitepaper.
Architecture layers (from outermost to innermost):

  Presentation  -> ui/
  Interaction   -> ui/ + core/ (shortcuts, commands)
  Application   -> core/ (messaging, identity, sync, search)
  Protocol      -> protocol/ (connection, federation, discovery, routing)
  Cryptography  -> crypto/ (X3DH, Double Ratchet, signatures, keys)
  Storage       -> storage/ (SQLCipher, encrypted cache, config, queue)

Extension points for future phases already exist as packages:
  plugins/, ai/, update/, sync/
"""

__version__ = "0.2.0"
__whitepaper_version__ = "3.0"
