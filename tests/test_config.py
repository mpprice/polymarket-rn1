"""Unit tests for src.config.Config."""
import os
from unittest.mock import patch

import pytest

from src.config import Config


class TestConfigDefaults:
    """Test default configuration values.

    NOTE: The .env file is loaded at module import time, so some "defaults"
    are overridden by the project .env. Tests below verify code defaults by
    clearing the relevant env vars, AND verify effective values with .env.
    """

    def test_target_sports_count(self):
        cfg = Config()
        assert len(cfg.target_sports) == 26

    def test_target_sports_contains_all_expected(self):
        cfg = Config()
        expected = [
            "epl", "bun", "lal", "ucl", "sea", "fl1", "uel", "elc", "itsb",
            "mex", "arg", "bl2", "por", "es2", "nba", "nfl", "nhl", "cbb",
            "cfb", "atp", "wta", "scop", "bra", "mls", "tur", "ere",
        ]
        for sport in expected:
            assert sport in cfg.target_sports, f"{sport} missing from target_sports"

    def test_newly_added_sports_present(self):
        """NHL, CBB, CFB, ERE were added in the sport expansion."""
        cfg = Config()
        for sport in ["nhl", "cbb", "cfb", "ere"]:
            assert sport in cfg.target_sports, f"{sport} missing"

    @patch.dict(os.environ, {}, clear=False)
    def test_code_default_max_edge(self):
        """Code default is 25.0; .env may override to 20.0."""
        # Remove the env override so we get the code default
        env_copy = os.environ.copy()
        env_copy.pop("MAX_EDGE_PCT", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cfg = Config()
            assert cfg.max_edge_pct == 25.0

    @patch.dict(os.environ, {}, clear=False)
    def test_code_default_min_edge(self):
        """Code default is 2.5; .env may override to 1.5."""
        env_copy = os.environ.copy()
        env_copy.pop("MIN_EDGE_PCT", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cfg = Config()
            assert cfg.min_edge_pct == 2.5

    def test_effective_max_edge_with_env(self):
        """With the project .env loaded, max_edge is 20.0."""
        cfg = Config()
        # The .env sets MAX_EDGE_PCT=20.0
        assert cfg.max_edge_pct == 20.0

    def test_effective_min_edge_with_env(self):
        """With the project .env loaded, min_edge is 3.0."""
        cfg = Config()
        assert cfg.min_edge_pct == 3.0

    def test_effective_max_total_exposure_with_env(self):
        """With the project .env loaded, exposure is 400."""
        cfg = Config()
        assert cfg.max_total_exposure_usdc == 400.0

    def test_default_bankroll(self):
        cfg = Config()
        assert cfg.bankroll_usdc == 500.0

    def test_default_max_position(self):
        cfg = Config()
        assert cfg.max_position_usdc == 8.0

    @patch.dict(os.environ, {}, clear=False)
    def test_code_default_max_total_exposure(self):
        """Code default is 200; .env overrides to 400."""
        env_copy = os.environ.copy()
        env_copy.pop("MAX_TOTAL_EXPOSURE_USDC", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cfg = Config()
            assert cfg.max_total_exposure_usdc == 200.0

    def test_default_chain_id(self):
        cfg = Config()
        assert cfg.chain_id == 137

    def test_default_urls(self):
        cfg = Config()
        assert cfg.clob_url == "https://clob.polymarket.com"
        assert cfg.gamma_url == "https://gamma-api.polymarket.com"
        assert cfg.data_url == "https://data-api.polymarket.com"

    def test_default_kelly_fraction(self):
        cfg = Config()
        assert cfg.kelly_fraction == 0.15

    @patch.dict(os.environ, {}, clear=False)
    def test_code_default_entry_price_range(self):
        """Code defaults: min=0.03, max=0.50; .env may override."""
        env_copy = os.environ.copy()
        env_copy.pop("MIN_ENTRY_PRICE", None)
        env_copy.pop("MAX_ENTRY_PRICE", None)
        with patch.dict(os.environ, env_copy, clear=True):
            cfg = Config()
            assert cfg.min_entry_price == 0.03
            assert cfg.max_entry_price == 0.50

    def test_effective_entry_price_range_with_env(self):
        """With project .env, entry prices are 0.05 and 0.95."""
        cfg = Config()
        assert cfg.min_entry_price == 0.05
        assert cfg.max_entry_price == 0.95

    def test_default_scan_interval(self):
        cfg = Config()
        assert cfg.scan_interval_seconds == 300

    def test_default_learning_enabled(self):
        cfg = Config()
        assert cfg.learning_enabled is True

    def test_default_merge_enabled(self):
        cfg = Config()
        assert cfg.merge_enabled is True


class TestConfigEnvOverrides:
    """Test that environment variables override defaults."""

    @patch.dict(os.environ, {"MAX_TOTAL_EXPOSURE_USDC": "400"})
    def test_max_total_exposure_override(self):
        cfg = Config()
        assert cfg.max_total_exposure_usdc == 400.0

    @patch.dict(os.environ, {"MAX_EDGE_PCT": "20.0"})
    def test_max_edge_override(self):
        cfg = Config()
        assert cfg.max_edge_pct == 20.0

    @patch.dict(os.environ, {"MIN_EDGE_PCT": "1.5"})
    def test_min_edge_override(self):
        cfg = Config()
        assert cfg.min_edge_pct == 1.5

    @patch.dict(os.environ, {"BANKROLL_USDC": "1000"})
    def test_bankroll_override(self):
        cfg = Config()
        assert cfg.bankroll_usdc == 1000.0

    @patch.dict(os.environ, {"MAX_POSITION_USDC": "15"})
    def test_max_position_override(self):
        cfg = Config()
        assert cfg.max_position_usdc == 15.0

    @patch.dict(os.environ, {"KELLY_FRACTION": "0.25"})
    def test_kelly_fraction_override(self):
        cfg = Config()
        assert cfg.kelly_fraction == 0.25

    @patch.dict(os.environ, {"SCAN_INTERVAL": "60"})
    def test_scan_interval_override(self):
        cfg = Config()
        assert cfg.scan_interval_seconds == 60

    @patch.dict(os.environ, {"LEARNING_ENABLED": "false"})
    def test_learning_disabled(self):
        cfg = Config()
        assert cfg.learning_enabled is False

    @patch.dict(os.environ, {"MERGE_ENABLED": "false"})
    def test_merge_disabled(self):
        cfg = Config()
        assert cfg.merge_enabled is False

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key-123"})
    def test_odds_api_key(self):
        cfg = Config()
        assert cfg.odds_api_key == "test-key-123"

    @patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xdeadbeef"})
    def test_private_key(self):
        cfg = Config()
        assert cfg.private_key == "0xdeadbeef"

    @patch.dict(os.environ, {"DATA_DIR": "/tmp/testdata"})
    def test_data_dir_override(self):
        cfg = Config()
        assert cfg.data_dir == "/tmp/testdata"
