from typing import Optional

from eth_account import Account

from app.utils.security import (
    decrypt_private_key,
    encrypt_private_key,
    hash_wallet_address,
)


class WalletService:
    @staticmethod
    def generate_wallet() -> dict:
        account = Account.create()
        address = account.address
        private_key = account.key.hex()

        encrypted_key = encrypt_private_key(private_key)
        address_hash = hash_wallet_address(address)

        return {
            "address": address,
            "private_key": private_key,
            "encrypted_private_key": encrypted_key,
            "address_hash": address_hash,
        }

    @staticmethod
    def hash_address(address: str) -> str:
        return hash_wallet_address(address)

    @staticmethod
    def validate_private_key(private_key: str) -> Optional[str]:
        """Validate an Ethereum private key and return its derived address.

        Returns the address if valid, None if invalid.
        """
        try:
            key = private_key if private_key.startswith("0x") else f"0x{private_key}"
            account = Account.from_key(key)
            return account.address
        except Exception:
            return None

    @staticmethod
    def decrypt_key(encrypted_key: str) -> str:
        return decrypt_private_key(encrypted_key)
