"""
Certificate Authority (CA) management for HTTPS interception.

On first run we generate a self-signed root CA. For every host the proxy
intercepts, we mint a leaf certificate signed by that CA on the fly and
cache it. The user must install ca.crt into their browser/OS trust store
for HTTPS traffic to be decrypted without warnings.
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import sys
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _default_ca_dir() -> Path:
    """
    Per-user CA directory. Stable across rebuilds so the cert you imported
    into the OS trust store keeps working.

    * macOS:  ~/Library/Application Support/MiniBurp/ca
    * Linux:  $XDG_DATA_HOME/miniburp/ca  (or ~/.local/share/miniburp/ca)
    * Windows: %APPDATA%/MiniBurp/ca  (or ~/AppData/Roaming/MiniBurp/ca)
    * Override: set MINIBURP_CA_DIR to any absolute path.
    """
    env = os.environ.get("MINIBURP_CA_DIR")
    if env:
        return Path(env).expanduser().resolve()

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "MiniBurp" / "ca"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "MiniBurp" / "ca"
    # Linux / BSD
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "miniburp" / "ca"


CA_DIR = _default_ca_dir()
CA_CERT_PATH = CA_DIR / "ca.crt"
CA_KEY_PATH = CA_DIR / "ca.key"
CERT_CACHE_DIR = CA_DIR / "certs"

_lock = threading.Lock()
_cert_cache: dict[str, tuple[str, str]] = {}  # host -> (cert_path, key_path)


def _ensure_dirs() -> None:
    CA_DIR.mkdir(parents=True, exist_ok=True)
    CERT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _generate_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Create a new self-signed root CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MiniBurp"),
        x509.NameAttribute(NameOID.COMMON_NAME, "MiniBurp Root CA"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert, key


def load_or_create_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the CA from disk, generating it if missing."""
    _ensure_dirs()
    if CA_CERT_PATH.exists() and CA_KEY_PATH.exists():
        cert = x509.load_pem_x509_certificate(CA_CERT_PATH.read_bytes())
        key = serialization.load_pem_private_key(CA_KEY_PATH.read_bytes(), password=None)
        return cert, key

    cert, key = _generate_ca()
    CA_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    CA_KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"[ca] Generated new CA at {CA_CERT_PATH}")
    if sys.platform == "darwin":
        print(
            "[ca] To avoid browser cert warnings, import this file into\n"
            "[ca]   Keychain Access → System (or login) → Certificates\n"
            "[ca] and set it to \"Always Trust\". The tracked Chrome instance\n"
            "[ca] also works without keychain trust via SPKI pinning."
        )
    else:
        print(
            "[ca] Import this file into your browser's trusted root CA store "
            "to intercept HTTPS without warnings."
        )
    return cert, key


def _san_for_host(host: str) -> x509.SubjectAlternativeName:
    """Build a SubjectAlternativeName. Accepts DNS names or IPs."""
    try:
        return x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))])
    except ValueError:
        return x509.SubjectAlternativeName([x509.DNSName(host)])


def get_host_cert(host: str) -> tuple[str, str]:
    """Return filesystem paths (cert_pem, key_pem) for the given host, minting if needed."""
    with _lock:
        if host in _cert_cache:
            return _cert_cache[host]

        ca_cert, ca_key = load_or_create_ca()

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, host),
        ])
        now = datetime.datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(_san_for_host(host), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )

        safe = host.replace(":", "_").replace("/", "_")
        cert_path = CERT_CACHE_DIR / f"{safe}.crt"
        key_path = CERT_CACHE_DIR / f"{safe}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        _cert_cache[host] = (str(cert_path), str(key_path))
        return _cert_cache[host]
