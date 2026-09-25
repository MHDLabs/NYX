from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
import hashlib
import base64


class Identity:
    """Cryptographic identity for NYX"""
    
    def __init__(self, private_key: rsa.RSAPrivateKey):
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._id = None
        self._public_key_bytes = None
    
    @classmethod
    def create(cls) -> 'Identity':
        """Create a new identity"""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        return cls(private_key)
    
    @classmethod
    def from_private_key(cls, private_key: rsa.RSAPrivateKey) -> 'Identity':
        """Create identity from existing private key"""
        return cls(private_key)
    
    @classmethod
    def load(cls, private_key_bytes: bytes) -> 'Identity':
        """Load identity from private key bytes"""
        private_key = serialization.load_pem_private_key(
            private_key_bytes,
            password=None,
            backend=default_backend()
        )
        return cls(private_key)
    
    @property
    def id(self) -> str:
        """Get identity ID (hash of public key)"""
        if self._id is None:
            pub_bytes = self.public_key_bytes
            self._id = hashlib.sha256(pub_bytes).hexdigest()[:16]
        return self._id
    
    @property
    def public_key_bytes(self) -> bytes:
        """Get public key as bytes"""
        if self._public_key_bytes is None:
            self._public_key_bytes = self._public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
        return self._public_key_bytes
    
    @property
    def private_key_bytes(self) -> bytes:
        """Get private key as bytes"""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
    
    def sign(self, message: bytes) -> bytes:
        """Sign a message"""
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return signature
    
    def verify(self, message: bytes, signature: bytes, public_key_bytes: bytes) -> bool:
        """Verify a signature"""
        try:
            public_key = serialization.load_pem_public_key(
                public_key_bytes,
                backend=default_backend()
            )
            public_key.verify(
                signature,
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except Exception:
            return False
    
    def encrypt(self, message: bytes, public_key_bytes: bytes) -> bytes:
        """Encrypt a message for a recipient"""
        public_key = serialization.load_pem_public_key(
            public_key_bytes,
            backend=default_backend()
        )
        ciphertext = public_key.encrypt(
            message,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return ciphertext
    
    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt a message"""
        plaintext = self._private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return plaintext
    
    def __repr__(self) -> str:
        return f"Identity(id={self.id})"