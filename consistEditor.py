#!/usr/bin/env python3
import argparse
import json
import logging
import re
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union, Any
import sys
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import threading
from abc import ABC, abstractmethod
import multiprocessing

# PERFORMANCE OPTIMIZATION: Pre-compile frequently used regex patterns
_REGEX_CACHE = {}

# PERFORMANCE OPTIMIZATION: Cache for alias normalization
_ALIAS_CACHE = {}

def get_compiled_regex(pattern: str, flags=0) -> re.Pattern:
    """Get cached compiled regex pattern to avoid recompilation."""
    key = (pattern, flags)
    if key not in _REGEX_CACHE:
        _REGEX_CACHE[key] = re.compile(pattern, flags)
    return _REGEX_CACHE[key]

# PERFORMANCE OPTIMIZATION: Pre-compile locomotive patterns
_LOCO_PATTERNS = [
    # WDG series - Goods locomotives
    r'(?i)(wdg[\-_]?(?:3a?|4(?:d|g)?|5))(?:[\-_]?[a-z0-9]*)*',
    # WDM series - Mixed locomotives  
    r'(?i)(wdm[\-_]?(?:2(?:a|b|c)?|3(?:a|b|c|d|f)?|7|16847|18500))(?:[\-_]?[a-z0-9]*)*',
    # WAP series - Passenger locomotives
    r'(?<![A-Za-z0-9])(wap(?:1|4(?:e)?|5|7))(?![A-Za-z0-9])',
    # WAG series - Goods locomotives
    r'(?<![A-Za-z0-9])(wag(?:5(?:a?|d|e|h|p)?|7|9(?:h)?|12))(?![A-Za-z0-9])',
    # WDP series - Shunting locomotives
    r'(?<![A-Za-z0-9])(wdp(?:1|2(?:a)?|3a?|4(?:b|d)?))(?![A-Za-z0-9])',
    # WCAM series - AC Mixed locomotives
    r'(?<![A-Za-z0-9])(wcam(?:1|2p?|3))(?![A-Za-z0-9])',
    # WCG series - Goods locomotives
    r'(?<![A-Za-z0-9])(wcg(?:1|2))(?![A-Za-z0-9])',
    # WCM series - Mixed locomotives
    r'(?<![A-Za-z0-9])(wcm(?:|1|6))(?![A-Za-z0-9])',
    # WAM series - AC Mixed locomotives
    r'(?<![A-Za-z0-9])(wam(?:4(?:6p)?))(?![A-Za-z0-9])',
    # Vande Bharat engines
    r'(?<![A-Za-z0-9])vbdc(?![A-Za-z0-9])',
    r'(?<![A-Za-z0-9])vbdmc(?![A-Za-z0-9])',
    # Other locomotives
    r'(?<![A-Za-z0-9])(ydm4)(?![A-Za-z0-9])',
    r'(?<![A-Za-z0-9])(zdm506)(?![A-Za-z0-9])',
]

_COMPILED_LOCO_PATTERNS = [get_compiled_regex(pattern) for pattern in _LOCO_PATTERNS]

# PERFORMANCE OPTIMIZATION: Pre-compile common regex patterns
_NORMALIZE_PATTERN = get_compiled_regex(r'[^a-z0-9]+')
_TOKENIZE_PATTERN = get_compiled_regex(r'[a-z0-9]+')

try:
    from fuzzywuzzy import fuzz, process

    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False
    # Suppress the warning - fallback works fine

try:
    import colorama
    from colorama import Fore, Back, Style

    colorama.init(autoreset=True)
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False

    # Fallback color class - no warning needed
    class _DummyColor:
        def __getattr__(self, name):
            return ""

    Fore = Back = Style = _DummyColor()


class AssetKind(Enum):
    """Asset type enumeration."""

    ENGINE = "Engine"
    WAGON = "Wagon"


class MatchPhase(Enum):
    """Asset resolution phase enumeration."""

    EXACT_NAME = auto()
    EXACT_NORM = auto()
    DIGIT_NEAR = auto()
    LOCAL_FOLDER = auto()
    IR_COMPOSITE = auto()
    WILDCARD_MATCH = auto()
    KEY_TOKEN_ALL = auto()
    KEY_TOKEN_PARTIAL = auto()
    FOLDER_SCORE = auto()
    GLOBAL_SCORE = auto()
    SEMANTIC_MATCH = auto()
    DEFAULT_ENGINE = auto()
    DEFAULT_WAGON = auto()
    UNRESOLVED = auto()


class TractionType(Enum):
    """Locomotive traction type."""

    ELECTRIC = "Electric"
    DIESEL = "Diesel"
    STEAM = "Steam"
    UNKNOWN = "Unknown"


@dataclass
class ScoreConfig:
    """Scoring configuration for asset matching."""

    # Basic scoring
    norm_exact_bonus: int = 60
    jaccard_multiplier: int = 100

    # Engine scoring
    engine_series_match: int = 140
    engine_series_mismatch: int = -80
    engine_class_match: int = 90
    engine_class_mismatch: int = -70
    engine_family_match: int = 50
    engine_family_mismatch: int = -25
    traction_match: int = 40
    traction_mismatch: int = -150

    # Coach/wagon scoring
    coach_type_match: int = 120
    coach_type_mismatch: int = -200
    freight_type_match: int = 110
    freight_type_mismatch: int = -55
    carbody_match: int = 70
    carbody_mismatch: int = -40
    set_type_match: int = 50

    # Penalty/bonus system
    passenger_freight_mismatch: int = -800
    unit_type_mismatch: int = -150
    default_penalty: int = -100
    non_default_bonus: int = 50

    # Token matching bonuses
    key_token_all_bonus: int = 60
    key_token_partial_bonus: int = 45
    ir_composite_bonus: int = 180
    digit_near_bonus: int = 120

    # Geographic and technical matching
    region_match: int = 40
    depot_match: int = 35
    tech_spec_match: int = 50
    manufacturer_match: int = 65
    region_mismatch: int = -15
    depot_mismatch: int = -10
    tech_spec_mismatch: int = -20
    manufacturer_mismatch: int = -25

    # Minimum score thresholds
    min_token_score: int = 150
    partial_coverage_threshold: float = 0.6
    near_digit_max_diff: int = 5


@dataclass
class AssetMetadata:
    """Metadata extracted from asset names and folders."""

    kind: AssetKind
    name: str
    folder: str = ""
    normalized: str = ""
    tokens: Set[str] = field(default_factory=set)
    core_tokens: Set[str] = field(default_factory=set)

    # Engine-specific attributes
    engine_class: str = ""
    engine_series: str = ""
    engine_family: str = ""
    traction: TractionType = TractionType.UNKNOWN
    is_unit: bool = False

    # Wagon-specific attributes
    coach_type: str = ""
    freight_type: str = ""
    carbody: str = ""
    set_type: str = ""

    # Geographic and technical attributes
    region: str = ""
    depot: str = ""
    tech_spec: str = ""
    manufacturer: str = ""

    # Additional metadata
    variant: Optional[int] = None
    livery: str = ""

    def __post_init__(self):
        """Post-initialization processing."""
        if not self.normalized:
            self.normalized = self._normalize_name()
        if not self.tokens:
            self.tokens = self._extract_tokens()
        if not self.core_tokens:
            self.core_tokens = self._extract_core_tokens()

    def get(self, key: str, default=None):
        """Dictionary-like access for backward compatibility."""
        return getattr(self, key, default)

    def _normalize_name(self) -> str:
        """Normalize asset name for comparison."""
        # Convert to lowercase and replace non-alphanumeric with spaces
        normalized = re.sub(r"[^a-z0-9]+", " ", self.name.lower())
        # Remove extra whitespace
        return " ".join(normalized.split())

    def _extract_tokens(self) -> Set[str]:
        """Extract tokens from name and folder."""
        tokens = set()
        # Extract from name
        name_tokens = _TOKENIZE_PATTERN.findall(self.normalized)
        tokens.update(name_tokens)
        # Extract from folder
        if self.folder:
            folder_norm = re.sub(r"[^a-z0-9]+", " ", self.folder.lower())
            folder_tokens = _TOKENIZE_PATTERN.findall(folder_norm)
            tokens.update(f"f:{token}" for token in folder_tokens)
        return tokens

    def _extract_core_tokens(self) -> Set[str]:
        """Extract core classification tokens."""
        core = set()
        for attr in [
            "engine_class",
            "engine_series",
            "coach_type",
            "freight_type",
            "carbody",
            "set_type",
        ]:
            value = getattr(self, attr)
            if value:
                core.add(value.lower())
        return core


@dataclass
class AssetRecord:
    """Complete asset record with metadata and file information."""

    kind: AssetKind
    name: str
    folder: str
    path: Path
    metadata: AssetMetadata
    key_tokens: Set[str] = field(default_factory=set)
    ir_composite: str = ""
    # PERFORMANCE OPTIMIZATION: Cache class detection to avoid repeated regex calls
    cached_class: str = ""
    # PERFORMANCE OPTIMIZATION: Cache normalized strings and tokens
    cached_normalized: str = ""
    cached_tokens: Set[str] = field(default_factory=set)

    def __post_init__(self):
        """Post-initialization processing."""
        if not self.key_tokens:
            self.key_tokens = self._generate_key_tokens()
        if not self.ir_composite:
            self.ir_composite = self._build_ir_composite()
        # PERFORMANCE OPTIMIZATION: Cache class detection result
        if not self.cached_class:
            self.cached_class = detect_wagon_or_engine_class(self.name, "Engine" if self.kind == AssetKind.ENGINE else "Wagon") or ""
        # PERFORMANCE OPTIMIZATION: Cache normalized strings and tokens
        if not self.cached_normalized:
            self.cached_normalized = re.sub(r"[^a-z0-9]+", " ", self.name.lower()).strip()
        if not self.cached_tokens:
            self.cached_tokens = set(_TOKENIZE_PATTERN.findall(self.cached_normalized))

    def __hash__(self):
        return hash((self.kind, self.name, self.folder, str(self.path)))

    def __eq__(self, other):
        if not isinstance(other, AssetRecord):
            return False
        return (self.kind, self.name, self.folder, str(self.path)) == (
            other.kind,
            other.name,
            other.folder,
            str(other.path),
        )

    @property
    def key_lower(self) -> str:
        """Generate lowercase key for indexing."""
        return f"{self.folder}|{self.name}".lower()

    def _generate_key_tokens(self) -> Set[str]:
        """Generate key tokens for indexing."""
        tokens = set(self.metadata.tokens)
        # Add folder-prefixed tokens
        if self.folder:
            folder_tokens = _TOKENIZE_PATTERN.findall(self.folder.lower())
            tokens.update(f"f:{token}" for token in folder_tokens)
        return tokens

    def _build_ir_composite(self) -> str:
        """Build Indian Railways composite identifier."""
        parts = []
        meta = self.metadata

        if meta.carbody:
            parts.append(meta.carbody.lower())

        if meta.freight_type:
            parts.append(meta.freight_type.lower())
        elif meta.coach_type:
            parts.append(meta.coach_type.lower())

        if meta.set_type:
            parts.append(meta.set_type.lower())

        if meta.engine_class:
            parts.append(meta.engine_class.lower())

        if meta.engine_series:
            parts.append(meta.engine_series.lower())

        return "_".join(parts) if parts else ""


@dataclass
class MatchResult:
    """Result of asset matching operation."""

    chosen: Optional[AssetRecord]
    phase: MatchPhase
    score: float
    target: AssetMetadata
    candidates_evaluated: int = 0
    match_details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        """Check if asset was successfully resolved."""
        return self.chosen is not None

    @property
    def is_changed(self) -> bool:
        """Check if resolution resulted in a change."""
        if not self.chosen:
            return False
        return not (
            self.chosen.folder.lower() == self.target.folder.lower()
            and self.chosen.name.lower() == self.target.name.lower()
        )


class IndianRailwaysClassifier:
    """Indian Railways locomotive and coach classification system - ENHANCED v2.2.5 WITH FREIGHT ANALYSIS."""

    # ENHANCED: Locomotive classifications including multiple units
    ENGINE_CLASSES = {
        # Electric passenger
        "wap1",
        "wap4",
        "wap5",
        "wap7",
        # Electric freight
        "wag5",
        "wag7",
        "wag9",
        "wag12",
        # Electric mixed/heritage
        "wam4",
        "wcam2",
        "wcam3",
        "wcg2",
        "wcm1",
        "wcm2",
        "wcm3",
        "wcm4",
        "wcm5",
        # Diesel freight
        "wdg3",
        "wdg3a",
        "wdg4",
        "wdg4d",
        "wdg4g",
        # Diesel mixed/mainline
        "wdm2",
        "wdm3",
        "wdm3a",
        "wdm3d",
        # Diesel passenger
        "wdp1",
        "wdp3",
        "wdp4",
        "wdp4b",
        "wdp4d",
        "wdp4e",
        # Diesel shunting
        "wds4",
        "wds6",
        # Heritage/meter gauge
        "ydm4",
        "yam1",
        # ENHANCED v2.2.1: Multiple units (also treated as engine classes)
        "emu",
        "memu",
        "dmu",
        "mmu",
        "demu",
        # ENHANCED v2.2.1: Maintenance equipment
        "plasser",
        "maintenance",
    }

    # Coach classifications
    COACH_TYPES = {
        "1a",
        "2a",
        "3a",
        "3e",
        "sl",
        "gs",
        "cc",
        "accc",
        "ec",
        "eog",
        "pc",
        "slr",
        "ac",
        "nonac",
        "chair",
        "executive",
        "general",
        "2s",
        "diner",
        "buffet",
        "restaurant",
        "luggage",
    }

    # ENHANCED v2.2.5: Freight wagon types based on freight_wag_files.csv analysis
    FREIGHT_TYPES = {
        # Open wagons (BOXN family) - ENHANCED
        "boxn",
        "boxnr",
        "boxncr",
        "boxng",
        "boxnhl",
        "boxnham",
        "boxnm1",
        "boxnm2",
        "boxnlb",
        "boxnhs",
        "boxnha",
        # Covered wagons - ENHANCED
        "bcn",
        "bcna",
        "bcne",
        "bcnh",
        "bcnhl",
        "bcnl",
        "bccnr",
        "bccw",
        "bcbfg",
        "bcfc",
        "bvcm",
        "covered",
        "ventilated",
        # ENHANCED v2.2.5: Additional covered wagon types from analysis
        "bccn",
        "bcnloa",
        # Tank wagons - ENHANCED v2.2.2
        "tank",
        "tanker",
        "btpn",
        "btap",
        "btcs",
        "btpgln",
        "btaln",
        "btfln",
        "btmn",
        "btlr",
        "btcdl",
        "btk",
        "btc",
        # ENHANCED v2.2.5: Additional tank types from analysis
        "bti",
        # Flat/container wagons
        "flat",
        "flatcar",
        "blc",
        "blca",
        "blcb",
        "bcacm",
        "bcacbm",
        "bfns",
        "bfr",
        "bfki",
        "bfkn",
        "bfat",
        "bft",
        "nmg",
        "nmgc",
        "container",
        "concor",
        "intermodal",
        # ENHANCED v2.2.5: Container wagon variants from analysis
        "bca",
        "bcb",
        "con",
        # Hopper wagons - ENHANCED v2.2.2
        "bobr",
        "bobrn",
        "bobrnhs",
        "boby",
        "bobyn",
        "gondola",
        "hopper",
        "stone",
        "aggregate",
        "ballast",
        "coal",
        "ore",
        # Brake vans - ENHANCED v2.2.2
        "brd",
        "brn",
        "brs",
        "bru",
        "bvzi",
        "bvzc",
        "brake",
        # ENHANCED v2.2.5: Additional brake wagon types from analysis
        "brna",
        # Special wagons
        "hpcv",
        "hcpv",
        "parcel",
        "mail",
        "low_loader",
        "well_wagon",
        "schnabel",
        "heavy_haul",
        "transformer",
        "reactor",
        # ENHANCED v2.2.5: Specialized freight from analysis
        "cement",
        "coil",
        "slab",
        "auto",
        "timber",
        "billet",
        "pipe",
        # ENHANCED v2.2.5: Milk transport wagons
        "milktanker",
        "vvn",
        # ENHANCED v2.2.5: Manufacturer series from analysis
        "bsam",
        "asmi",
        "apl",
    }

    # Carbody types
    CARBODY_TYPES = {"lhb", "icf", "integral", "conventional", "modern"}

    # Special train sets - ENHANCED v2.2.3
    SPECIAL_SETS = {
        "vandebharat",
        "vande",
        "vb",
        "train18",
        "humsafar",
        "tejas",
        "gatiman",
        "rajdhani",
        "shatabdi",
        "janshatabdi",
        "duronto",
        "garibrath",
        "antyodaya",
        "deendayalu",
        "anubhuti",
        "utk",
        "utkrisht",
        "doubledecker",
        "samparkkranti",
        "yuva",
    }

    # ENHANCED v2.2.4: Traction classification including multiple units
    ELECTRIC_CLASSES = {
        "wap1",
        "wap4",
        "wap5",
        "wap7",
        "wag5",
        "wag7",
        "wag9",
        "wag12",
        "wam4",
        "wcam2",
        "wcam3",
        "wcg2",
        "wcm1",
        "wcm2",
        "wcm3",
        "wcm4",
        "wcm5",
        # EMU/MEMU traction variants - ENHANCED
        "emu",
        "memu",
        "ac_emu",
        "dc_emu",
        "mmu",
    }

    # ENHANCED v2.2.2: Multiple unit classifications
    EMU_CLASSES = {
        "emu",
        "ac_emu",
        "dc_emu",
        "dmc",
        "dtc",
        "mc",
        "tc",
        "dc",
        "dt",
        "dmc1",
        "dmc2",
        "dtc1",
        "dtc2",
        "motorcoach",
        "trailercoach",
        "drivingcar",
        "drivingtrailer",
        "acemu",
        "dcemu",
    }
    MEMU_CLASSES = {
        "memu",
        "dmc",
        "dtc",
        "mc",
        "tc",
        "dc",
        "dt",
        "dmc1",
        "dmc2",
        "dtc1",
        "dtc2",
        "motorcoach",
        "trailercoach",
        "drivingcar",
        "drivingtrailer",
    }
    DMU_CLASSES = {
        "demu",
        "dmu",
        "dpc",
        "dtc",
        "tc",
        "dcc",
        "dpc1",
        "dpc2",
        "dcc1",
        "dcc2",
        "motorcoach",
        "trailercoach",
        "drivingcar",
        "drivingtrailer",
    }
    MMU_CLASSES = {
        "mmu",
        "dmc",
        "dtc",
        "mc",
        "tc",
        "dc",
        "dt",
        "dmc1",
        "dmc2",
        "dtc1",
        "dtc2",
        "motorcoach",
        "trailercoach",
        "drivingcar",
        "drivingtrailer",
    }

    DIESEL_CLASSES = {
        "wdg3",
        "wdg3a",
        "wdg4",
        "wdg4d",
        "wdg4g",
        "wdm2",
        "wdm3",
        "wdm3a",
        "wdm3d",
        "wdp1",
        "wdp3",
        "wdp4",
        "wdp4b",
        "wdp4d",
        "wdp4e",
        "wds4",
        "wds6",
    }

    # ENHANCED v2.2.5: Manufacturer/Series prefixes for freight detection
    FREIGHT_MANUFACTURER_PREFIXES = {"bsam_", "asmi", "con_"}

    # Comprehensive aliases system
    ALIASES = {
        # EMU/MEMU/DMU/MMU classes and car roles (expanded)
        "memu": "memu",
        "emu": "emu",
        "mmu": "mmu",
        "dmu": "dmu",
        "demu": "demu",
        "ac_emu": "ac_emu",
        "dc_emu": "dc_emu",
        "acemu": "ac_emu",
        "dcemu": "dc_emu",
        "dmc": "dmc",
        "dtc": "dtc",
        "mc": "mc",
        "tc": "tc",
        "dc": "dc",
        "dt": "dt",
        "dmc1": "dmc",
        "dmc2": "dmc",
        "dtc1": "dtc",
        "dtc2": "dtc",
        "motorcoach": "mc",
        "trailercoach": "tc",
        "drivingcar": "dc",
        "drivingtrailer": "dt",
        "dpc": "dpc",
        "dcc": "dcc",
        "dpc1": "dpc",
        "dpc2": "dpc",
        "dcc1": "dcc",
        "dcc2": "dcc",
        # AC Coach variations
        "1ac": "1a",
        "ac1": "1a",
        "acfirst": "1a",
        "ac_first": "1a",
        "firstac": "1a",
        "2ac": "2a",
        "ac2": "2a",
        "acsecond": "2a",
        "ac_second": "2a",
        "secondac": "2a",
        "3ac": "3a",
        "ac3": "3a",
        "acthird": "3a",
        "ac_third": "3a",
        "thirdac": "3a",
        # General classes
        "gs": "gs",
        "general": "gs",
        "gencar": "gs",
        "unreserved": "gs",
        "second": "gs",
        # Sleeper variations
        "sl": "sl",
        "slp": "sl",
        "sleeper": "sl",
        "sleeping": "sl",
        # Chair cars
        "cc": "cc",
        "chair": "cc",
        "chaircar": "cc",
        "accc": "accc",
        "ac_chair": "accc",
        "ac_cc": "accc",
        # Special coaches
        "pc": "pc",
        "pantry": "pc",
        "pantrycar": "pc",
        "catering": "pc",
        "eog": "eog",
        "generator": "eog",
        "power": "eog",
        "powercar": "eog",
        "slr": "slr",
        "guard": "slr",
        "luggage": "slr",
        "caboose": "slr",
        # Engine variations with hyphens and underscores
        "wap-4": "wap4",
        "wap_4": "wap4",
        "wap-7": "wap7",
        "wap_7": "wap7",
        "wdg-3": "wdg3a",
        "wdg_3": "wdg3a",
        "wdg-4": "wdg4",
        "wdg_4": "wdg4",
        "wdm-2": "wdm2",
        "wdm_2": "wdm2",
        "wdm-3": "wdm3a",
        "wdm_3": "wdm3a",
        # Carbody aliases
        "icf": "icf",
        "integral": "icf",
        "conventional": "icf",
        "lhb": "lhb",
        "linke_hofmann": "lhb",
        "modern": "lhb",
        # Freight aliases - ENHANCED v2.2.5
        "boxn": "boxn",
        "box": "boxn",
        "bcn": "bcn",
        "bcna": "bcna",
        "tank": "tank",
        "tanker": "tank",
        "btpn": "btpn",
        "flat": "flat",
        "flatcar": "flat",
        "container": "container",
        "parcel": "parcel",
        "hpcv": "hpcv",
        "hcpv": "hcpv",
        # New freight aliases from analysis
        "bsam": "bsam",
        "asmi": "asmi",
        "con": "container",
        "cement": "cement",
        "coil": "coil",
        "coal": "coal",
        "milktanker": "milktanker",
        "milk": "vvn",
        # Special set aliases
        "utk": "utk",
        "utkrisht": "utk",
        "utkal": "utk",
        "vande": "vande",
        "vandebharat": "vande",
        "vb": "vande",
        "train18": "vande",
        "humsafar": "humsafar",
        "tejas": "tejas",
        "rajdhani": "rajdhani",
        "shatabdi": "shatabdi",
        "duronto": "duronto",
    }

    @classmethod
    def get_traction_type(cls, engine_class: str) -> TractionType:
        """Determine traction type from engine class."""
        if not engine_class:
            return TractionType.UNKNOWN

        engine_class_lower = engine_class.lower()
        if engine_class_lower in cls.ELECTRIC_CLASSES:
            return TractionType.ELECTRIC
        elif engine_class_lower in cls.DIESEL_CLASSES:
            return TractionType.DIESEL
        else:
            return TractionType.UNKNOWN

    @classmethod
    def normalize_alias(cls, token: str) -> str:
        """Normalize token using alias system with caching."""
        token_lower = token.lower()
        if token_lower not in _ALIAS_CACHE:
            _ALIAS_CACHE[token_lower] = cls.ALIASES.get(token_lower, token_lower)
        return _ALIAS_CACHE[token_lower]

    @classmethod
    def is_engine_class(cls, token: str) -> bool:
        """Check if token is a valid engine class."""
        return token.lower() in cls.ENGINE_CLASSES

    @classmethod
    def is_coach_type(cls, token: str) -> bool:
        """Check if token is a valid coach type."""
        return token.lower() in cls.COACH_TYPES

    @classmethod
    def is_freight_type(cls, token: str) -> bool:
        """Check if token is a valid freight type."""
        return token.lower() in cls.FREIGHT_TYPES


class AssetMetadataExtractor:
    """Extracts metadata from asset names and folders."""

    def __init__(self, classifier: IndianRailwaysClassifier):
        self.classifier = classifier
        self.ignore_tokens = {
            "sound",
            "horn",
            "ai-horn",
            "cab",
            "cabview",
            "cvf",
            "sms",
            "sfx",
            "audio",
            "wav",
            "mp3",
            "readme",
            "manual",
            "docs",
            "preview",
            "thumbnail",
            "texture",
            "textures",
            "common",
        }

    def extract_metadata(
        self, kind: AssetKind, name: str, folder: str = ""
    ) -> AssetMetadata:
        """Extract comprehensive metadata from asset name and folder."""
        metadata = AssetMetadata(kind=kind, name=name, folder=folder)

        # Normalize and extract basic tokens
        combined_text = f"{folder} {name}".lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", combined_text).strip()
        tokens = set(normalized.split()) - self.ignore_tokens

        # Apply aliases
        normalized_tokens = {self.classifier.normalize_alias(token) for token in tokens}
        metadata.tokens = normalized_tokens

        # Extract specific classifications
        self._extract_engine_metadata(metadata, normalized_tokens, combined_text)
        self._extract_wagon_metadata(metadata, normalized_tokens, combined_text)
        self._extract_geographic_metadata(metadata, normalized_tokens)
        self._extract_technical_metadata(metadata, normalized_tokens)

        # Extract variant number
        variant_match = re.search(r"(\d+)$", name)
        if variant_match:
            try:
                metadata.variant = int(variant_match.group(1))
            except ValueError:
                pass

        return metadata

    def _extract_engine_metadata(
        self, metadata: AssetMetadata, tokens: Set[str], text: str
    ):
        """Extract engine-specific metadata."""
        if metadata.kind != AssetKind.ENGINE:
            return

        # Search both tokens and folder/name for engine class
        folder_norm = metadata.folder.lower().replace("_", " ")
        name_norm = metadata.name.lower().replace("_", " ")
        all_text = f"{folder_norm} {name_norm} {text}".lower()
        for token in tokens:
            if self.classifier.is_engine_class(token):
                metadata.engine_class = token.upper()
                metadata.traction = self.classifier.get_traction_type(token)
                break
        if not metadata.engine_class:
            for ec in self.classifier.ENGINE_CLASSES:
                if ec in all_text:
                    metadata.engine_class = ec.upper()
                    metadata.traction = self.classifier.get_traction_type(ec)
                    break

        # Extract engine series (class + number)
        if metadata.engine_class:
            series_pattern = (
                rf"(?i){re.escape(metadata.engine_class)}[-_]?(\d{{1,3}}[a-z]?)"
            )
            series_match = re.search(series_pattern, all_text)
            if series_match:
                metadata.engine_series = (
                    f"{metadata.engine_class}{series_match.group(1).upper()}"
                )

        # Detect multiple units
        unit_indicators = {"emu", "memu", "dmu", "demu", "mmu", "medha"}
        if tokens & unit_indicators or any(u in all_text for u in unit_indicators):
            metadata.is_unit = True
            if any(u in all_text for u in ["emu", "memu", "mmu"]):
                metadata.traction = TractionType.ELECTRIC
            elif any(u in all_text for u in ["dmu", "demu"]):
                metadata.traction = TractionType.DIESEL

        # Engine family detection
        family_indicators = {
            "alco": "ALCO",
            "emd": "EMD",
            "ge": "GE",
            "siemens": "Siemens",
            "alstom": "Alstom",
        }
        for token, family in family_indicators.items():
            if token in tokens or token in all_text:
                metadata.engine_family = family
                break

    def _extract_wagon_metadata(
        self, metadata: AssetMetadata, tokens: Set[str], text: str
    ):
        """Extract wagon-specific metadata."""
        if metadata.kind != AssetKind.WAGON:
            return

        # Coach type detection with priority
        folder_norm = metadata.folder.lower().replace("_", " ")
        name_norm = metadata.name.lower().replace("_", " ")
        all_text = f"{folder_norm} {name_norm} {text}".lower()
        coach_priority = [
            "pc",
            "slr",
            "eog",
            "1a",
            "2a",
            "3a",
            "3e",
            "accc",
            "cc",
            "sl",
            "gs",
        ]
        for coach_type in coach_priority:
            if coach_type in tokens or coach_type in all_text:
                metadata.coach_type = coach_type.upper()
                break

        # Enhanced pattern matching for coach types
        coach_patterns = {
            # Specific AC classes (HIGHEST PRIORITY - check these first)
            r'(?<![A-Za-z0-9])1a(?![A-Za-z0-9])': '1A',
            r'(?<![A-Za-z0-9])2a(?![A-Za-z0-9])': '2A', 
            r'(?<![A-Za-z0-9])3a(?![A-Za-z0-9])': '3A',
            # AC3 pattern for combined AC+class notation
            r'(?<![A-Za-z0-9])ac3(?![A-Za-z0-9])': '3A',
            r'(?<![A-Za-z0-9])ac2(?![A-Za-z0-9])': '2A',
            r'(?<![A-Za-z0-9])ac1(?![A-Za-z0-9])': '1A',
            
            # Vande Bharat patterns
            r'(?i)vbcc\d*(?![A-Za-z0-9])': 'CC',
            r'(?i)vande[-_]?bharat[-_]?cc\d*(?![A-Za-z0-9])': 'CC',
            # Vande Bharat Executive Chair Car patterns
            r'(?i)vbexcc\d*(?![A-Za-z0-9])': 'EC',
            r'(?i)vande[-_]?bharat[-_]?excc\d*(?![A-Za-z0-9])': 'EC',
            r'(?i)executive[-_]?chair[-_]?car(?![A-Za-z0-9])': 'EC',
            
            # AC Chair Car patterns (HIGH PRIORITY) - ENHANCED v2.2.3
            r'(?i)chaircar[-_]?ac|ac[-_]?chaircar': 'ACCC',
            r'(?i)chair[-_]?car[-_]?ac|ac[-_]?chair[-_]?car': 'ACCC',
            r'(?i)ac[-_]?cc|cc[-_]?ac': 'ACCC',
            
            # AC Tier patterns (should not conflict with specific classes above)
            r"(?i)ac[-_]?3[-_]?tier|3[-_]?tier[-_]?ac": "3A",
            r"(?i)ac[-_]?2[-_]?tier|2[-_]?tier[-_]?ac": "2A",
            r"(?i)ac[-_]?1[-_]?tier|1[-_]?tier[-_]?ac": "1A",
            
            # Generic AC chair patterns (lower priority)
            r"(?i)ac[-_]?chair|chair[-_]?ac": "ACCC",
            
            # Service coaches
            r"(?i)pantry[-_]?car|pantry": "PC",
            r"(?i)guard|luggage[-_]?van": "SLR",
            r"(?i)generator|power[-_]?car": "EOG",
            r"(?i)slr[-_]?": "SLR",
            
            # Additional coach types
            r"(?i)gs|general[-_]?second": "GS",
            r"(?i)slp|second[-_]?class[-_]?luggage": "SL",
            r"(?i)sl|second[-_]?class|sleeper": "SL",
            r"(?i)cc|chair[-_]?car": "CC",
            r"(?i)fc|first[-_]?class": "FC",
            r"(?i)sc|second[-_]?class": "SC",
        }

        if not metadata.coach_type:
            for pattern, coach_type in coach_patterns.items():
                if re.search(pattern, all_text):
                    metadata.coach_type = coach_type
                    break

        # Freight type detection - FIXED: prioritize longer matches to handle BOBYN vs BOBY
        # detect freight type with deterministic priority
        priority = ["hcpv", "hpcv", "parcel", "mail"]
        for key in priority:
            if key in all_text:
                metadata.freight_type = key.upper()
                break

        # FIXED: fallback with longest-match priority to handle BOBYN vs BOBY correctly
        if not metadata.freight_type:
            # Sort freight types by length (longest first) to prioritize BOBYN over BOBY
            sorted_freight_types = sorted(self.classifier.FREIGHT_TYPES, key=len, reverse=True)
            for ft in sorted_freight_types:
                if ft in all_text:
                    metadata.freight_type = ft.upper()
                    break

        # Carbody detection
        carbody_indicators = {"lhb", "icf", "integral", "conventional"}
        for token in tokens:
            if token in carbody_indicators:
                metadata.carbody = token.upper()
                break
        if not metadata.carbody:
            for cb in self.classifier.CARBODY_TYPES:
                if cb in all_text:
                    metadata.carbody = cb.upper()
                    break

        # Special set detection
        set_indicators = {
            "utk",
            "utkrisht",
            "humsafar",
            "tejas",
            "vande",
            "vandebharat",
            "rajdhani",
            "shatabdi",
            "duronto",
            "garibrath",
        }
        for token in tokens:
            if token in set_indicators:
                metadata.set_type = token.upper()
                break
        if not metadata.set_type:
            for st in self.classifier.SPECIAL_SETS:
                if st in all_text:
                    metadata.set_type = st.upper()
                    break

    def _extract_geographic_metadata(self, metadata: AssetMetadata, tokens: Set[str]):
        """Extract geographic metadata (regions, depots)."""
        # Zone codes
        zones = {
            "sr": "SR",
            "nr": "NR",
            "er": "ER",
            "wr": "WR",
            "cr": "CR",
            "scr": "SCR",
            "ecr": "ECR",
            "ncr": "NCR",
            "swr": "SWR",
            "nfr": "NFR",
            "nwr": "NWR",
            "ser": "SER",
            "secr": "SECR",
            "wcr": "WCR",
            "ecor": "ECOR",
            "ner": "NER",
        }

        # Major depot codes
        depots = {
            "mtp": "MTP",
            "bza": "BZA",
            "mas": "MAS",
            "ndls": "NDLS",
            "lko": "LKO",
            "mdg": "MDG",
            "kol": "KOL",
            "mum": "MUM",
            "pune": "PUNE",
            "gzb": "GZB",
            "ald": "ALD",
            "bbs": "BBS",
            "ghy": "GHY",
            "vskp": "VSKP",
            "kyn": "KYN",
            "trd": "TRD",
        }

        for token in tokens:
            if token in zones:
                metadata.region = zones[token]
            elif token in depots:
                metadata.depot = depots[token]

    def _extract_technical_metadata(self, metadata: AssetMetadata, tokens: Set[str]):
        """Extract technical specifications."""
        # Gauge detection
        gauge_indicators = {
            "bg": "BG",
            "mg": "MG",
            "ng": "NG",
            "broad": "BG",
            "meter": "MG",
            "narrow": "NG",
        }

        # Manufacturer detection
        manufacturers = {
            "clw": "CLW",
            "dlw": "DLW",
            "icf": "ICF",
            "rcf": "RCF",
            "beml": "BEML",
            "alstom": "Alstom",
            "siemens": "Siemens",
            "medha": "Medha",
            "bhel": "BHEL",
        }

        for token in tokens:
            if token in gauge_indicators:
                metadata.tech_spec = gauge_indicators[token]
            elif token in manufacturers:
                metadata.manufacturer = manufacturers[token]


class AssetIndex:
    """Comprehensive asset indexing system with multiple lookup methods."""

    def __init__(self):
        self.assets: List[AssetRecord] = []
        self.by_name: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.by_folder: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.by_kind: Dict[AssetKind, List[AssetRecord]] = defaultdict(list)
        self.by_engine_class: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.by_coach_type: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.by_freight_type: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.by_traction: Dict[TractionType, List[AssetRecord]] = defaultdict(list)
        self.by_ir_composite: Dict[str, List[AssetRecord]] = defaultdict(list)
        self.key_token_index: Dict[str, List[AssetRecord]] = defaultdict(list)

        # Performance optimization indices
        self.lhb_assets: Dict[str, AssetRecord] = {}
        self.icf_assets: Dict[str, AssetRecord] = {}
        self.emu_assets: Dict[str, AssetRecord] = {}

        self._lock = threading.RLock()

    def add_asset(self, asset: AssetRecord):
        """Add asset to all relevant indices."""
        with self._lock:
            self.assets.append(asset)

            # Basic indices
            self.by_name[asset.name.lower()].append(asset)
            self.by_folder[asset.folder.lower()].append(asset)
            self.by_kind[asset.kind].append(asset)

            # Metadata-based indices
            meta = asset.metadata
            if meta.engine_class:
                self.by_engine_class[meta.engine_class.lower()].append(asset)
            if meta.coach_type:
                self.by_coach_type[meta.coach_type.lower()].append(asset)
            if meta.freight_type:
                self.by_freight_type[meta.freight_type.lower()].append(asset)
            if meta.traction != TractionType.UNKNOWN:
                self.by_traction[meta.traction].append(asset)

            # IR composite index
            if asset.ir_composite:
                self.by_ir_composite[asset.ir_composite].append(asset)

            # Token index
            for token in asset.key_tokens:
                self.key_token_index[token].append(asset)

            # Performance indices
            if meta.carbody == "LHB":
                self.lhb_assets[asset.name] = asset
            elif meta.carbody == "ICF":
                self.icf_assets[asset.name] = asset

            if meta.is_unit and meta.traction == TractionType.ELECTRIC:
                self.emu_assets[asset.name] = asset

    def get_candidates(
        self, target: AssetMetadata, strategy: str = "comprehensive"
    ) -> List[AssetRecord]:
        """Get candidate assets for matching."""
        candidates = set()

        if strategy == "exact":
            # Only exact name matches
            candidates.update(self.by_name.get(target.name.lower(), []))

        elif strategy == "kind":
            # All assets of same kind
            candidates.update(self.by_kind.get(target.kind, []))

        elif strategy == "targeted":
            # Targeted search based on metadata
            if target.engine_class:
                candidates.update(
                    self.by_engine_class.get(target.engine_class.lower(), [])
                )
            if target.coach_type:
                candidates.update(self.by_coach_type.get(target.coach_type.lower(), []))
            if target.freight_type:
                candidates.update(
                    self.by_freight_type.get(target.freight_type.lower(), [])
                )
            if target.traction != TractionType.UNKNOWN:
                candidates.update(self.by_traction.get(target.traction, []))

        else:  # comprehensive
            # Start with same kind
            candidates.update(self.by_kind.get(target.kind, []))

            # Add targeted results
            if target.engine_class:
                candidates.update(
                    self.by_engine_class.get(target.engine_class.lower(), [])
                )
            if target.coach_type:
                candidates.update(self.by_coach_type.get(target.coach_type.lower(), []))
            if target.freight_type:
                candidates.update(
                    self.by_freight_type.get(target.freight_type.lower(), [])
                )

        # Filter by kind
        candidates = {c for c in candidates if c.kind == target.kind}

        return list(candidates)

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive index statistics."""
        return {
            "total_assets": len(self.assets),
            "engines": len(self.by_kind[AssetKind.ENGINE]),
            "wagons": len(self.by_kind[AssetKind.WAGON]),
            "folders": len(self.by_folder),
            "engine_classes": len(self.by_engine_class),
            "coach_types": len(self.by_coach_type),
            "freight_types": len(self.by_freight_type),
            "ir_composites": len(self.by_ir_composite),
            "lhb_assets": len(self.lhb_assets),
            "icf_assets": len(self.icf_assets),
            "emu_assets": len(self.emu_assets),
        }

    def __getstate__(self):
        """Support for pickling - exclude non-picklable objects."""
        state = self.__dict__.copy()
        # Remove threading objects that can't be pickled
        state['_lock'] = None
        return state

    def __setstate__(self, state):
        """Support for unpickling - restore state."""
        self.__dict__.update(state)
        # Recreate threading objects in worker processes
        if self._lock is None:
            self._lock = threading.RLock()


# ENHANCED v2.2.5: Policy implementation helper functions with freight analysis improvements
def detect_role_from_name(name: str) -> str:
    """Detect role (Engine/Wagon) from name patterns - ENHANCED v2.2.5 WITH VANDE BHARAT DETECTION."""
    name_lower = name.lower()

    # CRITICAL: Maintenance equipment detection FIRST
    maintenance_indicators = [
        "plasser",
        "tamper",
        "ballast_cleaner",
        "rail_grinder",
        "maintenance",
        "track_machine",
        "crane",
        "breakdown",
    ]
    if any(indicator in name_lower for indicator in maintenance_indicators):
        return "Engine"  # Maintenance equipment treated as engine for consist purposes

    # ENHANCED v2.2.5: Vande Bharat detection (CRITICAL FIX)
    vande_bharat_patterns = {
        # Power cars (should be engines)
        "vbpowercar": "Engine",
        "vb_powercar": "Engine",
        "powercar": "Engine",
        "power_car": "Engine",
        # Driver cars (should be engines)
        "vbdmc": "Engine",
        "vb_dmc": "Engine",
        "vbdc": "Engine",
        "vb_dc": "Engine",
        "drivingcar": "Engine",
        "driving_car": "Engine",
        # Coach cars (should be wagons)
        "vbcc": "Wagon",
        "vb_cc": "Wagon",
        "vbac": "Wagon",
        "vb_ac": "Wagon",
        "vbcoach": "Wagon",
        "vb_coach": "Wagon",
    }

    for pattern, role in vande_bharat_patterns.items():
        if pattern in name_lower:
            return role

    # Engine indicators
    engine_patterns = [
        r"\b(wap|wag|wdm|wdg|wdp|wds|wam|wcam|wcg|wcm)\d*\b",
        r"\b(emu|memu|dmu|mmu|demu)\b",
        r"\b(locomotive|engine|loco)\b",
    ]

    for pattern in engine_patterns:
        if re.search(pattern, name_lower):
            return "Engine"

    # Wagon indicators
    wagon_patterns = [
        r"\b(coach|wagon|car)\b",
        r"\b(boxn|bcn|tank|flat|container)\b",
        r"\b(1a|2a|3a|sl|gs|cc|accc|eog|pc|slr)\b",
    ]

    for pattern in wagon_patterns:
        if re.search(pattern, name_lower):
            return "Wagon"

    return ""


def enhance_wagon_matching_with_compatibility(
    pool: List[AssetRecord], wanted_class: str, wanted_name: str
) -> List[AssetRecord]:
    """Enhanced wagon matching with compatibility rules - ENHANCED v2.2.5 WITH FREIGHT ANALYSIS."""
    if not wanted_class:
        return pool

    compatible_pool = []

    # ENHANCED v2.2.5: Handle manufacturer prefix classes
    if wanted_class in ["BSAM", "ASMI", "CON"]:
        # For manufacturer series, be more permissive with base types
        manufacturer_compatible = {
            "BSAM": ["BOXN", "BOBYN", "BCNA", "TANK", "FLAT", "FREIGHT"],
            "ASMI": ["BCA", "BCB", "BCN", "BTP", "BLC", "CONTAINER"],
            "CON": ["CONTAINER", "FLAT", "BLC", "CONCOR"],
        }

        compatible_types = manufacturer_compatible.get(wanted_class, [wanted_class])

        for asset in pool:
            asset_class = detect_wagon_or_engine_class(asset.name, "Wagon" if asset.kind == AssetKind.WAGON else "Engine")
            if asset_class in compatible_types or asset_class == wanted_class:
                compatible_pool.append(asset)

        return compatible_pool

    # STRICT: Incompatible wagon types (these should NEVER match each other)
    incompatible_groups = {
        # Specialized wagons are incompatible with everything except themselves
        "COIL": ["BCNA", "BOXN", "TANK", "FLAT", "BLC", "CONTAINER", "FREIGHT"],
        "SLAB": ["BCNA", "BOXN", "TANK", "FLAT", "BLC", "CONTAINER", "FREIGHT"],
        "AUTO": ["BCNA", "BOXN", "TANK", "FLAT", "BLC", "CONTAINER", "FREIGHT"],
        "CEMENT": ["BCNA", "BOXN", "TANK", "FLAT", "BLC", "CONTAINER", "FREIGHT"],
        # BCNA (covered) incompatible with specialized types
        "BCNA": ["COIL", "SLAB", "AUTO", "CEMENT", "TIPPLER"],
        # BOXN (open) incompatible with specialized types
        "BOXN": ["COIL", "SLAB", "AUTO", "CEMENT", "TIPPLER"],
        # TANK wagons incompatible with specialized types
        "TANK": ["COIL", "SLAB", "AUTO", "CEMENT"],
    }

    # Define wagon compatibility groups (what CAN match) - ENHANCED v2.2.5
    wagon_compatibility = {
        # Specialized wagons (ONLY match themselves)
        "COIL": ["COIL"],
        "SLAB": ["SLAB"],
        "AUTO": ["AUTO"],
        "CEMENT": ["CEMENT"],
        "TIPPLER": ["TIPPLER"],
        # Container wagons group - ENHANCED
        "CONTAINER": ["CONTAINER", "FLAT", "BLC", "BLCA", "BLCB", "CONCOR", "CON"],
        "CON": ["CON", "CONTAINER", "FLAT", "BLC", "CONCOR"],
        # Covered wagons group (BCNA family only) - ENHANCED
        "BCNA": ["BCNA", "BCNE", "BCNH", "BCNL", "BCN", "BCCN"],
        "BCNE": ["BCNA", "BCNE", "BCNH", "BCNL", "BCN", "BCCN"],
        "BCN": ["BCNA", "BCNE", "BCNH", "BCNL", "BCN", "BCCN"],
        "BCCN": ["BCCN", "BCNA", "BCN", "BCBFG", "BCFC"],
        # Open wagons group (BOXN family only)
        "BOXN": ["BOXN", "BOXNR", "BOXNG", "BOXNHL", "BOXNM1", "BOXNM2", "BOSTH"],
        "BOXNR": ["BOXN", "BOXNR", "BOXNG", "BOXNHL", "BOXNM1", "BOXNM2", "BOSTH"],
        "BOSTH": ["BOXN", "BOXNR", "BOXNG", "BOXNHL", "BOXNM1", "BOXNM2", "BOSTH"],
        # Tank wagons group - ENHANCED v2.2.5
        "BTPN": ["BTPN", "BTAP", "BTCS", "BTFLN", "TANK", "BTI"],
        "BTFLN": ["BTPN", "BTAP", "BTCS", "BTFLN", "TANK", "BTI"],
        "TANK": ["BTPN", "BTAP", "BTCS", "BTFLN", "TANK", "BTI"],
        "BTI": ["BTPN", "BTAP", "BTCS", "BTFLN", "TANK", "BTI"],
        "MILKTANKER": ["MILKTANKER", "VVN", "TANK"],
        # Container/flat wagons group (extended) - ENHANCED
        "FLAT": ["FLAT", "BLC", "BLCA", "CONTAINER", "CONCOR", "BCA", "BCB"],
        "BLC": ["FLAT", "BLC", "BLCA", "CONTAINER", "CONCOR", "BCA", "BCB"],
        "BCA": ["BCA", "BCB", "BLC", "CONTAINER", "FLAT"],
        "BCB": ["BCA", "BCB", "BLC", "CONTAINER", "FLAT"],
        "CONCOR": ["FLAT", "BLC", "BLCA", "CONTAINER", "CONCOR"],
        # Parcel/mail wagons - STRICT: HCPV should prefer exact matches
        "HPCV": ["HPCV", "HCPV"],  # Only exact parcel van types
        "HCPV": ["HPCV", "HCPV"],  # Only exact parcel van types, exclude generic PARCEL
        "PARCEL": ["HPCV", "HCPV", "PARCEL"],  # PARCEL can accept all parcel types
        # Brake vans - ENHANCED v2.2.5 - BOBYN is freight wagon, not crew vehicle
        "BRD": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],  # brake vans
        "BRN": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],
        "BRNA": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],
        "BRW": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],
        "BRAKE": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],
        "BVZI": ["BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"],

        # Keep BOBYN strictly in freight group (open wagons)
        "BOBYN": ["BOBYN", "BOXN", "BOY", "BOST"],  # open freight family
    }

    compatible_classes = wagon_compatibility.get(wanted_class, [wanted_class])
    incompatible_classes = incompatible_groups.get(wanted_class, [])

    for asset in pool:
        asset_class = detect_wagon_or_engine_class(asset.name, "Wagon" if asset.kind == AssetKind.WAGON else "Engine")

        # STRICT: For BCCW, only match assets with class BCCW
        if wanted_class == "BCCW":
            if asset_class == "BCCW":
                compatible_pool.append(asset)
            continue

        # STRICT: Check for incompatible classes first
        if asset_class and asset_class in incompatible_classes:
            continue  # Skip incompatible wagon types

        # If asset has a detectable class, check compatibility
        if asset_class:
            # PRIORITY: Exact class match gets highest preference
            if _ci_eq(asset_class, wanted_class):
                # Exact match - always include with highest priority
                compatible_pool.append(asset)
                logging.debug(f"EXACT_CLASS_MATCH: {asset.folder}/{asset.name} included (exact class: {asset_class})")
            elif asset_class in compatible_classes:
                # Compatible but not exact - include with lower priority
                compatible_pool.append(asset)
                logging.debug(f"COMPATIBLE_CLASS_MATCH: {asset.folder}/{asset.name} included (compatible class: {asset_class} for wanted: {wanted_class})")
            # Note: Incompatible classes are filtered out in the main loop
        else:
            # If no detectable class, apply strict name-based filtering
            asset_name_lower = asset.name.lower()

            # CRITICAL: Container context checking
            if wanted_class == "CONTAINER":
                # For containers, require container-related terms
                if any(
                    term in asset_name_lower
                    for term in ["con_", "container", "flat", "blc"]
                ):
                    compatible_pool.append(asset)
                continue

            # STRICT: Specialized wagon filtering
            if wanted_class in ["COIL", "SLAB", "AUTO", "CEMENT", "TIPPLER"]:
                # For specialized wagons, require EXACT type match in name
                if wanted_class.lower() in asset_name_lower:
                    # Additional check: ensure it's not a different specialized type
                    other_specialized = ["coil", "slab", "auto", "cement", "tippler"]
                    other_specialized.remove(wanted_class.lower())
                    if not any(
                        other in asset_name_lower for other in other_specialized
                    ):
                        compatible_pool.append(asset)
                continue

            # STRICT: BCNA wagon filtering
            if wanted_class in ["BCNA", "BCN"]:
                # Reject if contains specialized wagon terms
                if any(
                    term in asset_name_lower
                    for term in ["coil", "slab", "auto", "cement", "con_"]
                ):
                    continue
                # Accept if contains BCNA-related terms
                if any(
                    term in asset_name_lower
                    for term in ["bcna", "bcne", "bcnh", "covered"]
                ):
                    compatible_pool.append(asset)
                continue

            # STRICT: BOXN wagon filtering
            if wanted_class == "BOXN":
                # Reject if contains specialized wagon terms or covered wagon terms
                if any(
                    term in asset_name_lower
                    for term in [
                        "coil",
                        "slab",
                        "auto",
                        "cement",
                        "covered",
                        "bcna",
                        "con_",
                    ]
                ):
                    continue
                # Accept if contains BOXN-related terms
                if any(term in asset_name_lower for term in ["boxn", "open"]):
                    compatible_pool.append(asset)
                continue

            # CRITICAL: Prevent container assets from matching non-container classes
            if any(
                term in asset_name_lower for term in ["con_", "container"]
            ) and wanted_class not in ["CONTAINER", "FLAT", "BLC"]:
                continue

            # CRITICAL: Prevent specialized wagon names from matching standard classes
            if any(
                term in asset_name_lower for term in ["coil", "slab", "auto", "cement"]
            ) and wanted_class not in ["COIL", "SLAB", "AUTO", "CEMENT"]:
                continue

            # CRITICAL: Prevent VVN/MILKTANKER wagons from matching container/shipping assets
            if wanted_class in ["VVN", "MILKTANKER", "TANK"]:
                # Reject shipping/container company names and container-related terms
                if any(
                    term in asset_name_lower
                    for term in ["maersk", "seal", "con_", "container", "ship", "marine", "navy"]
                ):
                    continue
                # Only allow if asset name contains milk/tank related terms
                if not any(
                    term in asset_name_lower
                    for term in ["milk", "tanker", "tank", "vvn", "btpn", "btfln", "bti"]
                ):
                    continue

            # CRITICAL: STRICT BOBYN filtering - only allow brake van freight wagons, exclude cabooses
            if wanted_class == "BOBYN":
                # For BOBYN (brake van freight wagons), be very strict - only allow if name contains brake van terms
                brake_van_terms = ["bobyn", "brn", "brd", "brna"]  # Exclude "brake" to avoid cabooses
                if not any(term in asset_name_lower for term in brake_van_terms):
                    continue
                # Additional check: ensure it's not a crew vehicle (caboose/guard van)
                crew_vehicle_terms = ["caboose", "guard", "crew", "van", "control", "accommodation"]
                if any(term in asset_name_lower for term in crew_vehicle_terms):
                    continue
                # Additional check: ensure it's not a different wagon type
                incompatible_terms = ["tank", "boxn", "bcna", "container", "flat", "coil", "slab", "auto", "cement"]
                if any(term in asset_name_lower for term in incompatible_terms):
                    continue

            # If no specific restrictions apply, allow the match
            compatible_pool.append(asset)

    return compatible_pool


def detect_family_from_name(name: str, role: str = "Engine") -> str:
    """Detect family from name.
    
    For engines: returns locomotive families (WAP/WAG/WDM/EMU/MEMU)
    For wagons: returns coach families (AC/RAJDHANI/SLEEPER/GENERAL)
    """
    name_lower = name.lower()

    # For wagons, detect coach families
    if role == "Wagon":
        # Coach families from store files
        if "rajdhani" in name_lower:
            return "RAJDHANI"
        elif "pantry" in name_lower or "pc" in name_lower:
            return "PANTRY"
        elif "sleeper" in name_lower or "sl" in name_lower:
            return "SLEEPER"
        elif "chair" in name_lower or "accc" in name_lower:
            return "CHAIR"
        elif "ac" in name_lower and ("1a" in name_lower or "2a" in name_lower or "3a" in name_lower):
            return "AC"
        elif "general" in name_lower or "gs" in name_lower:
            return "GENERAL"
        elif "power" in name_lower or "eog" in name_lower:
            return "POWER"
        return ""

    # For engines, use locomotive family detection
    # ENHANCED v2.2.4: Better EMU/MEMU distinction (order matters!)
    if "memu" in name_lower:
        return "MEMU"  # MEMU is more specific than EMU
    elif "emu" in name_lower:
        return "EMU"  # Generic EMU
    elif "demu" in name_lower or "dmu" in name_lower:
        return "DMU"
    elif "mmu" in name_lower:
        return "MMU"

    # Locomotive families - use token-based detection for short indicators
    # PRIORITIZE LOCOMOTIVE FAMILIES over generic indicators like "ai"
    locomotive_families_priority = {
        "wap": "WAP",
        "wag": "WAG",
        "wdm": "WDM",
        "wdg": "WDG",
        "wdp": "WDP",
        "wds": "WDS",
        "wam": "WAM",
        "wcam": "WCAM",
        "wcg": "WCG",
        "wcm": "WCM",
    }
    
    # Check priority locomotive families first
    for family_key, family_name in locomotive_families_priority.items():
        if len(family_key) <= 3:  # Short indicators need careful matching
            import re
            tokens = re.split(r'[\s_/-]', name_lower)
            # Check if family_key is a substring of any token with specific conditions
            for token in tokens:
                if family_key in token:
                    if token == family_key:
                        # Exact match
                        return family_name
                    elif token.startswith(family_key) and token[len(family_key):].isdigit():
                        # Family key followed by digits (e.g., "wdg4", "wap7")
                        return family_name
                    # Skip other cases to avoid false positives like "wag" in "wagons"
        else:  # Longer indicators use substring match
            if family_key in name_lower:
                return family_name
    
    # Generic families (lower priority)
    generic_families = {
        "ai": "AI",  # AI systems (horns, etc.)
        "acela": "ACELA",  # ACELA trains
    }
    
    for family_key, family_name in generic_families.items():
        if len(family_key) <= 3:  # Short indicators need careful matching
            import re
            tokens = re.split(r'[\s_/-]', name_lower)
            # Check if family_key is a substring of any token with specific conditions
            for token in tokens:
                if family_key in token:
                    if token == family_key:
                        # Exact match
                        return family_name
                    elif token.startswith(family_key) and token[len(family_key):].isdigit():
                        # Family key followed by digits (e.g., "wdg4", "wap7")
                        return family_name
                    # Skip other cases to avoid false positives like "wag" in "wagons"
        else:  # Longer indicators use substring match
            if family_key in name_lower:
                return family_name

    return ""


def detect_subtype_from_name(name: str) -> str:
    """Detect subtype (Passenger/Freight/Maintenance) from name - ENHANCED v2.2.5 WITH FREIGHT ANALYSIS."""
    name_lower = name.lower()

    # ENHANCED v2.2.5: Handle manufacturer prefixes FIRST
    clean_name = name_lower
    for prefix in ["bsam_", "asmi", "con_"]:
        if clean_name.startswith(prefix):
            clean_name = clean_name[len(prefix) :]
            break

    # CRITICAL: Container detection FIRST (before passenger indicators to avoid false classification)
    container_indicators = [
        "con_",
        "container",
        "concor",
        "intermodal",
        "flat",
        "flatcar",
        "blc",
        "blca",
        "blcb",  # Container-carrying wagons
    ]
    if any(
        indicator in name_lower or indicator in clean_name
        for indicator in container_indicators
    ):
        return "Freight"  # Containers are freight

    # ENHANCED: Check for passenger indicators AFTER container detection
    passenger_indicators = [
        '1a', '2a', '3a', 'ac', 'sl', 'gen', 'chair', 'sleeper', 'pantry', 'eog', 'pc', 'slr', 'fc', 'sc', 'gn', 'cc', 'accc'
    ]
    if any(indicator in name_lower for indicator in passenger_indicators):
        return "Passenger"

    # FIXED: Add explicit freight locomotive detection FIRST (higher priority)
    freight_locomotive_indicators = [
        "wdg",
        "wag",  # Freight locomotive families
        "wdg3",
        "wdg3a",
        "wdg4",
        "wdg4d",
        "wdg4g",  # Diesel freight
        "wag5",
        "wag7",
        "wag9",
        "wag12",  # Electric freight
        "freight",
        "goods",
        "cargo",  # Generic freight terms
    ]
    # Check for freight locomotives with word boundaries to avoid false positives
    for indicator in freight_locomotive_indicators:
        if len(indicator) <= 3:
            # For short indicators, check with word boundaries
            import re
            if re.search(r'\b' + re.escape(indicator) + r'\b', name_lower):
                return "Freight"
        else:
            # For longer indicators, use substring match
            if indicator in name_lower:
                return "Freight"

    # ENHANCED v2.2.6: Caboose/Brake Van Detection (HIGHEST PRIORITY for wagons)
    # Check for caboose indicators BEFORE freight wagon detection
    caboose_indicators = [
        # Direct caboose terms
        "caboose",
        "brake_van",
        "guard_van",
        "crew_car",
        # IR-specific patterns
        "ir_caboose",
        "ir_brake",
        "ir_guard",
        # Separator-based patterns
        "_caboose",
        "/caboose",
        "-caboose",
        "caboose_",
        "caboose/",
        "caboose-",
        # Brake van variations
        "brakevan",
        "guardvan",
        "crewvan",
        # Special designations
        "bvzi",  # Modern brake van
        "brd",   # Brake van D
        "brn",   # Brake van N
        "brna",  # Brake van NA
        "brw",   # Brake van W
    ]

    # ENHANCED v2.2.9: Compound name handling for caboose detection
    # Check if this is a compound name with multiple type indicators (wagon or locomotive)
    wagon_type_indicators = [
        "hcpv", "hpcv",  # Parcel vans
        "bcna", "bcne", "bcnh", "bcnl", "bccnr",  # Covered wagons
        "boxn", "boxnha", "boxnhl",  # Box wagons
        "tank", "tanker",  # Tank wagons
        "flat", "flatbed",  # Flat wagons
        "cement", "coil", "container",  # Specialized freight
    ]
    
    locomotive_family_indicators = [
        "wap", "wag", "wam", "wcam", "wcg", "wcm",  # Electric locomotives
        "wdg", "wdm", "wdp", "wds",  # Diesel locomotives
        "emu", "memu", "mmu", "dmu", "demu",  # EMU/DMU
        "brw", "mgs", "ajj", "tkd", "bza", "bpl", "et",  # Railway division codes that might be locomotive series
    ]
    
    has_wagon_indicators = False
    for indicator in wagon_type_indicators:
        if indicator in name_lower or indicator in clean_name:
            has_wagon_indicators = True
            break
    
    has_locomotive_indicators = False
    for indicator in locomotive_family_indicators:
        if indicator in name_lower or indicator in clean_name:
            has_locomotive_indicators = True
            break
    
    # Additional check: if name looks like a locomotive (has numbers, multiple parts), be conservative
    looks_like_locomotive = (
        any(char.isdigit() for char in name) and  # Has numbers
        ('_' in name or len(name.split()) > 1) and  # Has separators or multiple parts
        len(name) > 6  # Reasonably long name
    )
    
    # If this is a compound name with wagon OR locomotive indicators, OR looks like a locomotive, be more careful about caboose detection
    if has_wagon_indicators or has_locomotive_indicators or looks_like_locomotive:
        # Only classify as caboose if it's clearly a standalone brake van
        # Check if the primary class is actually a brake van type
        from consistEditor import detect_wagon_or_engine_class
        primary_class = detect_wagon_or_engine_class(name, "Wagon") or detect_wagon_or_engine_class(clean_name, "Wagon")
        brake_van_classes = {"BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"}
        
        # If the primary class is NOT a brake van type, don't classify as caboose
        # even if brake van indicators are present (they might be prefixes or part of compound names)
        if primary_class and primary_class not in brake_van_classes:
            logging.debug(f"SUBTYPE_DETECTION: Compound/locomotive name '{name}' with class '{primary_class}' - skipping caboose detection despite brake indicators")
        else:
            # For names that look like locomotives but don't have clear class detection,
            # be more conservative about caboose classification
            if looks_like_locomotive and not primary_class:
                logging.debug(f"SUBTYPE_DETECTION: Locomotive-like name '{name}' without clear class - skipping caboose detection")
            else:
                # Check for caboose indicators with high priority
                for indicator in caboose_indicators:
                    if indicator in name_lower or indicator in clean_name:
                        logging.debug(f"SUBTYPE_DETECTION: Detected caboose indicator '{indicator}' in '{name}' -> Caboose")
                        return "Caboose"  # Caboose wagons are distinct from service vehicles
    else:
        # For non-compound names, use the original logic
        # Check for caboose indicators with high priority
        for indicator in caboose_indicators:
            if indicator in name_lower or indicator in clean_name:
                logging.debug(f"SUBTYPE_DETECTION: Detected caboose indicator '{indicator}' in '{name}' -> Caboose")
                return "Caboose"  # Caboose wagons are distinct from service vehicles

    # ENHANCED v2.2.6: Brake Van Class Detection
    # If the wagon class is a known brake van type, classify as Caboose
    from consistEditor import detect_wagon_or_engine_class
    wagon_class = detect_wagon_or_engine_class(name, "Wagon")
    brake_van_classes = {"BRD", "BRN", "BRNA", "BRW", "BRAKE", "BVZI"}
    if wagon_class and wagon_class in brake_van_classes:
        logging.debug(f"SUBTYPE_DETECTION: Detected brake van class '{wagon_class}' in '{name}' -> Caboose")
        return "Caboose"  # Brake van classes are caboose vehicles

    # ENHANCED v2.2.5: Enhanced freight wagon detection with manufacturer prefixes
    freight_wagon_indicators = [
        # Covered wagons (BCNA family) - EXPLICIT
        "bcna",
        "bcne",
        "bcnh",
        "bcnl",
        "bccnr",
        "bccw",
        "bcn",
        "bccn",
        "bcbfg",
        "bcfc",
        # Open wagons (BOXN family) - EXPLICIT
        "boxn",
        "boxnr",
        "boxng",
        "boxnhl",
        "boxnm1",
        "boxnm2",
        "bosth",  # BOSTH family (Open wagons for coal/ballast)
        # Traditional freight wagons
        "tank",
        "freight",
        "goods",
        "cargo",
        "bobo",
        "bobr",
        "boby",
        "bobyn",
        "btpn",
        "btap",
        "btfln",
        "bti",
        "flat",
        "flatcar",
        "container",
        "concor",
        "blc",
        "brd",
        "brn",
        "brna",
        "brake",
        "bca",
        "bcb",
        # Parcel and mail (these are freight category)
        "hpcv",
        "hcpv",
        "parcel",
        "mail",
        "post",
        "baggage",
        "luggage_van",  # but not SLR which is passenger service
        # Specialized freight - ENHANCED v2.2.5
        "coil",
        "slab",
        "billet",
        "pipe",
        "automobile",
        "timber",
        "cement",
        "milktanker",
        # Manufacturer series - ENHANCED v2.2.5
        "bsam",
        "asmi",
        "apl",
    ]
    if any(
        indicator in name_lower or indicator in clean_name
        for indicator in freight_wagon_indicators
    ):
        return "Freight"

    # Passenger locomotive detection
    passenger_locomotive_indicators = [
        "wap",
        "wdp",  # Passenger locomotive families
        "wap1",
        "wap4",
        "wap5",
        "wap7",  # Electric passenger
        "wdp1",
        "wdp3",
        "wdp4",
        "wdp4b",
        "wdp4d",
        "wdp4e",  # Diesel passenger
    ]
    if any(indicator in name_lower for indicator in passenger_locomotive_indicators):
        return "Passenger"

    # Passenger coach indicators (expanded)
    passenger_coach_indicators = [
        "1a",
        "2a",
        "3a",
        "1ac",
        "2ac",
        "3ac",
        "ac1",
        "ac2",
        "ac3",
        "sl",
        "slp",
        "sleeper",
        "sleeping",
        "gs",
        "gen",
        "general",
        "gencar",
        "unreserved",
        "cc",
        "chair",
        "chaircar",
        "accc",
        "ac_chair",
        "passenger",
        "coach",
        "utk",
        "utkrisht",
        "humsafar",
        "tejas",
        "rajdhani",
        "shatabdi",
        "duronto",
        "vande",
        "vandebharat",
    ]
    # CRITICAL: Only detect as passenger if NOT a container or freight context
    if not any(
        con in name_lower or con in clean_name
        for con in ["con_", "container", "freight", "bsam", "asmi"]
    ):
        # Use more specific detection for short indicators to avoid false positives
        passenger_detected = False
        for indicator in passenger_coach_indicators:
            if len(indicator) <= 2:
                # For short indicators like "gs", check if they appear as separate tokens
                # Split on various separators and check for exact matches
                import re
                tokens = re.split(r'[\s_/-]', name_lower)
                if indicator in tokens:
                    # Additional check: make sure it's not part of a longer token
                    for token in tokens:
                        if token == indicator:
                            passenger_detected = True
                            break
                    if passenger_detected:
                        break
            else:
                # For longer indicators, use substring match
                if indicator in name_lower or indicator in clean_name:
                    passenger_detected = True
                    break
        if passenger_detected:
            return "Passenger"

    # Service/Maintenance indicators (lower priority than locomotive detection)
    service_indicators = [
        "eog",
        "generator",
        "power",
        "powercar",
        "maintenance",
        "service",
        "inspection",
        "pc",
        "pantry",
        "pantrycar",
        "catering",
        # Only detect AI/HORN as service if not part of locomotive name
        "ai_system",  # More specific than just "ai"
        "ai_horn",    # More specific than just "ai" + "horn"
        "horn_system", # More specific than just "horn"
    ]
    
    # Check service indicators with more specificity to avoid conflicts with locomotive names
    for indicator in service_indicators:
        if indicator in name_lower or indicator in clean_name:
            return "Service"

    # SLR is special - it's passenger service (guard's van on passenger trains)
    if "slr" in name_lower and not any(
        con in name_lower or con in clean_name for con in ["con_", "container"]
    ):
        return "Passenger"

    return ""


def detect_build_from_name_or_folder(name: str, folder: str) -> str:
    """Detect build type (LHB/ICF/UTK) from name or folder - ENHANCED v2.2.5 WITH BLUE REMOVED."""
    combined = f"{name} {folder}".lower()

    # Priority-ordered build indicators (more specific first)
    # CHANGE: Removed 'blue' and 'blu' as requested
    build_indicators = [
        # Special liveries/sets (highest priority) - ENHANCED v2.2.3: Added ANTYODAYA and DURONTO
        ("antyodaya", "ANTYODAYA"),
        ("antodaya", "ANTYODAYA"),  # Common misspelling
        ("duronto", "DURONTO"),
        ("utkrisht", "UTK"),
        ("utk", "UTK"),
        ("ukt", "UTK"),  # UKT is common misspelling
        ("tejas", "TEJAS"),
        ("humsafar", "HUMSAFAR"),
        ("vande", "VANDE"),
        ("vandebharat", "VANDE"),
        ("garibrath", "GARIBRATH"),
        ("garib_rath", "GARIBRATH"),
        ("samparkkranti", "SAMPARKKRANTI"),
        ("doubledecker", "DOUBLEDECKER"),
        ("_ai", "AI"),  # AI systems (more specific to avoid false positives)
        ("ai_", "AI"),  # AI systems
        (" ai ", "AI"),  # AI systems with word boundaries
        ("artificial_intelligence", "AI"),  # Full AI term
        ("ai_system", "AI"),  # AI system designation
        (" ai-", "AI"),  # AI with dash
        ("-ai ", "AI"),  # Dash AI
        ("ai-horn", "AI"),  # AI horn combination
        ("ai_horn", "AI"),  # AI horn with underscore
        ("aihorn", "AI"),  # AI horn without separator
        ("horn_ai", "AI"),  # Horn AI combination
        ("horn-ai", "AI"),  # Horn AI with dash
        ("ai system", "AI"),  # AI system with space
        ("ai-system", "AI"),  # AI system with dash
       
        # Build types (lower priority)
        ("lhb", "LHB"),
        ("linke_hofmann", "LHB"),
        ("modern", "LHB"),
        ("icf", "ICF"),
        ("integral", "ICF"),
        ("conventional", "ICF"),
        ("alco", "ALCO"),
        ("other", "OTHER"),
    ]

    # Check in priority order - return first match
    for indicator, build_type in build_indicators:
        # Special handling for AI to avoid false positives within words
        if indicator in ["_ai", "ai_", " ai ", "artificial_intelligence", "ai_system", " ai-", "-ai "]:
            # Use word boundaries for AI detection to avoid matching within words like "HYUNDAI"
            import re
            pattern = r'\b' + re.escape(indicator.strip()) + r'\b'
            if re.search(pattern, combined, re.IGNORECASE):
                return build_type
        elif indicator in combined:
            return build_type

    return ""


def detect_from_folder(folder: str) -> Tuple[str, str, str, str]:
    """Detect family, subtype, class, build from folder name."""
    if not folder:
        return "", "", "", ""

    folder_lower = folder.lower()

    family = detect_family_from_name(folder_lower, "Engine")  # Default to Engine for folder-only detection
    subtype = detect_subtype_from_name(folder_lower)
    klass = detect_wagon_or_engine_class(folder_lower)
    build = detect_build_from_name_or_folder("", folder_lower)

    return family, subtype, klass, build


def name_equal(name1: str, name2: str) -> bool:
    """Check if two names are equal (case-insensitive)."""
    return name1.lower().strip() == name2.lower().strip()


def get_traction_type_from_family(family: str) -> TractionType:
    """Get traction type from locomotive family - ENHANCED v2.2.3."""
    if not family:
        return TractionType.UNKNOWN

    family_lower = family.lower()

    # Electric locomotive families - ENHANCED v2.2.3
    electric_families = {
        "wap",
        "wag",
        "wam",
        "wcam",
        "wcg",
        "wcm",
        "emu",
        "memu",
        "mmu",
    }
    if family_lower in electric_families:
        return TractionType.ELECTRIC

    # Diesel locomotive families - ENHANCED v2.2.3
    diesel_families = {"wdm", "wdg", "wdp", "wds", "dmu", "demu"}
    if family_lower in diesel_families:
        return TractionType.DIESEL

    return TractionType.UNKNOWN


def _ci_eq(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").casefold() == (b or "").casefold()


def apply_strict_attribute_filter(
    pool: List[AssetRecord], family: str, subtype: str, klass: str, build: str
) -> List[AssetRecord]:
    """
    NEW: Apply strict attribute filtering - only assets with EXACT matches for all four attributes.
    This replaces the more lenient filtering used in the original version.
    """
    if not any([family, subtype, klass, build]):
        # If no attributes detected, return empty pool (will lead to UNRESOLVED)
        return []

    filtered = []

    for asset in pool:
        # Extract asset attributes (use cached values where available)
        asset_role = "Engine" if asset.kind == AssetKind.ENGINE else "Wagon"
        asset_family = detect_family_from_name(asset.name, asset_role) or detect_family_from_name(
            asset.folder, asset_role
        )
        asset_subtype = detect_subtype_from_name(
            asset.name
        ) or detect_subtype_from_name(asset.folder)
        # PERFORMANCE OPTIMIZATION: Use cached class detection
        asset_class = asset.cached_class
        asset_build = detect_build_from_name_or_folder(asset.name, asset.folder)

        # Initialize matches for this asset
        matches = True

        # ENHANCED v2.2.9: More flexible attribute matching for wagons
        # For wagons, be more lenient with Family attribute since many wagons don't have locomotive-style family codes
        is_wagon = asset.kind == AssetKind.WAGON
        
        if is_wagon:
            # For wagons: strict matching - require EXACT matches for all detected attributes
            # If target has an attribute, asset must have the same attribute value
            class_conflict = klass and not (_ci_eq(asset_class, klass) or (
                # Use compatibility for wagon class matching as fallback
                enhance_wagon_matching_with_compatibility([asset], klass, asset.name) if klass else False
            ))
            family_conflict = family and not _ci_eq(asset_family, family)
            subtype_conflict = subtype and not _ci_eq(asset_subtype, subtype)
            build_conflict = build and not _ci_eq(asset_build, build)
            
            # Reject if any required attribute doesn't match
            if class_conflict or family_conflict or subtype_conflict or build_conflict:
                matches = False
            else:
                # All required attributes match (or weren't specified)
                matches = True
        else:
            # For engines: strict matching - require EXACT matches for all detected attributes
            # If target has an attribute, asset must have the same attribute value
            class_conflict = klass and not _ci_eq(asset_class, klass)
            family_conflict = family and not _ci_eq(asset_family, family)
            subtype_conflict = subtype and not _ci_eq(asset_subtype, subtype)
            build_conflict = build and not _ci_eq(asset_build, build)
            
            # Reject if any required attribute doesn't match
            if class_conflict or family_conflict or subtype_conflict or build_conflict:
                matches = False
            else:
                # All required attributes match (or weren't specified)
                matches = True

        if matches:
            filtered.append(asset)

    return filtered


def find_digit_near_matches(pool: List[AssetRecord], wanted_name: str) -> List[AssetRecord]:
    """Find assets with similar digit patterns for digit-near matching."""
    import re

    # Extract digit patterns from wanted name
    wanted_digits = re.findall(r'\d+', wanted_name)
    if not wanted_digits:
        return []

    matches = []
    for asset in pool:
        asset_digits = re.findall(r'\d+', asset.name)
        if not asset_digits:
            continue

        # Check if digit patterns are similar (same count and close values)
        if len(wanted_digits) == len(asset_digits):
            digit_similarity = True
            for wanted_digit, asset_digit in zip(wanted_digits, asset_digits):
                try:
                    wanted_num = int(wanted_digit)
                    asset_num = int(asset_digit)
                    # Allow digit difference of max 2 (e.g., 28052 matches 28059)
                    if abs(wanted_num - asset_num) > 2:
                        digit_similarity = False
                        break
                except ValueError:
                    digit_similarity = False
                    break

            if digit_similarity:
                matches.append(asset)

    return matches


def find_wildcard_matches(pool: List[AssetRecord], wanted_name: str) -> List[AssetRecord]:
    """Find assets using wildcard pattern matching."""
    import re
    import fnmatch

    matches = []
    # Create wildcard patterns from the wanted name
    patterns = [
        wanted_name.replace('_', '*'),  # Replace underscores with wildcards
        re.sub(r'\d+', '*', wanted_name),  # Replace digits with wildcards
        wanted_name.replace('_', '?'),  # Replace underscores with single char wildcards
    ]

    for asset in pool:
        for pattern in patterns:
            if fnmatch.fnmatch(asset.name.lower(), pattern.lower()):
                matches.append(asset)
                break  # Only add once per asset

    return matches


def find_semantic_matches(pool: List[AssetRecord], wanted_name: str) -> List[AssetRecord]:
    """Find assets with high semantic similarity using fuzzy matching."""
    if not FUZZYWUZZY_AVAILABLE:
        return []  # Return empty if fuzzywuzzy not available

    matches = []
    threshold = 75  # Minimum similarity score

    for asset in pool:
        # Calculate multiple similarity metrics
        ratio = fuzz.ratio(wanted_name.lower(), asset.name.lower())
        partial_ratio = fuzz.partial_ratio(wanted_name.lower(), asset.name.lower())
        token_sort_ratio = fuzz.token_sort_ratio(wanted_name.lower(), asset.name.lower())

        # Use the highest similarity score
        similarity = max(ratio, partial_ratio, token_sort_ratio)

        if similarity >= threshold:
            matches.append(asset)

    return matches


def find_partial_token_matches(pool: List[AssetRecord], wanted_name: str) -> List[AssetRecord]:
    """Find assets with partial token matches (at least 50% of tokens match)."""
    wanted_name_norm = re.sub(r"[^a-z0-9]+", " ", wanted_name.lower()).strip()
    wanted_tokens = set(wanted_name_norm.split())

    if not wanted_tokens:
        return []

    matches = []
    for asset in pool:
        asset_name_norm = re.sub(r"[^a-z0-9]+", " ", asset.name.lower()).strip()
        asset_tokens = set(asset_name_norm.split())

        if not asset_tokens:
            continue

        # Calculate token overlap
        intersection = len(wanted_tokens & asset_tokens)
        total_unique = len(wanted_tokens | asset_tokens)

        if total_unique > 0:
            overlap_ratio = intersection / total_unique
            # Require at least 40% token overlap
            if overlap_ratio >= 0.4:
                matches.append(asset)

    return matches


def pick_strict_default(
    defaults_index: List[AssetRecord],
    wanted_role: str,
    family: str,
    subtype: str,
    klass: str,
    build: str,
) -> Optional[AssetRecord]:
    """
    NEW: Pick default with strict requirement - at least Subtype must match.
    """
    if not defaults_index or not subtype:
        return None  # No defaults available or no subtype to match

    # Filter defaults by role
    role_defaults = [
        d
        for d in defaults_index
        if (wanted_role == "Engine" and d.kind == AssetKind.ENGINE)
        or (wanted_role == "Wagon" and d.kind == AssetKind.WAGON)
    ]

    if not role_defaults:
        return None

    # Filter by subtype requirement (strict)
    subtype_matched = []
    for default in role_defaults:
        default_subtype = detect_subtype_from_name(
            default.name
        ) or detect_subtype_from_name(default.folder)
        if _ci_eq(default_subtype, subtype):
            subtype_matched.append(default)

    if not subtype_matched:
        return None  # No defaults match the required subtype

    # Score the subtype-matched defaults
    scored_defaults = []

    for default in subtype_matched:
        score = 100  # Base score for subtype match
        default_name_lower = default.name.lower()

        # Additional scoring for other attributes
        default_family = detect_family_from_name(
            default.name, wanted_role
        ) or detect_family_from_name(default.folder, wanted_role)
        default_class = detect_wagon_or_engine_class(default.name, wanted_role)
        default_build = detect_build_from_name_or_folder(default.name, default.folder)

        if family and _ci_eq(default_family, family):
            score += 50
        if klass and _ci_eq(default_class, klass):
            score += 75
        if build and _ci_eq(default_build, build):
            score += 100

        # Generic defaults bonus
        if "default" in default_name_lower:
            score += 25

        scored_defaults.append((default, score))

    # Sort by score and return best
    scored_defaults.sort(key=lambda x: (-x[1], x[0].name.lower()))
    return scored_defaults[0][0] if scored_defaults else None


def rank_by_name_then_tokens(
    pool: List[AssetRecord],
    wanted_name: str,
    wanted_folder: str,
    klass: str,
    build: str,
) -> Optional[AssetRecord]:
    """Rank candidates by name similarity then token matching."""
    if not pool:
        return None

    import random

    # PERFORMANCE OPTIMIZATION: Cache wanted name normalization
    wanted_name_norm = re.sub(r"[^a-z0-9]+", " ", wanted_name.lower()).strip()
    wanted_tokens = set(wanted_name_norm.split())

    # Add folder tokens if available
    if wanted_folder:
        folder_norm = re.sub(r"[^a-z0-9]+", " ", wanted_folder.lower()).strip()
        wanted_tokens.update(folder_norm.split())

    scored_candidates = []

    for asset in pool:
        score = 0

        # Normalized name equality (highest priority)
        asset_name_norm = asset.cached_normalized
        if asset_name_norm == wanted_name_norm:
            score += 1000

        # Token containment and overlap calculation
        asset_tokens = asset.cached_tokens
        token_intersection = wanted_tokens & asset_tokens
        token_containment = wanted_tokens <= asset_tokens
        token_overlap = len(token_intersection) > 0
        
        # Get asset class for comparison
        asset_class = asset.cached_class
        exact_class_match = asset_class and _ci_eq(asset_class, klass)
        
        # Optimized scoring logic
        if token_containment or (token_overlap and exact_class_match):
            # Determine class compatibility more efficiently
            if not klass:
                class_compatible = True
            elif asset.kind == AssetKind.WAGON:
                # For wagons, use cached compatibility check
                class_compatible = exact_class_match or not asset_class  # Simplified check
            else:
                # For engines, exact match required
                class_compatible = exact_class_match
            
            if class_compatible:
                # Score based on token match quality
                if token_containment:
                    score += 900  # Perfect token containment
                else:
                    score += 700  # Partial tokens with exact class
                
                # Class match bonuses
                if exact_class_match:
                    score += 300
                    # Extra bonus for high-priority classes
                    if klass in ["HCPV", "HPCV"]:
                        score += 100
            elif not asset_class:
                score += 600  # No class detected
            else:
                score += 50   # Incompatible class

        # Jaccard similarity (optimized)
        if wanted_tokens and asset_tokens:
            union_size = len(wanted_tokens | asset_tokens)
            if union_size > 0:
                jaccard_score = int((len(token_intersection) / union_size) * 800)
                score += jaccard_score

        # Same folder bonus
        if wanted_folder and asset.folder.lower() == wanted_folder.lower():
            score += 100

        # PERFORMANCE OPTIMIZATION: Cache build detection
        asset_build = ""
        if build:
            asset_build = detect_build_from_name_or_folder(asset.name, asset.folder)
            if asset_build == build:
                score += 200 if build in ["UTK", "TEJAS", "HUMSAFAR", "VANDE"] else 80

        # Class-specific bonuses (optimized)
        if klass and exact_class_match:
            if asset.kind == AssetKind.WAGON:
                score += 150
                if exact_class_match:
                    score += 100
            else:
                score += 150

        # Non-defaults bonus
        if not asset.folder.lower().startswith("_defaults"):
            score += 50

        # Add controlled random element for variety (optimized)
        if klass:
            if klass in ["BOBYN", "BOXN", "BRN", "BRNA"]:
                score += random.randint(0, 2)
            elif klass in ["WAG7", "WAG9", "WAP7"]:
                score += random.randint(0, 3)
            else:
                score += random.randint(0, 4)
        else:
            score += random.randint(0, 3)

        scored_candidates.append((asset, score))

    # Sort by score (descending), then use deterministic tie-breaking for consistency
    if klass == "BOBYN":
        # For BOBYN wagons, use deterministic sorting for consistency
        scored_candidates.sort(key=lambda x: (-x[1], x[0].name.lower()))
    else:
        # Use deterministic tie-breaking based on asset name hash for consistency
        scored_candidates.sort(key=lambda x: (-x[1], hash(x[0].name.lower()) % 100, hash(x[0].folder.lower()) % 100))

    # Conditional debug logging for top candidates (only when debug enabled)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(
            f"RANKING for {wanted_name}: top 3 candidates: {[(c[0].folder + '/' + c[0].name, c[1]) for c in scored_candidates[:3]]}"
        )

        # If multiple candidates have the same top score, log the variety
        if len(scored_candidates) > 1:
            top_score = scored_candidates[0][1]
            tied_candidates = [c for c in scored_candidates if c[1] == top_score]
            if len(tied_candidates) > 1:
                chosen_name = (
                    scored_candidates[0][0].folder + "/" + scored_candidates[0][0].name
                )
                logging.debug(
                    f"TIE-BREAKER: {len(tied_candidates)} candidates with score {top_score}, randomly selected: {chosen_name}"
                )

    return scored_candidates[0][0] if scored_candidates else None


def choose_best(
    candidates: List[AssetRecord],
    wanted_name: str,
    wanted_folder: str,
    klass: str,
    build: str,
) -> AssetRecord:
    """Choose the best candidate from exact matches."""
    if not candidates:
        return None

    import random

    # Prefer same folder
    same_folder = [
        c for c in candidates if c.folder.lower() == (wanted_folder or "").lower()
    ]
    if same_folder:
        candidates = same_folder

    # Prefer non-defaults
    non_defaults = [
        c for c in candidates if not c.folder.lower().startswith("_defaults")
    ]
    if non_defaults:
        candidates = non_defaults

    # If multiple candidates remain, use deterministic selection based on name hash
    if len(candidates) > 1:
        # Sort deterministically by name hash to ensure consistent selection
        candidates.sort(key=lambda x: hash((x.folder + x.name).lower()))
        chosen_name = candidates[0].folder + "/" + candidates[0].name
        logging.info(
            f"EXACT-MATCH TIE-BREAKER: {len(candidates)} exact matches for '{wanted_name}', randomly selected: {chosen_name}"
        )

    return candidates[0]


def detect_wagon_or_engine_class(name: str, wanted_role: str = "Engine") -> str:
    """
    Detect specific class (WAP7, 3A, BOXN, HCPV, BRN/BRNA, etc.) in a boundary-safe way.
    COMPREHENSIVE FIX with extensive debugging for BRN detection.
    """
    if not name:
        return ""
        
    name_lower = str(name).lower()
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(f"CLASS_DETECTION: Processing '{name}' -> '{name_lower}'")

    # Normalize separators so '_' and '-' act like spaces; collapse to single spaces
    norm = _NORMALIZE_PATTERN.sub(' ', name_lower).strip()
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(f"CLASS_DETECTION: Normalized -> '{norm}'")

    # ENHANCED v2.2.8: Compound wagon name handling (BEFORE pattern matching)
    # For wagons with multiple class indicators, prioritize certain classes
    class_indicators = ['brn', 'brna', 'brd', 'brw', 'hcpv', 'hpcv', 'bcn', 'boxn', 'bvcm']
    found_classes = [cls for cls in class_indicators if cls in name_lower]
    
    if len(found_classes) > 1:
        # This is a compound wagon - check for priority classes
        parcel_indicators = ['hcpv', 'hpcv']
        has_parcel = any(parcel in name_lower for parcel in parcel_indicators)
        
        if has_parcel:
            logging.debug(f"CLASS_DETECTION: Compound wagon detected with classes {found_classes}, prioritizing HCPV due to parcel indicator")
            return 'HCPV'
    
    # Special handling for BVCM + brake combinations
    if 'bvcm' in name_lower and 'brake' in name_lower:
        logging.debug(f"CLASS_DETECTION: BVCM + brake combination detected, prioritizing BVCM")
        return 'BVCM'

    # --- IMMEDIATE BRN/BRAKE DETECTION (HIGHEST PRIORITY) ---
    # Handle BRN family specifically since this is your main issue
    brn_patterns = {
        'brn': 'BRN',
        'brna': 'BRNA', 
        'brd': 'BRD',
        'brs': 'BRS',
        'bru': 'BRU',
        'brake': 'BRAKE',
        'bvzi': 'BVZI',
        'bvzc': 'BVZC',
    }
    
    # Check for BRN patterns in both original and normalized text
    for pattern, class_name in brn_patterns.items():
        # Check in original name with word boundaries
        regex_pattern = get_compiled_regex(rf'\b{pattern}\b')
        if regex_pattern.search(name_lower):
            logging.debug(f"CLASS_DETECTION: Found BRN pattern '{pattern}' in original -> {class_name}")
            return class_name
        # Check in normalized text
        if pattern in norm.split():
            logging.debug(f"CLASS_DETECTION: Found BRN pattern '{pattern}' in normalized -> {class_name}")
            return class_name
        # Check with underscores/hyphens
        if f'_{pattern}_' in name_lower or f'-{pattern}-' in name_lower:
            logging.debug(f"CLASS_DETECTION: Found BRN pattern '{pattern}' with separators -> {class_name}")
            return class_name
        if name_lower.startswith(f'{pattern}_') or name_lower.endswith(f'_{pattern}'):
            logging.debug(f"CLASS_DETECTION: Found BRN pattern '{pattern}' at boundary -> {class_name}")
            return class_name

    # --- High-priority buckets (cheap checks) ---
    if 'plasser' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found maintenance -> PLASSER")
        return "PLASSER"
    if any(term in name_lower for term in ['tamper','ballast_cleaner','rail_grinder','track_machine','crane','breakdown']):
        logging.debug(f"CLASS_DETECTION: Found maintenance equipment")
        return "MAINTENANCE"

    # --- LOCOMOTIVE CLASS DETECTION (check BEFORE AI_HORN to avoid conflicts) ---
    if wanted_role != "Wagon":
        for pattern in _COMPILED_LOCO_PATTERNS:
            m = pattern.search(name_lower)
            if m:
                # Use capture group 1 if it exists (base class), otherwise use full match
                if m.groups() and m.group(1):
                    result = m.group(1).upper().replace('-', '').replace('_', '')
                else:
                    result = m.group(0).upper()
                logging.debug(f"CLASS_DETECTION: Locomotive pattern -> {result}")
                return result

    # AI/HORN detection (AFTER locomotive detection to avoid conflicts)
    if 'ai' in name_lower and 'horn' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found AI horn -> AI_HORN")
        return "AI_HORN"
    if 'horn' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found horn -> HORN")
        return "HORN"

    # Manufacturer/prefix
    if name_lower.startswith('bsam_'):
        base = name_lower[5:]
        for ft in ['boxn','bobyn','bcna','tank','flat']:
            if base.startswith(ft):
                logging.debug(f"CLASS_DETECTION: BSAM prefix -> {ft.upper()}")
                return ft.upper()
        logging.debug(f"CLASS_DETECTION: Generic BSAM -> BSAM")
        return "BSAM"
    if name_lower.startswith('asmi'):
        base = name_lower[4:]
        for ft in ['bca','bcb','bcn','btp','blc']:
            if base.startswith(ft):
                logging.debug(f"CLASS_DETECTION: ASMI prefix -> {ft.upper()}")
                return ft.upper()
        logging.debug(f"CLASS_DETECTION: Generic ASMI -> ASMI")
        return "ASMI"

    # Multiple Unit classes
    if 'memu' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found MEMU")
        return 'MEMU'
    if 'emu' in name_lower and 'memu' not in name_lower:
        logging.debug(f"CLASS_DETECTION: Found EMU")
        return 'EMU'
    if 'dmu' in name_lower or 'demu' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found DMU")
        return 'DMU'
    if 'mmu' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found MMU")
        return 'MMU'

    # Container early
    if name_lower.startswith('con_') or 'container' in name_lower:
        logging.debug(f"CLASS_DETECTION: Found container")
        return "CONTAINER"

    # --- Explicit patterns (non-alnum lookarounds) - CHECKED FIRST ---
    patterns = {
        # Brake / Service - SPECIFIC FIX FOR BRN
        r'(?<![A-Za-z0-9])brn(?![A-Za-z0-9])': 'BRN',
        r'(?<![A-Za-z0-9])brna(?![A-Za-z0-9])': 'BRNA',
        r'(?<![A-Za-z0-9])brd(?![A-Za-z0-9])': 'BRD',
        r'(?<![A-Za-z0-9])brs(?![A-Za-z0-9])': 'BRS',
        r'(?<![A-Za-z0-9])bru(?![A-Za-z0-9])': 'BRU',
        r'(?<![A-Za-z0-9])bvcm(?![A-Za-z0-9])': 'BVCM',  # Brake van (before brake to avoid conflicts)
        r'(?<![A-Za-z0-9])brake(?![A-Za-z0-9])': 'BRAKE',
        
        # Coach classes from store files
        r'(?<![A-Za-z0-9])accc(?![A-Za-z0-9])': 'ACCC',  # AC Chair Car - CHECK FIRST
        r'(?<![A-Za-z0-9])1a(?![A-Za-z0-9])': '1A',      # First AC
        r'(?<![A-Za-z0-9])2a(?![A-Za-z0-9])': '2A',      # Second AC
        r'(?<![A-Za-z0-9])3a(?![A-Za-z0-9])': '3A',      # Third AC
        r'(?<![A-Za-z0-9])sl(?![A-Za-z0-9])': 'SL',      # Sleeper
        r'(?<![A-Za-z0-9])slr(?![A-Za-z0-9])': 'SLR',    # Sleeper cum Luggage
        r'(?<![A-Za-z0-9])sc(?![A-Za-z0-9])': 'SC',      # Second Class Chair
        r'(?<![A-Za-z0-9])gs(?![A-Za-z0-9])': 'GS',      # General Second
        r'(?<![A-Za-z0-9])pc(?![A-Za-z0-9])': 'PC',      # Pantry Car
        r'(?<![A-Za-z0-9])eog(?![A-Za-z0-9])': 'EOG',    # End on Generator
        r'(?<![A-Za-z0-9])fc(?![A-Za-z0-9])': 'FC',      # First Class
        # ACCC compound pattern - MUST BE BEFORE CC
        r'(?<![A-Za-z0-9])(?i:ac\s*[-_]?\s*cc|cc\s*[-_]?\s*ac)(?![A-Za-z0-9])': 'ACCC',              # AC Chair Car compound
        r'(?<![A-Za-z0-9])cc(?![A-Za-z0-9])': 'CC',      # AC Chair Car - MOVED AFTER ACCC
        r'(?<![A-Za-z0-9])gn(?![A-Za-z0-9])': 'GN',      # General
        r'(?<![A-Za-z0-9])2s(?![A-Za-z0-9])': '2S',      # Second Sitting
        
        # Additional wagon classes from store files
        r'(?<![A-Za-z0-9])btcs(?![A-Za-z0-9])': 'BTCS',  # Tank wagon
        r'(?<![A-Za-z0-9])btpgln(?![A-Za-z0-9])': 'BTPGLN',  # Tank wagon
        r'(?<![A-Za-z0-9])apl(?![A-Za-z0-9])': 'APL',    # Parcel wagon
        r'(?<![A-Za-z0-9])auto(?![A-Za-z0-9])': 'AUTO',  # Automobile wagon
        r'(?<![A-Za-z0-9])coil(?![A-Za-z0-9])': 'COIL',  # Coil wagon
        r'(?<![A-Za-z0-9])hopper(?![A-Za-z0-9])': 'HOPPER',  # Hopper wagon
        r'(?<![A-Za-z0-9])pipe(?![A-Za-z0-9])': 'PIPE',  # Pipe wagon
        r'(?<![A-Za-z0-9])tender(?![A-Za-z0-9])': 'TENDER',  # Tender wagon
        
        # Parcel/mail
        r'(?<![A-Za-z0-9])hcpv(?![A-Za-z0-9])': 'HCPV',
        r'(?<![A-Za-z0-9])hpcv(?![A-Za-z0-9])': 'HCPV',
        r'(?<![A-Za-z0-9])parcel(?![A-Za-z0-9])': 'PARCEL',
        r'(?<![A-Za-z0-9])mail(?![A-Za-z0-9])': 'MAIL',

        # BCN/BCNA family
        r'(?<![A-Za-z0-9])bcna\d*(?![A-Za-z0-9])': 'BCNA',
        r'(?<![A-Za-z0-9])bcne(?![A-Za-z0-9])': 'BCNE',
        r'(?<![A-Za-z0-9])bcnh(?![A-Za-z0-9])': 'BCNH',
        r'(?<![A-Za-z0-9])bcnl(?![A-Za-z0-9])': 'BCNL',
        r'(?<![A-Za-z0-9])bccnr(?![A-Za-z0-9])': 'BCCNR',
        r'(?<![A-Za-z0-9])bccw(?![A-Za-z0-9])': 'BCCW',
        r'(?<![A-Za-z0-9])bcn(?!a)(?![A-Za-z0-9])': 'BCN',
        r'(?<![A-Za-z0-9])bcnhl(?![A-Za-z0-9])': 'BCNHL',
        r'(?<![A-Za-z0-9])bccn(?![A-Za-z0-9])': 'BCCN',
        
        # BOBYN family (Brake van)
        r'(?<![A-Za-z0-9])bobyn[a-z0-9_\-]*?(?![A-Za-z0-9])': 'BOBYN',
        
        # BOXN family
        r'(?<![A-Za-z0-9])boxn[a-z0-9_\-]*?(?![A-Za-z0-9])': 'BOXN',
        
        # BOSTH family (Open wagons for coal/ballast)
        r'(?<![A-Za-z0-9])bosth[a-z0-9_\-]*?(?![A-Za-z0-9])': 'BOSTH',
        
        # Tanks
        r'(?<![A-Za-z0-9])btfln(?![A-Za-z0-9])': 'BTFLN',
        r'(?<![A-Za-z0-9])btpn(?![A-Za-z0-9])': 'BTPN',
        r'(?<![A-Za-z0-9])tank(?![A-Za-z0-9])': 'TANK',
        
        # Flats / Containers
        r'(?<![A-Za-z0-9])flat(?:car)?(?![A-Za-z0-9])': 'FLAT',
        r'(?<![A-Za-z0-9])blc[a-z0-9_\-]*(?![A-Za-z0-9])': 'BLC',
        
        # Specialized freight patterns (train-specific and OE wagons) - AVOIDING GN conflict
        r'(?<![A-Za-z0-9])\d+[-_]?class(?![A-Za-z0-9])': 'GN',  # Train number + CLASS (general class wagon)
        r'(?<![A-Za-z0-9])\d+[-_]?gc\d*(?![A-Za-z0-9])': 'EOG',  # Train number + GC (generator car)
        r'(?<![A-Za-z0-9])\d+[-_]?gen\d*(?![A-Za-z0-9])': 'EOG',  # Train number + GEN (generator car)
        r'(?<![A-Za-z0-9])\d+gene(?![A-Za-z0-9])': 'EOG',  # Train number + GENE (generator car)
        r'(?<![A-Za-z0-9])\d+[-_]?gene(?![A-Za-z0-9])': 'EOG',  # Train number + GENE (generator car)
        r'(?<![A-Za-z0-9])\d+[-_]?goods?(?![A-Za-z0-9])': 'BOXN',  # Train number + GOODS (covered wagon)
        r'(?<![A-Za-z0-9])\d+[-_]?cargo(?![A-Za-z0-9])': 'BOXN',  # Train number + CARGO (covered wagon)
        r'(?<![A-Za-z0-9])\d+[-_]?cont(?![A-Za-z0-9])': 'BOXN',  # Train number + CONT (covered wagon)
        r'(?<![A-Za-z0-9])\d+car\d*(?![A-Za-z0-9])': 'BOXN',  # Number + CAR + optional number (covered wagon)
        r'(?<![A-Za-z0-9])\d+grcar\d*(?![A-Za-z0-9])': 'BOXN',  # Number + GRCAR + optional number (covered wagon)
        r'(?<![A-Za-z0-9])\d+wdcar\d*(?![A-Za-z0-9])': 'BOXN',  # Number + WDCAR + optional number (covered wagon)
        r'(?<![A-Za-z0-9])oe\d+cardin\d*(?![A-Za-z0-9])': 'BOXN',  # OE + number + CARDIN + number (covered wagon)
        r'(?<![A-Za-z0-9])oebarcar(?![A-Za-z0-9])': 'BOXN',  # OE bar car (covered wagon)
        
        # Brand/product specific freight wagons (AVOIDING GN conflict with coaches)
        r'(?<![A-Za-z0-9])cream[-_]?bell(?![A-Za-z0-9])': 'MILKTANKER',  # Cream Bell (dairy) - milk tanker
        r'(?<![A-Za-z0-9])fanta[-_]?time(?![A-Za-z0-9])': 'TANK',  # Fanta Time (beverage) - tank wagon
        r'(?<![A-Za-z0-9])gnfc(?![A-Za-z0-9])': 'TANK',  # GNFC (chemicals) - chemical tank
        r'(?<![A-Za-z0-9])chem(?![A-Za-z0-9])': 'TANK',  # Chemical wagons - tank wagon
    }

    # Raw text pass
    for pat, klass in patterns.items():
        if re.search(pat, name_lower):
            logging.debug(f"CLASS_DETECTION: Matched pattern {pat} -> {klass}")
            return klass

    # Relaxed pass over normalized text
    for pat, klass in patterns.items():
        relaxed = pat.replace('(?<![A-Za-z0-9])', r'(?:^|\s|[^A-Za-z0-9])')
        relaxed = relaxed.replace('(?![A-Za-z0-9])', r'(?:[^A-Za-z0-9]|\s|$)')
        if re.search(relaxed, norm):
            logging.debug(f"CLASS_DETECTION: Matched relaxed pattern {relaxed} -> {klass}")
            return klass

    # ENHANCED: Embedded pattern pass for cases like "MAXBCNA", "SUPERBOXN", etc.
    # Add specific embedded patterns for common compound wagon names
    # Order matters: more specific patterns first
    embedded_patterns = {
        # BCN/BCNA family embedded patterns (more specific first)
        r'bcna\d*': 'BCNA',  # Matches bcna, bcna123, etc. when embedded
        r'bcne': 'BCNE',
        r'bcnh': 'BCNH',
        r'bcnl': 'BCNL',
        r'bccn': 'BCCN',
        r'bcn\d+': 'BCN',   # Matches bcn123, bcn456, etc. (with digits)
        r'bcn': 'BCN',      # Matches bcn only (at end to avoid conflicts)

        # ACCC embedded patterns
        r'accc': 'ACCC',    # AC Chair Car

        # BOXN family embedded patterns
        r'boxn[a-z0-9]*': 'BOXN',

        # BOBYN family embedded patterns
        r'bobyn[a-z0-9]*': 'BOBYN',

        # Additional wagon embedded patterns
        r'btcs': 'BTCS',
        r'bvcm': 'BVCM',
        r'coil': 'COIL',
        r'hopper': 'HOPPER',

        # Tank family embedded patterns
        r'btfln': 'BTFLN',
        r'btpn': 'BTPN',
    }

    for embedded_pat, klass in embedded_patterns.items():
        if re.search(embedded_pat, name_lower):
            # For embedded patterns, we want to match wagon codes within compound names
            # but avoid obvious false positives like matching 'bcna' in 'abcna'
            match = re.search(embedded_pat, name_lower)
            if match:
                start, end = match.span()
                matched_text = name_lower[start:end]

                # Avoid false positives: don't match if the wagon code is clearly part of another word
                # Exception: allow if the match is at the end of the string
                if end == len(name_lower):
                    # Match is at end of string - this is likely a valid embedded wagon code
                    logging.debug(f"CLASS_DETECTION: Matched embedded pattern {embedded_pat} at end -> {klass}")
                    return klass
                elif start > 0 and name_lower[start-1].isalpha():
                    # Match is preceded by a letter - likely a false positive like 'abcna'
                    continue
                else:
                    # Match appears to be a valid embedded wagon code
                    logging.debug(f"CLASS_DETECTION: Matched embedded pattern {embedded_pat} -> {klass}")
                    return klass

    # --- FIXED: GENERIC TOKEN/PREFIX FALLBACK (covers ALL freight classes) ---
    tokens = norm.split()
    logging.debug(f"CLASS_DETECTION: Tokens -> {tokens}")
    
    try:
        freight_types = set(IndianRailwaysClassifier.FREIGHT_TYPES)
        aliases = dict(IndianRailwaysClassifier.ALIASES)
        logging.debug(f"CLASS_DETECTION: freight_types size={len(freight_types)}, 'brn' in freight_types = {'brn' in freight_types}")
    except Exception as e:
        logging.debug(f"CLASS_DETECTION: Failed to load classifier data: {e}")
        freight_types = set()
        aliases = {}

    best = ""
    for tok in tokens:
        logging.debug(f"CLASS_DETECTION: Processing token '{tok}'")
        canon = aliases.get(tok, tok)  # apply alias map first (stays lowercase)
        logging.debug(f"CLASS_DETECTION: Token '{tok}' -> canonical '{canon}'")
        
        # FIXED: exact match with consistent case
        if canon in freight_types:  # Check lowercase 'brn' in {'brn', 'brna', ...}
            cand = canon.upper()  # Return uppercase 'BRN'
            logging.debug(f"CLASS_DETECTION: Exact match '{canon}' -> '{cand}'")
        else:
            # FIXED: longest-prefix with consistent case
            cand = ""
            for L in range(len(canon), 2, -1):  # Use lowercase canon, not uppercase cu
                prefix = canon[:L]
                if prefix in freight_types:  # Check lowercase prefix in lowercase set
                    cand = prefix.upper()  # Return uppercase result
                    logging.debug(f"CLASS_DETECTION: Prefix match '{prefix}' -> '{cand}'")
                    break
            
        if cand and (not best or len(cand) > len(best)):
            best = cand
            logging.debug(f"CLASS_DETECTION: New best candidate '{best}'")

    if best:
        logging.debug(f"CLASS_DETECTION: Generic token/prefix fallback -> {best}")
        return best

    # --- COACH CLASS DETECTION ---
    # Coach classes (1A, 2A, 3A, GS, SL, etc.)
    coach_patterns = {
        # Specific AC classes (HIGHEST PRIORITY - check these first)
        r'(?<![A-Za-z0-9])1a(?![A-Za-z0-9])': '1A',
        r'(?<![A-Za-z0-9])2a(?![A-Za-z0-9])': '2A', 
        r'(?<![A-Za-z0-9])3a(?![A-Za-z0-9])': '3A',
        # AC3 pattern for combined AC+class notation
        r'(?<![A-Za-z0-9])ac3(?![A-Za-z0-9])': '3A',
        r'(?<![A-Za-z0-9])ac2(?![A-Za-z0-9])': '2A',
        r'(?<![A-Za-z0-9])ac1(?![A-Za-z0-9])': '1A',
        
        # Vande Bharat patterns
        r'(?i)vbcc\d*(?![A-Za-z0-9])': 'CC',
        r'(?i)vande[-_]?bharat[-_]?cc\d*(?![A-Za-z0-9])': 'CC',
        # Vande Bharat Executive Chair Car patterns
        r'(?i)vbexcc\d*(?![A-Za-z0-9])': 'EC',
        r'(?i)vande[-_]?bharat[-_]?excc\d*(?![A-Za-z0-9])': 'EC',
        r'(?i)executive[-_]?chair[-_]?car(?![A-Za-z0-9])': 'EC',
        
        # AC Tier patterns (should not conflict with specific classes above)
        r'(?i)ac[-_]?1[-_]?tier|1[-_]?tier[-_]?ac': '1A',
        r'(?i)ac[-_]?2[-_]?tier|2[-_]?tier[-_]?ac': '2A', 
        r'(?i)ac[-_]?3[-_]?tier|3[-_]?tier[-_]?ac': '3A',
        
        # AC Chair Car patterns (HIGH PRIORITY) - ENHANCED v2.2.3
        r'(?i)chaircar[-_]?ac|ac[-_]?chaircar': 'ACCC',
        r'(?i)chair[-_]?car[-_]?ac|ac[-_]?chair[-_]?car': 'ACCC',
        r'(?i)ac[-_]?cc|cc[-_]?ac': 'ACCC',
        
        
        
        # Generic AC chair patterns (lower priority)
        r'(?i)ac[-_]?chair|chair[-_]?ac': 'ACCC',
        
        # Non-AC classes
        r'(?<![A-Za-z0-9])gs(?![A-Za-z0-9])': 'GS',
        r'(?<![A-Za-z0-9])slp(?![A-Za-z0-9])': 'SL',
        r'(?<![A-Za-z0-9])sl(?![A-Za-z0-9])': 'SL',
        r'(?<![A-Za-z0-9])sleeper(?![A-Za-z0-9])': 'SL',
        
        # Service cars
        r'(?i)pantry[-_]?car|pantry': 'PC',
        r'(?i)guard|luggage[-_]?van': 'SLR',
        r'(?<![A-Za-z0-9])slr(?![A-Za-z0-9])': 'SLR',
        r'(?i)generator|power[-_]?car': 'EOG',
        r'(?<![A-Za-z0-9])pc(?![A-Za-z0-9])': 'PC',
        r'(?<![A-Za-z0-9])pantry(?![A-Za-z0-9])': 'PC',
        r'(?<![A-Za-z0-9])eog(?![A-Za-z0-9])': 'EOG',
        r'(?<![A-Za-z0-9])generator(?![A-Za-z0-9])': 'EOG',
        
        # Chair Car
        r'(?<![A-Za-z0-9])cc(?![A-Za-z0-9])': 'CC',
        r'(?<![A-Za-z0-9])chair(?![A-Za-z0-9])': 'CC',
        
        # Other coach types
        r'(?<![A-Za-z0-9])fc(?![A-Za-z0-9])': 'FC',
        r'(?<![A-Za-z0-9])sc(?![A-Za-z0-9])': 'SC',
        r'(?<![A-Za-z0-9])gn(?![A-Za-z0-9])': 'GN',
        r'(?<![A-Za-z0-9])gen(?![A-Za-z0-9])': 'GN',
        r'(?<![A-Za-z0-9])general(?![A-Za-z0-9])': 'GN',
        
        # AC First class patterns (HIGH PRIORITY - before generic AC)
        r'(?i)ac[-_]?first|first[-_]?ac': '1A',
        r'(?i)ac1|1ac': '1A',
        
        # AC Second class patterns
        r'(?i)ac[-_]?second|second[-_]?ac': '2A',
        r'(?i)ac2|2ac': '2A',
        
        # AC Third class patterns  
        r'(?i)ac[-_]?third|third[-_]?ac': '3A',
        r'(?i)ac3|3ac': '3A',
        
        # OE (Overseas/Export) coach patterns
        r'(?i)oe[-_]?sleep[-_]?car|oesleepcar': 'SL',
        r'(?i)oe[-_]?serv[-_]?car|oeservcar': 'SLR',
        
        # Second class patterns
        r'(?i)second[-_]?class|secondclass': 'GS',
        r'(?i)second[-_]?cla': 'GS',
        
        # Express train patterns
        r'(?i)express[-_]?second[-_]?cla': 'GS',
        r'(?i)exp[-_]?second[-_]?cla': 'GS',
        
        # Generic coach patterns
        r'(?i)new[-_]?gc': 'GN',
        r'(?i)gc$': 'GN',  # GC at end of name
        r'(?i)^gc': 'GN',  # GC at start of name
        
        # H1/HA1 patterns (likely AC First class variants)
        r'(?i)h1$|ha1$': '1A',
        
        # Tender patterns (for steam locomotives)
        r'(?i)tender': 'TENDER',
        
        # Container patterns
        r'(?i)container': 'CONTAINER',
        r'(?i)con$': 'CONTAINER',
        
        # Trans patterns (likely transfer/container)
        r'(?i)trans': 'CONTAINER',
    }

    # Check coach patterns
    for pat, klass in coach_patterns.items():
        if re.search(pat, name_lower):
            logging.debug(f"CLASS_DETECTION: Matched coach pattern {pat} -> {klass}")
            return klass

    # Relaxed coach pattern matching
    for pat, klass in coach_patterns.items():
        relaxed = pat.replace('(?<![A-Za-z0-9])', r'(?:^|\s|[^A-Za-z0-9])')
        relaxed = relaxed.replace('(?![A-Za-z0-9])', r'(?:[^A-Za-z0-9]|\s|$)')
        if re.search(relaxed, norm):
            logging.debug(f"CLASS_DETECTION: Matched relaxed coach pattern {relaxed} -> {klass}")
            return klass

    logging.debug(f"CLASS_DETECTION: No class detected for '{name}'")
    return ""


class AssetResolver:
    """Main asset resolution engine with STRICT ATTRIBUTE LOCKING - ENHANCED v2.3.0."""

    def __init__(self, config: ScoreConfig, classifier: IndianRailwaysClassifier):
        self.config = config
        self.classifier = classifier
        self.extractor = AssetMetadataExtractor(classifier)
        self.index = AssetIndex()

        # Statistics
        self.stats = {
            "total_processed": 0,
            "resolved": 0,
            "changed": 0,
            "unresolved": 0,
            "by_phase": Counter(),
        }

        self._lock = threading.RLock()
        self._logged_matches = set()  # Track logged matches to prevent duplicates

        # Logging configuration
        self._verbose_logging = os.getenv('CONSIST_RESOLVER_VERBOSE', 'false').lower() == 'true'

    def __getstate__(self):
        """Support for pickling - exclude non-picklable objects."""
        state = self.__dict__.copy()
        # Remove threading objects that can't be pickled
        state['_lock'] = None
        state['_logged_matches'] = None  # Mark for recreation
        return state

    def __setstate__(self, state):
        """Support for unpickling - restore state."""
        self.__dict__.update(state)
        # Recreate threading objects in worker processes
        if self._lock is None:
            self._lock = threading.RLock()
        if self._logged_matches is None:
            self._logged_matches = set()

    def build_asset_index(
        self, trainset_dir: Path, required_folders: Optional[Set[str]] = None
    ) -> int:
        """Build comprehensive asset index from trainset directory."""
        logging.info(f"Building asset index from {trainset_dir}")
        if not trainset_dir.exists() or not trainset_dir.is_dir():
            raise ValueError(f"Trainset directory not found: {trainset_dir}")

        folders_to_scan = [d for d in trainset_dir.iterdir() if d.is_dir()]
        assets_found = 0

        # Process folders with progress reporting
        total_folders = len(folders_to_scan)
        for i, folder_path in enumerate(folders_to_scan):
            if i % 50 == 0:  # Progress every 50 folders
                logging.info(
                    f"Scanning folder {i+1}/{total_folders}: {folder_path.name}"
                )
            folder_assets = self._scan_folder(folder_path)
            assets_found += folder_assets

        logging.info(
            f"Asset index built: {assets_found} assets in {total_folders} folders"
        )

        # Log detailed statistics
        stats = self.index.get_statistics()
        logging.info(f"Index statistics: {stats}")

        return assets_found

    def _scan_folder(self, folder_path: Path) -> int:
        """Scan a single folder for assets."""
        assets_found = 0
        folder_name = folder_path.name

        # Find engine files
        for engine_file in folder_path.glob("*.eng"):
            try:
                metadata = self.extractor.extract_metadata(
                    AssetKind.ENGINE, engine_file.stem, folder_name
                )
                asset = AssetRecord(
                    kind=AssetKind.ENGINE,
                    name=engine_file.stem,
                    folder=folder_name,
                    path=engine_file,
                    metadata=metadata,
                )
                self.index.add_asset(asset)
                assets_found += 1
            except Exception as e:
                logging.debug(f"Error processing engine {engine_file}: {e}")

        # Find wagon files
        for wagon_file in folder_path.glob("*.wag"):
            try:
                metadata = self.extractor.extract_metadata(
                    AssetKind.WAGON, wagon_file.stem, folder_name
                )
                asset = AssetRecord(
                    kind=AssetKind.WAGON,
                    name=wagon_file.stem,
                    folder=folder_name,
                    path=wagon_file,
                    metadata=metadata,
                )
                self.index.add_asset(asset)
                assets_found += 1
            except Exception as e:
                logging.debug(f"Error processing wagon {wagon_file}: {e}")

        return assets_found

    def _log_match_once(self, match_key: str, message: str) -> None:
        """Log a match message only once to prevent duplicates."""
        if not self._verbose_logging:
            return  # Skip logging if verbose mode is disabled

        with self._lock:
            if match_key not in self._logged_matches:
                self._logged_matches.add(match_key)
                logging.info(message)

    def resolve_asset(self, kind: AssetKind, folder: str, name: str) -> MatchResult:
        """
        NEW STRICT ATTRIBUTE LOCKING RESOLVER v2.3.0:
        1. DERIVE AND LOCK - extract Family, Subtype, Class, Build from consist entry
        2. STRICT FILTERING - only consider trainset assets with EXACT matches for all four attributes
        3. NAME-FIRST - exact name match within locked attributes (first preference)
        4. TOKEN MATCHING - apply name/token ranking within locked attributes
        5. DEFAULT STRICT - require at least Subtype match for defaults
        6. UNRESOLVED - if no attributes detected or no matches found, mark as UNRESOLVED
        """
        with self._lock:
            self.stats["total_processed"] += 1

        # Convert kind to role string
        wanted_role = "Engine" if kind == AssetKind.ENGINE else "Wagon"

        # --- STEP 1: DERIVE AND LOCK ATTRIBUTES FROM CONSIST ENTRY ---
        family = detect_family_from_name(name, wanted_role) or detect_family_from_name(folder, wanted_role)
        subtype = detect_subtype_from_name(name) or detect_subtype_from_name(folder)
        klass = detect_wagon_or_engine_class(name, wanted_role) or detect_wagon_or_engine_class(
            folder, wanted_role
        )
        build = detect_build_from_name_or_folder(name, folder)

        # --- STEP 1.1: SPECIAL AI HORN MATCHING ---
        # If this is an AI horn equipped asset, match it to any AI horn wagon
        if build == "AI":
            self._log_match_once(
                f"ai_horn_detect_{name}",
                f"[AI_HORN] MATCH: '{name}' -> detected AI horn asset, searching for wagons"
            )
            # Look for any wagon asset containing both "ai" and "horn" in the name
            ai_horn_matches = [
                asset for asset in self.index.assets
                if asset.kind == AssetKind.WAGON and
                   "ai" in asset.name.lower() and "horn" in asset.name.lower()
            ]
            if ai_horn_matches:
                chosen = ai_horn_matches[0]  # Take the first match
                self._log_match_once(
                    f"ai_horn_found_{name}_{chosen.folder}_{chosen.name}",
                    f"[AI_HORN] MATCH: '{name}' -> {len(ai_horn_matches)} AI horn wagons found, selected: {chosen.folder}/{chosen.name}"
                )
                with self._lock:
                    self.stats["resolved"] += 1
                    if (
                        chosen.folder.lower() != folder.lower()
                        or chosen.name.lower() != name.lower()
                    ):
                        self.stats["changed"] += 1
                    self.stats["by_phase"][MatchPhase.EXACT_NAME] += 1

                return MatchResult(
                    chosen=chosen,
                    phase=MatchPhase.EXACT_NAME,
                    score=1000.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=1,
                    match_details={
                        "reason": "ai-horn-special-match",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                        "matched_to": f"{chosen.folder}/{chosen.name}"
                    },
                )
            else:
                logging.warning(f"AI HORN MATCH: No AI horn wagons found in trainset for '{name}' (looked for files containing 'ai' and 'horn')")
        # SECOND-CHANCE: if class is still empty but subtype suggests freight,
        # try combined detection over 'folder' and 'name' with generic token fallback.
        if not klass and (subtype or '').lower() == 'freight':
            combined = f"{folder}_{name}"
            alt = detect_wagon_or_engine_class(combined, wanted_role)
            if alt:
                logging.debug(f"CLASS_DETECTION: Second-chance (combined) -> {alt}")
                klass = alt

        # THIRD-CHANCE: if we have a freight wagon class but no subtype, set subtype to Freight
        if not subtype and klass:
            freight_wagon_classes = {
                # BCNA family
                'BCNA', 'BCNE', 'BCNH', 'BCNL', 'BCCNR', 'BCCW', 'BCN', 'BCCN', 'BCBFG', 'BCFC',
                # BOXN family  
                'BOXN', 'BOXNR', 'BOXNG', 'BOXNHL', 'BOXNM1', 'BOXNM2', 'BOSTH',
                # Tank wagons
                'BTPN', 'BTFLN', 'BTAP', 'BTCS', 'BTI', 'TANK', 'MILKTANKER', 'VVN',
                # Flat/Container wagons
                'FLAT', 'BLC', 'BLCA', 'BLCB', 'CONTAINER', 'CON', 'CONCOR', 'BCA', 'BCB',
                # Brake vans
                'BRD', 'BRN', 'BRNA', 'BRAKE', 'BOBYN', 'BVZI', 'BRW', 'BRS', 'BRU',
                # Parcel/Mail
                'HCPV', 'HPCV', 'PARCEL', 'MAIL',
                # Specialized freight
                'COIL', 'SLAB', 'AUTO', 'CEMENT', 'TIPPLER',
                # Manufacturer series
                'BSAM', 'ASMI', 'APL'
            }
            if klass.upper() in freight_wagon_classes:
                subtype = 'Freight'
                logging.debug(f"SUBTYPE_DETECTION: Fallback - detected freight wagon class {klass}, setting subtype to Freight")


        # --- STEP 1.5: DEFAULT CLASS FALLBACK FOR FREIGHT WAGONS ---
        # If we have a freight wagon but no class detected, default to oil tanker
        if not klass and subtype and subtype.lower() == 'freight' and wanted_role == 'Wagon':
            # Check if this looks like an oil/gas related wagon
            name_lower = name.lower()
            folder_lower = folder.lower()
            
            # Oil/Gas/ONGC related indicators
            oil_indicators = [
                'ongc', 'oil', 'gas', 'petrol', 'diesel', 'fuel', 'tanker', 'tank',
                'crude', 'refinery', 'pipeline', 'petroleum', 'energy', 'hydrocarbon'
            ]
            
            # Check name and folder for oil-related terms
            has_oil_indicator = any(
                indicator in name_lower or indicator in folder_lower 
                for indicator in oil_indicators
            )
            
            if has_oil_indicator:
                klass = 'TANK'  # Default to oil tanker
                logging.info(f"DEFAULT CLASS: Wagon {folder}/{name} has no class but appears oil-related, defaulting to TANK")
            else:
                # Generic freight wagon fallback - could be any type
                klass = 'FREIGHT'  # Generic freight wagon
                logging.info(f"DEFAULT CLASS: Wagon {folder}/{name} has no class, defaulting to FREIGHT")

        # --- STEP 2: CHECK FOR UNRESOLVED CONDITIONS ---
        # If ALL attributes are missing (empty), try fallback classification before marking as UNRESOLVED
        if not any([family, subtype, klass, build]):
            # ENHANCED v2.2.7: Fallback classification for wagons with no attributes
            if wanted_role == 'Wagon':
                name_lower = name.lower()
                folder_lower = folder.lower()
                combined_text = f"{folder} {name}".lower()

                # Check if it's NOT passenger (passenger wagons should remain unresolved if no attributes)
                passenger_indicators = [
                    '1a', '2a', '3a', 'ac', 'sl', 'gs', 'cc', 'chair', 'sleeper',
                    'passenger', 'coach', 'pantry', 'eog', 'rajdhani', 'shatabdi'
                ]

                is_passenger = any(indicator in combined_text for indicator in passenger_indicators)

                if not is_passenger:
                    # Oil/Gas/ONGC related indicators - set to tanker
                    oil_indicators = [
                        'ongc', 'oil', 'gas', 'petrol', 'diesel', 'fuel', 'tanker', 'tank',
                        'crude', 'refinery', 'pipeline', 'petroleum', 'energy', 'hydrocarbon',
                        'lng', 'lpg', 'chemical', 'petrochem'
                    ]

                    has_oil_indicator = any(
                        indicator in combined_text for indicator in oil_indicators
                    )

                    if has_oil_indicator:
                        # Set fallback attributes for oil/gas wagons
                        subtype = 'Freight'
                        klass = 'TANK'
                        logging.info(f"FALLBACK CLASSIFICATION: Wagon {folder}/{name} has no attributes but appears oil/gas related, defaulting to Freight/TANK")
                    else:
                        # Generic freight wagon fallback
                        subtype = 'Freight'
                        klass = 'FREIGHT'
                        logging.info(f"FALLBACK CLASSIFICATION: Wagon {folder}/{name} has no attributes, defaulting to Freight/FREIGHT")

                    # Continue with normal resolution using fallback attributes
                    # Don't return unresolved - let the normal flow handle it
                else:
                    # It's a passenger wagon with no attributes - leave as unresolved
                    logging.debug(f"FINAL MATCH: Wagon {name} -> UNRESOLVED Phase=UNRESOLVED Score=0 Reason=passenger-no-attributes")
                    with self._lock:
                        self.stats["unresolved"] += 1
                        self.stats["by_phase"][MatchPhase.UNRESOLVED] += 1
                    return MatchResult(
                        chosen=None,
                        phase=MatchPhase.UNRESOLVED,
                        score=0.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=0,
                        match_details={
                            "reason": "no-attributes-passenger",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )
            else:
                # ENGINE FALLBACK: For engines with no attributes, try nearest engine match
                if wanted_role == "Engine" and kind == AssetKind.ENGINE:
                    all_engines = [asset for asset in self.index.by_kind.get(AssetKind.ENGINE, [])]
                    if all_engines:
                        # Find the best engine match using name similarity
                        engine_match = rank_by_name_then_tokens(all_engines, name, folder, klass, build)
                        if engine_match:
                            logging.info(
                                f"ENGINE NEAREST MATCH (NO ATTRIBUTES): Found nearest engine match for '{name}': {engine_match.folder}/{engine_match.name}"
                            )
                            with self._lock:
                                self.stats["resolved"] += 1
                                if (
                                    engine_match.folder.lower() != folder.lower()
                                    or engine_match.name.lower() != name.lower()
                                ):
                                    self.stats["changed"] += 1
                                self.stats["by_phase"][MatchPhase.GLOBAL_SCORE] += 1

                            return MatchResult(
                                chosen=engine_match,
                                phase=MatchPhase.GLOBAL_SCORE,
                                score=550.0,
                                target=self.extractor.extract_metadata(kind, name, folder),
                                candidates_evaluated=len(all_engines),
                                match_details={
                                    "reason": "engine-nearest-match-no-attributes",
                                    "family": family,
                                    "subtype": subtype,
                                    "class": klass,
                                    "build": build,
                                },
                            )

                # Not a wagon or engine fallback didn't apply - mark as unresolved
                logging.debug(f"FINAL MATCH: Wagon {name} -> UNRESOLVED Phase=UNRESOLVED Score=0 Reason=no-attributes-detected")
                with self._lock:
                    self.stats["unresolved"] += 1
                    self.stats["by_phase"][MatchPhase.UNRESOLVED] += 1
                return MatchResult(
                    chosen=None,
                    phase=MatchPhase.UNRESOLVED,
                    score=0.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=0,
                    match_details={
                        "reason": "no-attributes",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                    },
                )

        # --- STEP 3: STRICT FILTERING - GET ONLY ATTRIBUTE-LOCKED CANDIDATES ---
        # Get all assets of the same kind
        all_assets = list(self.index.by_kind.get(kind, []))

        # --- STEP 3.5: EXACT NAME MATCH PRIORITY (BEFORE ATTRIBUTE FILTERING) ---
        # Check for exact name matches in the ENTIRE pool first (highest priority)
        all_exact_name_matches = [c for c in all_assets if name_equal(c.name, name)]
        if all_exact_name_matches:
            # Choose the best exact match, even if attributes don't match perfectly
            chosen = choose_best(all_exact_name_matches, name, folder, klass, build)
            if chosen:
                logging.debug(f"FINAL MATCH: Wagon {name} -> {chosen.folder}/{chosen.name} Phase=EXACT_NAME Score=1000 Reason=exact-name-any-attributes")
                with self._lock:
                    self.stats["resolved"] += 1
                    if (
                        chosen.folder.lower() != folder.lower()
                        or chosen.name.lower() != name.lower()
                    ):
                        self.stats["changed"] += 1
                    self.stats["by_phase"][MatchPhase.EXACT_NAME] += 1

                return MatchResult(
                    chosen=chosen,
                    phase=MatchPhase.EXACT_NAME,
                    score=1000.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=len(all_exact_name_matches),
                    match_details={
                        "reason": "exact-name-any-attributes",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                    },
                )

        # Apply strict attribute filtering - only exact matches (for non-exact matches)
        locked_pool = apply_strict_attribute_filter(
            all_assets, family, subtype, klass, build
        )

        # Debug logging for filtering results
        logging.debug(
            f"STRICT FILTER for {folder}/{name}: total_assets={len(all_assets)}, locked_pool={len(locked_pool)}"
        )

        if not locked_pool:
            # PERFORMANCE OPTIMIZATION: Try lenient fallback before giving up
            logging.debug(
                f"STRICT FILTER returned no matches for {folder}/{name}, trying lenient fallback..."
            )
            
            # Try more lenient filtering - focus on class match primarily
            lenient_pool = []
            for asset in all_assets:
                asset_class = asset.cached_class
                # Lenient matching: require class match if class is detected, otherwise accept any
                if not klass or _ci_eq(asset_class, klass):
                    lenient_pool.append(asset)
            
            if lenient_pool:
                logging.info(
                    f"LENIENT FALLBACK: Found {len(lenient_pool)} assets with class match for {folder}/{name}"
                )
                # Continue with normal matching using the lenient pool
                locked_pool = lenient_pool
            else:
                logging.debug(f"FINAL MATCH: Wagon {name} -> UNRESOLVED Phase=UNRESOLVED Score=0 Reason=no-matching-attributes-even-lenient")
                with self._lock:
                    self.stats["unresolved"] += 1
                    self.stats["by_phase"][MatchPhase.UNRESOLVED] += 1
                return MatchResult(
                    chosen=None,
                    phase=MatchPhase.UNRESOLVED,
                    score=0.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=len(all_assets),
                    match_details={
                        "reason": "no-matching-attributes-even-lenient",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                    },
                )

        # --- STEP 4: NAME-FIRST WITHIN LOCKED POOL ---
        # This is now secondary - only reached if no exact matches found in entire pool
        exact_name_matches = [c for c in locked_pool if name_equal(c.name, name)]
        if exact_name_matches:
            chosen = choose_best(exact_name_matches, name, folder, klass, build)
            if chosen:
                logging.debug(f"FINAL MATCH: Wagon {name} -> {chosen.folder}/{chosen.name} Phase=EXACT_NAME Score=1000 Reason=exact-name-locked")
                with self._lock:
                    self.stats["resolved"] += 1
                    if (
                        chosen.folder.lower() != folder.lower()
                        or chosen.name.lower() != name.lower()
                    ):
                        self.stats["changed"] += 1
                    self.stats["by_phase"][MatchPhase.EXACT_NAME] += 1

                return MatchResult(
                    chosen=chosen,
                    phase=MatchPhase.EXACT_NAME,
                    score=1000.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=len(exact_name_matches),
                    match_details={
                        "reason": "exact-name-locked",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                    },
                )

        # --- STEP 5: TOKEN MATCHING WITHIN LOCKED POOL ---
        token_match = rank_by_name_then_tokens(locked_pool, name, folder, klass, build)
        if token_match:
            logging.debug(f"FINAL MATCH: Wagon {name} -> {token_match.folder}/{token_match.name} Phase=KEY_TOKEN_ALL Score=900 Reason=token-match-locked")
            with self._lock:
                self.stats["resolved"] += 1
                if (
                    token_match.folder.lower() != folder.lower()
                    or token_match.name.lower() != name.lower()
                ):
                    self.stats["changed"] += 1
                self.stats["by_phase"][MatchPhase.KEY_TOKEN_ALL] += 1

            return MatchResult(
                chosen=token_match,
                phase=MatchPhase.KEY_TOKEN_ALL,
                score=900.0,
                target=self.extractor.extract_metadata(kind, name, folder),
                candidates_evaluated=len(locked_pool),
                match_details={
                    "reason": "token-match-locked",
                    "family": family,
                    "subtype": subtype,
                    "class": klass,
                    "build": build,
                },
            )

        # --- STEP 5.5: LOCAL FOLDER MATCHING ---
        # Try to find matches in the same folder first (for variety)
        local_folder_matches = [c for c in locked_pool if c.folder.lower() == folder.lower()]
        if local_folder_matches and not token_match:
            local_match = rank_by_name_then_tokens(local_folder_matches, name, folder, klass, build)
            if local_match:
                logging.debug(f"FINAL MATCH: Wagon {name} -> {local_match.folder}/{local_match.name} Phase=LOCAL_FOLDER Score=850 Reason=local-folder-match")
                with self._lock:
                    self.stats["resolved"] += 1
                    if (
                        local_match.folder.lower() != folder.lower()
                        or local_match.name.lower() != name.lower()
                    ):
                        self.stats["changed"] += 1
                    self.stats["by_phase"][MatchPhase.LOCAL_FOLDER] += 1

                return MatchResult(
                    chosen=local_match,
                    phase=MatchPhase.LOCAL_FOLDER,
                    score=850.0,
                    target=self.extractor.extract_metadata(kind, name, folder),
                    candidates_evaluated=len(local_folder_matches),
                    match_details={
                        "reason": "local-folder-match",
                        "family": family,
                        "subtype": subtype,
                        "class": klass,
                        "build": build,
                    },
                )

        # --- STEP 5.6: DIGIT NEAR MATCHING ---
        # Try to find matches with similar digit patterns
        if not token_match and not local_match:
            digit_near_matches = find_digit_near_matches(locked_pool, name)
            if digit_near_matches:
                digit_match = rank_by_name_then_tokens(digit_near_matches, name, folder, klass, build)
                if digit_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {digit_match.folder}/{digit_match.name} Phase=DIGIT_NEAR Score=800 Reason=digit-near-match")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            digit_match.folder.lower() != folder.lower()
                            or digit_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.DIGIT_NEAR] += 1

                    return MatchResult(
                        chosen=digit_match,
                        phase=MatchPhase.DIGIT_NEAR,
                        score=800.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(digit_near_matches),
                        match_details={
                            "reason": "digit-near-match",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        # --- STEP 5.7: WILDCARD MATCHING ---
        # Try wildcard matching for flexible patterns
        if not token_match and not local_match and not digit_match:
            wildcard_matches = find_wildcard_matches(locked_pool, name)
            if wildcard_matches:
                wildcard_match = rank_by_name_then_tokens(wildcard_matches, name, folder, klass, build)
                if wildcard_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {wildcard_match.folder}/{wildcard_match.name} Phase=WILDCARD_MATCH Score=750 Reason=wildcard-match")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            wildcard_match.folder.lower() != folder.lower()
                            or wildcard_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.WILDCARD_MATCH] += 1

                    return MatchResult(
                        chosen=wildcard_match,
                        phase=MatchPhase.WILDCARD_MATCH,
                        score=750.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(wildcard_matches),
                        match_details={
                            "reason": "wildcard-match",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        # --- STEP 5.8: SEMANTIC MATCHING ---
        # Try semantic similarity matching as a last resort before defaults
        if not token_match and not local_match and not digit_match and not wildcard_match:
            semantic_matches = find_semantic_matches(locked_pool, name)
            if semantic_matches:
                semantic_match = rank_by_name_then_tokens(semantic_matches, name, folder, klass, build)
                if semantic_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {semantic_match.folder}/{semantic_match.name} Phase=SEMANTIC_MATCH Score=700 Reason=semantic-match")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            semantic_match.folder.lower() != folder.lower()
                            or semantic_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.SEMANTIC_MATCH] += 1

                    return MatchResult(
                        chosen=semantic_match,
                        phase=MatchPhase.SEMANTIC_MATCH,
                        score=700.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(semantic_matches),
                        match_details={
                            "reason": "semantic-match",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        # --- STEP 5.9: PARTIAL TOKEN MATCHING ---
        # Try partial token matching for more flexible matching
        if not token_match and not local_match and not digit_match and not wildcard_match and not semantic_match:
            partial_token_matches = find_partial_token_matches(locked_pool, name)
            if partial_token_matches:
                partial_match = rank_by_name_then_tokens(partial_token_matches, name, folder, klass, build)
                if partial_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {partial_match.folder}/{partial_match.name} Phase=KEY_TOKEN_PARTIAL Score=650 Reason=partial-token-match")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            partial_match.folder.lower() != folder.lower()
                            or partial_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.KEY_TOKEN_PARTIAL] += 1

                    return MatchResult(
                        chosen=partial_match,
                        phase=MatchPhase.KEY_TOKEN_PARTIAL,
                        score=650.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(partial_token_matches),
                        match_details={
                            "reason": "partial-token-match",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        # --- STEP 6: STRICT DEFAULT FALLBACK ---
        # For defaults, require at least Subtype to match
        defaults_index = [
            a for a in self.index.assets if a.folder.lower().startswith("_defaults")
        ]
        default_match = pick_strict_default(
            defaults_index, wanted_role, family, subtype, klass, build
        )
        if default_match:
            logging.debug(f"FINAL MATCH: Wagon {name} -> {default_match.folder}/{default_match.name} Phase={phase} Score=600 Reason=strict-default")
            with self._lock:
                self.stats["resolved"] += 1
                if (
                    default_match.folder.lower() != folder.lower()
                    or default_match.name.lower() != name.lower()
                ):
                    self.stats["changed"] += 1
                phase = (
                    MatchPhase.DEFAULT_ENGINE
                    if wanted_role == "Engine"
                    else MatchPhase.DEFAULT_WAGON
                )
                self.stats["by_phase"][phase] += 1

            return MatchResult(
                chosen=default_match,
                phase=phase,
                score=600.0,
                target=self.extractor.extract_metadata(kind, name, folder),
                candidates_evaluated=len(defaults_index),
                match_details={
                    "reason": "strict-default",
                    "family": family,
                    "subtype": subtype,
                    "class": klass,
                    "build": build,
                },
            )

        # --- STEP 6.5: ENGINE NEAREST MATCH FALLBACK ---
        # For engines, if no matches found in locked pool, find nearest engine match
        if not default_match and wanted_role == "Engine" and kind == AssetKind.ENGINE:
            all_engines = [asset for asset in self.index.by_kind.get(AssetKind.ENGINE, [])]
            if all_engines:
                # Find the best engine match using name similarity
                engine_match = rank_by_name_then_tokens(all_engines, name, folder, klass, build)
                if engine_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {engine_match.folder}/{engine_match.name} Phase=GLOBAL_SCORE Score=550 Reason=engine-nearest-match")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            engine_match.folder.lower() != folder.lower()
                            or engine_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.GLOBAL_SCORE] += 1

                    return MatchResult(
                        chosen=engine_match,
                        phase=MatchPhase.GLOBAL_SCORE,
                        score=550.0,
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(all_engines),
                        match_details={
                            "reason": "engine-nearest-match",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        # --- STEP 7: FINAL UNRESOLVED ---
        # Last chance: For engines, try nearest match even if we get to final unresolved
        if wanted_role == "Engine" and kind == AssetKind.ENGINE:
            all_engines = [asset for asset in self.index.by_kind.get(AssetKind.ENGINE, [])]
            if all_engines:
                # Find the best engine match using name similarity
                engine_match = rank_by_name_then_tokens(all_engines, name, folder, klass, build)
                if engine_match:
                    logging.debug(f"FINAL MATCH: Wagon {name} -> {engine_match.folder}/{engine_match.name} Phase=GLOBAL_SCORE Score=500 Reason=engine-nearest-match-final")
                    with self._lock:
                        self.stats["resolved"] += 1
                        if (
                            engine_match.folder.lower() != folder.lower()
                            or engine_match.name.lower() != name.lower()
                        ):
                            self.stats["changed"] += 1
                        self.stats["by_phase"][MatchPhase.GLOBAL_SCORE] += 1

                    return MatchResult(
                        chosen=engine_match,
                        phase=MatchPhase.GLOBAL_SCORE,
                        score=500.0,  # Slightly lower score for final fallback
                        target=self.extractor.extract_metadata(kind, name, folder),
                        candidates_evaluated=len(all_engines),
                        match_details={
                            "reason": "engine-nearest-match-final",
                            "family": family,
                            "subtype": subtype,
                            "class": klass,
                            "build": build,
                        },
                    )

        logging.debug(f"FINAL MATCH: Wagon {name} -> UNRESOLVED Phase=UNRESOLVED Score=0 Reason=no-final-match")
        with self._lock:
            self.stats["unresolved"] += 1
            self.stats["by_phase"][MatchPhase.UNRESOLVED] += 1

        return MatchResult(
            chosen=None,
            phase=MatchPhase.UNRESOLVED,
            score=0.0,
            target=self.extractor.extract_metadata(kind, name, folder),
            candidates_evaluated=len(locked_pool) if 'locked_pool' in locals() else 0,
            match_details={
                "reason": "no-final-match",
                "family": family if 'family' in locals() else "",
                "subtype": subtype if 'subtype' in locals() else "",
                "class": klass if 'klass' in locals() else "",
                "build": build if 'build' in locals() else "",
            },
        )

class ConsistParser:
    """Parser for MSTS consist (.con) files."""

    def _read_lines_with_encoding(self, path):
        data = path.read_bytes()
        # BOM / NUL detection → UTF-16
        if (
            data.startswith(b"\xff\xfe")
            or data.startswith(b"\xfe\xff")
            or b"\x00" in data[:128]
        ):
            try:
                return data.decode("utf-16").splitlines()
            except UnicodeError:
                return data.decode("utf-16le", errors="ignore").splitlines()
        # fallbacks
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return data.decode(enc).splitlines()
            except UnicodeError:
                pass
        return data.decode("utf-8", errors="ignore").splitlines()

    @dataclass
    class ConsistEntry:
        """Represent a single asset reference in consist file."""

        index: int
        kind: AssetKind
        folder: str
        name: str
        kind_token: str
        line_content: str

    @dataclass
    class ParsedConsist:
        """Represent a parsed consist file."""

        path: Path
        filename: str
        entries: List["ConsistParser.ConsistEntry"]
        lines: List[str]

        def get_required_folders(self) -> Set[str]:
            """Get set of all folders referenced in this consist."""
            return {entry.folder for entry in self.entries if entry.folder}

    def parse_consist_file(self, consist_path: Path) -> ParsedConsist:
        try:
            lines = self._read_lines_with_encoding(consist_path)
        except Exception as e:
            logging.warning(f"Failed to read consist file {consist_path}: {e}")
            return self.ParsedConsist(consist_path, consist_path.name, [], [])

        entries = []

        block_type = None
        block_lines = []
        for i, line in enumerate(lines):
            # Detect block start
            if re.match(r"\s*Wagon\s*\(", line, re.IGNORECASE):
                block_type = "Wagon"
                block_lines = [line]
                continue
            elif re.match(r"\s*Engine\s*\(", line, re.IGNORECASE):
                block_type = "Engine"
                block_lines = [line]
                continue
            # If inside block, collect lines
            if block_type:
                block_lines.append(line)
                # Detect block end
                if line.strip() == ")":
                    # Search for asset data inside block
                    for j, block_line in enumerate(block_lines):
                        match = re.search(
                            r"(EngineData|WagonData)\s*\(([^)]*)\)",
                            block_line,
                            re.IGNORECASE,
                        )
                        if match:
                            kind_token = match.group(1)
                            inside = match.group(2).strip()
                            import shlex

                            try:
                                tokens = shlex.split(inside)
                            except Exception:
                                tokens = inside.split()
                            raw_name = tokens[0] if len(tokens) > 0 else ""
                            raw_folder = tokens[1] if len(tokens) > 1 else ""
                            kind = (
                                AssetKind.ENGINE
                                if kind_token.lower().startswith("engine")
                                else AssetKind.WAGON
                            )
                            entry = self.ConsistEntry(
                                index=i - len(block_lines) + j + 1,
                                kind=kind,
                                folder=raw_folder,
                                name=raw_name,
                                kind_token=kind_token,
                                line_content=block_line.strip(),
                            )
                            entries.append(entry)
                    block_type = None
                    block_lines = []

        return self.ParsedConsist(
            consist_path, consist_path.name, entries, [line.rstrip() for line in lines]
        )

    def parse_multiple_consists(self, consist_paths: List[Path]) -> List[ParsedConsist]:
        """Parse multiple consist files with parallel processing."""
        results = []

        if len(consist_paths) == 1:
            # Single file - no need for threading
            return [self.parse_consist_file(consist_paths[0])]

        # Parallel processing for multiple files
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_path = {
                executor.submit(self.parse_consist_file, path): path
                for path in consist_paths
            }

            for future in as_completed(future_to_path):
                try:
                    result = future.result(timeout=30)
                    results.append(result)
                except Exception as e:
                    path = future_to_path[future]
                    logging.error(f"Failed to parse {path}: {e}")

        return results


class MSSTResolver:
    """CLI wrapper for AssetResolver, handling config and consist resolution."""

    def __init__(self, config_path=None):
        # Load config if provided, else use defaults
        if config_path and hasattr(config_path, "exists") and config_path.exists():
            import json

            with open(config_path, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
            self.config = ScoreConfig(**config_dict)
        else:
            self.config = ScoreConfig()

        self.classifier = IndianRailwaysClassifier()
        self.resolver = AssetResolver(self.config, self.classifier)
        self.parser = ConsistParser()

    def _resolve_asset_worker(self, entry_data):
        """Worker function for parallel asset resolution."""
        kind, folder, name = entry_data
        return self.resolver.resolve_asset(kind, folder, name)

    def resolve_consists(
        self, consists_path, trainset_dir, dry_run=False, explain=False
    ):
        start_time = time.time()
        consists_path = Path(consists_path)
        trainset_dir = Path(trainset_dir)

        # Find all .con files
        consist_files = list(consists_path.glob("*.con"))
        if not consist_files:
            logging.error(f"No consist files found in {consists_path}")
            return {"unresolved": 0}

        logging.info(f"Found {len(consist_files)} consist files to process")

        # Parse consists
        consists = self.parser.parse_multiple_consists(consist_files)

        # Log parsing results
        total_entries = sum(len(c.entries) for c in consists)
        logging.info(
            f"Parsed {len(consists)} consists with {total_entries} asset references"
        )

        # Build asset index
        assets_indexed = self.resolver.build_asset_index(trainset_dir)

        # Prepare all entries for parallel processing
        all_entries = []
        entry_info = []  # Keep track of which result belongs to which entry (filename, entry, original_index)

        for consist in consists:
            for entry in consist.entries:
                idx = len(all_entries)
                all_entries.append((entry.kind, entry.folder, entry.name))
                entry_info.append((consist.filename, entry, idx))

        # Resolve all entries using parallel processing
        logging.info("Starting STRICT ATTRIBUTE LOCKING resolution process with optimized parallel processing...")
        
        # Use ProcessPoolExecutor for CPU-bound asset resolution with dynamic worker scaling
        cpu_count = multiprocessing.cpu_count()
        max_workers = min(cpu_count * 2, 16)  # Cap at 16 to avoid excessive overhead
        logging.info(f"Using ProcessPoolExecutor with {max_workers} workers (CPU cores: {cpu_count})")
        
        results = []
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all entries for parallel processing and track original index
            future_to_entry = {
                executor.submit(self._resolve_asset_worker, entry_data): (entry_data, info)
                for entry_data, info in zip(all_entries, entry_info)
            }

            # Collect results as they complete
            raw_results = []
            for future in as_completed(future_to_entry):
                entry_data, (consist_filename, entry, orig_idx) = future_to_entry[future]
                try:
                    result = future.result(timeout=30)  # 30 second timeout per asset
                    raw_results.append((orig_idx, entry_data, result, consist_filename, entry))
                except Exception as e:
                    logging.error(f"Error processing asset {entry.folder}/{entry.name}: {str(e)}")
                    failed_result = MatchResult(
                        chosen=None,
                        phase=MatchPhase.UNRESOLVED,
                        score=0.0,
                        target=self.resolver.extractor.extract_metadata(entry.kind, entry.name, entry.folder),
                        candidates_evaluated=0,
                        match_details={"reason": f"parallel_processing_error: {str(e)}"}
                    )
                    raw_results.append((orig_idx, entry_data, failed_result, consist_filename, entry))

            # Sort results by original order to ensure consistency
            raw_results.sort(key=lambda x: x[0])
            
            # Process results in deterministic order
            seen_explains = set()
            # Buckets for explain messages - we will print/log changed first, unresolved next, unchanged last
            changed_explains: List[Tuple[int, str]] = []
            unresolved_explains: List[Tuple[int, str, str]] = []  # (orig_idx, explain_msg, diff_text)
            unchanged_explains: List[Tuple[int, str]] = []
            for orig_idx, entry_data, result, consist_filename, entry in raw_results:
                try:
                    results.append(result)
                    
                    if explain:
                        details = result.match_details
                        resolved_info = (
                            f"{result.chosen.folder}/{result.chosen.name}"
                            if result.chosen
                            else "UNRESOLVED"
                        )

                        # Prefer locked class from attribute detection; fallback to chosen asset metadata
                        resolved_class = ""
                        locked_class = details.get("class", "") if details else ""
                        if locked_class:
                            resolved_class = locked_class
                        elif result.chosen:
                            meta = result.chosen.metadata
                            if getattr(meta, "freight_type", ""):
                                resolved_class = meta.freight_type
                            elif getattr(meta, "coach_type", ""):
                                resolved_class = meta.coach_type
                            elif getattr(meta, "engine_class", ""):
                                resolved_class = meta.engine_class
                            else:
                                resolved_class = ""
                        else:
                            resolved_class = ""

                        # Show both detected (locked) attributes and chosen asset attributes
                        chosen_meta = {
                            "family": "",
                            "subtype": "",
                            "class": "",
                            "build": "",
                        }
                        if result.chosen and getattr(result.chosen, "metadata", None):
                            meta = result.chosen.metadata
                            chosen_meta["family"] = getattr(meta, "family", "") or ""
                            chosen_meta["subtype"] = getattr(meta, "subtype", "") or ""
                            # prefer freight/coach/engine class fields
                            chosen_meta["class"] = (
                                getattr(meta, "freight_type", "")
                                or getattr(meta, "coach_type", "")
                                or getattr(meta, "engine_class", "")
                                or ""
                            )
                            chosen_meta["build"] = getattr(meta, "build", "") or ""

                        locked_meta = {
                            "family": details.get("family", "") if details else "",
                            "subtype": details.get("subtype", "") if details else "",
                            "class": details.get("class", "") if details else "",
                            "build": details.get("build", "") if details else "",
                        }

                        # Build per-attribute display showing LOCKED -> CHOSEN
                        attr_pairs = []
                        for label, key in (
                            ("Family", "family"),
                            ("Subtype", "subtype"),
                            ("Class", "class"),
                            ("Build", "build"),
                        ):
                            lval = locked_meta.get(key, "") or "-"
                            cval = chosen_meta.get(key, "") or "-"
                            if lval != cval:
                                attr_pairs.append(f"{label}: {lval} -> {cval}")
                            else:
                                attr_pairs.append(f"{label}: {lval}")

                        # Compose a compact explain message that clearly shows changed attrs
                        attrs_text = " | ".join(attr_pairs)

                        # Build a stable key from the original entry data including the original index
                        entry_key = (
                            entry_data[0].value if hasattr(entry_data[0], 'value') else str(entry_data[0]),
                            entry_data[1],
                            entry_data[2],
                            orig_idx,
                        )

                        explain_msg = (
                            f"{entry_key[0]} {entry_key[1]}/{entry_key[2]} -> {resolved_info} "
                            f"Phase={result.phase.name} Score={result.score:.0f} "
                            f"Reason={details.get('reason', '')} | {attrs_text}"
                        )

                        # Collect explain messages into buckets so we can control display order
                        if entry_key not in seen_explains:
                            seen_explains.add(entry_key)
                            # Determine category: changed (asset resolved to different target),
                            # unresolved (no chosen), or unchanged (already matching)
                            if result.chosen is None:
                                # Build a small diff summary for unresolved (what was requested)
                                # Show locked vs requested attributes
                                diff_text = attrs_text
                                unresolved_explains.append((orig_idx, explain_msg, diff_text))
                            else:
                                if result.is_changed:
                                    changed_explains.append((orig_idx, explain_msg))
                                else:
                                    unchanged_explains.append((orig_idx, explain_msg))
                except Exception as e:
                    logging.error(f"Failed to resolve asset {entry_data}: {e}")
                    # Create unresolved result for failed entries
                    failed_result = MatchResult(
                        chosen=None,
                        phase=MatchPhase.UNRESOLVED,
                        score=0.0,
                        target=self.resolver.extractor.extract_metadata(entry.kind, entry.name, entry.folder),
                        candidates_evaluated=0,
                        match_details={"reason": f"parallel_processing_error: {str(e)}"}
                    )
                    results.append(failed_result)

        # Emit explain messages in the requested order: changed, unresolved (with diffs), unchanged
        if explain:
            # Sort each bucket by original index to preserve file order
            changed_explains.sort(key=lambda x: x[0])
            unresolved_explains.sort(key=lambda x: x[0])
            unchanged_explains.sort(key=lambda x: x[0])

            # First print changed explains
            if changed_explains:
                logging.info("--- CHANGED ENTRIES ---")
                print("--- CHANGED ENTRIES ---")
            for _idx, msg in changed_explains:
                print(msg)
                logging.info(f"FINAL MATCH: {msg}")

            # Then print unresolved explains including diffs
            if unresolved_explains:
                logging.info("--- UNRESOLVED ENTRIES ---")
                print("--- UNRESOLVED ENTRIES ---")
            for _idx, msg, diff in unresolved_explains:
                # Show the explain msg and a compact diff line
                print(msg)
                logging.info(f"FINAL MATCH: {msg}")
                if diff:
                    print(f"  DIFF: {diff}")
                    logging.info(f"FINAL DIFF: {diff}")

            # Finally, print unchanged explains
            if unchanged_explains:
                logging.info("--- UNCHANGED (already matching) ---")
                print("--- UNCHANGED (already matching) ---")
            for _idx, msg in unchanged_explains:
                print(msg)
                logging.info(f"FINAL MATCH: {msg}")

        logging.info(f"Asset resolution completed. Processed {len(results)} entries.")        # Write results if not dry_run
        if not dry_run:
            logging.info("Writing changes to consist files...")
            self._write_results(consists, results)
        else:
            logging.info("DRY-RUN mode: No changes written to files")

        # Final stats
        duration = time.time() - start_time
        stats = self._generate_final_statistics(results, duration, assets_indexed)
        self._display_summary(stats, dry_run)

        return stats

    def _write_results(self, consists, results):
        """Write resolution results back to consist files."""
        result_iter = iter(results)
        files_modified = 0

        for consist in consists:
            lines_modified = False
            changes_made = 0
            new_lines = consist.lines.copy()

            for entry in consist.entries:
                result = next(result_iter)

                if result.is_resolved and result.is_changed:
                    chosen = result.chosen
                    # Modify the line
                    old_line = new_lines[entry.index]
                    new_line = (
                        f'    {entry.kind_token} ( {chosen.name} "{chosen.folder}" )'
                    )
                    new_lines[entry.index] = new_line
                    lines_modified = True
                    changes_made += 1
                    logging.info(
                        f"CHANGE: {consist.filename} line {entry.index + 1}: {entry.folder}/{entry.name} -> {chosen.folder}/{chosen.name}"
                    )

            # Write file if modified
            if lines_modified:
                try:
                    with open(consist.path, "w", encoding="utf-8") as f:
                        f.write("\n".join(new_lines) + "\n")
                    files_modified += 1
                    logging.info(
                        f"Updated consist file: {consist.path} ({changes_made} changes)"
                    )
                except Exception as e:
                    logging.error(f"Failed to write {consist.path}: {e}")

        logging.info(
            f"File writing completed. Modified {files_modified} consist files."
        )

    def _generate_final_statistics(self, results, duration, assets_indexed):
        """Generate comprehensive final statistics."""
        total = len(results)
        resolved = sum(1 for r in results if r.is_resolved)
        changed = sum(1 for r in results if r.is_changed)
        unresolved = total - resolved
        already_matching = resolved - changed

        phase_counts = Counter(r.phase for r in results)

        return {
            "total_processed": total,
            "resolved": resolved,
            "changed": changed,
            "unresolved": unresolved,
            "already_matching": already_matching,
            "assets_indexed": assets_indexed,
            "duration_seconds": duration,
            "resolution_rate": resolved / total if total > 0 else 0,
            "change_rate": changed / total if total > 0 else 0,
            "phase_breakdown": dict(phase_counts),
            "resolver_stats": self.resolver.index.get_statistics(),
        }

    def _display_summary(self, stats, dry_run):
        """Display comprehensive summary with colors and log to file."""
        summary_lines = []

        header = "MSTS ASSET RESOLVER - STRICT ATTRIBUTE LOCKING v2.3.0 - FINAL SUMMARY"
        separator = "=" * 80

        # Build summary content
        summary_lines.append("")
        summary_lines.append(separator)
        summary_lines.append(header)
        summary_lines.append(separator)
        summary_lines.append(
            f"Files Processed: {stats['total_processed']} asset references"
        )
        summary_lines.append(
            f"Assets Indexed: {stats['assets_indexed']} trainset assets"
        )
        summary_lines.append(
            f"Processing Time: {stats['duration_seconds']:.1f} seconds"
        )
        summary_lines.append("")

        # Resolution statistics
        summary_lines.append(
            f"RESOLVED: {stats['resolved']} ({stats['resolution_rate']*100:.1f}%)"
        )
        summary_lines.append(
            f"CHANGED: {stats['changed']} ({stats['change_rate']*100:.1f}%)"
        )
        already_matching_rate = (
            stats["already_matching"] / stats["total_processed"]
            if stats["total_processed"] > 0
            else 0
        )
        summary_lines.append(
            f"ALREADY MATCHING: {stats['already_matching']} ({already_matching_rate*100:.1f}%)"
        )
        summary_lines.append(
            f"UNRESOLVED: {stats['unresolved']} ({(1 - stats['resolution_rate'])*100:.1f}%)"
        )
        summary_lines.append("")

        # Phase breakdown
        summary_lines.append("Resolution Method Breakdown:")
        for phase, count in stats["phase_breakdown"].items():
            percentage = (count / stats["total_processed"]) * 100
            summary_lines.append(f"  • {phase.name}: {count} ({percentage:.1f}%)")
        summary_lines.append("")

        # Recommendations
        summary_lines.append("Recommendations:")
        if stats["unresolved"] > 0:
            summary_lines.append(
                "  • Review UNRESOLVED items for missing trainset assets or incomplete attribute detection"
            )
        if stats["changed"] > 0 and dry_run:
            summary_lines.append(
                f"  • Run without --dry-run to apply {stats['changed']} changes"
            )
        if stats["resolution_rate"] > 0.8:
            summary_lines.append(
                f"  • Excellent! {stats['resolution_rate']*100:.1f}% resolution rate achieved with strict matching"
            )

        summary_lines.append("STRICT ATTRIBUTE LOCKING FEATURES:")
        summary_lines.append(
            "  • Family, Subtype, Class, Build derived and locked from consist entries"
        )
        summary_lines.append(
            "  • Only trainset assets with EXACT attribute matches considered"
        )
        summary_lines.append("  • Default fallback requires at least Subtype match")
        summary_lines.append(
            "  • Entries with no detectable attributes marked UNRESOLVED"
        )
        summary_lines.append(separator)

        # Display on terminal with colors
        print("\n" + "=" * 80)
        self._print_status(
            "MSTS ASSET RESOLVER - STRICT ATTRIBUTE LOCKING v2.3.0 - FINAL SUMMARY",
            Fore.YELLOW + Style.BRIGHT,
        )
        print("=" * 80)

        # Basic statistics
        print(f"Files Processed: {stats['total_processed']} asset references")
        print(f"Assets Indexed: {stats['assets_indexed']} trainset assets")
        print(f"Processing Time: {stats['duration_seconds']:.1f} seconds")
        print()

        # Resolution statistics
        self._print_colored_stat(
            "RESOLVED", stats["resolved"], stats["resolution_rate"], Fore.GREEN
        )
        self._print_colored_stat(
            "CHANGED", stats["changed"], stats["change_rate"], Fore.BLUE
        )
        self._print_colored_stat(
            "ALREADY MATCHING",
            stats["already_matching"],
            already_matching_rate,
            Fore.YELLOW,
        )
        self._print_colored_stat(
            "UNRESOLVED", stats["unresolved"], 1 - stats["resolution_rate"], Fore.RED
        )
        print()

        # Phase breakdown
        print("Resolution Method Breakdown:")
        for phase, count in stats["phase_breakdown"].items():
            percentage = (count / stats["total_processed"]) * 100
            print(f"  • {phase.name}: {count} ({percentage:.1f}%)")
        print()

        # Recommendations
        print("Recommendations:")
        if stats["unresolved"] > 0:
            self._print_status(
                "  • Review UNRESOLVED items for missing trainset assets or incomplete attribute detection",
                Fore.RED,
            )
        if stats["changed"] > 0 and dry_run:
            self._print_status(
                f"  • Run without --dry-run to apply {stats['changed']} changes",
                Fore.GREEN,
            )
        if stats["resolution_rate"] > 0.8:
            self._print_status(
                f"  • Excellent! {stats['resolution_rate']*100:.1f}% resolution rate achieved with strict matching",
                Fore.GREEN,
            )

        print()
        print("STRICT ATTRIBUTE LOCKING FEATURES:")
        self._print_status(
            "  • Family, Subtype, Class, Build derived and locked from consist entries",
            Fore.CYAN,
        )
        self._print_status(
            "  • Only trainset assets with EXACT attribute matches considered",
            Fore.CYAN,
        )
        self._print_status(
            "  • Default fallback requires at least Subtype match", Fore.CYAN
        )
        self._print_status(
            "  • Entries with no detectable attributes marked UNRESOLVED", Fore.CYAN
        )

        print("=" * 80)

        # Log complete summary to file
        for line in summary_lines:
            logging.info(f"SUMMARY: {line}")

    def _print_colored_stat(self, label: str, value: int, percentage: float, color):
        """Print a colored statistic line."""
        if COLORS_AVAILABLE:
            print(f"{color}{label}: {value} ({percentage*100:.1f}%)")
        else:
            print(f"{label}: {value} ({percentage*100:.1f}%)")

    def _print_status(self, message: str, color=None):
        """Print a status message with optional color."""
        if COLORS_AVAILABLE and color:
            print(color + message)
        else:
            print(message)


def main():
    """Main application entry point."""
    parser = argparse.ArgumentParser(
        description="Advanced MSTS Asset Resolver with STRICT ATTRIBUTE LOCKING (v2.3.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python consistEditor.py consists/ trainsets/
  python consistEditor.py consists/ trainsets/ --dry-run --explain
  python consistEditor.py consists/ trainsets/ --config config.json

NEW v2.3.0 STRICT ATTRIBUTE LOCKING:
  - DERIVE AND LOCK: Extract Family, Subtype, Class, Build from consist entry
  - STRICT FILTERING: Only consider trainset assets with EXACT matches for all four attributes
  - NAME-FIRST: Exact name match within locked attributes (highest priority)
  - TOKEN MATCHING: Apply name/token ranking within locked attributes
  - DEFAULT STRICT: Require at least Subtype match for defaults
  - UNRESOLVED: If no attributes detected or no matches found, mark as UNRESOLVED

PREVIOUS ENHANCEMENTS (v2.2.5):
  - ENHANCED: Comprehensive freight detection based on 533 real wagon files analysis
  - NEW: Support for BSAM series (168 wagons), CON_ containers (42 wagons), ASMI series (37 wagons)
  - NEW: Enhanced freight types: CEMENT, COIL, COAL, MILKTANKER, BCCN, BRNA, BRW, BCA, BCB, BTI
        """,
    )

    parser.add_argument(
        "consists_path",
        type=Path,
        help="Path to consists directory containing .con files",
    )
    parser.add_argument(
        "trainset_dir",
        type=Path,
        help="Path to trainset directory containing asset folders",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without modifying files"
    )
    parser.add_argument(
        "--explain", action="store_true", help="Show detailed resolution information"
    )
    parser.add_argument("--config", type=Path, help="Path to configuration JSON file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible results (default: 42)")

    args = parser.parse_args()

    # Delete previous log file FIRST - with retry for Windows file locking
    log_path = Path("msts_resolver.log")
    if log_path.exists():
        import time

        for attempt in range(3):
            try:
                log_path.unlink()
                break
            except (PermissionError, OSError) as e:
                if attempt < 2:  # Try up to 3 times
                    time.sleep(0.5)  # Short delay between retries
                else:
                    # If all retries fail, try to rename it instead
                    try:
                        import random

                        backup_name = (
                            f"msts_resolver_old_{random.randint(1000,9999)}.log"
                        )
                        log_path.rename(backup_name)
                    except Exception:
                        # If everything fails, just continue - logs will append
                        pass
            except Exception:
                break

    # Set up logging AFTER deleting old log
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("msts_resolver.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    try:
        # Create resolver instance
        resolver = MSSTResolver(args.config)

        # Show startup message
        import random
        import time

        # Use fixed seed for consistent results, or configurable seed
        if hasattr(args, 'seed') and args.seed is not None:
            random.seed(args.seed)
            logging.info(f"Random seed set to: {args.seed}")
        else:
            # Use fixed seed for consistent results instead of time-based
            random.seed(42)  # Fixed seed for reproducible results
            logging.info(f"Random seed initialized: 42 (fixed for consistency)")

        startup_msg = "MSTS Asset Resolver v2.3.0 - STRICT ATTRIBUTE LOCKING"
        print(startup_msg)
        print("=" * 80)
        logging.info(startup_msg)
        logging.info("=" * 80)
        logging.info("NEW v2.3.0 STRICT ATTRIBUTE LOCKING:")
        logging.info(
            "- DERIVE AND LOCK: Extract Family, Subtype, Class, Build from consist entry"
        )
        logging.info(
            "- STRICT FILTERING: Only consider trainset assets with EXACT matches for all four attributes"
        )
        logging.info(
            "- NAME-FIRST: Exact name match within locked attributes (highest priority)"
        )
        logging.info(
            "- TOKEN MATCHING: Apply name/token ranking within locked attributes"
        )
        logging.info("- DEFAULT STRICT: Require at least Subtype match for defaults")
        logging.info(
            "- UNRESOLVED: If no attributes detected or no matches found, mark as UNRESOLVED"
        )
        logging.info("PREVIOUS v2.2.5 ENHANCEMENTS:")
        logging.info(
            "- ENHANCED: Comprehensive freight detection based on 533 real wagon files analysis"
        )
        logging.info(
            "- NEW: Support for BSAM series (168 wagons), CON_ containers (42 wagons), ASMI series (37 wagons)"
        )
        logging.info(
            "- NEW: Enhanced freight types: CEMENT, COIL, COAL, MILKTANKER, BCCN, BRNA, BRW, BCA, BCB, BTI"
        )
        # Random seed already initialized above for consistency

        # Run resolution
        results = resolver.resolve_consists(
            consists_path=args.consists_path,
            trainset_dir=args.trainset_dir,
            dry_run=args.dry_run,
            explain=args.explain,
        )

        # Exit with appropriate code
        if results["unresolved"] > 0:
            sys.exit(1)  # Some assets unresolved
        else:
            sys.exit(0)  # All assets resolved

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()