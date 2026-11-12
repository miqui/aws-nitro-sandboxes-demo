import base64
import unittest

import kms_broker


class FakeKms:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    def decrypt(self, **kw):
        self.kwargs = kw
        return self.resp


class BrokerTests(unittest.TestCase):
    def test_passes_recipient_and_returns_only_recipient_ciphertext(self):
        k = FakeKms({"CiphertextForRecipient": b"enc", "Plaintext": b"MUST-NOT-LEAK"})
        out = kms_broker.decrypt_for_recipient(k, base64.b64encode(b"ct").decode(),
                                               base64.b64encode(b"doc").decode(), key_id="alias/x")
        self.assertEqual(base64.b64decode(out), b"enc")
        self.assertEqual(k.kwargs["Recipient"]["AttestationDocument"], b"doc")
        self.assertEqual(k.kwargs["CiphertextBlob"], b"ct")
        self.assertNotIn("MUST-NOT-LEAK", out)

    def test_missing_ciphertext_for_recipient_raises(self):
        with self.assertRaises(RuntimeError):
            kms_broker.decrypt_for_recipient(FakeKms({"Plaintext": b"x"}), "", "")

    def test_make_client_without_boto3(self):
        saved, kms_broker.boto3 = kms_broker.boto3, None
        try:
            with self.assertRaises(RuntimeError):
                kms_broker.make_client("us-east-1")
        finally:
            kms_broker.boto3 = saved


if __name__ == "__main__":
    unittest.main()
