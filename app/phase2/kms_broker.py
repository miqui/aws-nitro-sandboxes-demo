"""PHASE 2 - UNVERIFIED / UNEXERCISED SKELETON. Never run against AWS by this repo's tests.

Parent-side KMS broker for attestation-bound secret release. Intended flow (composed from
documented pieces, no AWS end-to-end recipe was found for sandboxes):
  guest --attestation doc (public_key set) over vsock--> broker
  broker --kms:Decrypt(CiphertextBlob, Recipient={AttestationDocument,...})--> KMS
  KMS --CiphertextForRecipient--> broker --vsock--> guest, which decrypts with its private key
The KMS key policy must pin kms:RecipientAttestation:ImageSha384 to the sandbox image PCR0.
Open blocker: no documented way found to build a sandbox EIF with known PCRs.
No credentials are read or stored here; boto3 comes from the instance role at runtime.
"""
from __future__ import annotations

import base64
from typing import Any, Optional

try:  # guarded: the repo's tests and --dry-run never need boto3
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None

KEY_ENCRYPTION_ALGORITHM = "RSAES_OAEP_SHA_256"  # UNVERIFIED against the sandbox flow


def make_client(region: str) -> Any:
    if boto3 is None:
        raise RuntimeError("boto3 is not installed; phase 2 broker cannot create a KMS client")
    return boto3.client("kms", region_name=region)


def decrypt_for_recipient(client: Any, ciphertext_b64: str, attestation_doc_b64: str,
                          key_id: Optional[str] = None) -> str:
    """Ask KMS to decrypt, returning the blob re-encrypted to the sandbox's public key (base64).

    The plaintext never reaches the parent: KMS returns CiphertextForRecipient only.
    """
    params = {
        "CiphertextBlob": base64.b64decode(ciphertext_b64),
        "Recipient": {
            "KeyEncryptionAlgorithm": KEY_ENCRYPTION_ALGORITHM,
            "AttestationDocument": base64.b64decode(attestation_doc_b64),
        },
    }
    if key_id:
        params["KeyId"] = key_id
    resp = client.decrypt(**params)
    blob = resp.get("CiphertextForRecipient")
    if not blob:
        raise RuntimeError("KMS response has no CiphertextForRecipient")
    return base64.b64encode(blob).decode("ascii")
