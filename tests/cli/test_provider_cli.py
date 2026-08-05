from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER_CMD = (ROOT / "traittutor_cli" / "provider_cmd.py").read_text(encoding="utf-8")


class ProviderCliDocsContractTest(unittest.TestCase):
    def test_provider_contract_describes_copilot_as_validation_not_oauth_login(self) -> None:
        self.assertIn(
            'help="Provider: openai-codex (OAuth login) | github-copilot (validate existing Copilot auth)"',
            PROVIDER_CMD,
        )
        self.assertIn('"""Authenticate or validate provider access."""', PROVIDER_CMD)
        self.assertIn("GitHub Copilot auth validation succeeded.", PROVIDER_CMD)
        self.assertIn("GitHub Copilot auth validation failed:", PROVIDER_CMD)
        self.assertNotIn("OAuth provider: openai-codex | github-copilot", PROVIDER_CMD)
        self.assertNotIn("GitHub Copilot OAuth authentication succeeded.", PROVIDER_CMD)

if __name__ == "__main__":
    unittest.main()
