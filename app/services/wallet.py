from eth_account import Account

from app.utils.security import (
    decrypt_private_key,
    encrypt_private_key,
    hash_wallet_address,
    verify_wallet_signature,
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
    def verify_signature(
        wallet_address: str, message: str, signature: str
    ) -> bool:
        return verify_wallet_signature(wallet_address, message, signature)

    @staticmethod
    def decrypt_key(encrypted_key: str) -> str:
        return decrypt_private_key(encrypted_key)
