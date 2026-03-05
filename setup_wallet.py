#!/usr/bin/env python3
"""One-time wallet setup: generate or import keys, derive API credentials.

Usage:
    # Generate a fresh wallet
    python setup_wallet.py --generate

    # Use existing private key (will derive API creds)
    python setup_wallet.py --key 0x...

    # Check existing .env credentials
    python setup_wallet.py --check
"""
import argparse
import sys
import os


def generate_wallet():
    """Generate a new Ethereum wallet for Polymarket."""
    try:
        from eth_account import Account
    except ImportError:
        print("Install eth-account: pip install eth-account")
        sys.exit(1)

    account = Account.create()
    print("\n=== NEW WALLET GENERATED ===")
    print(f"Address:     {account.address}")
    print(f"Private Key: {account.key.hex()}")
    print("\nFunding instructions:")
    print(f"1. Send USDC to {account.address} on Polygon network")
    print("2. You can send directly from Coinbase/Binance via Polygon")
    print("3. You need ~0.1 POL for gas (very cheap on Polygon)")
    print(f"\nAdd to .env:")
    print(f"POLYMARKET_PRIVATE_KEY={account.key.hex()}")
    return account.key.hex()


def derive_api_creds(private_key: str):
    """Derive Polymarket API credentials from private key."""
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        print("Install py-clob-client: pip install py-clob-client")
        sys.exit(1)

    print("\nDeriving Polymarket API credentials...")
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=0,
    )

    creds = client.create_or_derive_api_creds()
    print("\n=== API CREDENTIALS ===")
    print(f"API Key:        {creds.api_key}")
    print(f"API Secret:     {creds.api_secret}")
    print(f"API Passphrase: {creds.api_passphrase}")
    print(f"\nAdd to .env:")
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f"POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    return creds


def check_credentials():
    """Verify existing credentials work."""
    from dotenv import load_dotenv
    load_dotenv()

    key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    api_key = os.getenv("POLYMARKET_API_KEY", "")
    odds_key = os.getenv("ODDS_API_KEY", "")

    print("\n=== CREDENTIAL CHECK ===")
    print(f"Private Key:    {'SET' if key else 'MISSING'}")
    print(f"API Key:        {'SET' if api_key else 'MISSING'}")
    print(f"Odds API Key:   {'SET' if odds_key else 'MISSING'}")

    if key:
        try:
            from eth_account import Account
            account = Account.from_key(key)
            print(f"Wallet Address: {account.address}")
        except Exception as e:
            print(f"Private key error: {e}")

    if api_key:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            client = ClobClient(
                "https://clob.polymarket.com",
                key=key,
                chain_id=137,
                signature_type=0,
            )
            client.set_api_creds(ApiCreds(
                api_key=api_key,
                api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
                api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE", ""),
            ))
            orders = client.get_open_orders()
            print(f"CLOB Connection: OK (open orders: {len(orders)})")
        except Exception as e:
            print(f"CLOB Connection: FAILED - {e}")

    if odds_key:
        import requests
        resp = requests.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": odds_key},
            timeout=10,
        )
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"Odds API:       {'OK' if resp.ok else 'FAILED'} (requests remaining: {remaining})")


def main():
    parser = argparse.ArgumentParser(description="Polymarket wallet setup")
    parser.add_argument("--generate", action="store_true", help="Generate a new wallet")
    parser.add_argument("--key", type=str, help="Derive API creds from existing private key")
    parser.add_argument("--check", action="store_true", help="Check existing .env credentials")
    args = parser.parse_args()

    if args.generate:
        pk = generate_wallet()
        derive_api_creds(pk)
    elif args.key:
        derive_api_creds(args.key)
    elif args.check:
        check_credentials()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
