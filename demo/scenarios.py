"""Scenario definitions, scale presets and dimension pools (spec section 26).

Every scenario is deterministic: the injected change is a pure function of the
scenario name plus the fixed target cell. Ground truth is never written into
metadata; it stays in the evaluation output directory.
"""

from __future__ import annotations

from dataclasses import dataclass

SCENARIOS = (
    "ecpm_drop",
    "traffic_drop",
    "mixed_offset",
    "no_change",
    "incomplete_day",
    "schema_drift",
    "config_duplicate",
    "canonical_67",
)

PLATFORMS = ("android", "ios")

# ISO-3166 alpha-2 codes; the preset chooses how many appear in the data.
COUNTRY_POOL = (
    "US", "DE", "JP", "KR", "GB", "BR", "IN", "FR", "CA", "AU",
    "IT", "ES", "MX", "ID", "TR", "RU", "NL", "PL", "SE", "TH",
    "VN", "PH", "MY", "SG", "TW", "HK", "AR", "CL", "CO", "PE",
    "ZA", "EG", "NG", "SA", "AE", "IL", "NO", "DK", "FI", "CH",
    "AT", "BE", "PT", "GR", "CZ", "HU", "RO", "NZ",
)

# Ad network names; the anchors (AppLovin/AdMob/Unity) are used by scenarios.
NETWORK_POOL = (
    "AppLovin", "AdMob", "Unity", "Meta", "IronSource", "Tapjoy",
    "Vungle", "Mintegral", "Pangle", "AdColony", "Chartboost", "Liftoff",
    "Moloco", "DigitalTurbine", "Smaato", "InMobi", "Mopub", "Fyber",
    "Ogury", "Amazon",
)

# Three versions per platform at small scale keeps the release-config table
# aligned with the M2 connector fixtures (6 rows) while the M4 generator owns
# the data.
VERSION_POOL_SMALL = ("4.2.0", "4.2.1", "4.3.0")
VERSION_POOL_MEDIUM = ("4.2.0", "4.2.1", "4.3.0", "4.3.1", "4.4.0")

CAMPAIGN_IDS = (
    "cmp_001", "cmp_002", "cmp_003", "cmp_004", "cmp_005", "cmp_006",
)
CAMPAIGN_CHANNELS = ("apple_search_ads", "google_uac", "meta_ads", "tiktok", "unity_ads", "applovin")


@dataclass(frozen=True)
class ScalePreset:
    name: str
    countries: tuple[str, ...]
    versions: tuple[str, ...]
    networks: tuple[str, ...]
    observations: int  # campaign observation window (days)

    def combos_per_day(self) -> int:
        return len(self.countries) * len(PLATFORMS) * len(self.versions) * len(self.networks)


SCALE_PRESETS = {
    "small": ScalePreset(
        name="small",
        countries=COUNTRY_POOL[:24],
        versions=VERSION_POOL_SMALL,
        networks=NETWORK_POOL[:12],
        observations=8,
    ),
    "medium": ScalePreset(
        name="medium",
        countries=COUNTRY_POOL[:48],
        versions=VERSION_POOL_MEDIUM,
        networks=NETWORK_POOL,
        observations=8,
    ),
}


def scale_preset(scale: str) -> ScalePreset:
    if scale not in SCALE_PRESETS:
        raise SystemExit(
            f"unknown scale '{scale}'; choose one of {sorted(SCALE_PRESETS)}"
        )
    return SCALE_PRESETS[scale]


@dataclass(frozen=True)
class TargetCell:
    country: str
    platform: str
    app_version: str
    ad_network: str

    def as_dict(self) -> dict:
        return {
            "country": self.country,
            "platform": self.platform,
            "app_version": self.app_version,
            "ad_network": self.ad_network,
        }


# The scenario target from the specification (spec 26.3): US / Android / 4.2.1 /
# AppLovin. It is present at every scale by construction.
PRIMARY_TARGET = TargetCell("US", "android", "4.2.1", "AppLovin")
# The offsetting group used by mixed_offset.
OFFSET_TARGET = TargetCell("DE", "ios", "4.2.0", "AdMob")

INJECTIONS = {
    # "eCPM down, impressions stable" / "traffic down, eCPM stable" (spec 26.3):
    # the untouched factor is pinned to the baseline day for the target cell, so
    # the decomposition attributes the whole change to the injected factor.
    "ecpm_drop": {PRIMARY_TARGET: {"ecpm_factor": 0.62, "stable_impressions": True}},
    "traffic_drop": {PRIMARY_TARGET: {"impressions_factor": 0.55, "stable_ecpm": True}},
    "mixed_offset": {
        PRIMARY_TARGET: {"ecpm_factor": 0.70},
        OFFSET_TARGET: {"ecpm_factor": 1.25},
    },
    # no_change / incomplete_day / schema_drift / config_duplicate are handled
    # explicitly by the generator.
}


def noise_multiplier(rng_value: float, low: float = 0.7, high: float = 1.3) -> float:
    return low + (high - low) * rng_value
