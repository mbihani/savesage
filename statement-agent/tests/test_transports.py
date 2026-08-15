import unittest

from harness.transports import extraction_payload, judge_payload


class TransportShapeTest(unittest.TestCase):
    def test_luna_uses_openai_file_data_url(self) -> None:
        payload = extraction_payload(b"synthetic-not-a-pdf", "synthetic.pdf", "prompt", {})
        block = payload["messages"][0]["content"][0]
        self.assertEqual(block["type"], "file")
        self.assertTrue(block["file"]["file_data"].startswith("data:application/pdf;base64,"))
        self.assertNotIn("document", str(block))

    def test_opus_uses_anthropic_document_base64(self) -> None:
        payload = judge_payload(b"synthetic-not-a-pdf", "prompt")
        block = payload["messages"][0]["content"][0]
        self.assertEqual(block["type"], "document")
        self.assertEqual(block["source"]["media_type"], "application/pdf")
        self.assertNotIn("data:application/pdf", block["source"]["data"])
        self.assertNotIn("reasoning_effort", payload)


if __name__ == "__main__":
    unittest.main()
