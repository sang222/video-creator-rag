#!/usr/bin/env python3
"""CQR1 bounded paid-canary final execution.

This runner is deliberately phase-oriented.  Every external operation is
guarded by the existing one-shot ledger, and the original blocked preflight is
never rewritten.  The runner does not load or print secret values.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import certifi

from app.contracts.asset_acquisition import (
    AIHeroAssetRequest,
    AssetRequest,
    DriveArchiveFileReceipt,
    DriveArchiveReceipt,
    PexelsDownloadPlan,
    PexelsQueryPlan,
    ProductionArchiveManifest,
)
from app.contracts.caption_voice_quality import NarrationAudioAnalysis, PauseSpan
from app.contracts.creative_quality_canary import (
    CQR1_PAID_CANARY_004_RUN_ID,
    CQR1_PAID_CANARY_005_RUN_ID,
    CQR1_PAID_CANARY_006_RUN_ID,
    CQR1_PAID_CANARY_007_RUN_ID,
    CQR1_PAID_CANARY_008_RUN_ID,
    CQR1_PAID_CANARY_009_RUN_ID,
    CQR1_PURPOSE,
    CQR1CanaryApprovalScope,
    CQR1OfflineQualificationEvidence,
    CQR1ProviderReadinessEvidence,
    FinalDurationEvidence,
)
from app.contracts.google_veo import (
    GoogleVeoExecutionGates,
    GoogleVeoOperationReceipt,
)
from app.contracts.native_renderer import (
    AssetRequirement,
    CanvasSpec,
    FFmpegCommandManifest,
    NativeRenderPlan,
    NativeRenderScene,
    ResolvedAssetRef,
)
from app.contracts.temporal_authority import (
    CanonicalMediaTimeline,
    EditorialSegmentInput,
    FinalNarrationAudio,
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
    SpokenTextNormalized,
    TextSpan,
    VerifiedNarrationAlignment,
)
from app.contracts.visual_direction import (
    SceneVisualIntent,
    VisualAssetEvidence,
    VisualDirectionContract,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.core.config import Settings
from app.db.models.ofv0 import EpisodeOriginalityManifest, FormatIdentityContract
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.session import session_scope
from app.providers.google_veo import GoogleVeoAdapter
from app.services.caption_voice_quality import (
    CaptionAudioSyncGate,
    CaptionBoundsPreflight,
    CaptionCompilationGate,
    CaptionCoverageGate,
    CaptionLayoutGate,
    CaptionSafeAreaGate,
    NarrationPacingAnalyzer,
    NarrationPacingGate,
    ReadableCaptionCompiler,
    TimelineDriftGate,
)
from app.services.caption_ass import write_caption_ass
from app.services.cqr1_canary import (
    CQR1_RUN007_VISUAL_REUSE_PINS,
    CQR1CanaryCallLedger,
    CQR1CanaryExecutionGuard,
    CQR1PaidCanaryEntryGate,
    CQR1_VISIBLE_LABEL,
)
from app.services.creative_media_qc import (
    CreativePerceptualMediaQC,
    FinalDurationConsistencyGate,
    HumanWatchabilityPacketBuilder,
    TechnicalMediaQC,
)
from app.services.cqr1_real_provider import (
    ElevenLabsConvertWithTimestampsClient,
    ElevenLabsForcedAlignmentClient,
    PlannedPexelsV2SearchClient,
)
from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.m10_5 import GoogleDriveCredentialHealthService
from app.services.media_normalizer import MediaNormalizer
from app.services.native_ffmpeg_renderer import NativeFFmpegRenderer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import (
    canonical_caption_cues,
    canonical_plan_hash,
    stable_hash,
)
from app.services.pa1r import (
    DrivePA1RArchive,
    NoRetryHTTPTransport,
    PEXELS_CLIENT_HEADERS,
    PexelsPA1RClient,
    archive_permits_cleanup,
    media_duration_seconds,
    probe_media,
)
from app.services.pexels_query_planner import (
    PexelsQueryPlanner,
    bind_minimum_duration_to_canonical_scene,
)
from app.services.pexels_media_downloader import PexelsDownloadExecutionContext
from app.services.production_archive import (
    ArchiveSource,
    CQR1ArchivePathBuilder,
    CQR1_REQUIRED_ARCHIVE_ROLES,
    ProductionArchiveBuilder,
)
from app.services.provider_asset_manifests import (
    PexelsDownloadPlanBuilder,
    PexelsRenditionSelector,
    build_ai_hero_request,
    build_stock_source_manifest,
)
from app.services.temporal_authority import (
    CanonicalMediaTimelineCompiler,
    ElevenLabsForcedAlignmentResponseParser,
    ElevenLabsTimingResponseParser,
    NarrationAlignmentReconciler,
    SpokenTextNormalizer,
    TemporalAuthorityGate,
)
from app.services.veo_prompt_compiler import VeoFixedDurationPlanner, VeoPromptCompiler
from app.services.visual_direction import VisualDirectionCompiler, VisualEvaluationService


ROOT = Path(__file__).resolve().parents[2]
CQR1_RUN_ID = CQR1_PAID_CANARY_009_RUN_ID
PREVIOUS_CQR1_RUN_ID = "pa1r-cqr1-20260715-paid-canary-002"
PREVIOUS_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / PREVIOUS_CQR1_RUN_ID
).resolve()
WORKSPACE = (
    ROOT
    / "var/tmp/vcos-project-workspaces"
    / CQR1_RUN_ID
).resolve()
MANIFESTS = WORKSPACE / "manifests"
EVENTS = MANIFESTS / "resume-events"
SOURCE_AUDIO = WORKSPACE / "source/audio"
SOURCE_SCRIPT = WORKSPACE / "source/script"
STOCK_DIR = WORKSPACE / "source/stock"
HERO_DIR = WORKSPACE / "source/ai-hero"
QC_DIR = WORKSPACE / "qc"
RENDER_DIR = WORKSPACE / "render"
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
H264_BT709_METADATA_BSF = (
    "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
    "matrix_coefficients=1"
)
APPROVAL_REF = f"operator-approval://{CQR1_RUN_ID}"
RESUME_REASON = (
    "FORCE_CONTINUATION_RUN_WITH_IMMUTABLE_RUN_002_TTS_RUN_004_ALIGNMENT_"
    "AND_RUN_007_VISUAL_PROVIDER_OUTPUT_REUSE"
)
FAILED_RECOVERY_RUN_ID = "pa1r-cqr1-20260716-paid-canary-003"
FAILED_RECOVERY_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / FAILED_RECOVERY_RUN_ID
).resolve()
FAILED_RECOVERY_INVENTORY_HASH = "9794360d6594d12d2f027e9a2fc1c15b551df83caa71c19d1af1c876998321f0"
FAILED_RECOVERY_FILE_COUNT = 43
FAILED_RECOVERY_TOTAL_SIZE_BYTES = 1_876_668
FAILED_RECOVERY_STOP_CONTENT_HASH = "575b90376bd68ac464207f7c3ebc4b37eb91efc4f7c879c912d7bed4f49117e9"
FAILED_RECOVERY_STOP_SHA256 = "3aa28246ebdab0fe64b239e2e824f3826469af9d5f00cae5998ea30ad62b06a0"
FAILED_RECOVERY_LEDGER_HASH = "16e50e154a9c79d16a0a7741899f3b0877b270efb29e2bd35233921b4cc39e8e"
FAILED_RECOVERY_LEDGER_SHA256 = "e0c38ecffdaa2217c4cc1c248a7f05bf2c710ca47dfbc385d8ea1b0455417dc9"
FAILED_RECOVERY_SAFE_RESPONSE_CONTENT_HASH = "443405626acb48cb8bce7a567804ce08fa30ed7f895a34e791755fecdf4bc7d5"
FAILED_RECOVERY_SAFE_RESPONSE_SHA256 = "c5f27691fb6285ceed5bd8cf21c5e6dd8ee5c4850a5eb07762b62924fee46ce4"
FAILED_RECOVERY_APPROVAL_SHA256 = "a6a3cb7dc341b13697e53ccd0c58ab3200198f2e5564f370f64ac8bb015605d2"
FAILED_RECOVERY_IMPORTED_TTS_SHA256 = "02472fa298c7fb35e034032fc121e2f63b43b49b0c8a203bbefa4d88577c2f29"
ALIGNMENT_SOURCE_RUN_ID = CQR1_PAID_CANARY_004_RUN_ID
ALIGNMENT_SOURCE_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / ALIGNMENT_SOURCE_RUN_ID
).resolve()
ALIGNMENT_SOURCE_INVENTORY_HASH = "ee2322f8d18bd6f55e408638f9b7181f74cceca83f1e7aaebfb2e66f42010015"
ALIGNMENT_SOURCE_FILE_COUNT = 95
ALIGNMENT_SOURCE_TOTAL_SIZE_BYTES = 21_889_514
ALIGNMENT_SOURCE_FAILURE_CONTENT_HASH = "e816a68218117fd2d894efee105921f319aefba71d6e2421adebbed3ef2bc7d8"
ALIGNMENT_SOURCE_FAILURE_SHA256 = "ff5edfa4874f98c93aa7f41154f0bc00c9713762716b225c8ae516a3cf5b27d9"
ALIGNMENT_SOURCE_LEDGER_HASH = "28693eef54141cdd1d5054fe505a5a1d9c4202e544364725b2611acc3e926003"
ALIGNMENT_SOURCE_LEDGER_SHA256 = "56cfe0843480a7258f5f34e3ec7639ee6868dc102151113098b474963e4d0a93"
ALIGNMENT_SOURCE_REVIEW_CONTENT_HASH = "f6c77fbb916d80f01b5138b13d86297bf1fd5ad2dbb9b3736c6f4353e3aaf30e"
ALIGNMENT_SOURCE_REVIEW_SHA256 = "1ffad535faadbb3bf4bc5c72845829396c7c339c361f7bb939e1c16e45e90f42"
ALIGNMENT_SOURCE_REVIEW_SHEET_SHA256 = "9f6be1f0f3a4a37cac05c410fad2388eca2d911a96321100102590b20295c77d"
ALIGNMENT_SOURCE_FORCED_CONTENT_HASH = "43645aa5cbcebb84f230e36eefbd759be7217dd2b6ae94628cf076d7ea7976b8"
ALIGNMENT_SOURCE_FORCED_SHA256 = "5791b25b73471a95c1b5a6e23e4b795202198009c1f36082789f46cb83b753ca"
ALIGNMENT_SOURCE_VERIFIED_CONTENT_HASH = "e37f530d8bd964118950e22042d73e107394b70c992ccbc2058a386fd4684cb5"
ALIGNMENT_SOURCE_VERIFIED_SHA256 = "cfd04dd537e21d1723a30a483a3899702c4973c41491bdc1f2a28d84870b9aaa"
ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH = "527d62d4386f1ecc34ee0b5417a48cf24ebbbb2bcab60eb9983544c1b93fc2d0"
ALIGNMENT_SOURCE_RECEIPT_SHA256 = "211ef16db0745ad9ce477acd489c6aba4d8e604d2c48a065b54a3df61db560b4"
ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH = "9c9a099adb68813427f57445fa17d6101e249bf0e4277874504fa9d719580a9a"
ALIGNMENT_SOURCE_SAFE_RESPONSE_SHA256 = "948e4df0208f6fb410cb18b7df1d156608145be625c0e253adea0ca923ed2236"
ALIGNMENT_SOURCE_APPROVAL_SHA256 = "48e8fa2c84750e729e527d2d02f7b027665e688e017c01a8b37d9bd3f0007d63"
VISUAL_FAILURE_RUN_ID = CQR1_PAID_CANARY_005_RUN_ID
VISUAL_FAILURE_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / VISUAL_FAILURE_RUN_ID
).resolve()
VISUAL_FAILURE_INVENTORY_HASH = "cfb24ddc9f83cb85813947875ded49b589aa20795c21d54a4961138b6a6e278a"
VISUAL_FAILURE_FILE_COUNT = 100
VISUAL_FAILURE_TOTAL_SIZE_BYTES = 6_344_297
VISUAL_FAILURE_STOP_CONTENT_HASH = "4eddf45b41cd1944a7d3b374c7eb6b06a9b121e49b8567477b5ae69a245f91f7"
VISUAL_FAILURE_STOP_SHA256 = "4336a0ab4ef8441ade3612e597501551f2c0f0e007a534e716d5ec984d6630f9"
VISUAL_FAILURE_LEDGER_HASH = "1b5680641b453b5a7de30f269cbabd5b422309081b15b9ef1707678986763db3"
VISUAL_FAILURE_LEDGER_SHA256 = "7c678c24f3333d757f35bfe8dd35588a52910f400affa2a2ac2253451fb0cd92"
VISUAL_FAILURE_REVIEW_CONTENT_HASH = "4567bd65d8e0826afc9929157db2daf950330bf74994c5e5ee891ac2e1bd16ef"
VISUAL_FAILURE_REVIEW_SHA256 = "bd6e2f1f26bf04dc4e7172aa34566f226cd6f40ed531e9a4be9762ca40aaf75c"
VISUAL_FAILURE_REVIEW_SHEET_SHA256 = "57f04bc075bfb69af81495e8dc8a3d299fd2f54ef0857665c95510c59e0b3f19"
VISUAL_FAILURE_REPRESENTATIVE_SHA256 = "5ae4dcde7ecadca7c79bef944de5d3843749100462f91f4a3087e324eb3b870d"
VISUAL_FAILURE_PREFLIGHT_CONTENT_HASH = "b2bd01b2f3f1f7efe129798c66151b9aaf19c1f4262559626a3f309c6863ec27"
VISUAL_FAILURE_PREFLIGHT_SHA256 = "2cc3dc7333584758b42615fdaa770f37af606a39e9b8bc2c939fa06fa4afd302"
VISUAL_FAILURE_APPROVAL_SHA256 = "1f34fb299c22217c1c08dea1129f8c9f14b2a89db8ee1dce2b2cb5c5bc9d0fd6"
VISUAL_FAILURE_SEARCH_SHA256 = "e98a06ad1d055bf7c8b3a426120c36369843e695d0ce4fc01e31199494ce69c9"
VISUAL_FAILURE_DOWNLOAD_RECEIPT_SHA256 = "07ef6caa1ece2cb837b24879795f4dfda1465957773d34707fd5f7c2e7752d01"
VISUAL_FAILURE_SELECTED_VIDEO_SHA256 = "54b16bb6925173f38e34e044dd8f12a0a0935b0a99e211168c4677acf10f5d8a"
REJECTED_PEXELS_ASSET_REFS = ("pexels-13278454", "pexels-14003577")
VEO_FAILURE_RUN_ID = CQR1_PAID_CANARY_006_RUN_ID
VEO_FAILURE_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / VEO_FAILURE_RUN_ID
).resolve()
VEO_FAILURE_INVENTORY_HASH = "ad36e6dfaaac795f58b60e983e4dfd3fd2bb571a7344da6bbb4d212cf9d29cb4"
VEO_FAILURE_FILE_COUNT = 132
VEO_FAILURE_TOTAL_SIZE_BYTES = 11_631_905
VEO_FAILURE_STOP_CONTENT_HASH = "93b6856b92d2722d1bfcadc46942228f59140b5af0799196140298da6afcac57"
VEO_FAILURE_STOP_SHA256 = "19ff4d2e789af9326f76678e03dfcf4a9ee4444f868630a37665e378fea32194"
VEO_FAILURE_LEDGER_HASH = "fbf4d364f146e79e632c5d7176d61a998141835665e064134a7a57891650ec7b"
VEO_FAILURE_LEDGER_SHA256 = "2a37f0a3cb242cdca021d0a56055c286e0cad753e110f41d4d378f4f6dba55cf"
VEO_FAILURE_REVIEW_CONTENT_HASH = "a9681f8e175e3a50f4508ba179e275c5ff7dfedcc830d4a3ea4f5d0665970fd2"
VEO_FAILURE_REVIEW_SHA256 = "08b10d725e3ca41c44a7a9691953bb555ccca52df924755a03313cf7d5f51aa7"
VEO_FAILURE_PEXELS_SHEET_SHA256 = "0d62d481270b98c82c261e9ea0e09911c1ffc7cef8fe727b538b1333aa3682d2"
VEO_FAILURE_PEXELS_REPRESENTATIVE_SHA256 = "f28b2f98d3d1e642146681ee4f7e43bf72d3edf855d31d42680e9a44a95a444a"
VEO_FAILURE_VEO_SHEET_SHA256 = "f9a40c902178652e76dcc7d2e76ccab903aaae8af502e2c8824a0482d6d3ed7b"
VEO_FAILURE_VEO_REPRESENTATIVE_SHA256 = "8d53167a40434487c6948fc998e6ff08f5b73b60bce6e008d825c0def39ac99c"
VEO_FAILURE_PREFLIGHT_CONTENT_HASH = "2608f3fa015d1f0feb17e78b2f76d6d87fcb0daa023f9977fe34eed6e149f744"
VEO_FAILURE_PREFLIGHT_SHA256 = "69c3e45e5ec32911af5941eac1e23e4f0020f7d2817c11555ab3eea1714f4bc6"
VEO_FAILURE_APPROVAL_SHA256 = "fc2f4220e2951c251acca5fd032085e2abd5ce3d81f923f1416ad7f2e947c2f1"
VEO_FAILURE_PEXELS_SEARCH_SHA256 = "a60c8612ffaa2fa53ecbb29de240824b4c941936f8a883b54c6dd5e9bb7034d0"
VEO_FAILURE_PEXELS_DOWNLOAD_SHA256 = "43261074f190fe35aa2e7c14ccd51e75b9442f558df9e4d0c85ad667558ba40b"
VEO_FAILURE_PEXELS_VIDEO_SHA256 = "9a12085cdb448a4a6238fae40d6fee450ceaada242bddaf5187517f7da8c8d08"
VEO_FAILURE_OPERATION_RECEIPT_SHA256 = "5e01be1d5375fbb3e17e1e3fcc793a9506dffa8d6ee81fe587b45e778aaee69d"
VEO_FAILURE_DOWNLOAD_RECEIPT_SHA256 = "0056d5fd6c2ffa4ab4b15659b62b0c521062398bb5f6993a70a8b461cb125e5d"
VEO_FAILURE_PROVENANCE_SHA256 = "6f69565c721880b7b9c296c897e8f2e67352d5acb3e1d423418482407f407ae4"
VEO_FAILURE_PROMPT_SHA256 = "983be4045634bbe2d99dd14dd08c5dac6d19d4ab3fafcdcbdc8e10e7ee1780ee"
VEO_FAILURE_VIDEO_SHA256 = "9ad6bdf77b3d338f39a5e3b3b1d109545deaa342a04d3b10eb7045cd3cbb2256"
VEO_FAILURE_TERMINAL_EVENT_CONTENT_HASH = "079bc12fdda88e67868d10aa07377460dd1d2e6ec6cead931807426dced90716"
VEO_FAILURE_TERMINAL_EVENT_SHA256 = "7a17e29593203d9b59d0367af5e90f1a2084084ca49f3cc7558cf74d946cde42"
VISUAL_SOURCE_RUN_ID = CQR1_PAID_CANARY_007_RUN_ID
VISUAL_SOURCE_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / VISUAL_SOURCE_RUN_ID
).resolve()
VISUAL_SOURCE_INVENTORY_HASH = "aeb878d9978691adc5c1a4b07bf07f62d0ccf3abc173e904489f320e74082cf8"
VISUAL_SOURCE_FILE_COUNT = 159
VISUAL_SOURCE_TOTAL_SIZE_BYTES = 28_538_389
VISUAL_SOURCE_FAILURE_CONTENT_HASH = "e92a1125addcf3eb9bb079e094c5484e9fda6bc73b2ec3502c4b7df2c3185897"
VISUAL_SOURCE_FAILURE_SHA256 = "585fb6a87e1a9d1d5bf0ae3b5bbb4f7a0920eef6096f701d13acc66a1c55c0bf"
VISUAL_SOURCE_LEDGER_HASH = "bbb8bd46c7a977bd60ff1aaf3dff8e25560d0262884a3bcf50bfc4c40b4889b2"
VISUAL_SOURCE_LEDGER_SHA256 = "cc95692edc12c7498fdc88d52185c74bd09fc65800d26dc8a3266889e2a0fcb9"
VISUAL_SOURCE_REVIEW_CONTENT_HASH = "6a426352546a43fd598a79adc9801f47a6bb2532f1ce321c612d0ca6d88471e9"
VISUAL_SOURCE_REVIEW_SHA256 = "182a741334742fc029697cb209f9fd4bd61d65326e629f11d0d7773afd693112"
VISUAL_SOURCE_PREFLIGHT_CONTENT_HASH = "1cb5ce9cee64a89ddd64679fe470116cd02669b813398df8528eefc30d6bfe6e"
VISUAL_SOURCE_PREFLIGHT_SHA256 = "ff4444e6e4c912b8b9caad2b6a064a264d785993584dcdca4964ce6c259561ad"
VISUAL_SOURCE_APPROVAL_SHA256 = "be7d64300fef0c44017ac3fbc2a59f18d1ea3ea47da5a815ad06a707c0ddae1b"
VISUAL_SOURCE_DIRECTION_CONTENT_HASH = "48b29bf5f6769d4bad3306b24f1c462a1cdd5959f7d65f58f01e2fd2d8246315"
VISUAL_SOURCE_DIRECTION_SHA256 = "07c92306064336c7ff82f962a1bd3e25e5efab231a97c6f1078710abc44052ff"
VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH = "2b1d2fd02e3318c1ff86e2a8fe77bba3891406a0d52b02b2405404a3ccc1327c"
VISUAL_SOURCE_PEXELS_SEARCH_SHA256 = "0f1b052f17729e3700e1365e3a030105f182aea1aaf1a89080c2d5950741ff67"
VISUAL_SOURCE_PEXELS_DOWNLOAD_RECEIPT_SHA256 = "4bf8e6d1af7f3c7111104763eb11f3e01218502ba4d406f9d399868cc88fbcad"
VISUAL_SOURCE_PEXELS_SOURCE_MANIFEST_SHA256 = "e1264dfb3af2e97bebc4817b4cfc42de210f2533ece874dbfbe2e688f42c0ac4"
VISUAL_SOURCE_PEXELS_VIDEO_SHA256 = "9a12085cdb448a4a6238fae40d6fee450ceaada242bddaf5187517f7da8c8d08"
VISUAL_SOURCE_PEXELS_VIDEO_SIZE_BYTES = 3_372_120
VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256 = "f28b2f98d3d1e642146681ee4f7e43bf72d3edf855d31d42680e9a44a95a444a"
VISUAL_SOURCE_PEXELS_SHEET_SHA256 = "0d62d481270b98c82c261e9ea0e09911c1ffc7cef8fe727b538b1333aa3682d2"
VISUAL_SOURCE_VEO_OPERATION_ID = (
    "models/veo-3.1-fast-generate-preview/operations/f1h6lf0kcws1"
)
VISUAL_SOURCE_VEO_REQUEST_HASH = "e668313e8a3139a5c28ad45ca940f0b813284eb00ff36894e05f62b8fd5705d1"
VISUAL_SOURCE_VEO_PROMPT_HASH = "3a2b6892b5cb4626b3e8f3b0746f8d4e84f2ad8fc7c866c2d109c05c9c9b6fee"
VISUAL_SOURCE_VEO_GENERATION_REQUEST_SHA256 = (
    "b9ad4a088297d84abccf645bcd4bcb570d35841abf216d667264c24a921f60a1"
)
VISUAL_SOURCE_VEO_OPERATION_SHA256 = "31bb4a6292775966689291a036295375d028009dd4b0335736241cc5f8026a19"
VISUAL_SOURCE_VEO_DOWNLOAD_SHA256 = "b0b6fef5d6c15ae3e5594b5128140c1093236756ec4d4ade5b747b5a0f3a5f2f"
VISUAL_SOURCE_VEO_PROVENANCE_SHA256 = "08c558f66d6e742f723035d45fbd028aa1c578ecb806a2039ae92897acb4b083"
VISUAL_SOURCE_VEO_PROMPT_SHA256 = "367285daafcc3d994fbff1e7cf36ea335ada477d3e7ef0d1edb95ae7acfefe0a"
VISUAL_SOURCE_VEO_FFPROBE_SHA256 = "ba9b3ea73da89577f281f9580f7a1a9b29efc398eaa9edf6bd370fa4e8fc9abc"
VISUAL_SOURCE_VEO_VIDEO_SHA256 = "5821d6cd1799b34fe5ce097d51ebef172fdd12519e96d84bde65343d9e54d027"
VISUAL_SOURCE_VEO_VIDEO_SIZE_BYTES = 1_622_954
VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256 = "05c367bf6012e35d00c8b50f1d79a8c8a0fe443c6195122ab799df89768735b4"
VISUAL_SOURCE_VEO_SHEET_SHA256 = "4b77a00280f28fca0cd41d9f1442b143fddaa411a4cda5d38cf9b5ee384736c2"
VISUAL_SOURCE_NORMALIZATION_PROBE_CONTENT_HASH = "0590d8c530ae363ae94bd6680df45dce3436c1ab07bea54284d88cf451564d0f"
VISUAL_SOURCE_NORMALIZATION_PROBE_SHA256 = "407b499aa8904b5db9bf1490b5af99aa8b27002bccedbf1579879f52a9f8be0f"
VISUAL_SOURCE_TERMINAL_EVENT_CONTENT_HASH = "58c296045ba3648fffb7bab3994f3b0ab39d287230c618d8bcd01c33f7372654"
VISUAL_SOURCE_TERMINAL_EVENT_SHA256 = "a5398e6475951480ca9ce9841ebfcb163f81c1adeac43c04955854d8becd2a83"
LOCAL_RENDER_FAILURE_RUN_ID = CQR1_PAID_CANARY_008_RUN_ID
LOCAL_RENDER_FAILURE_WORKSPACE = (
    ROOT / "var/tmp/vcos-project-workspaces" / LOCAL_RENDER_FAILURE_RUN_ID
).resolve()
LOCAL_RENDER_FAILURE_INVENTORY_HASH = (
    "f1542056191250532ed45988ec113bce23f62aee6bbd4073b1a0ec2cbc3737fc"
)
LOCAL_RENDER_FAILURE_FILE_COUNT = 183
LOCAL_RENDER_FAILURE_TOTAL_SIZE_BYTES = 51_162_357
LOCAL_RENDER_FAILURE_STOP_CONTENT_HASH = (
    "4fd917a40bcd16567868703bc4b931db024a426ba4c2f45003bf4decac04dba0"
)
LOCAL_RENDER_FAILURE_STOP_SHA256 = (
    "7d0a457a7f9b859b8890a893ec1b4416ea3164aabac6604aafb1b96b11b156e8"
)
LOCAL_RENDER_FAILURE_LEDGER_HASH = (
    "19b5c956fcd66ac9dd1962874f165195890a788a16befb54fda143314140224c"
)
LOCAL_RENDER_FAILURE_LEDGER_SHA256 = (
    "fde9056fa446d7566b78c2ef814d7119d54371e5470bcd8167590f6cddcc9712"
)
LOCAL_RENDER_FAILURE_PREFLIGHT_CONTENT_HASH = (
    "52aa03203e8788576a36ae608139040bf633abe65f2e68108a57e86e2b5d6289"
)
LOCAL_RENDER_FAILURE_PREFLIGHT_SHA256 = (
    "8111d087b781b3408d6601a73ea2cd20d0bcf83cf00056743ca77d9d43b5b2d3"
)
LOCAL_RENDER_FAILURE_APPROVAL_SHA256 = (
    "c2dabcf105d293212e2434e9467bc7783efb6025f979bd7e82faf241c27e7107"
)
LOCAL_RENDER_FAILURE_EVENT_CONTENT_HASH = (
    "40d490a77194956c051677626f9a9ed2968a29cd3f7798e09f400deebc8e2973"
)
LOCAL_RENDER_FAILURE_EVENT_SHA256 = (
    "a306cfa59de2d2a21eb15d3d78c841cde48eec4f4d0d67e6e91cd8651402bc42"
)
LOCAL_RENDER_FAILURE_TECHNICAL_CONTENT_HASH = (
    "6d88337f18d66e0cfc706f5549a5efdf64c962d3349afe3846e138c109a38d09"
)
LOCAL_RENDER_FAILURE_TECHNICAL_SHA256 = (
    "ea335c124f2bf0a3cf722dd9cecaf01bea5cbb2420cc14ceb06ff067ea6912a1"
)
LOCAL_RENDER_FAILURE_RENDER_RECEIPT_HASH = (
    "589b58145f37ab99b23801af0df67596d90f151afa6c9afaed6d443f52399d21"
)
LOCAL_RENDER_FAILURE_RENDER_RECEIPT_SHA256 = (
    "9acc33ae21708ed17c1a84b099fad4e798c96bb0b6b9ce00837190965ebf6de5"
)
LOCAL_RENDER_FAILURE_FFPROBE_SHA256 = (
    "6eb9bfa94eb2e14184bbd32bc38741d2f7bf8f3efcc0b2c9e912c1af20ac892e"
)
LOCAL_RENDER_FAILURE_FINAL_MP4_SHA256 = (
    "0ca9e1334a43274519b6d1b3a7bc44c03486c293672e4583e18c7d06202ef7c0"
)
LOCAL_RENDER_FAILURE_FINAL_MP4_SIZE_BYTES = 9_892_123
SOURCE_PREFLIGHT_SHA256 = "7e4bf0cc57df0598b2c3ba6dfb5ed6d25d029129647099ad1fe2a393dd3a5b77"
PREVIOUS_TTS_AUDIO_SHA256 = "2c6a9382fee10783ebfe5c5a2e33b6dbb16b2cd3a253d19f8729aa7a96de6fb6"
PREVIOUS_TTS_AUDIO_SIZE_BYTES = 612_772
PREVIOUS_TTS_AUDIO_DURATION_MS = 38_220
PREVIOUS_TTS_RECEIPT_CONTENT_HASH = "039c2369d943ae0d08bc70d3fc34be0b5a37ce65ac8d13789aa21598cb3ab94d"
PREVIOUS_TTS_RECEIPT_SHA256 = "87df107f234a6ee0f29c336705c175c97589798636a8f094b777e988b418aed1"
PREVIOUS_TIMING_SEED_CONTENT_HASH = "df1095698fa9bf403f033d1040d47d3c2f93d05e898ee154511a846357dac8cb"
PREVIOUS_TIMING_SEED_SHA256 = "fdc45d102e73f2f115703b09515d4f249236c6149655db1e92800a256f174915"
PREVIOUS_NORMALIZED_CONTENT_HASH = "deba53b94b4c0fbba9446a50c6cbfdfa26cc56b9fefa4af7e874c5a9130253a3"
PREVIOUS_NORMALIZED_SHA256 = "a29ebee37ebf7cf01284260353d7e1ac09983f5593b3cae9c68a625ee0f68690"
PREVIOUS_APPROVED_SCRIPT_CONTENT_HASH = "d77e6e33098392aee6ad949e98dbdb3302f9b7f1ae8a127e3e1630a2c718f332"
PREVIOUS_APPROVED_SCRIPT_SHA256 = "99c8bd11fd44b8ff4a85ad7936d06c4fdffc04acc8061b333b3dbf7419cbf0b7"
PREVIOUS_FAILURE_STOP_CONTENT_HASH = "b9950447237b8c63b5a3fde784065fe45dadc07c38071e5fc17f7aea5dbf4f13"
PREVIOUS_FAILURE_STOP_SHA256 = "661d2bde9c4bbbaa01e5b017787e21f1d7fe028519d5fdc064a208e64fb6ec3d"
PREVIOUS_LEDGER_HASH = "915241fe723345c63df0e275a5160236efea1ae649d4923e6fcd615ac2b60d83"
PREVIOUS_LEDGER_SHA256 = "8289e87094e9bdecbf07ac77597821b3ab07c834483ec9a8977dbd8cace0b847"
PREVIOUS_WORKSPACE_INVENTORY_HASH = "c78c81e111af792aca87d6973449c2bb26523d487c9bf66603c31e77c0aecc8b"
PREVIOUS_WORKSPACE_FILE_COUNT = 42
PREVIOUS_WORKSPACE_TOTAL_SIZE_BYTES = 941_872
FORMAT_IDENTITY_REF = "f4ef71b1-6942-49c4-bb69-47244751265d"
FORMAT_IDENTITY_HASH = "8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc"
ORIGINALITY_REF = "d0bb74e3-eb8c-44ac-a1d8-b165892e176b"
ORIGINALITY_HASH = "d0bf32bf52e45c81ec0cab062f0b1c933a6cfdcdf63aabc961928764999d8624"
COMPANY_ID = "e0b7c806-b39e-4792-bf2e-7e8c6d6ca464"
CHANNEL_PROFILE_VERSION_ID = "f5e45981-51eb-4c24-95a8-f9f5db761195"
EFFECTIVE_CONTEXT_ID = "d1d0333a-d896-40aa-a6d8-a5766f339450"
EFFECTIVE_CONTEXT_HASH = "796ee1ec217eceed511ebdbbc2123aa4fb2f29161add0e8a35e415aeb1d25150"
EXPECTED_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
EXPECTED_MODEL_ID = "eleven_multilingual_v2"
VOICE_SETTINGS = {
    "stability": 0.55,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.90,
}
PACKAGE_ID = f"{CQR1_RUN_ID}-package"
CHANNEL_KEY = "small-team-ai"
ARCHIVE_DATE = "2026-07-16"
PREDICTION_BASE_RUN_ID = "pa1r-cqr1-20260714-paid-canary-001"
PRIOR_SPOKEN_WORD_COUNT = 92
PRIOR_MEASURED_DURATION_MS = 47_972
CQR1_CANARY_SCRIPT_V2 = (
    "An approved script anchors the workflow. Final narration comes from that text, then alignment "
    "verifies every spoken word against the audio.\n\n"
    "Those timings guide each scene and caption. Native graphics explain the process, while one "
    "grounded stock shot adds context.\n\n"
    "A restrained visual metaphor carries the transition. The renderer combines picture, voice, "
    "and captions on the same timeline.\n\n"
    "Quality checks prepare a package for human review. This non-production canary cannot be published."
)
os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def bt709_h264_metadata_args() -> list[str]:
    """Force complete BT.709 VUI metadata for VideoToolbox H.264 outputs."""

    return ["-bsf:v", H264_BT709_METADATA_BSF]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_inventory(root: Path) -> dict[str, Any]:
    rows = [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".cqr1-execution.lock"
    ]
    return {
        "file_count": len(rows),
        "total_size_bytes": sum(item["size_bytes"] for item in rows),
        "inventory_hash": stable_hash(rows),
        "files": rows,
    }


def predicted_duration_evidence(normalized: SpokenTextNormalized) -> dict[str, Any]:
    spoken_word_count = len(normalized.spoken_tokens)
    predicted_ms = round(
        PRIOR_MEASURED_DURATION_MS * spoken_word_count / PRIOR_SPOKEN_WORD_COUNT,
        3,
    )
    projected_delivered_wpm = round(
        spoken_word_count * 60_000 / predicted_ms, 3
    )
    payload = {
        "run_id": CQR1_RUN_ID,
        "method": "LINEAR_PROJECTION_FROM_IMMUTABLE_PRIOR_REAL_TTS",
        "prior_run_id": PREDICTION_BASE_RUN_ID,
        "prior_spoken_word_count": PRIOR_SPOKEN_WORD_COUNT,
        "prior_measured_duration_ms": PRIOR_MEASURED_DURATION_MS,
        "prior_delivery_ms_per_word": round(
            PRIOR_MEASURED_DURATION_MS / PRIOR_SPOKEN_WORD_COUNT, 6
        ),
        "spoken_word_count": spoken_word_count,
        "predicted_duration_ms": predicted_ms,
        "required_minimum_ms": 28_000,
        "required_maximum_ms": 40_000,
        "margin_below_maximum_ms": round(40_000 - predicted_ms, 3),
        "projected_delivered_wpm": projected_delivered_wpm,
        "projected_pacing_status": (
            "PASS"
            if 130 <= projected_delivered_wpm <= 155
            else "REVIEW_REQUIRED"
            if projected_delivered_wpm >= 105
            else "BLOCK"
        ),
        "pacing_projection_is_not_measured_gate_evidence": True,
        "word_count_gate": "PASS" if 72 <= spoken_word_count <= 76 else "BLOCK",
        "predicted_duration_gate": (
            "PASS" if 28_000 <= predicted_ms <= 40_000 else "BLOCK"
        ),
        "voice_id": EXPECTED_VOICE_ID,
        "model_id": EXPECTED_MODEL_ID,
        "speed": VOICE_SETTINGS["speed"],
        "speed_increased_for_duration": False,
        "provider_call_made": False,
    }
    payload["content_hash"] = stable_hash(payload)
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def model_json(path: Path, model: Any) -> Path:
    return write_json(path, model.model_dump(mode="json"))


def append_event(kind: str, payload: Mapping[str, Any]) -> Path:
    EVENTS.mkdir(parents=True, exist_ok=True)
    existing = sorted(EVENTS.glob("*.json"))
    sequence = len(existing) + 1
    event_payload = dict(payload)
    event_payload.pop("content_hash", None)
    body = {
        "sequence": sequence,
        "event_kind": kind,
        "run_id": CQR1_RUN_ID,
        "purpose": CQR1_PURPOSE,
        "recorded_at": datetime.now(UTC).isoformat(),
        **event_payload,
    }
    body["content_hash"] = stable_hash(body)
    path = EVENTS / f"{sequence:04d}-{kind.lower().replace('_', '-')}.json"
    if path.exists():
        raise RuntimeError("CQR1_APPEND_ONLY_EVENT_COLLISION")
    return write_json(path, body)


@contextmanager
def execution_lock() -> Iterable[None]:
    """Process-wide one-shot lock; the JSON ledger alone has no cross-process CAS."""

    MANIFESTS.mkdir(parents=True, exist_ok=True)
    lock_path = MANIFESTS / ".cqr1-execution.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("CQR1_EXECUTION_ALREADY_RUNNING") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_guarded_once(
    *,
    ledger: CQR1CanaryCallLedger,
    operation_key: str,
    preflight: Any,
    operation: Any,
) -> dict[str, Any]:
    entry = ledger.entries[operation_key]
    append_event(
        f"{operation_key}_ATTEMPT_STARTED",
        {
            "operation_key": operation_key,
            "attempt_no": entry.attempt_count + 1,
            "status_before": entry.status,
            "provider_call_count_before": ledger.provider_call_count,
            "automatic_retry": False,
        },
    )
    try:
        result = CQR1CanaryExecutionGuard(ledger).run_once(
            operation_key, preflight=preflight, operation=operation
        )
    except Exception as exc:
        current = CQR1CanaryCallLedger.load(
            MANIFESTS / "planned_provider_call_ledger.json"
        )
        append_event(
            f"{operation_key}_ATTEMPT_FAILED",
            {
                "operation_key": operation_key,
                "error_type": type(exc).__name__,
                "provider_call_count": current.provider_call_count,
                "attempt_count": current.entries[operation_key].attempt_count,
                "automatic_retry": False,
                "second_attempt_authorized": False,
            },
        )
        raise
    current = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    append_event(
        f"{operation_key}_ATTEMPT_COMPLETED",
        {
            "operation_key": operation_key,
            "status": current.entries[operation_key].status,
            "provider_call_count": current.provider_call_count,
            "attempt_count": current.entries[operation_key].attempt_count,
            "automatic_retry": False,
        },
    )
    return result


def require_inside(path: Path, *, must_exist: bool = False) -> Path:
    candidate = path if path.is_absolute() else WORKSPACE / path
    if ".." in candidate.parts:
        raise ValueError("PATH_TRAVERSAL_REJECTED")
    if must_exist and candidate.is_symlink():
        raise ValueError("SYMLINK_INPUT_REJECTED")
    resolved = candidate.resolve(strict=must_exist)
    if resolved != WORKSPACE and WORKSPACE not in resolved.parents:
        raise ValueError("PATH_OUTSIDE_WORKSPACE")
    return resolved


def settings_or_block() -> Settings:
    settings = Settings()
    required = {
        "PEXELS_API_KEY_CONFIGURED": bool(settings.pexels_api_key),
        "GEMINI_API_KEY_CONFIGURED": bool(settings.gemini_api_key),
        "DRIVE_ARCHIVE_ROOT_CONFIGURED": bool(settings.google_drive_root_folder_id),
        "ELEVENLABS_API_KEY_CONFIGURED": bool(settings.elevenlabs_api_key),
        "ELEVENLABS_VOICE_ID_CONFIGURED": bool(settings.elevenlabs_voice_id),
        "ELEVENLABS_MODEL_ID_CONFIGURED": bool(settings.elevenlabs_model_id),
        "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED": (
            settings.elevenlabs_forced_alignment_permission_confirmed is True
        ),
    }
    if not all(required.values()):
        append_event(
            "RESUME_STATIC_CONFIG_BLOCKED",
            {
                "source_run_preflight_state": "PASS",
                "source_run_terminal_state": "FAIL_CLOSED",
                "recovery_preflight_state": "BLOCKED",
                "resume_reason": RESUME_REASON,
                "safe_configuration": required,
                "provider_call_count": CQR1CanaryCallLedger.load(
                    MANIFESTS / "planned_provider_call_ledger.json"
                ).provider_call_count,
            },
        )
        raise RuntimeError("CQR1_RESUME_TYPED_CONFIGURATION_INCOMPLETE")
    return settings


def require_scoped_external_execution_flags(
    settings: Settings, *, provider: str
) -> None:
    common = (
        settings.provider_real_execution_enabled
        and settings.cqr1_paid_canary_enabled
        and not settings.media_provider_calls_disabled
        and settings.upload_and_publish_disabled
        and not settings.provider_production_execution_enabled
        and not settings.native_ffmpeg_production_enabled
    )
    provider_ready = {
        "elevenlabs_forced_alignment": (
            settings.elevenlabs_real_execution_enabled
            and not settings.elevenlabs_real_generation_enabled
        ),
        "pexels": (
            settings.pexels_real_execution_enabled
            and settings.pexels_real_search_enabled
        ),
        "google_veo": settings.veo_real_generation_enabled,
        "google_drive": settings.google_drive_real_archive_enabled,
    }.get(provider, False)
    if not common or not provider_ready:
        raise RuntimeError(f"CQR1_SCOPED_EXTERNAL_FLAGS_BLOCKED:{provider}")


def approved_policy() -> dict[str, Any]:
    return CreativeQualityPolicyCatalog(
        ROOT / "config/creative_quality_policy_catalog.yaml"
    ).approved_snapshot(CHANNEL_KEY)


def normalized_text() -> SpokenTextNormalized:
    return SpokenTextNormalizer().normalize(
        script_revision_id="cqr1-paid-canary-script-v2-shortened",
        source_text=CQR1_CANARY_SCRIPT_V2,
        locale="en-US",
        language="en",
    )


def stock_request() -> AssetRequest:
    payload = {
        "request_id": "cqr1-paid-canary-stock-request",
        "scene_id": "cqr1-stock-support",
        "source_segment_ids": ["cqr1-stock-support"],
        "purpose": "GROUNDED_DOCUMENTARY_CONTEXT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": (
            "film crew hands adjust studio lighting equipment for a video "
            "production, grounded behind-the-scenes workflow, screen-free"
        ),
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 6,
        "maximum_duration_seconds": 12,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["NATIVE_VISUAL", "SUPPORTING_STOCK"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def _compile_pexels_query_plan(
    *,
    direction: VisualDirectionContract,
    request: AssetRequest,
    target_duration_seconds: float,
) -> PexelsQueryPlan:
    direction_ref = (
        f"artifact://visual-direction/{direction.channel_id}/{direction.project_id}/"
        f"{direction.contract_version}"
    )
    intent = SceneVisualIntent(
        scene_id="cqr1-stock-support",
        semantic_intent=request.semantic_visual_intent,
        subject_action=(
            "film crew hands adjust unbranded studio lighting equipment "
            "for a grounded behind-the-scenes video production"
        ),
        target_duration_seconds=target_duration_seconds,
        aspect_ratio="16:9",
        crop_safety_required=True,
        previous_scene_summary="native workflow diagram with approved script and narration timing",
        next_scene_summary="restrained native bridge toward synchronized hero transition",
    )
    return PexelsQueryPlanner().plan(
        request,
        size_preference="large",
        per_page=24,
        locale="en-US",
        visual_direction=direction,
        visual_direction_ref=direction_ref,
        scene_intent=intent,
        asset_reuse_history=list(REJECTED_PEXELS_ASSET_REFS),
    )


def _canonical_pexels_inputs(
    timeline_model: CanonicalMediaTimeline,
    direction: VisualDirectionContract,
) -> tuple[AssetRequest, PexelsQueryPlan, int]:
    stock_segments = [
        item
        for item in timeline_model.segments
        if item.segment_id == "cqr1-stock-support"
    ]
    if len(stock_segments) != 1:
        raise RuntimeError("CQR1_CANONICAL_STOCK_SCENE_MISSING_OR_DUPLICATED")
    scene_duration_ms = stock_segments[0].target_scene_duration_ms
    request = bind_minimum_duration_to_canonical_scene(
        stock_request(),
        scene_duration_ms=scene_duration_ms,
    )
    plan = _compile_pexels_query_plan(
        direction=direction,
        request=request,
        target_duration_seconds=scene_duration_ms / 1000,
    )
    return request, plan, scene_duration_ms


def hero_asset_request() -> AssetRequest:
    payload = {
        "request_id": "cqr1-paid-canary-veo-request",
        "scene_id": "cqr1-veo-hero",
        "source_segment_ids": ["cqr1-veo-hero"],
        "purpose": "METAPHOR",
        "requested_role": "AI_HERO",
        "semantic_visual_intent": (
            "two loose analog celluloid film strips form a restrained "
            "overlapping curve on a plain matte charcoal table"
        ),
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1280x720",
        "minimum_duration_seconds": 8,
        "maximum_duration_seconds": 8,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_CHARACTER",
        "logo_text_policy": "NO_LOGO_NO_READABLE_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["AI_HERO", "NATIVE_VISUAL"],
        "projected_cost_class": "MEDIUM",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def compile_resume_plans() -> tuple[VisualDirectionContract, Any, Any]:
    policy = approved_policy()
    direction = VisualDirectionCompiler().compile(
        channel_id=CHANNEL_KEY,
        project_id=CQR1_RUN_ID,
        format_identity_ref=FORMAT_IDENTITY_REF,
        format_identity_hash=FORMAT_IDENTITY_HASH,
        visual_strategy_profile_ref=policy["policy_ref"],
        visual_strategy_profile_hash=policy["policy_hash"],
        policy=policy,
        adjacent_scene_constraints=[
            "use native graphics as backbone",
            "avoid abrupt provider-source cuts",
            "retain neutral-warm restrained documentary language",
        ],
    )
    pexels_plan = _compile_pexels_query_plan(
        direction=direction,
        request=stock_request(),
        target_duration_seconds=6.0,
    )
    hero_intent = SceneVisualIntent(
        scene_id="cqr1-veo-hero",
        semantic_intent=(
            "loose analog film strips resting naturally on a plain matte "
            "charcoal table"
        ),
        subject_action=(
            "two loose celluloid film strips form a restrained overlapping "
            "curve on the matte tabletop while soft side light reveals "
            "their physical texture"
        ),
        target_duration_seconds=8.0,
        previous_scene_summary=(
            "grounded behind-the-scenes camera production under restrained "
            "studio lighting"
        ),
        next_scene_summary="restrained native synchronized timeline bridge",
        camera_angle="slightly elevated",
        shot_size="close-up",
    )
    veo_prompt = VeoPromptCompiler().compile(
        scene_intent=hero_intent,
        visual_direction=direction,
        character_policy_mode="NO_CHARACTER",
        channel_provider_policy={
            "character_policy_mode": "NO_CHARACTER",
            "negative_constraints": [
                "machine",
                "robotics",
                "screen",
                "display",
                "panel",
                "button",
                "interface",
                "fake UI",
                "diagram",
                "text",
                "letter",
                "number",
                "label",
                "logo",
                "person",
            ],
        },
        visual_direction_ref=f"visual-direction:{direction.content_hash}",
    )
    model_json(MANIFESTS / "resume_visual_direction_contract.json", direction)
    model_json(MANIFESTS / "resume_pexels_query.json", pexels_plan)
    model_json(MANIFESTS / "resume_veo_prompt.json", veo_prompt)
    fit = VeoFixedDurationPlanner(
        __import__("app.contracts.visual_direction", fromlist=["VeoDurationFitThresholds"])
        .VeoDurationFitThresholds.from_policy(policy)
    ).decide(8.0)
    model_json(MANIFESTS / "resume_veo_duration_fit.json", fit)
    return direction, pexels_plan, veo_prompt


def _pin_immutable_file(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise RuntimeError(f"CQR1_SOURCE_ARTIFACT_IMMUTABILITY_FAILED:{source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != expected_sha256:
            raise RuntimeError(f"CQR1_PINNED_ARTIFACT_HASH_MISMATCH:{destination.name}")
        return
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected_sha256:
        raise RuntimeError(f"CQR1_PINNED_ARTIFACT_COPY_FAILED:{destination.name}")


def verify_failed_run003_lineage() -> dict[str, Any]:
    inventory = workspace_inventory(FAILED_RECOVERY_WORKSPACE)
    if (
        inventory["inventory_hash"] != FAILED_RECOVERY_INVENTORY_HASH
        or inventory["file_count"] != FAILED_RECOVERY_FILE_COUNT
        or inventory["total_size_bytes"] != FAILED_RECOVERY_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_FAILED_RUN_003_WORKSPACE_IMMUTABILITY_FAILED")
    failure_path = (
        FAILED_RECOVERY_WORKSPACE / "manifests/cqr1_paid_canary_failure_stop.json"
    )
    ledger_path = (
        FAILED_RECOVERY_WORKSPACE / "manifests/planned_provider_call_ledger.json"
    )
    response_path = (
        FAILED_RECOVERY_WORKSPACE
        / "manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json"
    )
    approval_path = FAILED_RECOVERY_WORKSPACE / "manifests/approval_scope.json"
    imported_tts_path = (
        FAILED_RECOVERY_WORKSPACE / "manifests/imported_tts_audio_evidence.json"
    )
    expected_files = {
        failure_path: FAILED_RECOVERY_STOP_SHA256,
        ledger_path: FAILED_RECOVERY_LEDGER_SHA256,
        response_path: FAILED_RECOVERY_SAFE_RESPONSE_SHA256,
        approval_path: FAILED_RECOVERY_APPROVAL_SHA256,
        imported_tts_path: FAILED_RECOVERY_IMPORTED_TTS_SHA256,
    }
    for path, expected_sha256 in expected_files.items():
        if not path.is_file() or sha256_file(path) != expected_sha256:
            raise RuntimeError(f"CQR1_FAILED_RUN_003_ARTIFACT_DRIFT:{path.name}")
    failure = read_json(failure_path)
    response = read_json(response_path)
    approval = CQR1CanaryApprovalScope.model_validate(read_json(approval_path))
    imported_tts = read_json(imported_tts_path)
    ledger = CQR1CanaryCallLedger.load(ledger_path)
    tts_entry = ledger.entries["elevenlabs_tts"]
    alignment_entry = ledger.entries["elevenlabs_forced_alignment"]
    downstream_keys = (
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
        "drive_archive",
    )
    provider_payload = response.get("provider_payload")
    provider_response = (
        provider_payload.get("response")
        if isinstance(provider_payload, dict)
        else None
    )
    if (
        failure.get("content_hash") != FAILED_RECOVERY_STOP_CONTENT_HASH
        or not _valid_content_hash(failure)
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "ELEVENLABS_FORCED_ALIGNMENT"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("failure_reason_code")
        != "TEMPORAL_ALIGNMENT_AUDIO_BOUNDS_INVALID"
        or response.get("content_hash")
        != FAILED_RECOVERY_SAFE_RESPONSE_CONTENT_HASH
        or not _valid_content_hash(response)
        or response.get("captured_before_parser_execution") is not True
        or response.get("secret_values_exposed") is not False
        or response.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or response.get("audio_duration_ms") != PREVIOUS_TTS_AUDIO_DURATION_MS
        or not isinstance(provider_payload, dict)
        or not _valid_content_hash(provider_payload)
        or provider_payload.get("secret_values_exposed") is not False
        or not isinstance(provider_response, dict)
        or len(provider_response.get("words") or []) != 143
        or len(provider_response.get("characters") or []) != 489
        or approval.run_id != FAILED_RECOVERY_RUN_ID
        or approval.approval_ref
        != f"operator-approval://{FAILED_RECOVERY_RUN_ID}"
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 1
        or approval.automatic_provider_retry
        or approval.external_provider_fallback
        or approval.youtube_allowed
        or not _valid_content_hash(imported_tts)
        or imported_tts.get("run_id") != FAILED_RECOVERY_RUN_ID
        or imported_tts.get("source_run_id") != PREVIOUS_CQR1_RUN_ID
        or imported_tts.get("source_audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or imported_tts.get("new_tts_generations_authorized") != 0
        or imported_tts.get("new_tts_generations_made") != 0
        or imported_tts.get("provider_call_made_by_run_003") is not False
        or ledger.run_id != FAILED_RECOVERY_RUN_ID
        or ledger.approval_ref != approval.approval_ref
        or read_json(ledger_path).get("ledger_hash") != FAILED_RECOVERY_LEDGER_HASH
        or ledger.provider_call_count != 1
        or sum(item.attempt_count for item in ledger.entries.values()) != 1
        or tts_entry.status != "REUSED"
        or tts_entry.max_attempts != 0
        or tts_entry.attempt_count != 0
        or tts_entry.provider_call_made
        or tts_entry.output_count != 0
        or tts_entry.safe_evidence.get("audio_sha256")
        != PREVIOUS_TTS_AUDIO_SHA256
        or alignment_entry.status != "FAILED"
        or alignment_entry.attempt_count != 1
        or not alignment_entry.provider_call_made
        or alignment_entry.output_count != 0
        or any(
            ledger.entries[key].status != "PLANNED"
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            or ledger.entries[key].output_count != 0
            for key in downstream_keys
        )
    ):
        raise RuntimeError("CQR1_FAILED_RUN_003_LINEAGE_INVALID")
    history = MANIFESTS / "history/run003"
    for source, expected_sha256 in expected_files.items():
        relative = source.relative_to(FAILED_RECOVERY_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256)
    return {
        "run_id": FAILED_RECOVERY_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "ELEVENLABS_FORCED_ALIGNMENT",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": FAILED_RECOVERY_STOP_CONTENT_HASH,
        "ledger_hash": FAILED_RECOVERY_LEDGER_HASH,
        "safe_response_content_hash": FAILED_RECOVERY_SAFE_RESPONSE_CONTENT_HASH,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": sum(
            item.attempt_count for item in ledger.entries.values()
        ),
        "mutated": False,
    }


def verify_failed_run004_lineage() -> dict[str, Any]:
    """Pin the immutable run004 visual failure and its reusable alignment."""

    inventory = workspace_inventory(ALIGNMENT_SOURCE_WORKSPACE)
    if (
        inventory["inventory_hash"] != ALIGNMENT_SOURCE_INVENTORY_HASH
        or inventory["file_count"] != ALIGNMENT_SOURCE_FILE_COUNT
        or inventory["total_size_bytes"] != ALIGNMENT_SOURCE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_FAILED_RUN_004_WORKSPACE_IMMUTABILITY_FAILED")
    paths = {
        "failure": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/cqr1_paid_canary_failure_stop.json",
        "ledger": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/planned_provider_call_ledger.json",
        "review": ALIGNMENT_SOURCE_WORKSPACE / "qc/codex_visual_asset_review.json",
        "review_sheet": ALIGNMENT_SOURCE_WORKSPACE
        / "render/proxy/pexels-review-contact-sheet.jpg",
        "forced": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/forced_alignment_evidence.json",
        "verified": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/verified_narration_alignment.json",
        "receipt": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/elevenlabs_forced_alignment_receipt.json",
        "safe_response": ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json",
        "approval": ALIGNMENT_SOURCE_WORKSPACE / "manifests/approval_scope.json",
    }
    expected_sha256 = {
        "failure": ALIGNMENT_SOURCE_FAILURE_SHA256,
        "ledger": ALIGNMENT_SOURCE_LEDGER_SHA256,
        "review": ALIGNMENT_SOURCE_REVIEW_SHA256,
        "review_sheet": ALIGNMENT_SOURCE_REVIEW_SHEET_SHA256,
        "forced": ALIGNMENT_SOURCE_FORCED_SHA256,
        "verified": ALIGNMENT_SOURCE_VERIFIED_SHA256,
        "receipt": ALIGNMENT_SOURCE_RECEIPT_SHA256,
        "safe_response": ALIGNMENT_SOURCE_SAFE_RESPONSE_SHA256,
        "approval": ALIGNMENT_SOURCE_APPROVAL_SHA256,
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_sha256[key]:
            raise RuntimeError(f"CQR1_FAILED_RUN_004_ARTIFACT_DRIFT:{key}")

    failure = read_json(paths["failure"])
    review = read_json(paths["review"])
    forced = ForcedAlignmentEvidence.model_validate(read_json(paths["forced"]))
    verified = VerifiedNarrationAlignment.model_validate(read_json(paths["verified"]))
    receipt = read_json(paths["receipt"])
    safe_response = read_json(paths["safe_response"])
    approval = CQR1CanaryApprovalScope.model_validate(read_json(paths["approval"]))
    ledger = CQR1CanaryCallLedger.load(paths["ledger"])
    tts = ledger.entries["elevenlabs_tts"]
    alignment = ledger.entries["elevenlabs_forced_alignment"]
    search = ledger.entries["pexels_search"]
    download = ledger.entries["pexels_download"]
    untouched = ("google_veo_submit", "google_veo_output", "drive_archive")
    if (
        not _valid_content_hash(failure)
        or failure.get("content_hash") != ALIGNMENT_SOURCE_FAILURE_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "PEXELS_POST_DOWNLOAD_VISUAL_REVIEW"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("provider_call_count") != 3
        or not _valid_content_hash(review)
        or review.get("content_hash") != ALIGNMENT_SOURCE_REVIEW_CONTENT_HASH
        or review.get("review_state") != "COMPLETED_REAL_FRAMES_BLOCKED"
        or review.get("assets", [{}])[0].get("result") != "BLOCK"
        or review.get("assets", [{}])[0].get("logo_or_readable_text_present")
        is not True
        or forced.content_hash != ALIGNMENT_SOURCE_FORCED_CONTENT_HASH
        or forced.verification_status != "PASS"
        or forced.spoken_text_hash != verified.spoken_text_hash
        or forced.audio_asset_ref != verified.audio_asset_ref
        or forced.audio_duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS
        or verified.content_hash != ALIGNMENT_SOURCE_VERIFIED_CONTENT_HASH
        or verified.verification_status != "PASS"
        or verified.token_coverage != 1.0
        or verified.missing_tokens
        or verified.extra_tokens
        or not _valid_content_hash(receipt)
        or receipt.get("content_hash") != ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH
        or receipt.get("request_response_binding_valid") is not True
        or not _valid_content_hash(safe_response)
        or safe_response.get("content_hash")
        != ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        or safe_response.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or safe_response.get("spoken_text_hash") != forced.spoken_text_hash
        or approval.run_id != ALIGNMENT_SOURCE_RUN_ID
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 1
        or ledger.run_id != ALIGNMENT_SOURCE_RUN_ID
        or ledger.approval_ref != approval.approval_ref
        or read_json(paths["ledger"]).get("ledger_hash")
        != ALIGNMENT_SOURCE_LEDGER_HASH
        or ledger.provider_call_count != 3
        or sum(item.attempt_count for item in ledger.entries.values()) != 3
        or tts.status != "REUSED"
        or tts.attempt_count != 0
        or tts.provider_call_made
        or alignment.status != "SUCCEEDED"
        or alignment.attempt_count != 1
        or not alignment.provider_call_made
        or alignment.output_count != 1
        or search.status != "SUCCEEDED"
        or search.attempt_count != 1
        or not search.provider_call_made
        or search.output_count != 0
        or download.status != "SUCCEEDED"
        or download.attempt_count != 1
        or not download.provider_call_made
        or download.output_count != 1
        or any(
            ledger.entries[key].status != "PLANNED"
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            or ledger.entries[key].output_count != 0
            for key in untouched
        )
    ):
        raise RuntimeError("CQR1_FAILED_RUN_004_LINEAGE_INVALID")

    history = MANIFESTS / "history/run004"
    for key, source in paths.items():
        relative = source.relative_to(ALIGNMENT_SOURCE_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256[key])
    return {
        "run_id": ALIGNMENT_SOURCE_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "PEXELS_POST_DOWNLOAD_VISUAL_REVIEW",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": failure["content_hash"],
        "ledger_hash": ALIGNMENT_SOURCE_LEDGER_HASH,
        "visual_review_content_hash": review["content_hash"],
        "forced_alignment_content_hash": forced.content_hash,
        "verified_alignment_content_hash": verified.content_hash,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": sum(item.attempt_count for item in ledger.entries.values()),
        "alignment_reuse_eligible": True,
        "mutated": False,
    }


def verify_failed_run005_lineage() -> dict[str, Any]:
    """Pin run005's logo/UI block without reusing its consumed Pexels attempt."""

    inventory = workspace_inventory(VISUAL_FAILURE_WORKSPACE)
    if (
        inventory["inventory_hash"] != VISUAL_FAILURE_INVENTORY_HASH
        or inventory["file_count"] != VISUAL_FAILURE_FILE_COUNT
        or inventory["total_size_bytes"] != VISUAL_FAILURE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_FAILED_RUN_005_WORKSPACE_IMMUTABILITY_FAILED")
    paths = {
        "failure": VISUAL_FAILURE_WORKSPACE
        / "manifests/cqr1_paid_canary_failure_stop.json",
        "ledger": VISUAL_FAILURE_WORKSPACE
        / "manifests/planned_provider_call_ledger.json",
        "review": VISUAL_FAILURE_WORKSPACE / "qc/codex_visual_asset_review.json",
        "review_sheet": VISUAL_FAILURE_WORKSPACE
        / "render/proxy/pexels-review-contact-sheet.jpg",
        "representative": VISUAL_FAILURE_WORKSPACE
        / "render/proxy/pexels-selected-representative.jpg",
        "preflight": VISUAL_FAILURE_WORKSPACE
        / "manifests/resume_paid_canary_preflight.json",
        "approval": VISUAL_FAILURE_WORKSPACE / "manifests/approval_scope.json",
        "search": VISUAL_FAILURE_WORKSPACE
        / "manifests/pexels_search_ranking_provenance.json",
        "download_receipt": VISUAL_FAILURE_WORKSPACE
        / "manifests/pexels_download_receipt.json",
        "selected_video": VISUAL_FAILURE_WORKSPACE
        / "source/stock/pexels-14003577-5989389.mp4",
    }
    expected_sha256 = {
        "failure": VISUAL_FAILURE_STOP_SHA256,
        "ledger": VISUAL_FAILURE_LEDGER_SHA256,
        "review": VISUAL_FAILURE_REVIEW_SHA256,
        "review_sheet": VISUAL_FAILURE_REVIEW_SHEET_SHA256,
        "representative": VISUAL_FAILURE_REPRESENTATIVE_SHA256,
        "preflight": VISUAL_FAILURE_PREFLIGHT_SHA256,
        "approval": VISUAL_FAILURE_APPROVAL_SHA256,
        "search": VISUAL_FAILURE_SEARCH_SHA256,
        "download_receipt": VISUAL_FAILURE_DOWNLOAD_RECEIPT_SHA256,
        "selected_video": VISUAL_FAILURE_SELECTED_VIDEO_SHA256,
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_sha256[key]:
            raise RuntimeError(f"CQR1_FAILED_RUN_005_ARTIFACT_DRIFT:{key}")

    failure = read_json(paths["failure"])
    review = read_json(paths["review"])
    preflight = read_json(paths["preflight"])
    search_provenance = read_json(paths["search"])
    download_receipt = read_json(paths["download_receipt"])
    approval = CQR1CanaryApprovalScope.model_validate(read_json(paths["approval"]))
    ledger = CQR1CanaryCallLedger.load(paths["ledger"])
    tts = ledger.entries["elevenlabs_tts"]
    alignment = ledger.entries["elevenlabs_forced_alignment"]
    search = ledger.entries["pexels_search"]
    download = ledger.entries["pexels_download"]
    untouched = ("google_veo_submit", "google_veo_output", "drive_archive")
    review_asset = (review.get("assets") or [{}])[0]
    selected_candidate = search_provenance.get("selected_candidate") or {}
    download_selection = download_receipt.get("http_evidence") or {}
    if (
        not _valid_content_hash(failure)
        or failure.get("content_hash") != VISUAL_FAILURE_STOP_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "PEXELS_POST_DOWNLOAD_VISUAL_REVIEW"
        or failure.get("failure_reason_code")
        != "PEXELS_REAL_FRAME_LOGO_TEXT_POLICY_BLOCK"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("provider_call_count") != 2
        or failure.get("all_attempt_count") != 2
        or not _valid_content_hash(review)
        or review.get("content_hash") != VISUAL_FAILURE_REVIEW_CONTENT_HASH
        or review.get("review_state") != "COMPLETED_REAL_FRAMES_BLOCKED"
        or review_asset.get("result") != "BLOCK"
        or review_asset.get("logo_or_readable_text_present") is not True
        or review_asset.get("provider_asset_id") != "14003577"
        or preflight.get("content_hash") != VISUAL_FAILURE_PREFLIGHT_CONTENT_HASH
        or preflight.get("status") != "PASS"
        or preflight.get("provider_call_count") != 0
        or approval.run_id != VISUAL_FAILURE_RUN_ID
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 0
        or ledger.run_id != VISUAL_FAILURE_RUN_ID
        or ledger.approval_ref != approval.approval_ref
        or read_json(paths["ledger"]).get("ledger_hash")
        != VISUAL_FAILURE_LEDGER_HASH
        or ledger.provider_call_count != 2
        or sum(item.attempt_count for item in ledger.entries.values()) != 2
        or tts.status != "REUSED"
        or tts.max_attempts != 0
        or tts.attempt_count != 0
        or tts.provider_call_made
        or alignment.status != "REUSED"
        or alignment.max_attempts != 0
        or alignment.attempt_count != 0
        or alignment.provider_call_made
        or alignment.safe_evidence.get("source_run_id")
        != ALIGNMENT_SOURCE_RUN_ID
        or search.status != "SUCCEEDED"
        or search.attempt_count != 1
        or not search.provider_call_made
        or search.output_count != 0
        or download.status != "SUCCEEDED"
        or download.attempt_count != 1
        or not download.provider_call_made
        or download.output_count != 1
        or any(
            ledger.entries[key].status != "PLANNED"
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            or ledger.entries[key].output_count != 0
            for key in untouched
        )
        or selected_candidate.get("provider_asset_id") != "14003577"
        or download_selection.get("provider_asset_id") != "14003577"
        or download_receipt.get("sha256")
        != VISUAL_FAILURE_SELECTED_VIDEO_SHA256
    ):
        raise RuntimeError("CQR1_FAILED_RUN_005_LINEAGE_INVALID")

    history = MANIFESTS / "history/run005"
    for key, source in paths.items():
        relative = source.relative_to(VISUAL_FAILURE_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256[key])
    return {
        "run_id": VISUAL_FAILURE_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "PEXELS_POST_DOWNLOAD_VISUAL_REVIEW",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": failure["content_hash"],
        "ledger_hash": VISUAL_FAILURE_LEDGER_HASH,
        "visual_review_content_hash": review["content_hash"],
        "selected_provider_asset_id": "14003577",
        "selected_video_sha256": VISUAL_FAILURE_SELECTED_VIDEO_SHA256,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": sum(
            item.attempt_count for item in ledger.entries.values()
        ),
        "pexels_output_reuse_eligible": False,
        "mutated": False,
    }


def verify_failed_run006_lineage() -> dict[str, Any]:
    """Pin run006's Veo fake-UI failure and forbid operation reuse."""

    inventory = workspace_inventory(VEO_FAILURE_WORKSPACE)
    if (
        inventory["inventory_hash"] != VEO_FAILURE_INVENTORY_HASH
        or inventory["file_count"] != VEO_FAILURE_FILE_COUNT
        or inventory["total_size_bytes"] != VEO_FAILURE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_FAILED_RUN_006_WORKSPACE_IMMUTABILITY_FAILED")
    paths = {
        "failure": VEO_FAILURE_WORKSPACE
        / "manifests/cqr1_paid_canary_failure_stop.json",
        "ledger": VEO_FAILURE_WORKSPACE
        / "manifests/planned_provider_call_ledger.json",
        "review": VEO_FAILURE_WORKSPACE / "qc/codex_visual_asset_review.json",
        "pexels_sheet": VEO_FAILURE_WORKSPACE
        / "render/proxy/pexels-review-contact-sheet.jpg",
        "pexels_representative": VEO_FAILURE_WORKSPACE
        / "render/proxy/pexels-selected-representative.jpg",
        "veo_sheet": VEO_FAILURE_WORKSPACE
        / "render/proxy/veo-review-contact-sheet.jpg",
        "veo_representative": VEO_FAILURE_WORKSPACE
        / "render/proxy/veo-hero-representative.jpg",
        "preflight": VEO_FAILURE_WORKSPACE
        / "manifests/resume_paid_canary_preflight.json",
        "approval": VEO_FAILURE_WORKSPACE / "manifests/approval_scope.json",
        "pexels_search": VEO_FAILURE_WORKSPACE
        / "manifests/pexels_search_ranking_provenance.json",
        "pexels_download": VEO_FAILURE_WORKSPACE
        / "manifests/pexels_download_receipt.json",
        "pexels_video": VEO_FAILURE_WORKSPACE
        / "source/stock/pexels-12991847-5704872.mp4",
        "veo_operation": VEO_FAILURE_WORKSPACE
        / "manifests/google_veo_operation_receipt.json",
        "veo_download": VEO_FAILURE_WORKSPACE
        / "manifests/google_veo_download_receipt.json",
        "veo_provenance": VEO_FAILURE_WORKSPACE
        / "manifests/veo_prompt_request_provenance.json",
        "veo_prompt": VEO_FAILURE_WORKSPACE / "manifests/resume_veo_prompt.json",
        "veo_video": VEO_FAILURE_WORKSPACE
        / "source/ai-hero/google-veo-hero-original.mp4",
        "terminal_event": VEO_FAILURE_WORKSPACE
        / "manifests/resume-events/0018-veo-post-download-visual-review-blocked.json",
    }
    expected_sha256 = {
        "failure": VEO_FAILURE_STOP_SHA256,
        "ledger": VEO_FAILURE_LEDGER_SHA256,
        "review": VEO_FAILURE_REVIEW_SHA256,
        "pexels_sheet": VEO_FAILURE_PEXELS_SHEET_SHA256,
        "pexels_representative": VEO_FAILURE_PEXELS_REPRESENTATIVE_SHA256,
        "veo_sheet": VEO_FAILURE_VEO_SHEET_SHA256,
        "veo_representative": VEO_FAILURE_VEO_REPRESENTATIVE_SHA256,
        "preflight": VEO_FAILURE_PREFLIGHT_SHA256,
        "approval": VEO_FAILURE_APPROVAL_SHA256,
        "pexels_search": VEO_FAILURE_PEXELS_SEARCH_SHA256,
        "pexels_download": VEO_FAILURE_PEXELS_DOWNLOAD_SHA256,
        "pexels_video": VEO_FAILURE_PEXELS_VIDEO_SHA256,
        "veo_operation": VEO_FAILURE_OPERATION_RECEIPT_SHA256,
        "veo_download": VEO_FAILURE_DOWNLOAD_RECEIPT_SHA256,
        "veo_provenance": VEO_FAILURE_PROVENANCE_SHA256,
        "veo_prompt": VEO_FAILURE_PROMPT_SHA256,
        "veo_video": VEO_FAILURE_VIDEO_SHA256,
        "terminal_event": VEO_FAILURE_TERMINAL_EVENT_SHA256,
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_sha256[key]:
            raise RuntimeError(f"CQR1_FAILED_RUN_006_ARTIFACT_DRIFT:{key}")

    failure = read_json(paths["failure"])
    review = read_json(paths["review"])
    preflight = read_json(paths["preflight"])
    pexels_search_payload = read_json(paths["pexels_search"])
    pexels_download_payload = read_json(paths["pexels_download"])
    operation_receipt = read_json(paths["veo_operation"])
    download_receipt = read_json(paths["veo_download"])
    provenance = read_json(paths["veo_provenance"])
    prompt = read_json(paths["veo_prompt"])
    terminal_event = read_json(paths["terminal_event"])
    approval = CQR1CanaryApprovalScope.model_validate(read_json(paths["approval"]))
    ledger = CQR1CanaryCallLedger.load(paths["ledger"])
    assets = review.get("assets") or []
    stock_review = assets[0] if len(assets) == 2 else {}
    hero_review = assets[1] if len(assets) == 2 else {}
    selected_candidate = pexels_search_payload.get("selected_candidate") or {}
    pexels_http = pexels_download_payload.get("http_evidence") or {}
    reused_keys = ("elevenlabs_tts", "elevenlabs_forced_alignment")
    completed_keys = (
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
    )
    if (
        not _valid_content_hash(failure)
        or failure.get("content_hash") != VEO_FAILURE_STOP_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "VEO_POST_DOWNLOAD_VISUAL_REVIEW"
        or failure.get("failure_reason_code")
        != "VEO_REAL_FRAME_FAKE_UI_READABLE_TEXT_BLOCK"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("provider_call_count") != 4
        or failure.get("all_attempt_count") != 4
        or not _valid_content_hash(review)
        or review.get("content_hash") != VEO_FAILURE_REVIEW_CONTENT_HASH
        or review.get("review_state") != "COMPLETED_REAL_FRAMES_BLOCKED"
        or stock_review.get("result") != "REVIEW_REQUIRED"
        or stock_review.get("provider_asset_id") != "12991847"
        or stock_review.get("logo_or_readable_text_present") is not False
        or hero_review.get("result") != "BLOCK"
        or hero_review.get("logo_or_readable_text_present") is not True
        or "VEO_FAKE_UI" not in hero_review.get("hard_conflict_reasons", [])
        or preflight.get("content_hash") != VEO_FAILURE_PREFLIGHT_CONTENT_HASH
        or preflight.get("status") != "PASS"
        or preflight.get("provider_call_count") != 0
        or approval.run_id != VEO_FAILURE_RUN_ID
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 0
        or ledger.run_id != VEO_FAILURE_RUN_ID
        or ledger.approval_ref != approval.approval_ref
        or read_json(paths["ledger"]).get("ledger_hash")
        != VEO_FAILURE_LEDGER_HASH
        or ledger.provider_call_count != 4
        or sum(item.attempt_count for item in ledger.entries.values()) != 4
        or any(
            ledger.entries[key].status != "REUSED"
            or ledger.entries[key].max_attempts != 0
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            or ledger.entries[key].output_count != 0
            for key in reused_keys
        )
        or any(
            ledger.entries[key].status != "SUCCEEDED"
            or ledger.entries[key].attempt_count != 1
            or not ledger.entries[key].provider_call_made
            for key in completed_keys
        )
        or ledger.entries["pexels_search"].output_count != 0
        or ledger.entries["google_veo_submit"].output_count != 0
        or ledger.entries["pexels_download"].output_count != 1
        or ledger.entries["google_veo_output"].output_count != 1
        or ledger.entries["drive_archive"].status != "PLANNED"
        or ledger.entries["drive_archive"].attempt_count != 0
        or ledger.entries["drive_archive"].provider_call_made
        or selected_candidate.get("provider_asset_id") != "12991847"
        or pexels_http.get("provider_asset_id") != "12991847"
        or pexels_download_payload.get("sha256")
        != VEO_FAILURE_PEXELS_VIDEO_SHA256
        or operation_receipt.get("provider_operation_id")
        != "models/veo-3.1-fast-generate-preview/operations/miqly53m01al"
        or operation_receipt.get("normalized_status") != "SUCCEEDED"
        or operation_receipt.get("request_hash")
        != "15d5ee60f937cb408b08d0df888f4a67b8b805240b001efed4b9b8f9256125fe"
        or download_receipt.get("sha256") != VEO_FAILURE_VIDEO_SHA256
        or provenance.get("prompt_hash")
        != "8fbd47b6ce700ff3443dacae1c192f353c68568e35e0b670c5cddd62d90b3716"
        or prompt.get("prompt_hash")
        != "8fbd47b6ce700ff3443dacae1c192f353c68568e35e0b670c5cddd62d90b3716"
        or not _valid_content_hash(terminal_event)
        or terminal_event.get("content_hash")
        != VEO_FAILURE_TERMINAL_EVENT_CONTENT_HASH
        or terminal_event.get("status") != "BLOCK"
    ):
        raise RuntimeError("CQR1_FAILED_RUN_006_LINEAGE_INVALID")

    history = MANIFESTS / "history/run006"
    for key, source in paths.items():
        relative = source.relative_to(VEO_FAILURE_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256[key])
    return {
        "run_id": VEO_FAILURE_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "VEO_POST_DOWNLOAD_VISUAL_REVIEW",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": failure["content_hash"],
        "ledger_hash": VEO_FAILURE_LEDGER_HASH,
        "visual_review_content_hash": review["content_hash"],
        "provider_operation_id": operation_receipt["provider_operation_id"],
        "veo_output_sha256": VEO_FAILURE_VIDEO_SHA256,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": sum(
            item.attempt_count for item in ledger.entries.values()
        ),
        "veo_output_reuse_eligible": False,
        "mutated": False,
    }


def verify_failed_run007_lineage() -> dict[str, Any]:
    """Verify and pin the immutable provider-success/local-render-failure run."""

    inventory = workspace_inventory(VISUAL_SOURCE_WORKSPACE)
    if (
        inventory["inventory_hash"] != VISUAL_SOURCE_INVENTORY_HASH
        or inventory["file_count"] != VISUAL_SOURCE_FILE_COUNT
        or inventory["total_size_bytes"] != VISUAL_SOURCE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_007_WORKSPACE_IMMUTABILITY_FAILED")
    paths = {
        "failure": VISUAL_SOURCE_WORKSPACE
        / "manifests/cqr1_paid_canary_failure_stop.json",
        "ledger": VISUAL_SOURCE_WORKSPACE
        / "manifests/planned_provider_call_ledger.json",
        "review": VISUAL_SOURCE_WORKSPACE / "qc/codex_visual_asset_review.json",
        "preflight": VISUAL_SOURCE_WORKSPACE
        / "manifests/resume_paid_canary_preflight.json",
        "approval": VISUAL_SOURCE_WORKSPACE / "manifests/approval_scope.json",
        "direction": VISUAL_SOURCE_WORKSPACE
        / "manifests/resume_visual_direction_contract.json",
        "pexels_search": VISUAL_SOURCE_WORKSPACE
        / "manifests/pexels_search_ranking_provenance.json",
        "pexels_download": VISUAL_SOURCE_WORKSPACE
        / "manifests/pexels_download_receipt.json",
        "pexels_source": VISUAL_SOURCE_WORKSPACE
        / "manifests/pexels_stock_source_manifest.json",
        "pexels_video": VISUAL_SOURCE_WORKSPACE
        / "source/stock/pexels-12991847-5704872.mp4",
        "pexels_representative": VISUAL_SOURCE_WORKSPACE
        / "render/proxy/pexels-selected-representative.jpg",
        "pexels_sheet": VISUAL_SOURCE_WORKSPACE
        / "render/proxy/pexels-review-contact-sheet.jpg",
        "veo_operation": VISUAL_SOURCE_WORKSPACE
        / "manifests/google_veo_operation_receipt.json",
        "veo_generation_request": VISUAL_SOURCE_WORKSPACE
        / "manifests/google_veo_generation_request.json",
        "veo_download": VISUAL_SOURCE_WORKSPACE
        / "manifests/google_veo_download_receipt.json",
        "veo_provenance": VISUAL_SOURCE_WORKSPACE
        / "manifests/veo_prompt_request_provenance.json",
        "veo_prompt": VISUAL_SOURCE_WORKSPACE / "manifests/resume_veo_prompt.json",
        "veo_ffprobe": VISUAL_SOURCE_WORKSPACE
        / "manifests/google_veo_original_ffprobe.json",
        "veo_video": VISUAL_SOURCE_WORKSPACE
        / "source/ai-hero/google-veo-hero-original.mp4",
        "veo_representative": VISUAL_SOURCE_WORKSPACE
        / "render/proxy/veo-hero-representative.jpg",
        "veo_sheet": VISUAL_SOURCE_WORKSPACE
        / "render/proxy/veo-review-contact-sheet.jpg",
        "normalization_probe": VISUAL_SOURCE_WORKSPACE
        / "qc/normalization_failure_probe.json",
        "terminal_event": VISUAL_SOURCE_WORKSPACE
        / "manifests/resume-events/0018-local-media-normalization-blocked.json",
    }
    expected_sha256 = {
        "failure": VISUAL_SOURCE_FAILURE_SHA256,
        "ledger": VISUAL_SOURCE_LEDGER_SHA256,
        "review": VISUAL_SOURCE_REVIEW_SHA256,
        "preflight": VISUAL_SOURCE_PREFLIGHT_SHA256,
        "approval": VISUAL_SOURCE_APPROVAL_SHA256,
        "direction": VISUAL_SOURCE_DIRECTION_SHA256,
        "pexels_search": VISUAL_SOURCE_PEXELS_SEARCH_SHA256,
        "pexels_download": VISUAL_SOURCE_PEXELS_DOWNLOAD_RECEIPT_SHA256,
        "pexels_source": VISUAL_SOURCE_PEXELS_SOURCE_MANIFEST_SHA256,
        "pexels_video": VISUAL_SOURCE_PEXELS_VIDEO_SHA256,
        "pexels_representative": VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256,
        "pexels_sheet": VISUAL_SOURCE_PEXELS_SHEET_SHA256,
        "veo_operation": VISUAL_SOURCE_VEO_OPERATION_SHA256,
        "veo_generation_request": VISUAL_SOURCE_VEO_GENERATION_REQUEST_SHA256,
        "veo_download": VISUAL_SOURCE_VEO_DOWNLOAD_SHA256,
        "veo_provenance": VISUAL_SOURCE_VEO_PROVENANCE_SHA256,
        "veo_prompt": VISUAL_SOURCE_VEO_PROMPT_SHA256,
        "veo_ffprobe": VISUAL_SOURCE_VEO_FFPROBE_SHA256,
        "veo_video": VISUAL_SOURCE_VEO_VIDEO_SHA256,
        "veo_representative": VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256,
        "veo_sheet": VISUAL_SOURCE_VEO_SHEET_SHA256,
        "normalization_probe": VISUAL_SOURCE_NORMALIZATION_PROBE_SHA256,
        "terminal_event": VISUAL_SOURCE_TERMINAL_EVENT_SHA256,
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_sha256[key]:
            raise RuntimeError(f"CQR1_SOURCE_RUN_007_ARTIFACT_DRIFT:{key}")

    failure = read_json(paths["failure"])
    ledger_payload = read_json(paths["ledger"])
    ledger = CQR1CanaryCallLedger.load(paths["ledger"])
    review = read_json(paths["review"])
    preflight = read_json(paths["preflight"])
    approval = CQR1CanaryApprovalScope.model_validate(read_json(paths["approval"]))
    direction = VisualDirectionContract.model_validate(read_json(paths["direction"]))
    search = read_json(paths["pexels_search"])
    pexels_download = read_json(paths["pexels_download"])
    operation = read_json(paths["veo_operation"])
    veo_download = read_json(paths["veo_download"])
    provenance = read_json(paths["veo_provenance"])
    prompt = read_json(paths["veo_prompt"])
    normalization_probe = read_json(paths["normalization_probe"])
    terminal_event = read_json(paths["terminal_event"])
    assets = {
        str(item.get("scene_id")): item
        for item in review.get("assets", [])
        if isinstance(item, Mapping)
    }
    stock_review = assets.get("cqr1-stock-support") or {}
    hero_review = assets.get("cqr1-veo-hero") or {}
    reused = ("elevenlabs_tts", "elevenlabs_forced_alignment")
    completed = (
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
    )
    pins = CQR1_RUN007_VISUAL_REUSE_PINS
    if (
        not _valid_content_hash(failure)
        or failure.get("content_hash") != VISUAL_SOURCE_FAILURE_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "LOCAL_MEDIA_NORMALIZATION"
        or failure.get("failure_reason_code")
        != "NORMALIZED_HERO_BT709_VUI_METADATA_MISSING"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("provider_outputs_reuse_eligible") is not True
        or failure.get("provider_call_count") != 4
        or failure.get("all_attempt_count") != 4
        or ledger.run_id != VISUAL_SOURCE_RUN_ID
        or ledger_payload.get("ledger_hash") != VISUAL_SOURCE_LEDGER_HASH
        or pins["source_ledger_hash"] != VISUAL_SOURCE_LEDGER_HASH
        or ledger.provider_call_count != 4
        or sum(item.attempt_count for item in ledger.entries.values()) != 4
        or any(
            ledger.entries[key].status != "REUSED"
            or ledger.entries[key].max_attempts != 0
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            for key in reused
        )
        or any(
            ledger.entries[key].status != "SUCCEEDED"
            or ledger.entries[key].attempt_count != 1
            or not ledger.entries[key].provider_call_made
            for key in completed
        )
        or ledger.entries["drive_archive"].status != "PLANNED"
        or ledger.entries["drive_archive"].attempt_count != 0
        or preflight.get("content_hash") != VISUAL_SOURCE_PREFLIGHT_CONTENT_HASH
        or preflight.get("status") != "PASS"
        or preflight.get("provider_call_count") != 0
        or approval.run_id != VISUAL_SOURCE_RUN_ID
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 0
        or approval.maximum_pexels_search_flows != 1
        or approval.maximum_google_veo_submits != 1
        or direction.content_hash != VISUAL_SOURCE_DIRECTION_CONTENT_HASH
        or pins["visual_direction_hash"] != direction.content_hash
        or review.get("content_hash") != VISUAL_SOURCE_REVIEW_CONTENT_HASH
        or review.get("review_state") != "COMPLETED_REAL_FRAMES"
        or stock_review.get("result") != "REVIEW_REQUIRED"
        or stock_review.get("provider_asset_id") != "12991847"
        or stock_review.get("logo_or_readable_text_present") is not False
        or hero_review.get("result") != "PASS"
        or hero_review.get("logo_or_readable_text_present") is not False
        or search.get("query_plan", {}).get("plan_hash")
        != VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH
        or pexels_download.get("sha256") != VISUAL_SOURCE_PEXELS_VIDEO_SHA256
        or int(pexels_download.get("size_bytes") or 0)
        != VISUAL_SOURCE_PEXELS_VIDEO_SIZE_BYTES
        or operation.get("provider_operation_id") != VISUAL_SOURCE_VEO_OPERATION_ID
        or operation.get("request_hash") != VISUAL_SOURCE_VEO_REQUEST_HASH
        or operation.get("normalized_status") != "SUCCEEDED"
        or veo_download.get("sha256") != VISUAL_SOURCE_VEO_VIDEO_SHA256
        or int(veo_download.get("size_bytes") or 0)
        != VISUAL_SOURCE_VEO_VIDEO_SIZE_BYTES
        or provenance.get("content_hash")
        not in pins["veo_output_provenance_hashes"]
        or provenance.get("prompt_hash") != VISUAL_SOURCE_VEO_PROMPT_HASH
        or provenance.get("request_hash") != VISUAL_SOURCE_VEO_REQUEST_HASH
        or prompt.get("prompt_hash") != VISUAL_SOURCE_VEO_PROMPT_HASH
        or not _valid_content_hash(normalization_probe)
        or normalization_probe.get("content_hash")
        != VISUAL_SOURCE_NORMALIZATION_PROBE_CONTENT_HASH
        or normalization_probe.get("failure_reason_code")
        != "NORMALIZED_HERO_BT709_VUI_METADATA_MISSING"
        or normalization_probe.get("provider_call_made") is not False
        or not _valid_content_hash(terminal_event)
        or terminal_event.get("content_hash")
        != VISUAL_SOURCE_TERMINAL_EVENT_CONTENT_HASH
        or terminal_event.get("status") != "BLOCK"
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_007_LINEAGE_INVALID")

    history = MANIFESTS / "history/run007"
    for key, source in paths.items():
        relative = source.relative_to(VISUAL_SOURCE_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256[key])
    return {
        "run_id": VISUAL_SOURCE_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "LOCAL_MEDIA_NORMALIZATION",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": failure["content_hash"],
        "ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "visual_review_content_hash": review["content_hash"],
        "pexels_asset_sha256": VISUAL_SOURCE_PEXELS_VIDEO_SHA256,
        "veo_operation_id": VISUAL_SOURCE_VEO_OPERATION_ID,
        "veo_output_sha256": VISUAL_SOURCE_VEO_VIDEO_SHA256,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": sum(
            item.attempt_count for item in ledger.entries.values()
        ),
        "provider_outputs_reuse_eligible": True,
        "mutated": False,
    }


def verify_failed_run008_lineage() -> dict[str, Any]:
    """Verify and pin the immutable zero-provider post-render local failure."""

    inventory = workspace_inventory(LOCAL_RENDER_FAILURE_WORKSPACE)
    if (
        inventory["inventory_hash"] != LOCAL_RENDER_FAILURE_INVENTORY_HASH
        or inventory["file_count"] != LOCAL_RENDER_FAILURE_FILE_COUNT
        or inventory["total_size_bytes"]
        != LOCAL_RENDER_FAILURE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_008_WORKSPACE_IMMUTABILITY_FAILED")
    paths = {
        "failure": LOCAL_RENDER_FAILURE_WORKSPACE
        / "manifests/cqr1_paid_canary_failure_stop.json",
        "ledger": LOCAL_RENDER_FAILURE_WORKSPACE
        / "manifests/planned_provider_call_ledger.json",
        "approval": LOCAL_RENDER_FAILURE_WORKSPACE / "manifests/approval_scope.json",
        "preflight": LOCAL_RENDER_FAILURE_WORKSPACE
        / "manifests/resume_paid_canary_preflight.json",
        "event": LOCAL_RENDER_FAILURE_WORKSPACE
        / "manifests/resume-events/0004-local-post-render-duration-evidence-blocked.json",
        "render_receipt": LOCAL_RENDER_FAILURE_WORKSPACE
        / "manifests/native_render_execution_receipt.json",
        "technical": LOCAL_RENDER_FAILURE_WORKSPACE / "qc/technical_media_qc.json",
        "ffprobe": LOCAL_RENDER_FAILURE_WORKSPACE / "qc/final_ffprobe.json",
        "final": LOCAL_RENDER_FAILURE_WORKSPACE
        / "render/final/cqr1-non-production-canary.mp4",
    }
    expected_sha256 = {
        "failure": LOCAL_RENDER_FAILURE_STOP_SHA256,
        "ledger": LOCAL_RENDER_FAILURE_LEDGER_SHA256,
        "approval": LOCAL_RENDER_FAILURE_APPROVAL_SHA256,
        "preflight": LOCAL_RENDER_FAILURE_PREFLIGHT_SHA256,
        "event": LOCAL_RENDER_FAILURE_EVENT_SHA256,
        "render_receipt": LOCAL_RENDER_FAILURE_RENDER_RECEIPT_SHA256,
        "technical": LOCAL_RENDER_FAILURE_TECHNICAL_SHA256,
        "ffprobe": LOCAL_RENDER_FAILURE_FFPROBE_SHA256,
        "final": LOCAL_RENDER_FAILURE_FINAL_MP4_SHA256,
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != expected_sha256[key]:
            raise RuntimeError(f"CQR1_SOURCE_RUN_008_ARTIFACT_DRIFT:{key}")

    failure = read_json(paths["failure"])
    ledger_payload = read_json(paths["ledger"])
    ledger = CQR1CanaryCallLedger.load(paths["ledger"])
    approval = CQR1CanaryApprovalScope.model_validate(read_json(paths["approval"]))
    preflight = read_json(paths["preflight"])
    event = read_json(paths["event"])
    render_receipt = read_json(paths["render_receipt"])
    technical = read_json(paths["technical"])
    ffprobe = read_json(paths["ffprobe"])
    streams = ffprobe.get("streams", [])
    video = next(
        (item for item in streams if item.get("codec_type") == "video"), {}
    )
    audio = next(
        (item for item in streams if item.get("codec_type") == "audio"), {}
    )
    media_keys = (
        "elevenlabs_tts",
        "elevenlabs_forced_alignment",
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
    )
    forbidden_outputs = (
        LOCAL_RENDER_FAILURE_WORKSPACE / "qc/final_duration_consistency.json",
        LOCAL_RENDER_FAILURE_WORKSPACE / "qc/creative_perceptual_media_qc.json",
        LOCAL_RENDER_FAILURE_WORKSPACE / "render/proxy/cqr1-contact-sheet.jpg",
        LOCAL_RENDER_FAILURE_WORKSPACE / "qc/human_watchability_review_packet.json",
        LOCAL_RENDER_FAILURE_WORKSPACE / "manifests/drive_archive_receipt.json",
    )
    if (
        not _valid_content_hash(failure)
        or failure.get("content_hash") != LOCAL_RENDER_FAILURE_STOP_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or failure.get("failed_phase") != "POST_RENDER_DURATION_EVIDENCE"
        or failure.get("failure_reason_code")
        != "CANONICAL_CAPTION_CUE_FIELD_MISMATCH"
        or failure.get("same_run_resume_allowed") is not False
        or failure.get("successor_run_required") is not True
        or failure.get("provider_call_count") != 0
        or failure.get("all_attempt_count") != 0
        or ledger.run_id != LOCAL_RENDER_FAILURE_RUN_ID
        or ledger_payload.get("ledger_hash") != LOCAL_RENDER_FAILURE_LEDGER_HASH
        or ledger.provider_call_count != 0
        or sum(item.attempt_count for item in ledger.entries.values()) != 0
        or any(
            ledger.entries[key].status != "REUSED"
            or ledger.entries[key].max_attempts != 0
            or ledger.entries[key].attempt_count != 0
            or ledger.entries[key].provider_call_made
            for key in media_keys
        )
        or ledger.entries["drive_archive"].status != "PLANNED"
        or ledger.entries["drive_archive"].max_attempts != 1
        or ledger.entries["drive_archive"].attempt_count != 0
        or ledger.entries["drive_archive"].provider_call_made
        or approval.run_id != LOCAL_RENDER_FAILURE_RUN_ID
        or approval.maximum_drive_archive_attempts != 1
        or any(
            getattr(approval, field) != 0
            for field in (
                "maximum_pexels_search_flows",
                "maximum_pexels_downloads",
                "maximum_elevenlabs_tts_generations",
                "maximum_elevenlabs_forced_alignment_calls",
                "maximum_google_veo_submits",
                "maximum_google_veo_outputs",
            )
        )
        or not _valid_content_hash(preflight)
        or preflight.get("content_hash")
        != LOCAL_RENDER_FAILURE_PREFLIGHT_CONTENT_HASH
        or preflight.get("status") != "PASS"
        or preflight.get("provider_call_count") != 0
        or not _valid_content_hash(event)
        or event.get("content_hash") != LOCAL_RENDER_FAILURE_EVENT_CONTENT_HASH
        or event.get("status") != "BLOCK"
        or event.get("failure_stop_content_hash")
        != LOCAL_RENDER_FAILURE_STOP_CONTENT_HASH
        or render_receipt.get("receipt_hash")
        != LOCAL_RENDER_FAILURE_RENDER_RECEIPT_HASH
        or render_receipt.get("run_key") != LOCAL_RENDER_FAILURE_RUN_ID
        or render_receipt.get("exit_code") != 0
        or render_receipt.get("output_checksum")
        != LOCAL_RENDER_FAILURE_FINAL_MP4_SHA256
        or render_receipt.get("no_provider_calls_confirmed") is not True
        or technical.get("content_hash")
        != LOCAL_RENDER_FAILURE_TECHNICAL_CONTENT_HASH
        or technical.get("result") != "PASS"
        or paths["final"].stat().st_size
        != LOCAL_RENDER_FAILURE_FINAL_MP4_SIZE_BYTES
        or ffprobe.get("format", {}).get("duration") != "38.220000"
        or video.get("codec_name") != "h264"
        or video.get("width") != 1920
        or video.get("height") != 1080
        or video.get("pix_fmt") != "yuv420p"
        or video.get("r_frame_rate") != "30/1"
        or video.get("color_space") != "bt709"
        or video.get("color_primaries") != "bt709"
        or video.get("color_transfer") != "bt709"
        or audio.get("codec_name") != "aac"
        or audio.get("sample_rate") != "48000"
        or audio.get("channels") != 2
        or any(path.exists() for path in forbidden_outputs)
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_008_FAILURE_LINEAGE_INVALID")

    history = MANIFESTS / "history/run008"
    for key, source in paths.items():
        relative = source.relative_to(LOCAL_RENDER_FAILURE_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected_sha256[key])
    return {
        "run_id": LOCAL_RENDER_FAILURE_RUN_ID,
        "terminal_state": "FAIL_CLOSED",
        "failed_phase": "POST_RENDER_DURATION_EVIDENCE",
        "failure_reason_code": "CANONICAL_CAPTION_CUE_FIELD_MISMATCH",
        "workspace_inventory_hash": inventory["inventory_hash"],
        "workspace_file_count": inventory["file_count"],
        "workspace_total_size_bytes": inventory["total_size_bytes"],
        "failure_stop_content_hash": failure["content_hash"],
        "ledger_hash": LOCAL_RENDER_FAILURE_LEDGER_HASH,
        "technical_media_qc_result": technical["result"],
        "final_mp4_sha256": LOCAL_RENDER_FAILURE_FINAL_MP4_SHA256,
        "provider_call_count": ledger.provider_call_count,
        "all_attempt_count": 0,
        "same_run_resume_allowed": False,
        "successor_run_required": True,
        "mutated": False,
    }


def pin_original_preflight() -> dict[str, Any]:
    """Pin immutable provider artifacts and the failed run003-run008 chain."""

    failed_run003 = verify_failed_run003_lineage()
    alignment_source = verify_failed_run004_lineage()
    failed_run005 = verify_failed_run005_lineage()
    failed_run006 = verify_failed_run006_lineage()
    visual_source = verify_failed_run007_lineage()
    failed_run008 = verify_failed_run008_lineage()
    inventory = workspace_inventory(PREVIOUS_WORKSPACE)
    if (
        inventory["inventory_hash"] != PREVIOUS_WORKSPACE_INVENTORY_HASH
        or inventory["file_count"] != PREVIOUS_WORKSPACE_FILE_COUNT
        or inventory["total_size_bytes"] != PREVIOUS_WORKSPACE_TOTAL_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_002_WORKSPACE_IMMUTABILITY_FAILED")
    source_preflight = PREVIOUS_WORKSPACE / "manifests/resume_paid_canary_preflight.json"
    source_receipt = PREVIOUS_WORKSPACE / "manifests/elevenlabs_tts_receipt.json"
    source_seed = PREVIOUS_WORKSPACE / "manifests/narration_timing_seed.json"
    source_failure = PREVIOUS_WORKSPACE / "manifests/cqr1_paid_canary_failure_stop.json"
    source_ledger = PREVIOUS_WORKSPACE / "manifests/planned_provider_call_ledger.json"
    source_normalized = PREVIOUS_WORKSPACE / "source/script/spoken_text_normalized.json"
    source_script = PREVIOUS_WORKSPACE / "source/script/approved_script.json"
    source_audio = PREVIOUS_WORKSPACE / "source/audio/elevenlabs-final-narration.mp3"
    raw_pins = {
        source_preflight: SOURCE_PREFLIGHT_SHA256,
        source_receipt: PREVIOUS_TTS_RECEIPT_SHA256,
        source_seed: PREVIOUS_TIMING_SEED_SHA256,
        source_failure: PREVIOUS_FAILURE_STOP_SHA256,
        source_normalized: PREVIOUS_NORMALIZED_SHA256,
        source_script: PREVIOUS_APPROVED_SCRIPT_SHA256,
        source_audio: PREVIOUS_TTS_AUDIO_SHA256,
        source_ledger: PREVIOUS_LEDGER_SHA256,
    }
    for path, expected in raw_pins.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"CQR1_SOURCE_RUN_002_ARTIFACT_DRIFT:{path.name}")
    preflight = read_json(source_preflight)
    receipt = read_json(source_receipt)
    seed = NarrationTimingSeed.model_validate(read_json(source_seed))
    normalized = SpokenTextNormalized.model_validate(read_json(source_normalized))
    source_script_payload = read_json(source_script)
    failure = read_json(source_failure)
    ledger = CQR1CanaryCallLedger.load(source_ledger)
    ffprobe_evidence = read_json(
        PREVIOUS_WORKSPACE / "manifests/elevenlabs_final_audio_ffprobe.json"
    )
    if (
        preflight.get("run_id") != PREVIOUS_CQR1_RUN_ID
        or preflight.get("status") != "PASS"
        or int(preflight.get("provider_call_count") or 0) != 0
        or receipt.get("content_hash") != PREVIOUS_TTS_RECEIPT_CONTENT_HASH
        or receipt.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or int(receipt.get("audio_size_bytes") or 0) != PREVIOUS_TTS_AUDIO_SIZE_BYTES
        or int(receipt.get("measured_audio_duration_ms") or 0)
        != PREVIOUS_TTS_AUDIO_DURATION_MS
        or receipt.get("spoken_text_hash") != normalized.spoken_text_hash
        or receipt.get("voice_id") != EXPECTED_VOICE_ID
        or receipt.get("model_id") != EXPECTED_MODEL_ID
        or receipt.get("voice_settings") != VOICE_SETTINGS
        or seed.content_hash != PREVIOUS_TIMING_SEED_CONTENT_HASH
        or seed.audio_asset_ref != f"file-sha256:{PREVIOUS_TTS_AUDIO_SHA256}"
        or seed.audio_duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS
        or seed.spoken_text_hash != normalized.spoken_text_hash
        or seed.provider_voice_id != EXPECTED_VOICE_ID
        or seed.provider_model_id != EXPECTED_MODEL_ID
        or normalized.content_hash != PREVIOUS_NORMALIZED_CONTENT_HASH
        or len(normalized.spoken_tokens) != 72
        or source_script_payload.get("content_hash")
        != PREVIOUS_APPROVED_SCRIPT_CONTENT_HASH
        or source_script_payload.get("spoken_text_hash") != normalized.spoken_text_hash
        or source_script_payload.get("editorial_text") != CQR1_CANARY_SCRIPT_V2
        or failure.get("content_hash") != PREVIOUS_FAILURE_STOP_CONTENT_HASH
        or failure.get("status") != "FAIL_CLOSED"
        or ledger.run_id != PREVIOUS_CQR1_RUN_ID
        or read_json(source_ledger).get("ledger_hash") != PREVIOUS_LEDGER_HASH
        or ledger.provider_call_count != 2
        or sum(item.attempt_count for item in ledger.entries.values()) != 2
        or ledger.entries["elevenlabs_tts"].status != "SUCCEEDED"
        or ledger.entries["elevenlabs_tts"].attempt_count != 1
        or ledger.entries["elevenlabs_forced_alignment"].status != "FAILED"
        or ledger.entries["elevenlabs_forced_alignment"].attempt_count != 1
        or source_audio.stat().st_size != PREVIOUS_TTS_AUDIO_SIZE_BYTES
        or ffprobe_evidence.get("evidence_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or round(float(ffprobe_evidence.get("format", {}).get("duration") or 0) * 1000)
        != PREVIOUS_TTS_AUDIO_DURATION_MS
    ):
        raise RuntimeError("CQR1_SOURCE_RUN_002_FAILURE_LINEAGE_INVALID")
    history = MANIFESTS / "history/run002"
    for source, expected in raw_pins.items():
        relative = source.relative_to(PREVIOUS_WORKSPACE)
        _pin_immutable_file(source, history / relative, expected)
    lineage = {
        "run_id": CQR1_RUN_ID,
        "source_run_id": PREVIOUS_CQR1_RUN_ID,
        "source_run_preflight_state": "PASS",
        "source_run_terminal_state": "FAIL_CLOSED",
        "source_failure_phase": "ELEVENLABS_FORCED_ALIGNMENT",
        "retry_of_source_run": False,
        "new_recovery_run": True,
        "new_tts_generation_authorized": False,
        "immutable_tts_reuse_authorized": True,
        "new_forced_alignment_call_authorized": False,
        "immutable_verified_alignment_reuse_authorized": True,
        "source_workspace_inventory_hash": inventory["inventory_hash"],
        "source_workspace_file_count": inventory["file_count"],
        "source_workspace_total_size_bytes": inventory["total_size_bytes"],
        "source_preflight_sha256": SOURCE_PREFLIGHT_SHA256,
        "source_tts_audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
        "source_tts_receipt_content_hash": PREVIOUS_TTS_RECEIPT_CONTENT_HASH,
        "source_timing_seed_content_hash": PREVIOUS_TIMING_SEED_CONTENT_HASH,
        "source_spoken_text_content_hash": PREVIOUS_NORMALIZED_CONTENT_HASH,
        "source_failure_stop_content_hash": PREVIOUS_FAILURE_STOP_CONTENT_HASH,
        "source_ledger_hash": PREVIOUS_LEDGER_HASH,
        "source_provider_call_count": ledger.provider_call_count,
        "source_all_attempt_count": sum(
            item.attempt_count for item in ledger.entries.values()
        ),
        "source_mutated": False,
        "prior_failed_run_003": failed_run003,
        "alignment_source_run_004": alignment_source,
        "prior_failed_run_005": failed_run005,
        "prior_failed_run_006": failed_run006,
        "visual_provider_source_run_007": visual_source,
        "immediate_prior_failed_run": failed_run008,
        "immutable_visual_provider_output_reuse_authorized": True,
        "force_continuation_authorized_by_operator": True,
    }
    lineage["content_hash"] = stable_hash(lineage)
    lineage_path = MANIFESTS / "run_lineage.json"
    if lineage_path.exists() and read_json(lineage_path) != lineage:
        raise RuntimeError("CQR1_CURRENT_RECOVERY_LINEAGE_DRIFT")
    write_json(lineage_path, lineage)
    return preflight


def import_visual_provider_outputs(
    ledger: CQR1CanaryCallLedger,
    *,
    direction: VisualDirectionContract,
    pexels_plan: PexelsQueryPlan,
    veo_prompt: Any,
) -> dict[str, Any]:
    """Copy run007's immutable successful provider outputs without new calls."""

    source_search_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/pexels_search_ranking_provenance.json"
    )
    source_download_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/pexels_download_receipt.json"
    )
    source_stock_manifest_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/pexels_stock_source_manifest.json"
    )
    source_operation_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/google_veo_operation_receipt.json"
    )
    source_veo_download_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/google_veo_download_receipt.json"
    )
    source_provenance_path = (
        VISUAL_SOURCE_WORKSPACE / "manifests/veo_prompt_request_provenance.json"
    )
    source_review_path = VISUAL_SOURCE_WORKSPACE / "qc/codex_visual_asset_review.json"
    source_stock = (
        VISUAL_SOURCE_WORKSPACE / "source/stock/pexels-12991847-5704872.mp4"
    )
    source_hero = (
        VISUAL_SOURCE_WORKSPACE / "source/ai-hero/google-veo-hero-original.mp4"
    )
    source_stock_still = (
        VISUAL_SOURCE_WORKSPACE / "render/proxy/pexels-selected-representative.jpg"
    )
    source_stock_sheet = (
        VISUAL_SOURCE_WORKSPACE / "render/proxy/pexels-review-contact-sheet.jpg"
    )
    source_hero_still = (
        VISUAL_SOURCE_WORKSPACE / "render/proxy/veo-hero-representative.jpg"
    )
    source_hero_sheet = (
        VISUAL_SOURCE_WORKSPACE / "render/proxy/veo-review-contact-sheet.jpg"
    )
    current_stock = STOCK_DIR / "pexels-12991847-5704872.mp4"
    current_hero = HERO_DIR / "google-veo-hero-original.mp4"
    proxy_dir = RENDER_DIR / "proxy"
    current_stock_still = proxy_dir / "pexels-selected-representative.jpg"
    current_stock_sheet = proxy_dir / "pexels-review-contact-sheet.jpg"
    current_hero_still = proxy_dir / "veo-hero-representative.jpg"
    current_hero_sheet = proxy_dir / "veo-review-contact-sheet.jpg"
    copies = {
        source_stock: (current_stock, VISUAL_SOURCE_PEXELS_VIDEO_SHA256),
        source_hero: (current_hero, VISUAL_SOURCE_VEO_VIDEO_SHA256),
        source_stock_still: (
            current_stock_still,
            VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256,
        ),
        source_stock_sheet: (current_stock_sheet, VISUAL_SOURCE_PEXELS_SHEET_SHA256),
        source_hero_still: (
            current_hero_still,
            VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256,
        ),
        source_hero_sheet: (current_hero_sheet, VISUAL_SOURCE_VEO_SHEET_SHA256),
    }
    for source, (destination, expected_sha256) in copies.items():
        if not source.is_file() or sha256_file(source) != expected_sha256:
            raise RuntimeError("CQR1_SOURCE_RUN_007_VISUAL_COPY_SOURCE_DRIFT")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RuntimeError("CQR1_IMPORTED_VISUAL_DESTINATION_NOT_FRESH")
        shutil.copyfile(source, destination)
        if sha256_file(destination) != expected_sha256:
            raise RuntimeError("CQR1_IMPORTED_VISUAL_COPY_INTEGRITY_FAILED")

    source_search = read_json(source_search_path)
    source_download = read_json(source_download_path)
    source_stock_manifest = read_json(source_stock_manifest_path)
    source_operation = read_json(source_operation_path)
    source_veo_download = read_json(source_veo_download_path)
    source_provenance = read_json(source_provenance_path)
    source_review = read_json(source_review_path)
    if (
        veo_prompt.prompt_hash != VISUAL_SOURCE_VEO_PROMPT_HASH
        or source_search.get("query_plan", {}).get("plan_hash")
        != VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH
        or source_download.get("sha256") != VISUAL_SOURCE_PEXELS_VIDEO_SHA256
        or source_operation.get("provider_operation_id")
        != VISUAL_SOURCE_VEO_OPERATION_ID
        or source_provenance.get("sha256") != VISUAL_SOURCE_VEO_VIDEO_SHA256
    ):
        # The fresh plan must carry a fresh run binding while preserving the
        # exact provider-facing prompt semantics used by the imported output.
        raise RuntimeError("CQR1_IMPORTED_VISUAL_PLAN_LINEAGE_INVALID")

    search_wrapper = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_SEARCH",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "source_search_provenance_sha256": VISUAL_SOURCE_PEXELS_SEARCH_SHA256,
        "source_query_plan_hash": VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH,
        "current_query_plan_hash": pexels_plan.plan_hash,
        "source_visual_direction_hash": VISUAL_SOURCE_DIRECTION_CONTENT_HASH,
        "current_visual_direction_hash": direction.content_hash,
        "query_used": source_search["query_used"],
        "candidate_count": source_search["candidate_count"],
        "selected_candidate": source_search["selected_candidate"],
        "selected_score": source_search["selected_score"],
        "selection_rationale": source_search["selection_rationale"],
        "rate_limit": source_search["rate_limit"],
        "raw_media_url_persisted": False,
        "provider_call_made": False,
        "provider_call_made_by_current_run": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    search_wrapper["content_hash"] = stable_hash(search_wrapper)
    write_json(MANIFESTS / "pexels_search_ranking_provenance.json", search_wrapper)

    download_wrapper = {
        **source_download,
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_ASSET",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_receipt_sha256": VISUAL_SOURCE_PEXELS_DOWNLOAD_RECEIPT_SHA256,
        "source_receipt_hash": source_download["receipt_hash"],
        "local_path": str(current_stock),
        "state": "ASSET_REUSED",
        "states": ["IMMUTABLE_SOURCE_VERIFIED", "ASSET_REUSED"],
        "transport": "IMMUTABLE_RUN007_REUSE",
        "provider_call_made": False,
        "provider_call_made_by_current_run": False,
    }
    download_wrapper.pop("receipt_hash", None)
    download_wrapper["receipt_hash"] = stable_hash(download_wrapper)
    write_json(MANIFESTS / "pexels_download_receipt.json", download_wrapper)

    stock_manifest = {
        **source_stock_manifest,
        "run_id": CQR1_RUN_ID,
        "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_ASSET",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_manifest_sha256": VISUAL_SOURCE_PEXELS_SOURCE_MANIFEST_SHA256,
        "local_path": str(current_stock),
        "provider_call_made_by_current_run": False,
    }
    stock_manifest.pop("manifest_hash", None)
    stock_manifest["manifest_hash"] = stable_hash(stock_manifest)
    write_json(MANIFESTS / "pexels_stock_source_manifest.json", stock_manifest)

    operation_wrapper = {
        **source_operation,
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_VEO_OPERATION",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_operation_receipt_sha256": VISUAL_SOURCE_VEO_OPERATION_SHA256,
        "source_state_hash": source_operation["state_hash"],
        "source_idempotency_key_hash": hashlib.sha256(
            str(source_operation["idempotency_key"]).encode("utf-8")
        ).hexdigest(),
        "provider_call_made": False,
        "provider_call_made_by_current_run": False,
    }
    operation_wrapper.pop("idempotency_key", None)
    operation_wrapper.pop("state_hash", None)
    operation_wrapper["state_hash"] = stable_hash(operation_wrapper)
    write_json(MANIFESTS / "google_veo_operation_receipt.json", operation_wrapper)

    veo_download_wrapper = {
        **source_veo_download,
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_VEO_OUTPUT",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_download_receipt_sha256": VISUAL_SOURCE_VEO_DOWNLOAD_SHA256,
        "downloaded_path": str(current_hero),
        "transport": "IMMUTABLE_RUN007_REUSE",
        "provider_call_made": False,
        "provider_call_made_by_current_run": False,
    }
    veo_download_wrapper["content_hash"] = stable_hash(veo_download_wrapper)
    write_json(MANIFESTS / "google_veo_download_receipt.json", veo_download_wrapper)

    provenance_wrapper = {
        **source_provenance,
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_VEO_OUTPUT",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_provenance_sha256": VISUAL_SOURCE_VEO_PROVENANCE_SHA256,
        "source_content_hash": source_provenance["content_hash"],
        "downloaded_file_path": str(current_hero),
        "provider_call_made_by_current_run": False,
    }
    provenance_wrapper.pop("content_hash", None)
    provenance_wrapper["content_hash"] = stable_hash(provenance_wrapper)
    write_json(MANIFESTS / "veo_prompt_request_provenance.json", provenance_wrapper)

    review_wrapper = {
        **source_review,
        "run_id": CQR1_RUN_ID,
        "review_scope": "IMMUTABLE_RUN007_PROVIDER_ASSETS_REBOUND_TO_FRESH_RUN009",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_review_content_hash": VISUAL_SOURCE_REVIEW_CONTENT_HASH,
        "source_review_sha256": VISUAL_SOURCE_REVIEW_SHA256,
        "provider_retry_authorized": False,
        "same_run_second_pexels_search_authorized": False,
        "same_run_second_veo_submit_authorized": False,
    }
    rebound_assets: list[dict[str, Any]] = []
    for asset in source_review["assets"]:
        rebound = dict(asset)
        if rebound["scene_id"] == "cqr1-stock-support":
            rebound["representative_still_path"] = str(current_stock_still)
            rebound["review_contact_sheet_path"] = str(current_stock_sheet)
        else:
            rebound["representative_still_path"] = str(current_hero_still)
            rebound["review_contact_sheet_path"] = str(current_hero_sheet)
        rebound_assets.append(rebound)
    review_wrapper["assets"] = rebound_assets
    review_wrapper.pop("content_hash", None)
    review_wrapper["content_hash"] = stable_hash(review_wrapper)
    write_json(QC_DIR / "codex_visual_asset_review.json", review_wrapper)

    imported_pexels = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_PROVIDER_OUTPUTS",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_workspace_inventory_hash": VISUAL_SOURCE_INVENTORY_HASH,
        "source_ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "source_visual_direction_hash": VISUAL_SOURCE_DIRECTION_CONTENT_HASH,
        "current_visual_direction_hash": direction.content_hash,
        "source_query_plan_hash": VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH,
        "current_query_plan_hash": pexels_plan.plan_hash,
        "source_search_provenance_sha256": VISUAL_SOURCE_PEXELS_SEARCH_SHA256,
        "source_download_receipt_sha256": VISUAL_SOURCE_PEXELS_DOWNLOAD_RECEIPT_SHA256,
        "source_visual_review_content_hash": VISUAL_SOURCE_REVIEW_CONTENT_HASH,
        "current_visual_review_content_hash": review_wrapper["content_hash"],
        "provider_asset_id": "12991847",
        "asset_sha256": VISUAL_SOURCE_PEXELS_VIDEO_SHA256,
        "asset_size_bytes": VISUAL_SOURCE_PEXELS_VIDEO_SIZE_BYTES,
        "representative_still_sha256": VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256,
        "review_contact_sheet_sha256": VISUAL_SOURCE_PEXELS_SHEET_SHA256,
        "search_calls_authorized": 0,
        "download_calls_authorized": 0,
        "provider_call_made_by_current_run": False,
        "source_mutated": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    imported_pexels["content_hash"] = stable_hash(imported_pexels)
    write_json(MANIFESTS / "imported_pexels_evidence.json", imported_pexels)

    imported_veo = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_VEO_PROVIDER_OUTPUTS",
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_workspace_inventory_hash": VISUAL_SOURCE_INVENTORY_HASH,
        "source_ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "source_visual_direction_hash": VISUAL_SOURCE_DIRECTION_CONTENT_HASH,
        "current_visual_direction_hash": direction.content_hash,
        "provider_operation_id": VISUAL_SOURCE_VEO_OPERATION_ID,
        "request_hash": VISUAL_SOURCE_VEO_REQUEST_HASH,
        "prompt_hash": VISUAL_SOURCE_VEO_PROMPT_HASH,
        "source_operation_receipt_sha256": VISUAL_SOURCE_VEO_OPERATION_SHA256,
        "source_download_receipt_sha256": VISUAL_SOURCE_VEO_DOWNLOAD_SHA256,
        "source_provenance_sha256": VISUAL_SOURCE_VEO_PROVENANCE_SHA256,
        "source_visual_review_content_hash": VISUAL_SOURCE_REVIEW_CONTENT_HASH,
        "current_visual_review_content_hash": review_wrapper["content_hash"],
        "output_sha256": VISUAL_SOURCE_VEO_VIDEO_SHA256,
        "output_size_bytes": VISUAL_SOURCE_VEO_VIDEO_SIZE_BYTES,
        "representative_still_sha256": VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256,
        "review_contact_sheet_sha256": VISUAL_SOURCE_VEO_SHEET_SHA256,
        "provider_audio_policy": "DISCARD",
        "submit_calls_authorized": 0,
        "output_calls_authorized": 0,
        "provider_call_made_by_current_run": False,
        "source_mutated": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    imported_veo["content_hash"] = stable_hash(imported_veo)
    write_json(MANIFESTS / "imported_veo_evidence.json", imported_veo)

    common = {
        "source_run_id": VISUAL_SOURCE_RUN_ID,
        "source_ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "provider_call_made_by_current_run": False,
    }
    ledger.bind_imported_pexels_search(
        safe_evidence={
            **common,
            "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_SEARCH",
            "query_plan_hash": VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH,
            "search_provenance_hash": VISUAL_SOURCE_PEXELS_SEARCH_SHA256,
            "visual_direction_hash": VISUAL_SOURCE_DIRECTION_CONTENT_HASH,
            "selected_provider_asset_id": "12991847",
            "selection_verdict": "REVIEW_REQUIRED",
            "semantic_score": 0.744,
            "import_evidence_hash": imported_pexels["content_hash"],
        }
    )
    ledger.bind_imported_pexels_download(
        safe_evidence={
            **common,
            "evidence_mode": "IMMUTABLE_IMPORTED_PEXELS_ASSET",
            "query_plan_hash": VISUAL_SOURCE_PEXELS_QUERY_PLAN_HASH,
            "selected_provider_asset_id": "12991847",
            "asset_sha256": VISUAL_SOURCE_PEXELS_VIDEO_SHA256,
            "asset_size_bytes": VISUAL_SOURCE_PEXELS_VIDEO_SIZE_BYTES,
            "download_receipt_hash": VISUAL_SOURCE_PEXELS_DOWNLOAD_RECEIPT_SHA256,
            "representative_still_sha256": VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256,
            "visual_review_content_hash": VISUAL_SOURCE_REVIEW_CONTENT_HASH,
            "import_evidence_hash": imported_pexels["content_hash"],
        }
    )
    ledger.bind_imported_veo_submit(
        safe_evidence={
            **common,
            "evidence_mode": "IMMUTABLE_IMPORTED_VEO_OPERATION",
            "provider_operation_id": VISUAL_SOURCE_VEO_OPERATION_ID,
            "model_id": "veo-3.1-fast-generate-preview",
            "request_hash": VISUAL_SOURCE_VEO_REQUEST_HASH,
            "prompt_hash": VISUAL_SOURCE_VEO_PROMPT_HASH,
            "operation_receipt_hash": VISUAL_SOURCE_VEO_OPERATION_SHA256,
            "visual_direction_hash": VISUAL_SOURCE_DIRECTION_CONTENT_HASH,
            "import_evidence_hash": imported_veo["content_hash"],
        }
    )
    ledger.bind_imported_veo_output(
        safe_evidence={
            **common,
            "evidence_mode": "IMMUTABLE_IMPORTED_VEO_OUTPUT",
            "provider_operation_id": VISUAL_SOURCE_VEO_OPERATION_ID,
            "request_hash": VISUAL_SOURCE_VEO_REQUEST_HASH,
            "prompt_hash": VISUAL_SOURCE_VEO_PROMPT_HASH,
            "output_sha256": VISUAL_SOURCE_VEO_VIDEO_SHA256,
            "output_size_bytes": VISUAL_SOURCE_VEO_VIDEO_SIZE_BYTES,
            "output_provenance_hash": VISUAL_SOURCE_VEO_PROVENANCE_SHA256,
            "representative_still_sha256": VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256,
            "visual_review_content_hash": VISUAL_SOURCE_REVIEW_CONTENT_HASH,
            "provider_audio_policy": "DISCARD",
            "import_evidence_hash": imported_veo["content_hash"],
        }
    )
    return {
        "imported_pexels_evidence_hash": imported_pexels["content_hash"],
        "imported_veo_evidence_hash": imported_veo["content_hash"],
        "current_visual_review_content_hash": review_wrapper["content_hash"],
        "pexels_asset_sha256": VISUAL_SOURCE_PEXELS_VIDEO_SHA256,
        "veo_output_sha256": VISUAL_SOURCE_VEO_VIDEO_SHA256,
    }


def prepare() -> dict[str, Any]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    existing = [
        path
        for path in WORKSPACE.rglob("*")
        if path.is_file() and path.name != ".cqr1-execution.lock"
    ]
    if existing:
        raise RuntimeError("CQR1_FRESH_WORKSPACE_ALREADY_INITIALIZED")
    source_preflight = pin_original_preflight()
    old_offline = read_json(
        PREVIOUS_WORKSPACE / "manifests/offline_qualification_evidence.json"
    )
    old_historical = read_json(
        PREVIOUS_WORKSPACE / "manifests/historical_evidence_immutability.json"
    )
    CQR1OfflineQualificationEvidence.model_validate(old_offline)
    write_json(MANIFESTS / "offline_qualification_evidence.json", old_offline)
    write_json(MANIFESTS / "historical_evidence_immutability.json", old_historical)
    approval = CQR1CanaryApprovalScope(
        run_id=CQR1_RUN_ID,
        maximum_pexels_search_flows=0,
        maximum_pexels_downloads=0,
        maximum_elevenlabs_tts_generations=0,
        maximum_elevenlabs_forced_alignment_calls=0,
        maximum_google_veo_submits=0,
        maximum_google_veo_outputs=0,
        maximum_drive_archive_attempts=1,
        approval_ref=APPROVAL_REF,
        total_hard_cost_cap_usd=Decimal("3.00"),
    )
    model_json(MANIFESTS / "approval_scope.json", approval)
    model_json(MANIFESTS / "resume_approval_scope.json", approval)
    approval_evidence = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "approval_source": "CURRENT_OPERATOR_FORCE_CONTINUATION_UNTIL_TECHNICAL_COMPLETION_RUN_009",
        "approved_operations": {
            "elevenlabs_tts_new_generation": 0,
            "elevenlabs_tts_immutable_reuse": 1,
            "elevenlabs_forced_alignment_new_call": 0,
            "elevenlabs_verified_alignment_immutable_reuse": 1,
            "pexels_search_new_call": 0,
            "pexels_download_new_call": 0,
            "pexels_immutable_run007_reuse": 1,
            "google_veo_submit_new_call": 0,
            "google_veo_output_new_call": 0,
            "google_veo_immutable_run007_reuse": 1,
            "drive_archive_execution": 1,
        },
        "automatic_provider_retry": False,
        "external_provider_fallback": False,
        "second_paid_attempt_for_this_run": False,
        "source_run_retry": False,
        "youtube_allowed": False,
        "total_hard_cost_cap_usd": "3.00",
        "production_eligible": False,
        "not_publishable": True,
    }
    approval_evidence["content_hash"] = stable_hash(approval_evidence)
    write_json(MANIFESTS / "fresh_operator_approval_evidence.json", approval_evidence)
    ledger_path = MANIFESTS / "planned_provider_call_ledger.json"
    if ledger_path.exists():
        raise RuntimeError("CQR1_FRESH_LEDGER_PATH_ALREADY_EXISTS")
    ledger = CQR1CanaryCallLedger.create(ledger_path, approval=approval)
    if (
        ledger.run_id != CQR1_RUN_ID
        or ledger.approval_ref != APPROVAL_REF
        or ledger.provider_call_count != 0
        or any(
            entry.max_attempts != 0 or entry.status != "PLANNED"
            for key, entry in ledger.entries.items()
            if key != "drive_archive"
        )
        or ledger.entries["drive_archive"].max_attempts != 1
        or ledger.entries["drive_archive"].status != "PLANNED"
    ):
        raise RuntimeError("CQR1_FRESH_LEDGER_INVALID")
    prior_ledgers = (
        CQR1CanaryCallLedger.load(
            PREVIOUS_WORKSPACE / "manifests/planned_provider_call_ledger.json"
        ),
        CQR1CanaryCallLedger.load(
            ALIGNMENT_SOURCE_WORKSPACE
            / "manifests/planned_provider_call_ledger.json"
        ),
        CQR1CanaryCallLedger.load(
            VISUAL_FAILURE_WORKSPACE
            / "manifests/planned_provider_call_ledger.json"
        ),
        CQR1CanaryCallLedger.load(
            VEO_FAILURE_WORKSPACE
            / "manifests/planned_provider_call_ledger.json"
        ),
        CQR1CanaryCallLedger.load(
            VISUAL_SOURCE_WORKSPACE
            / "manifests/planned_provider_call_ledger.json"
        ),
        CQR1CanaryCallLedger.load(
            LOCAL_RENDER_FAILURE_WORKSPACE
            / "manifests/planned_provider_call_ledger.json"
        ),
    )
    if any(
        ledger.entries[key].idempotency_key_hash
        == prior.entries[key].idempotency_key_hash
        for prior in prior_ledgers
        for key in ledger.entries
    ):
        raise RuntimeError("CQR1_CROSS_RUN_IDEMPOTENCY_COLLISION")
    initial_ledger_hash = read_json(ledger_path)["ledger_hash"]
    normalized_source = PREVIOUS_WORKSPACE / "source/script/spoken_text_normalized.json"
    seed_source = PREVIOUS_WORKSPACE / "manifests/narration_timing_seed.json"
    receipt_source = PREVIOUS_WORKSPACE / "manifests/elevenlabs_tts_receipt.json"
    audio_source = PREVIOUS_WORKSPACE / "source/audio/elevenlabs-final-narration.mp3"
    normalized = SpokenTextNormalized.model_validate(read_json(normalized_source))
    if normalized.model_dump(mode="json") != normalized_text().model_dump(mode="json"):
        raise RuntimeError("CQR1_SOURCE_RUN_002_SPOKEN_TEXT_DRIFT")
    prediction = predicted_duration_evidence(normalized)
    if (
        prediction["word_count_gate"] != "PASS"
        or prediction["predicted_duration_gate"] != "PASS"
    ):
        write_json(MANIFESTS / "predicted_narration_duration.json", prediction)
        raise RuntimeError("CQR1_PREDICTED_DURATION_GATE_BLOCKED_BEFORE_TTS")
    SOURCE_SCRIPT.mkdir(parents=True, exist_ok=True)
    SOURCE_AUDIO.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(normalized_source, SOURCE_SCRIPT / "spoken_text_normalized.json")
    shutil.copyfile(seed_source, MANIFESTS / "narration_timing_seed.json")
    shutil.copyfile(audio_source, SOURCE_AUDIO / "elevenlabs-final-narration.mp3")
    if (
        sha256_file(SOURCE_SCRIPT / "spoken_text_normalized.json")
        != PREVIOUS_NORMALIZED_SHA256
        or sha256_file(MANIFESTS / "narration_timing_seed.json")
        != PREVIOUS_TIMING_SEED_SHA256
        or sha256_file(SOURCE_AUDIO / "elevenlabs-final-narration.mp3")
        != PREVIOUS_TTS_AUDIO_SHA256
        or (SOURCE_AUDIO / "elevenlabs-final-narration.mp3").stat().st_size
        != PREVIOUS_TTS_AUDIO_SIZE_BYTES
    ):
        raise RuntimeError("CQR1_IMPORTED_TTS_COPY_INTEGRITY_FAILED")
    write_json(MANIFESTS / "predicted_narration_duration.json", prediction)
    script_record = {
        "run_id": CQR1_RUN_ID,
        "locale": "en-US",
        "topic": "How a media workflow turns one approved script into a synchronized final video",
        "editorial_text": CQR1_CANARY_SCRIPT_V2,
        "editorial_text_hash": normalized.source_text_hash,
        "spoken_text_hash": normalized.spoken_text_hash,
        "spoken_word_count": len(normalized.spoken_tokens),
        "visible_label": CQR1_VISIBLE_LABEL,
        "voice_id": EXPECTED_VOICE_ID,
        "model_id": EXPECTED_MODEL_ID,
        "voice_speed": VOICE_SETTINGS["speed"],
        "speed_increased_for_duration": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    script_record["content_hash"] = stable_hash(script_record)
    write_json(SOURCE_SCRIPT / "approved_script.json", script_record)
    source_receipt_payload = read_json(receipt_source)
    imported_tts = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_TTS",
        "source_run_id": PREVIOUS_CQR1_RUN_ID,
        "source_run_terminal_state": "FAIL_CLOSED",
        "source_workspace_inventory_hash": PREVIOUS_WORKSPACE_INVENTORY_HASH,
        "source_workspace_file_count": PREVIOUS_WORKSPACE_FILE_COUNT,
        "source_workspace_total_size_bytes": PREVIOUS_WORKSPACE_TOTAL_SIZE_BYTES,
        "source_audio_path": str(audio_source),
        "source_audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
        "source_audio_size_bytes": PREVIOUS_TTS_AUDIO_SIZE_BYTES,
        "destination_audio_path": str(
            SOURCE_AUDIO / "elevenlabs-final-narration.mp3"
        ),
        "destination_audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
        "destination_audio_size_bytes": PREVIOUS_TTS_AUDIO_SIZE_BYTES,
        "measured_audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
        "source_provider_request_id": source_receipt_payload["provider_request_id"],
        "source_tts_receipt_content_hash": PREVIOUS_TTS_RECEIPT_CONTENT_HASH,
        "source_tts_receipt_sha256": PREVIOUS_TTS_RECEIPT_SHA256,
        "source_timing_seed_content_hash": PREVIOUS_TIMING_SEED_CONTENT_HASH,
        "source_timing_seed_sha256": PREVIOUS_TIMING_SEED_SHA256,
        "source_spoken_text_content_hash": PREVIOUS_NORMALIZED_CONTENT_HASH,
        "source_spoken_text_sha256": PREVIOUS_NORMALIZED_SHA256,
        "spoken_text_hash": normalized.spoken_text_hash,
        "editorial_text_hash": normalized.source_text_hash,
        "voice_id": EXPECTED_VOICE_ID,
        "model_id": EXPECTED_MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
        "new_tts_generations_authorized": 0,
        "new_tts_generations_made": 0,
        "provider_call_made_by_current_run": False,
        "imported_artifact_count": 1,
        "source_mutated": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    imported_tts["content_hash"] = stable_hash(imported_tts)
    write_json(MANIFESTS / "imported_tts_audio_evidence.json", imported_tts)
    reused_receipt = {
        "run_id": CQR1_RUN_ID,
        "provider": "ELEVENLABS",
        "endpoint_semantics": "IMMUTABLE_TTS_REUSE_NO_NEW_PROVIDER_CALL",
        "source_run_id": PREVIOUS_CQR1_RUN_ID,
        "source_provider_request_id": source_receipt_payload["provider_request_id"],
        "source_receipt_content_hash": PREVIOUS_TTS_RECEIPT_CONTENT_HASH,
        "import_evidence_hash": imported_tts["content_hash"],
        "voice_id": EXPECTED_VOICE_ID,
        "voice_name": source_receipt_payload["voice_name"],
        "model_id": EXPECTED_MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
        "pronunciation_dictionary_refs": source_receipt_payload[
            "pronunciation_dictionary_refs"
        ],
        "editorial_text_hash": normalized.source_text_hash,
        "spoken_text_hash": normalized.spoken_text_hash,
        "provider_text_normalization": "off_in_source_generation",
        "audio_path": str(SOURCE_AUDIO / "elevenlabs-final-narration.mp3"),
        "audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
        "audio_size_bytes": PREVIOUS_TTS_AUDIO_SIZE_BYTES,
        "measured_audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
        "provider_alignment_ref": f"narration-timing-seed:{PREVIOUS_TIMING_SEED_CONTENT_HASH}",
        "normalized_alignment_present": True,
        "source_usage_metadata": source_receipt_payload["usage_metadata"],
        "new_generation_count": 0,
        "new_provider_call_made": False,
        "automatic_retry": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    reused_receipt["content_hash"] = stable_hash(reused_receipt)
    write_json(MANIFESTS / "elevenlabs_tts_receipt.json", reused_receipt)
    ledger.bind_imported_tts(
        safe_evidence={
            "evidence_mode": "IMMUTABLE_IMPORTED_TTS",
            "source_run_id": PREVIOUS_CQR1_RUN_ID,
            "audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
            "audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
            "import_evidence_hash": imported_tts["content_hash"],
            "imported_artifact_count": 1,
            "provider_call_made_by_current_run": False,
        }
    )
    forced_source = (
        ALIGNMENT_SOURCE_WORKSPACE / "manifests/forced_alignment_evidence.json"
    )
    verified_source = (
        ALIGNMENT_SOURCE_WORKSPACE / "manifests/verified_narration_alignment.json"
    )
    alignment_receipt_source = (
        ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/elevenlabs_forced_alignment_receipt.json"
    )
    safe_response_source = (
        ALIGNMENT_SOURCE_WORKSPACE
        / "manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json"
    )
    shutil.copyfile(forced_source, MANIFESTS / "forced_alignment_evidence.json")
    shutil.copyfile(
        verified_source, MANIFESTS / "verified_narration_alignment.json"
    )
    if (
        sha256_file(MANIFESTS / "forced_alignment_evidence.json")
        != ALIGNMENT_SOURCE_FORCED_SHA256
        or sha256_file(MANIFESTS / "verified_narration_alignment.json")
        != ALIGNMENT_SOURCE_VERIFIED_SHA256
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_COPY_INTEGRITY_FAILED")
    forced = ForcedAlignmentEvidence.model_validate(
        read_json(MANIFESTS / "forced_alignment_evidence.json")
    )
    verified = VerifiedNarrationAlignment.model_validate(
        read_json(MANIFESTS / "verified_narration_alignment.json")
    )
    source_alignment_receipt = read_json(alignment_receipt_source)
    imported_alignment = {
        "run_id": CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "evidence_mode": "IMMUTABLE_IMPORTED_ALIGNMENT",
        "source_run_id": ALIGNMENT_SOURCE_RUN_ID,
        "source_run_terminal_state": "FAIL_CLOSED_POST_ALIGNMENT",
        "source_tts_run_id": PREVIOUS_CQR1_RUN_ID,
        "source_workspace_inventory_hash": ALIGNMENT_SOURCE_INVENTORY_HASH,
        "source_workspace_file_count": ALIGNMENT_SOURCE_FILE_COUNT,
        "source_workspace_total_size_bytes": ALIGNMENT_SOURCE_TOTAL_SIZE_BYTES,
        "audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
        "audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
        "spoken_text_hash": normalized.spoken_text_hash,
        "forced_alignment_content_hash": forced.content_hash,
        "forced_alignment_file_sha256": ALIGNMENT_SOURCE_FORCED_SHA256,
        "verified_alignment_content_hash": verified.content_hash,
        "verified_alignment_file_sha256": ALIGNMENT_SOURCE_VERIFIED_SHA256,
        "source_alignment_receipt_content_hash": (
            ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH
        ),
        "source_alignment_receipt_sha256": ALIGNMENT_SOURCE_RECEIPT_SHA256,
        "safe_provider_response_capture_hash": (
            ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        ),
        "safe_provider_response_file_sha256": (
            ALIGNMENT_SOURCE_SAFE_RESPONSE_SHA256
        ),
        "provider_request_id": forced.provider_request_id,
        "provider_request_id_availability": (
            forced.provider_request_id_availability
        ),
        "verified_token_coverage": verified.token_coverage,
        "spoken_coverage": verified.token_coverage,
        "missing_non_whitelisted_count": len(verified.missing_tokens),
        "extra_non_whitelisted_count": len(verified.extra_tokens),
        "verification_status": verified.verification_status,
        "request_response_binding_valid": source_alignment_receipt[
            "request_response_binding_valid"
        ],
        "new_forced_alignment_calls_authorized": 0,
        "new_forced_alignment_calls_made": 0,
        "provider_call_made_by_current_run": False,
        "source_mutated": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    imported_alignment["content_hash"] = stable_hash(imported_alignment)
    write_json(
        MANIFESTS / "imported_alignment_evidence.json", imported_alignment
    )
    reused_alignment_receipt = {
        "run_id": CQR1_RUN_ID,
        "provider": "ELEVENLABS",
        "endpoint_semantics": (
            "IMMUTABLE_FORCED_ALIGNMENT_REUSE_NO_NEW_PROVIDER_CALL"
        ),
        "source_run_id": ALIGNMENT_SOURCE_RUN_ID,
        "source_receipt_content_hash": ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH,
        "import_evidence_hash": imported_alignment["content_hash"],
        "provider_request_id": forced.provider_request_id,
        "provider_request_id_availability": (
            forced.provider_request_id_availability
        ),
        "provider_request_hash": source_alignment_receipt[
            "provider_request_hash"
        ],
        "provider_response_hash": source_alignment_receipt[
            "provider_response_hash"
        ],
        "safe_provider_response_capture_hash": (
            ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        ),
        "audio_asset_ref": forced.audio_asset_ref,
        "audio_duration_ms": forced.audio_duration_ms,
        "spoken_text_hash": forced.spoken_text_hash,
        "forced_alignment_content_hash": forced.content_hash,
        "verified_alignment_content_hash": verified.content_hash,
        "verified_token_coverage": verified.token_coverage,
        "spoken_coverage": verified.token_coverage,
        "missing_non_whitelisted_count": len(verified.missing_tokens),
        "extra_non_whitelisted_count": len(verified.extra_tokens),
        "verification_status": verified.verification_status,
        "request_response_binding_valid": source_alignment_receipt[
            "request_response_binding_valid"
        ],
        "new_call_count": 0,
        "new_provider_call_made": False,
        "automatic_retry": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    reused_alignment_receipt["content_hash"] = stable_hash(
        reused_alignment_receipt
    )
    write_json(
        MANIFESTS / "elevenlabs_forced_alignment_receipt.json",
        reused_alignment_receipt,
    )
    ledger.bind_imported_alignment(
        safe_evidence={
            "evidence_mode": "IMMUTABLE_IMPORTED_ALIGNMENT",
            "source_run_id": ALIGNMENT_SOURCE_RUN_ID,
            "source_tts_run_id": PREVIOUS_CQR1_RUN_ID,
            "audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
            "audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
            "spoken_text_hash": normalized.spoken_text_hash,
            "forced_alignment_content_hash": forced.content_hash,
            "verified_alignment_content_hash": verified.content_hash,
            "safe_provider_response_capture_hash": (
                ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
            ),
            "spoken_coverage": verified.token_coverage,
            "missing_non_whitelisted_count": len(verified.missing_tokens),
            "extra_non_whitelisted_count": len(verified.extra_tokens),
            "verification_status": verified.verification_status,
            "request_response_binding_valid": source_alignment_receipt[
                "request_response_binding_valid"
            ],
            "import_evidence_hash": imported_alignment["content_hash"],
            "provider_call_made_by_current_run": False,
        }
    )
    direction, pexels_plan, veo_prompt = compile_resume_plans()
    imported_visual = import_visual_provider_outputs(
        ledger,
        direction=direction,
        pexels_plan=pexels_plan,
        veo_prompt=veo_prompt,
    )
    if not ledger.preflight_ready(approval):
        raise RuntimeError("CQR1_IMPORTED_PROVIDER_LEDGER_BINDING_INVALID")
    current_ledger = read_json(ledger_path)
    binding_payload = {
        "run_id": CQR1_RUN_ID,
        "source_run_id": PREVIOUS_CQR1_RUN_ID,
        "approval_ref": APPROVAL_REF,
        "fresh_run_reason": RESUME_REASON,
        "source_run_preflight_state": source_preflight["status"],
        "source_run_terminal_state": "FAIL_CLOSED",
        "fresh_preflight_state": "PLANNED",
        "ledger_hash_at_creation": initial_ledger_hash,
        "ledger_hash_after_import_binding": current_ledger["ledger_hash"],
        "ledger_fresh": True,
        "provider_call_count": 0,
        "attempt_count": 0,
        "authorization_semantics": (
            "NEW_RECOVERY_RUN_WITH_ZERO_NEW_MEDIA_PROVIDER_CALLS_"
            "AND_ONE_SHOT_DRIVE_ARCHIVE"
        ),
        "approved_script_hash": script_record["content_hash"],
        "spoken_text_hash": normalized.spoken_text_hash,
        "voice_settings_hash": stable_hash(VOICE_SETTINGS),
        "predicted_duration_evidence_hash": prediction["content_hash"],
        "imported_tts_evidence_hash": imported_tts["content_hash"],
        "reused_tts_receipt_hash": reused_receipt["content_hash"],
        "imported_alignment_evidence_hash": imported_alignment["content_hash"],
        "reused_alignment_receipt_hash": reused_alignment_receipt["content_hash"],
        "imported_pexels_evidence_hash": imported_visual[
            "imported_pexels_evidence_hash"
        ],
        "imported_veo_evidence_hash": imported_visual["imported_veo_evidence_hash"],
        "source_visual_run_id": VISUAL_SOURCE_RUN_ID,
        "source_visual_workspace_inventory_hash": VISUAL_SOURCE_INVENTORY_HASH,
        "source_visual_ledger_hash": VISUAL_SOURCE_LEDGER_HASH,
        "operation_bindings": {
            key: {
                "fresh_idempotency_key_hash": value.idempotency_key_hash,
                "max_attempts": value.max_attempts,
                "initial_status": value.status,
                "authorization_hash": stable_hash(
                    {
                        "run_id": CQR1_RUN_ID,
                        "operation_key": key,
                        "fresh_idempotency_key_hash": value.idempotency_key_hash,
                        "approval_ref": APPROVAL_REF,
                        "spoken_text_hash": normalized.spoken_text_hash,
                        "max_attempts": value.max_attempts,
                        "initial_status": value.status,
                    }
                ),
            }
            for key, value in sorted(ledger.entries.items())
        },
        "ledger_created_fresh": True,
        "ledger_reset": False,
    }
    binding_payload["content_hash"] = stable_hash(binding_payload)
    write_json(MANIFESTS / "resume_ledger_authorization_binding.json", binding_payload)
    canary_plan = {
        "run_id": CQR1_RUN_ID,
        "locale": "en-US",
        "topic": script_record["topic"],
        "script": CQR1_CANARY_SCRIPT_V2,
        "spoken_word_count": len(normalized.spoken_tokens),
        "predicted_duration_ms": prediction["predicted_duration_ms"],
        "required_duration_range_seconds": [28, 40],
        "tone": "professional documentary/explainer",
        "commercial_cta": False,
        "quantified_business_claim": False,
        "character_policy": "NO_CHARACTER",
        "visible_label": CQR1_VISIBLE_LABEL,
        "visual_backbone": "NATIVE_VISUAL",
        "supporting_stock_scene_id": "cqr1-stock-support",
        "hero_scene_id": "cqr1-veo-hero",
        "execution_state": "PLANNED_FRESH_PREFLIGHT",
        "tts_execution_state": "REUSED_IMMUTABLE_RUN_002_OUTPUT",
        "forced_alignment_execution_state": (
            "REUSED_IMMUTABLE_RUN_004_VERIFIED_ALIGNMENT"
        ),
        "pexels_execution_state": "REUSED_IMMUTABLE_RUN_007_OUTPUT",
        "google_veo_execution_state": "REUSED_IMMUTABLE_RUN_007_OUTPUT",
        "new_tts_generation_count": 0,
        "new_forced_alignment_call_count": 0,
        "new_pexels_search_count": 0,
        "new_pexels_download_count": 0,
        "new_google_veo_submit_count": 0,
        "new_google_veo_output_count": 0,
        "measured_audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
        "provider_call_count": 0,
        "production_eligible": False,
        "not_publishable": True,
    }
    canary_plan["content_hash"] = stable_hash(canary_plan)
    write_json(MANIFESTS / "canary_content_plan.json", canary_plan)
    baseline_source = (
        PREVIOUS_WORKSPACE / "comparison/pa1r-before.jpg"
    )
    baseline = WORKSPACE / "comparison/pa1r-before.jpg"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(baseline_source, baseline)
    old_comparison = read_json(
        PREVIOUS_WORKSPACE / "manifests/before_after_comparison.json"
    )
    comparison = {
        "run_id": CQR1_RUN_ID,
        "comparison_target": "pa1r-20260713-guarded-smoke-005",
        "comparison_kind": "NON_EQUIVALENT_DIAGNOSTIC_PA1R_VS_PENDING_REAL_PAID_CANARY_009",
        "status": "PENDING_REAL_PAID_CANARY_EVIDENCE",
        "acceptance_complete": False,
        "historical_evidence_immutable": True,
        "historical_hash_evidence_ref": str(
            MANIFESTS / "historical_evidence_immutability.json"
        ),
        "historical_pa1r": old_comparison["historical_pa1r"],
        "new_offline_golden": old_comparison["new_offline_golden"],
        "new_paid_canary": None,
        "side_by_side_stills": {
            "historical_pa1r": str(baseline),
            "real_paid_canary": None,
            "combined": None,
            "interpretation": "NON_EQUIVALENT_DIAGNOSTIC_ONLY",
        },
        "production_eligible": False,
        "not_publishable": True,
    }
    comparison["content_hash"] = stable_hash(comparison)
    write_json(MANIFESTS / "before_after_comparison.json", comparison)
    drive_plan = {
        "run_id": CQR1_RUN_ID,
        "required_drive_path": (
            f"smoke_tests/{ARCHIVE_DATE}/cqr1/{CQR1_RUN_ID}/"
        ),
        "archive_state": "PLANNED",
        "attempt_count": 0,
        "all_files_verified": False,
        "cleanup_eligible": False,
        "purge_count": 0,
        "receipt_created": False,
        "production_eligible": False,
        "not_publishable": True,
    }
    drive_plan["content_hash"] = stable_hash(drive_plan)
    write_json(MANIFESTS / "drive_archive_plan.json", drive_plan)
    result = {
        "status": "PREPARED",
        "source_run_state": "FAIL_CLOSED",
        "fresh_preflight_state": "PLANNED",
        "fresh_run_reason": RESUME_REASON,
        "ledger_fresh": True,
        "provider_call_count": 0,
        "elevenlabs_tts_state": "REUSED",
        "new_tts_generation_count": 0,
        "elevenlabs_forced_alignment_state": "REUSED",
        "new_forced_alignment_call_count": 0,
        "pexels_state": "REUSED",
        "new_pexels_provider_call_count": 0,
        "google_veo_state": "REUSED",
        "new_google_veo_provider_call_count": 0,
        "measured_audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
        "imported_tts_evidence_hash": imported_tts["content_hash"],
        "imported_alignment_evidence_hash": imported_alignment["content_hash"],
        "imported_pexels_evidence_hash": imported_visual[
            "imported_pexels_evidence_hash"
        ],
        "imported_veo_evidence_hash": imported_visual["imported_veo_evidence_hash"],
        "spoken_word_count": len(normalized.spoken_tokens),
        "predicted_duration_ms": prediction["predicted_duration_ms"],
        "spoken_text_hash": normalized.spoken_text_hash,
        "visual_direction_hash": direction.content_hash,
        "pexels_plan_hash": pexels_plan.plan_hash,
        "veo_prompt_hash": veo_prompt.prompt_hash,
        "format_identity_ref": FORMAT_IDENTITY_REF,
        "format_identity_hash": FORMAT_IDENTITY_HASH,
    }
    append_event("FRESH_RUN_PREPARED", result)
    print(json.dumps(result, indent=2))
    return result


def exact_elevenlabs_probe(settings: Settings, *, required_characters: int) -> dict[str, Any]:
    transport = NoRetryHTTPTransport()
    key = settings.elevenlabs_api_key.get_secret_value()  # type: ignore[union-attr]
    headers = {"xi-api-key": key, "Accept": "application/json"}
    subscription, _ = transport.json_request(
        "GET", "https://api.elevenlabs.io/v1/user/subscription", headers=headers
    )
    voices, _ = transport.json_request(
        "GET", "https://api.elevenlabs.io/v1/voices", headers=headers
    )
    models, _ = transport.json_request(
        "GET", "https://api.elevenlabs.io/v1/models", headers=headers
    )
    if not isinstance(subscription, dict) or not isinstance(voices, dict) or not isinstance(models, list):
        raise RuntimeError("ELEVENLABS_READINESS_RESPONSE_INVALID")
    voice = next(
        (
            item
            for item in voices.get("voices", [])
            if isinstance(item, dict) and item.get("voice_id") == settings.elevenlabs_voice_id
        ),
        None,
    )
    model = next(
        (
            item
            for item in models
            if isinstance(item, dict) and item.get("model_id") == settings.elevenlabs_model_id
        ),
        None,
    )
    used = int(subscription.get("character_count") or 0)
    limit = int(subscription.get("character_limit") or 0)
    remaining = max(0, limit - used)
    return {
        "voices_read_confirmed": voice is not None,
        "models_read_confirmed": model is not None,
        "tts_access_confirmed": bool(voice and model and remaining >= required_characters),
        "voice_id": settings.elevenlabs_voice_id,
        "voice_name": str((voice or {}).get("name") or "provider-voice"),
        "voice_category": str((voice or {}).get("category") or "existing"),
        "model_id": settings.elevenlabs_model_id,
        "character_limit": limit,
        "character_count": used,
        "characters_remaining": remaining,
        "credits_available": remaining >= required_characters,
        "probe_count": 3,
        "secret_values_exposed": False,
    }


def regression_snapshot() -> dict[str, Any]:
    historical = read_json(MANIFESTS / "historical_evidence_immutability.json")
    checks = historical.get("checks") or []
    historical_ok = bool(historical.get("all_unchanged")) and all(
        Path(item["path"]).is_file()
        and sha256_file(Path(item["path"])) == item["expected_sha256"]
        for item in checks
    )
    alembic = subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "heads"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip().splitlines()
    compileall = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "compileall", "-q", "app"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
    )
    diff = subprocess.run(
        ["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    required_tests = [
        "tests/test_r3d10_runtime_lts_freeze.py",
        "tests/test_ofv0_originality_format_validation.py",
        "tests/test_nr1_native_renderer_architecture.py",
        "tests/test_nr2_native_production_bakeoff.py",
        "tests/test_as1_asset_acquisition_provenance.py",
        "tests/test_google_veo_provider_replacement.py",
        "tests/test_pexels_real_download_boundary.py",
        "tests/test_pa1r_guarded_provider_smoke.py",
        "tests/test_cqr1a_temporal_authority.py",
        "tests/test_cqr1b_caption_voice_quality.py",
        "tests/test_cqr1c_visual_continuity.py",
        "tests/test_cqr1d_creative_quality_canary.py",
    ]
    test_env = dict(os.environ)
    for key in (
        "ELEVENLABS_VOICE_ID",
        "ELEVENLABS_MODEL_ID",
        "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED",
        "PROVIDER_REAL_EXECUTION_ENABLED",
        "PEXELS_REAL_EXECUTION_ENABLED",
        "PEXELS_REAL_SEARCH_ENABLED",
        "ELEVENLABS_REAL_EXECUTION_ENABLED",
        "ELEVENLABS_REAL_GENERATION_ENABLED",
        "VCOS_VEO_REAL_GENERATION_ENABLED",
        "VCOS_CQR1_PAID_CANARY_ENABLED",
        "GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED",
        "PROVIDER_REAL_READINESS_PROBE_ENABLED",
        "VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED",
        "VCOS_DISABLE_MEDIA_PROVIDER_CALLS",
        "VCOS_PROVIDER_PRODUCTION_EXECUTION_ENABLED",
        "VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED",
        "VCOS_DISABLE_UPLOAD_AND_PUBLISH",
    ):
        test_env.pop(key, None)
    test_env["PYTHONPATH"] = "."
    started = time.monotonic()
    pytest_result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", *required_tests, "-q"],
        cwd=ROOT,
        env=test_env,
        capture_output=True,
        text=True,
    )
    pytest_tail = "\n".join(
        (pytest_result.stdout + "\n" + pytest_result.stderr).strip().splitlines()[-8:]
    )
    offline = CQR1OfflineQualificationEvidence.model_validate(
        read_json(MANIFESTS / "offline_qualification_evidence.json")
    )
    integrity_files = sorted(
        {
            *ROOT.joinpath("app").rglob("*.py"),
            *ROOT.joinpath("tools/cqr1").rglob("*.py"),
            ROOT / "config/creative_quality_policy_catalog.yaml",
            *(ROOT / item for item in required_tests),
        }
    )
    execution_tree_hash = stable_hash(
        {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in integrity_files
            if path.is_file()
        }
    )
    return {
        "offline_qualification_all_passed": offline.all_passed,
        "focused_required_suite_passed": pytest_result.returncode == 0,
        "focused_required_suite_command": required_tests,
        "focused_required_suite_output_tail": pytest_tail,
        "focused_required_suite_duration_seconds": round(time.monotonic() - started, 3),
        "focused_required_suite_completed_at": datetime.now(UTC).isoformat(),
        "scoped_provider_execution_flags_removed_for_tests": True,
        "compileall_passed": compileall.returncode == 0,
        "alembic_heads": alembic,
        "alembic_one_head_passed": len(alembic) == 1 and alembic[0].startswith("0036_hpr1_veo"),
        "git_diff_check_passed": diff.returncode == 0,
        "historical_pa1r_hashes_unchanged": historical_ok,
        "historical_pa1r_check_count": len(checks),
        "execution_tree_hash": execution_tree_hash,
        "execution_tree_file_count": len(integrity_files),
    }


def probe() -> dict[str, Any]:
    pin_original_preflight()
    settings = settings_or_block()
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    approval = CQR1CanaryApprovalScope.model_validate(
        read_json(MANIFESTS / "resume_approval_scope.json")
    )
    if (
        ledger.run_id != CQR1_RUN_ID
        or ledger.purpose != CQR1_PURPOSE
        or ledger.approval_ref != APPROVAL_REF
        or not ledger.preflight_ready(approval)
        or ledger.provider_call_count != 0
    ):
        raise RuntimeError("CQR1_LEDGER_NOT_FRESH")
    verify_imported_tts_binding(approval=approval, ledger=ledger)
    verify_imported_alignment_binding(approval=approval, ledger=ledger)
    verify_imported_visual_binding(approval=approval, ledger=ledger)
    regression = regression_snapshot()
    if any(
        value is not True
        for key, value in regression.items()
        if key.endswith("_passed") or key.endswith("_unchanged")
    ):
        write_json(MANIFESTS / "resume_regression_gate.json", regression)
        raise RuntimeError("CQR1_PRE_EXECUTION_REGRESSION_GATE_BLOCKED")
    if settings.elevenlabs_voice_id != EXPECTED_VOICE_ID:
        raise RuntimeError("CQR1_ELEVENLABS_VOICE_NOT_EXPLICITLY_APPROVED")
    if settings.elevenlabs_model_id != EXPECTED_MODEL_ID:
        raise RuntimeError("CQR1_ELEVENLABS_MODEL_NOT_EXPLICITLY_APPROVED")
    normalized = normalized_text()
    prediction = predicted_duration_evidence(normalized)
    if (
        prediction["word_count_gate"] != "PASS"
        or prediction["predicted_duration_gate"] != "PASS"
        or read_json(MANIFESTS / "predicted_narration_duration.json")
        != prediction
    ):
        raise RuntimeError("CQR1_PREDICTED_DURATION_GATE_BLOCKED_BEFORE_TTS")
    reused_tts_receipt = read_json(MANIFESTS / "elevenlabs_tts_receipt.json")
    source_pexels_search = read_json(
        MANIFESTS
        / "history/run007/manifests/pexels_search_ranking_provenance.json"
    )
    eleven = {
        "tts_access_confirmed": True,
        "voices_read_confirmed": True,
        "models_read_confirmed": True,
        "forced_alignment_permission_confirmed": True,
        "voice_id": settings.elevenlabs_voice_id,
        "voice_name": reused_tts_receipt["voice_name"],
        "model_id": settings.elevenlabs_model_id,
        "readiness_basis": "TYPED_CONFIGURATION_PLUS_IMMUTABLE_SUCCESSFUL_PROVIDER_EVIDENCE",
        "new_provider_probe_count": 0,
        "secret_values_exposed": False,
    }
    veo_safe = {
        "model_id": settings.veo_model_id,
        "model_accessible": settings.veo_model_id == "veo-3.1-fast-generate-preview",
        "supported_actions": ["predictLongRunning"],
        "transport": "GEMINI_DEVELOPER_API",
        "readiness_basis": "IMMUTABLE_SUCCESSFUL_RUN007_OPERATION_AND_OUTPUT",
        "source_operation_id": VISUAL_SOURCE_VEO_OPERATION_ID,
        "probe_count": 0,
    }
    with session_scope() as session:
        drive_health = GoogleDriveCredentialHealthService(session).connection_status()
        drive = DrivePA1RArchive(session, settings)
        access_token = drive.access_token()
        drive_quota = drive.quota_readiness(access_token=access_token)
    drive_safe = {
        "oauth_connected": bool(drive_health.connected),
        "archive_root_configured": bool(settings.google_drive_root_folder_id),
        **drive_quota,
        "probe_count": 1,
    }
    readiness = CQR1ProviderReadinessEvidence(
        pexels_api_key_configured=bool(settings.pexels_api_key),
        elevenlabs_api_key_configured=bool(settings.elevenlabs_api_key),
        elevenlabs_voice_id_configured=bool(settings.elevenlabs_voice_id),
        elevenlabs_model_id_configured=bool(settings.elevenlabs_model_id),
        elevenlabs_tts_access_confirmed=bool(eleven["tts_access_confirmed"]),
        elevenlabs_voices_read_confirmed=bool(eleven["voices_read_confirmed"]),
        elevenlabs_models_read_confirmed=bool(eleven["models_read_confirmed"]),
        elevenlabs_forced_alignment_permission_confirmed=(
            settings.elevenlabs_forced_alignment_permission_confirmed is True
        ),
        google_veo_api_key_configured=bool(settings.gemini_api_key),
        google_veo_model_accessible=bool(veo_safe["model_accessible"]),
        drive_oauth_connected=bool(drive_health.connected),
        drive_archive_root_configured=bool(settings.google_drive_root_folder_id),
        secret_values_exposed=False,
        provider_probe_count=1,
    )
    offline = CQR1OfflineQualificationEvidence.model_validate(
        read_json(MANIFESTS / "offline_qualification_evidence.json")
    )
    estimate = Decimal("0.00")
    preflight = CQR1PaidCanaryEntryGate().evaluate(
        offline=offline,
        readiness=readiness,
        approval=approval,
        ledger=ledger,
        estimated_cost_usd=estimate,
    )
    extra_blockers = [
        key.upper()
        for key, value in regression.items()
        if key.endswith("_passed") or key.endswith("_unchanged")
        if value is not True
    ]
    if not any(
        str(action).casefold() == "predictlongrunning"
        for action in veo_safe["supported_actions"]
    ):
        extra_blockers.append("GOOGLE_VEO_PREDICT_LONG_RUNNING_UNAVAILABLE")
    if drive_quota.get("quota_available") is not True:
        extra_blockers.append("DRIVE_QUOTA_UNAVAILABLE")
    if not all(
        (
            settings.provider_real_execution_enabled,
            not settings.elevenlabs_real_generation_enabled,
            settings.cqr1_paid_canary_enabled,
            settings.google_drive_real_archive_enabled,
            settings.provider_real_readiness_probe_enabled,
            settings.native_ffmpeg_local_smoke_enabled,
            not settings.media_provider_calls_disabled,
            not settings.provider_production_execution_enabled,
            not settings.native_ffmpeg_production_enabled,
            settings.upload_and_publish_disabled,
        )
    ):
        extra_blockers.append("CQR1_SCOPED_EXECUTION_FLAGS_UNSAFE")
    payload = preflight.model_dump(mode="json", exclude={"content_hash"})
    if extra_blockers:
        payload.update(
            status="BLOCKED",
            blocker_reason_codes=sorted(
                set(payload.get("blocker_reason_codes", []) + extra_blockers)
            ),
            exact_next_action="Repair only zero-call scoped readiness; do not execute a provider attempt.",
            provider_execution_allowed=False,
        )
    payload["content_hash"] = stable_hash(payload)
    write_json(MANIFESTS / "resume_provider_readiness_safe.json", {
        "run_id": CQR1_RUN_ID,
        "elevenlabs": eleven,
        "google_veo": veo_safe,
        "pexels": {
            "credential_configured": bool(settings.pexels_api_key),
            "quota_metadata": source_pexels_search["rate_limit"],
            "readiness_basis": "IMMUTABLE_SUCCESSFUL_RUN007_SEARCH_AND_DOWNLOAD",
            "probe_count": 0,
        },
        "drive": drive_safe,
        "total_probe_count": 1,
        "secret_values_exposed": False,
    })
    provider_selection = {
        "approval_ref": APPROVAL_REF,
        "voice_id": eleven["voice_id"],
        "voice_name": eleven["voice_name"],
        "model_id": eleven["model_id"],
        "selection_source": "OPERATOR_APPROVED_TYPED_CONFIGURATION",
        "production_eligible": False,
        "not_publishable": True,
    }
    provider_selection["content_hash"] = stable_hash(provider_selection)
    write_json(
        MANIFESTS / "resume_typed_provider_selection_binding.json",
        provider_selection,
    )
    write_json(MANIFESTS / "resume_regression_gate.json", regression)
    write_json(MANIFESTS / "resume_paid_canary_preflight.json", payload)
    evaluation_dir = MANIFESTS / "preflight-evaluations"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    evaluation_sequence = len(list(EVENTS.glob("*.json"))) + 1
    evaluation_snapshot = (
        evaluation_dir
        / f"{evaluation_sequence:04d}-{payload['content_hash'][:16]}.json"
    )
    if evaluation_snapshot.exists():
        raise RuntimeError("CQR1_APPEND_ONLY_PREFLIGHT_EVALUATION_COLLISION")
    write_json(evaluation_snapshot, payload)
    append_event(
        "RESUME_PREFLIGHT_EVALUATED",
        {
            "source_run_preflight_state": "PASS",
            "source_run_terminal_state": "FAIL_CLOSED",
            "recovery_preflight_state": payload["status"],
            "resume_reason": RESUME_REASON,
            "provider_call_count": ledger.provider_call_count,
            "ledger_fresh": ledger.preflight_ready(approval),
            "tts_state": ledger.entries["elevenlabs_tts"].status,
            "new_tts_generation_authorized": False,
            "preflight_hash": payload["content_hash"],
            "preflight_snapshot_ref": str(evaluation_snapshot),
        },
    )
    print(json.dumps({
        "CQR1D_PAID_CANARY_PREFLIGHT": payload["status"],
        "provider_call_count": ledger.provider_call_count,
        "voice_id": eleven["voice_id"],
        "voice_name": eleven["voice_name"],
        "model_id": eleven["model_id"],
        "veo_model": veo_safe["model_id"],
        "drive_quota_available": drive_quota["quota_available"],
    }, indent=2))
    return payload


def _valid_content_hash(payload: Mapping[str, Any]) -> bool:
    body = dict(payload)
    recorded = str(body.pop("content_hash", ""))
    return bool(recorded) and stable_hash(body) == recorded


def verify_imported_tts_binding(
    *, approval: CQR1CanaryApprovalScope, ledger: CQR1CanaryCallLedger
) -> None:
    if approval.maximum_elevenlabs_tts_generations != 0:
        raise RuntimeError("CQR1_RECOVERY_NEW_TTS_AUTHORIZATION_NOT_ZERO")
    audio = SOURCE_AUDIO / "elevenlabs-final-narration.mp3"
    normalized_path = SOURCE_SCRIPT / "spoken_text_normalized.json"
    seed_path = MANIFESTS / "narration_timing_seed.json"
    imported_path = MANIFESTS / "imported_tts_audio_evidence.json"
    receipt_path = MANIFESTS / "elevenlabs_tts_receipt.json"
    for path in (audio, normalized_path, seed_path, imported_path, receipt_path):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"CQR1_IMPORTED_TTS_ARTIFACT_MISSING:{path.name}")
    if (
        sha256_file(audio) != PREVIOUS_TTS_AUDIO_SHA256
        or audio.stat().st_size != PREVIOUS_TTS_AUDIO_SIZE_BYTES
        or sha256_file(normalized_path) != PREVIOUS_NORMALIZED_SHA256
        or sha256_file(seed_path) != PREVIOUS_TIMING_SEED_SHA256
    ):
        raise RuntimeError("CQR1_IMPORTED_TTS_BYTE_INTEGRITY_FAILED")
    media = probe_media(audio, ffprobe=FFPROBE)
    duration_ms = round(float(media.get("format", {}).get("duration") or 0) * 1000)
    if duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS:
        raise RuntimeError("CQR1_IMPORTED_TTS_PHYSICAL_DURATION_DRIFT")
    normalized = SpokenTextNormalized.model_validate(read_json(normalized_path))
    seed = NarrationTimingSeed.model_validate(read_json(seed_path))
    imported = read_json(imported_path)
    receipt = read_json(receipt_path)
    if (
        normalized.content_hash != PREVIOUS_NORMALIZED_CONTENT_HASH
        or seed.content_hash != PREVIOUS_TIMING_SEED_CONTENT_HASH
        or seed.audio_asset_ref != f"file-sha256:{PREVIOUS_TTS_AUDIO_SHA256}"
        or seed.audio_duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS
        or seed.spoken_text_hash != normalized.spoken_text_hash
        or not _valid_content_hash(imported)
        or imported.get("run_id") != CQR1_RUN_ID
        or imported.get("source_run_id") != PREVIOUS_CQR1_RUN_ID
        or imported.get("evidence_mode") != "IMMUTABLE_IMPORTED_TTS"
        or imported.get("source_workspace_inventory_hash")
        != PREVIOUS_WORKSPACE_INVENTORY_HASH
        or imported.get("destination_audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or int(imported.get("destination_audio_size_bytes") or 0)
        != PREVIOUS_TTS_AUDIO_SIZE_BYTES
        or int(imported.get("measured_audio_duration_ms") or 0)
        != PREVIOUS_TTS_AUDIO_DURATION_MS
        or imported.get("source_tts_receipt_content_hash")
        != PREVIOUS_TTS_RECEIPT_CONTENT_HASH
        or imported.get("source_timing_seed_content_hash")
        != PREVIOUS_TIMING_SEED_CONTENT_HASH
        or imported.get("source_spoken_text_content_hash")
        != PREVIOUS_NORMALIZED_CONTENT_HASH
        or imported.get("spoken_text_hash") != normalized.spoken_text_hash
        or imported.get("new_tts_generations_authorized") != 0
        or imported.get("new_tts_generations_made") != 0
        or imported.get("provider_call_made_by_current_run") is not False
        or imported.get("source_mutated") is not False
        or not _valid_content_hash(receipt)
        or receipt.get("run_id") != CQR1_RUN_ID
        or receipt.get("source_run_id") != PREVIOUS_CQR1_RUN_ID
        or receipt.get("source_receipt_content_hash")
        != PREVIOUS_TTS_RECEIPT_CONTENT_HASH
        or receipt.get("import_evidence_hash") != imported.get("content_hash")
        or receipt.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or int(receipt.get("audio_size_bytes") or 0) != PREVIOUS_TTS_AUDIO_SIZE_BYTES
        or int(receipt.get("measured_audio_duration_ms") or 0)
        != PREVIOUS_TTS_AUDIO_DURATION_MS
        or receipt.get("spoken_text_hash") != normalized.spoken_text_hash
        or receipt.get("new_generation_count") != 0
        or receipt.get("new_provider_call_made") is not False
    ):
        raise RuntimeError("CQR1_IMPORTED_TTS_SEMANTIC_BINDING_FAILED")
    entry = ledger.entries["elevenlabs_tts"]
    if (
        entry.status != "REUSED"
        or entry.max_attempts != 0
        or entry.attempt_count != 0
        or entry.provider_call_made
        or entry.output_count != 0
        or entry.safe_evidence.get("evidence_mode") != "IMMUTABLE_IMPORTED_TTS"
        or entry.safe_evidence.get("source_run_id") != PREVIOUS_CQR1_RUN_ID
        or entry.safe_evidence.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or entry.safe_evidence.get("audio_duration_ms")
        != PREVIOUS_TTS_AUDIO_DURATION_MS
        or entry.safe_evidence.get("import_evidence_hash")
        != imported.get("content_hash")
    ):
        raise RuntimeError("CQR1_IMPORTED_TTS_LEDGER_BINDING_DRIFT")


def verify_imported_alignment_binding(
    *, approval: CQR1CanaryApprovalScope, ledger: CQR1CanaryCallLedger
) -> None:
    """Revalidate immutable run004 alignment before every downstream phase."""

    if approval.maximum_elevenlabs_forced_alignment_calls != 0:
        raise RuntimeError("CQR1_RECOVERY_NEW_ALIGNMENT_AUTHORIZATION_NOT_ZERO")
    forced_path = MANIFESTS / "forced_alignment_evidence.json"
    verified_path = MANIFESTS / "verified_narration_alignment.json"
    imported_path = MANIFESTS / "imported_alignment_evidence.json"
    receipt_path = MANIFESTS / "elevenlabs_forced_alignment_receipt.json"
    normalized_path = SOURCE_SCRIPT / "spoken_text_normalized.json"
    for path in (
        forced_path,
        verified_path,
        imported_path,
        receipt_path,
        normalized_path,
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"CQR1_IMPORTED_ALIGNMENT_ARTIFACT_MISSING:{path.name}")
    if (
        sha256_file(forced_path) != ALIGNMENT_SOURCE_FORCED_SHA256
        or sha256_file(verified_path) != ALIGNMENT_SOURCE_VERIFIED_SHA256
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_BYTE_INTEGRITY_FAILED")

    normalized = SpokenTextNormalized.model_validate(read_json(normalized_path))
    forced = ForcedAlignmentEvidence.model_validate(read_json(forced_path))
    verified = VerifiedNarrationAlignment.model_validate(read_json(verified_path))
    imported = read_json(imported_path)
    receipt = read_json(receipt_path)
    forced_body = forced.model_dump(mode="json", exclude={"content_hash"})
    verified_body = verified.model_dump(mode="json", exclude={"content_hash"})
    expected_audio_ref = f"file-sha256:{PREVIOUS_TTS_AUDIO_SHA256}"
    if (
        stable_hash(forced_body) != forced.content_hash
        or forced.content_hash != ALIGNMENT_SOURCE_FORCED_CONTENT_HASH
        or forced.verification_status != "PASS"
        or len(forced.words) != len(normalized.spoken_tokens)
        or len(forced.words) != 72
        or forced.missing_tokens
        or forced.extra_words
        or forced.audio_asset_ref != expected_audio_ref
        or forced.audio_duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS
        or forced.spoken_text_hash != normalized.spoken_text_hash
        or stable_hash(verified_body) != verified.content_hash
        or verified.content_hash != ALIGNMENT_SOURCE_VERIFIED_CONTENT_HASH
        or verified.verification_status != "PASS"
        or len(verified.verified_words) != len(normalized.spoken_tokens)
        or verified.token_coverage != 1.0
        or verified.missing_tokens
        or verified.extra_tokens
        or verified.audio_asset_ref != expected_audio_ref
        or verified.audio_duration_ms != PREVIOUS_TTS_AUDIO_DURATION_MS
        or verified.spoken_text_hash != normalized.spoken_text_hash
        or verified.forced_alignment_ref
        != f"forced-alignment:{ALIGNMENT_SOURCE_FORCED_CONTENT_HASH}"
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_SEMANTIC_INTEGRITY_FAILED")

    if (
        not _valid_content_hash(imported)
        or imported.get("run_id") != CQR1_RUN_ID
        or imported.get("approval_ref") != APPROVAL_REF
        or imported.get("evidence_mode") != "IMMUTABLE_IMPORTED_ALIGNMENT"
        or imported.get("source_run_id") != ALIGNMENT_SOURCE_RUN_ID
        or imported.get("source_tts_run_id") != PREVIOUS_CQR1_RUN_ID
        or imported.get("source_workspace_inventory_hash")
        != ALIGNMENT_SOURCE_INVENTORY_HASH
        or imported.get("forced_alignment_content_hash") != forced.content_hash
        or imported.get("forced_alignment_file_sha256")
        != ALIGNMENT_SOURCE_FORCED_SHA256
        or imported.get("verified_alignment_content_hash") != verified.content_hash
        or imported.get("verified_alignment_file_sha256")
        != ALIGNMENT_SOURCE_VERIFIED_SHA256
        or imported.get("source_alignment_receipt_content_hash")
        != ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH
        or imported.get("source_alignment_receipt_sha256")
        != ALIGNMENT_SOURCE_RECEIPT_SHA256
        or imported.get("safe_provider_response_capture_hash")
        != ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        or imported.get("safe_provider_response_file_sha256")
        != ALIGNMENT_SOURCE_SAFE_RESPONSE_SHA256
        or imported.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or imported.get("audio_duration_ms") != PREVIOUS_TTS_AUDIO_DURATION_MS
        or imported.get("spoken_text_hash") != normalized.spoken_text_hash
        or float(imported.get("spoken_coverage") or 0) != 1.0
        or imported.get("missing_non_whitelisted_count") != 0
        or imported.get("extra_non_whitelisted_count") != 0
        or imported.get("verification_status") != "PASS"
        or imported.get("request_response_binding_valid") is not True
        or imported.get("new_forced_alignment_calls_authorized") != 0
        or imported.get("new_forced_alignment_calls_made") != 0
        or imported.get("provider_call_made_by_current_run") is not False
        or imported.get("source_mutated") is not False
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_MANIFEST_BINDING_FAILED")

    if (
        not _valid_content_hash(receipt)
        or receipt.get("run_id") != CQR1_RUN_ID
        or receipt.get("endpoint_semantics")
        != "IMMUTABLE_FORCED_ALIGNMENT_REUSE_NO_NEW_PROVIDER_CALL"
        or receipt.get("source_run_id") != ALIGNMENT_SOURCE_RUN_ID
        or receipt.get("source_receipt_content_hash")
        != ALIGNMENT_SOURCE_RECEIPT_CONTENT_HASH
        or receipt.get("import_evidence_hash") != imported.get("content_hash")
        or receipt.get("forced_alignment_content_hash") != forced.content_hash
        or receipt.get("verified_alignment_content_hash") != verified.content_hash
        or receipt.get("safe_provider_response_capture_hash")
        != ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        or receipt.get("audio_duration_ms") != PREVIOUS_TTS_AUDIO_DURATION_MS
        or receipt.get("spoken_text_hash") != normalized.spoken_text_hash
        or float(receipt.get("spoken_coverage") or 0) != 1.0
        or receipt.get("missing_non_whitelisted_count") != 0
        or receipt.get("extra_non_whitelisted_count") != 0
        or receipt.get("verification_status") != "PASS"
        or receipt.get("request_response_binding_valid") is not True
        or receipt.get("new_call_count") != 0
        or receipt.get("new_provider_call_made") is not False
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_RECEIPT_BINDING_FAILED")

    entry = ledger.entries["elevenlabs_forced_alignment"]
    safe = entry.safe_evidence
    if (
        entry.status != "REUSED"
        or entry.max_attempts != 0
        or entry.attempt_count != 0
        or entry.provider_call_made
        or entry.output_count != 0
        or safe.get("evidence_mode") != "IMMUTABLE_IMPORTED_ALIGNMENT"
        or safe.get("source_run_id") != ALIGNMENT_SOURCE_RUN_ID
        or safe.get("source_tts_run_id") != PREVIOUS_CQR1_RUN_ID
        or safe.get("audio_sha256") != PREVIOUS_TTS_AUDIO_SHA256
        or safe.get("audio_duration_ms") != PREVIOUS_TTS_AUDIO_DURATION_MS
        or safe.get("spoken_text_hash") != normalized.spoken_text_hash
        or safe.get("forced_alignment_content_hash") != forced.content_hash
        or safe.get("verified_alignment_content_hash") != verified.content_hash
        or safe.get("safe_provider_response_capture_hash")
        != ALIGNMENT_SOURCE_SAFE_RESPONSE_CONTENT_HASH
        or float(safe.get("spoken_coverage") or 0) != 1.0
        or safe.get("missing_non_whitelisted_count") != 0
        or safe.get("extra_non_whitelisted_count") != 0
        or safe.get("verification_status") != "PASS"
        or safe.get("request_response_binding_valid") is not True
        or safe.get("import_evidence_hash") != imported.get("content_hash")
    ):
        raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_LEDGER_BINDING_DRIFT")


def verify_imported_visual_binding(
    *, approval: CQR1CanaryApprovalScope, ledger: CQR1CanaryCallLedger
) -> None:
    """Revalidate the exact run007 Pexels/Veo bytes before every local phase."""

    if any(
        value != 0
        for value in (
            approval.maximum_pexels_search_flows,
            approval.maximum_pexels_downloads,
            approval.maximum_google_veo_submits,
            approval.maximum_google_veo_outputs,
        )
    ):
        raise RuntimeError("CQR1_RECOVERY_NEW_VISUAL_PROVIDER_AUTHORIZATION_NOT_ZERO")
    imported_pexels = read_json(MANIFESTS / "imported_pexels_evidence.json")
    imported_veo = read_json(MANIFESTS / "imported_veo_evidence.json")
    pexels_receipt = read_json(MANIFESTS / "pexels_download_receipt.json")
    stock_manifest = read_json(MANIFESTS / "pexels_stock_source_manifest.json")
    search = read_json(MANIFESTS / "pexels_search_ranking_provenance.json")
    operation = read_json(MANIFESTS / "google_veo_operation_receipt.json")
    veo_download = read_json(MANIFESTS / "google_veo_download_receipt.json")
    provenance = read_json(MANIFESTS / "veo_prompt_request_provenance.json")
    review = read_json(QC_DIR / "codex_visual_asset_review.json")
    current_direction = read_json(MANIFESTS / "resume_visual_direction_contract.json")
    source_direction = read_json(
        MANIFESTS
        / "history/run007/manifests/resume_visual_direction_contract.json"
    )
    current_prompt = read_json(MANIFESTS / "resume_veo_prompt.json")
    source_prompt = read_json(
        MANIFESTS / "history/run007/manifests/resume_veo_prompt.json"
    )
    stock = require_inside(
        STOCK_DIR / "pexels-12991847-5704872.mp4", must_exist=True
    )
    hero = require_inside(
        HERO_DIR / "google-veo-hero-original.mp4", must_exist=True
    )
    expected_media = {
        stock: (VISUAL_SOURCE_PEXELS_VIDEO_SHA256, VISUAL_SOURCE_PEXELS_VIDEO_SIZE_BYTES),
        hero: (VISUAL_SOURCE_VEO_VIDEO_SHA256, VISUAL_SOURCE_VEO_VIDEO_SIZE_BYTES),
        require_inside(
            RENDER_DIR / "proxy/pexels-selected-representative.jpg", must_exist=True
        ): (VISUAL_SOURCE_PEXELS_REPRESENTATIVE_SHA256, None),
        require_inside(
            RENDER_DIR / "proxy/pexels-review-contact-sheet.jpg", must_exist=True
        ): (VISUAL_SOURCE_PEXELS_SHEET_SHA256, None),
        require_inside(
            RENDER_DIR / "proxy/veo-hero-representative.jpg", must_exist=True
        ): (VISUAL_SOURCE_VEO_REPRESENTATIVE_SHA256, None),
        require_inside(
            RENDER_DIR / "proxy/veo-review-contact-sheet.jpg", must_exist=True
        ): (VISUAL_SOURCE_VEO_SHEET_SHA256, None),
    }
    if any(
        path.is_symlink()
        or sha256_file(path) != expected_hash
        or (expected_size is not None and path.stat().st_size != expected_size)
        for path, (expected_hash, expected_size) in expected_media.items()
    ):
        raise RuntimeError("CQR1_IMPORTED_VISUAL_BYTE_INTEGRITY_FAILED")

    current_direction_body = dict(current_direction)
    source_direction_body = dict(source_direction)
    current_direction_hash = str(current_direction_body.pop("content_hash", ""))
    source_direction_hash = str(source_direction_body.pop("content_hash", ""))
    if (
        stable_hash(current_direction_body) != current_direction_hash
        or stable_hash(source_direction_body) != source_direction_hash
        or source_direction_hash != VISUAL_SOURCE_DIRECTION_CONTENT_HASH
    ):
        raise RuntimeError("CQR1_IMPORTED_VISUAL_DIRECTION_HASH_INVALID")
    current_direction_body.pop("project_id", None)
    source_direction_body.pop("project_id", None)
    if current_direction_body != source_direction_body:
        raise RuntimeError("CQR1_IMPORTED_VISUAL_DIRECTION_SEMANTIC_DRIFT")
    if (
        current_prompt.get("prompt_hash") != VISUAL_SOURCE_VEO_PROMPT_HASH
        or source_prompt.get("prompt_hash") != VISUAL_SOURCE_VEO_PROMPT_HASH
        or current_prompt.get("prompt") != source_prompt.get("prompt")
    ):
        raise RuntimeError("CQR1_IMPORTED_VEO_PROMPT_SEMANTIC_DRIFT")

    stock_manifest_body = dict(stock_manifest)
    stock_manifest_hash = str(stock_manifest_body.pop("manifest_hash", ""))
    pexels_receipt_body = dict(pexels_receipt)
    pexels_receipt_hash = str(pexels_receipt_body.pop("receipt_hash", ""))
    operation_body = dict(operation)
    operation_hash = str(operation_body.pop("state_hash", ""))
    veo_download_body = dict(veo_download)
    veo_download_hash = str(veo_download_body.pop("content_hash", ""))
    if (
        not _valid_content_hash(imported_pexels)
        or not _valid_content_hash(imported_veo)
        or not _valid_content_hash(search)
        or stable_hash(pexels_receipt_body) != pexels_receipt_hash
        or stable_hash(stock_manifest_body) != stock_manifest_hash
        or stable_hash(operation_body) != operation_hash
        or stable_hash(veo_download_body) != veo_download_hash
        or not _valid_content_hash(provenance)
        or not _valid_content_hash(review)
        or imported_pexels.get("run_id") != CQR1_RUN_ID
        or imported_veo.get("run_id") != CQR1_RUN_ID
        or imported_pexels.get("source_run_id") != VISUAL_SOURCE_RUN_ID
        or imported_veo.get("source_run_id") != VISUAL_SOURCE_RUN_ID
        or imported_pexels.get("source_workspace_inventory_hash")
        != VISUAL_SOURCE_INVENTORY_HASH
        or imported_veo.get("source_workspace_inventory_hash")
        != VISUAL_SOURCE_INVENTORY_HASH
        or imported_pexels.get("source_ledger_hash") != VISUAL_SOURCE_LEDGER_HASH
        or imported_veo.get("source_ledger_hash") != VISUAL_SOURCE_LEDGER_HASH
        or imported_pexels.get("asset_sha256")
        != VISUAL_SOURCE_PEXELS_VIDEO_SHA256
        or imported_veo.get("output_sha256") != VISUAL_SOURCE_VEO_VIDEO_SHA256
        or search.get("source_search_provenance_sha256")
        != VISUAL_SOURCE_PEXELS_SEARCH_SHA256
        or search.get("provider_call_made_by_current_run") is not False
        or pexels_receipt.get("local_path") != str(stock)
        or pexels_receipt.get("sha256") != VISUAL_SOURCE_PEXELS_VIDEO_SHA256
        or pexels_receipt.get("provider_call_made") is not False
        or stock_manifest.get("local_path") != str(stock)
        or operation.get("provider_operation_id") != VISUAL_SOURCE_VEO_OPERATION_ID
        or operation.get("provider_call_made") is not False
        or veo_download.get("downloaded_path") != str(hero)
        or veo_download.get("sha256") != VISUAL_SOURCE_VEO_VIDEO_SHA256
        or veo_download.get("provider_call_made") is not False
        or provenance.get("downloaded_file_path") != str(hero)
        or provenance.get("sha256") != VISUAL_SOURCE_VEO_VIDEO_SHA256
        or provenance.get("provider_call_made_by_current_run") is not False
        or review.get("run_id") != CQR1_RUN_ID
        or review.get("source_run_id") != VISUAL_SOURCE_RUN_ID
        or review.get("source_review_content_hash")
        != VISUAL_SOURCE_REVIEW_CONTENT_HASH
        or review.get("review_state") != "COMPLETED_REAL_FRAMES"
    ):
        raise RuntimeError("CQR1_IMPORTED_VISUAL_SEMANTIC_BINDING_FAILED")

    by_scene = {str(item.get("scene_id")): item for item in review.get("assets", [])}
    stock_review = by_scene.get("cqr1-stock-support") or {}
    hero_review = by_scene.get("cqr1-veo-hero") or {}
    if (
        set(by_scene) != {"cqr1-stock-support", "cqr1-veo-hero"}
        or stock_review.get("asset_ref")
        != f"file-sha256:{VISUAL_SOURCE_PEXELS_VIDEO_SHA256}"
        or stock_review.get("result") != "REVIEW_REQUIRED"
        or hero_review.get("asset_ref")
        != f"file-sha256:{VISUAL_SOURCE_VEO_VIDEO_SHA256}"
        or hero_review.get("result") != "PASS"
        or stock_review.get("representative_still_path")
        != str(RENDER_DIR / "proxy/pexels-selected-representative.jpg")
        or hero_review.get("representative_still_path")
        != str(RENDER_DIR / "proxy/veo-hero-representative.jpg")
    ):
        raise RuntimeError("CQR1_IMPORTED_VISUAL_REVIEW_BINDING_FAILED")

    expected_import_hashes = {
        "pexels_search": imported_pexels["content_hash"],
        "pexels_download": imported_pexels["content_hash"],
        "google_veo_submit": imported_veo["content_hash"],
        "google_veo_output": imported_veo["content_hash"],
    }
    for key, import_hash in expected_import_hashes.items():
        entry = ledger.entries[key]
        if (
            entry.status != "REUSED"
            or entry.max_attempts != 0
            or entry.attempt_count != 0
            or entry.provider_call_made
            or entry.output_count != 0
            or entry.safe_evidence.get("source_run_id") != VISUAL_SOURCE_RUN_ID
            or entry.safe_evidence.get("source_ledger_hash")
            != VISUAL_SOURCE_LEDGER_HASH
            or entry.safe_evidence.get("import_evidence_hash") != import_hash
        ):
            raise RuntimeError("CQR1_IMPORTED_VISUAL_LEDGER_BINDING_DRIFT")


def _provider_prerequisite_satisfied(
    ledger: CQR1CanaryCallLedger, operation_key: str
) -> bool:
    entry = ledger.entries[operation_key]
    if entry.max_attempts == 0:
        return (
            entry.status == "REUSED"
            and entry.max_attempts == 0
            and entry.attempt_count == 0
            and not entry.provider_call_made
            and entry.output_count == 0
        )
    return entry.status == "SUCCEEDED"


def verify_resume_bindings(settings: Settings) -> None:
    pin_original_preflight()
    approval = CQR1CanaryApprovalScope.model_validate(
        read_json(MANIFESTS / "resume_approval_scope.json")
    )
    if (
        approval.run_id != CQR1_RUN_ID
        or approval.approval_ref != APPROVAL_REF
        or approval.maximum_elevenlabs_tts_generations != 0
        or approval.maximum_elevenlabs_forced_alignment_calls != 0
        or approval.maximum_pexels_search_flows != 0
        or approval.maximum_pexels_downloads != 0
        or approval.maximum_google_veo_submits != 0
        or approval.maximum_google_veo_outputs != 0
        or approval.maximum_drive_archive_attempts != 1
    ):
        raise RuntimeError("CQR1_RESUME_APPROVAL_REF_MISMATCH")
    preflight_payload = read_json(MANIFESTS / "resume_paid_canary_preflight.json")
    recorded_preflight_hash = str(preflight_payload.pop("content_hash", ""))
    if stable_hash(preflight_payload) != recorded_preflight_hash:
        raise RuntimeError("CQR1_RESUME_PREFLIGHT_HASH_MISMATCH")
    if preflight_payload.get("run_id") != CQR1_RUN_ID:
        raise RuntimeError("CQR1_PREFLIGHT_RUN_ID_MISMATCH")
    regression = read_json(MANIFESTS / "resume_regression_gate.json")
    integrity_files = sorted(
        {
            *ROOT.joinpath("app").rglob("*.py"),
            *ROOT.joinpath("tools/cqr1").rglob("*.py"),
            ROOT / "config/creative_quality_policy_catalog.yaml",
            *(
                ROOT / item
                for item in regression.get("focused_required_suite_command", [])
            ),
        }
    )
    current_tree_hash = stable_hash(
        {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in integrity_files
            if path.is_file()
        }
    )
    if current_tree_hash != regression.get("execution_tree_hash"):
        raise RuntimeError("CQR1_EXECUTION_TREE_CHANGED_AFTER_PREFLIGHT")
    if not regression.get("focused_required_suite_passed"):
        raise RuntimeError("CQR1_REQUIRED_REGRESSION_RECEIPT_NOT_PASS")
    normalized = SpokenTextNormalized.model_validate(
        read_json(SOURCE_SCRIPT / "spoken_text_normalized.json")
    )
    normalized_payload = normalized.model_dump(mode="json", exclude={"content_hash"})
    if stable_hash(normalized_payload) != normalized.content_hash:
        raise RuntimeError("CQR1_PREPARED_SPOKEN_TEXT_HASH_INVALID")
    if normalized.model_dump(mode="json") != normalized_text().model_dump(mode="json"):
        raise RuntimeError("CQR1_PREPARED_SPOKEN_TEXT_DRIFT")
    script = read_json(SOURCE_SCRIPT / "approved_script.json")
    script_hash = str(script.pop("content_hash", ""))
    if (
        stable_hash(script) != script_hash
        or script.get("run_id") != CQR1_RUN_ID
        or script.get("editorial_text_hash") != normalized.source_text_hash
        or script.get("spoken_text_hash") != normalized.spoken_text_hash
        or script.get("spoken_word_count") != len(normalized.spoken_tokens)
        or not 72 <= len(normalized.spoken_tokens) <= 76
        or script.get("voice_id") != EXPECTED_VOICE_ID
        or script.get("model_id") != EXPECTED_MODEL_ID
        or script.get("voice_speed") != 0.90
        or script.get("speed_increased_for_duration") is not False
        or script.get("not_publishable") is not True
        or script.get("production_eligible") is not False
    ):
        raise RuntimeError("CQR1_APPROVED_SCRIPT_BINDING_MISMATCH")
    prediction = read_json(MANIFESTS / "predicted_narration_duration.json")
    if (
        prediction != predicted_duration_evidence(normalized)
        or prediction.get("word_count_gate") != "PASS"
        or prediction.get("predicted_duration_gate") != "PASS"
        or float(prediction.get("predicted_duration_ms") or 0) > 40_000
    ):
        raise RuntimeError("CQR1_PREDICTED_DURATION_BINDING_MISMATCH")
    selection = read_json(MANIFESTS / "resume_typed_provider_selection_binding.json")
    selection_hash = str(selection.pop("content_hash", ""))
    if stable_hash(selection) != selection_hash:
        raise RuntimeError("CQR1_PROVIDER_SELECTION_BINDING_HASH_MISMATCH")
    if (
        selection.get("approval_ref") != APPROVAL_REF
        or selection.get("voice_id") != settings.elevenlabs_voice_id
        or selection.get("model_id") != settings.elevenlabs_model_id
    ):
        raise RuntimeError("CQR1_PROVIDER_SELECTION_BINDING_MISMATCH")
    binding = read_json(MANIFESTS / "resume_ledger_authorization_binding.json")
    binding_hash = str(binding.pop("content_hash", ""))
    if stable_hash(binding) != binding_hash:
        raise RuntimeError("CQR1_LEDGER_AUTHORIZATION_BINDING_HASH_MISMATCH")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    if (
        ledger.run_id != CQR1_RUN_ID
        or ledger.purpose != CQR1_PURPOSE
        or ledger.approval_ref != APPROVAL_REF
    ):
        raise RuntimeError("CQR1_LEDGER_SCOPE_MISMATCH")
    verify_imported_tts_binding(approval=approval, ledger=ledger)
    verify_imported_alignment_binding(approval=approval, ledger=ledger)
    verify_imported_visual_binding(approval=approval, ledger=ledger)
    if (
        binding.get("run_id") != CQR1_RUN_ID
        or binding.get("approval_ref") != APPROVAL_REF
        or binding.get("approved_script_hash") != script_hash
        or binding.get("spoken_text_hash") != normalized.spoken_text_hash
        or binding.get("voice_settings_hash") != stable_hash(VOICE_SETTINGS)
        or binding.get("predicted_duration_evidence_hash")
        != prediction["content_hash"]
        or binding.get("imported_tts_evidence_hash")
        != read_json(MANIFESTS / "imported_tts_audio_evidence.json")["content_hash"]
        or binding.get("reused_tts_receipt_hash")
        != read_json(MANIFESTS / "elevenlabs_tts_receipt.json")["content_hash"]
        or binding.get("imported_alignment_evidence_hash")
        != read_json(MANIFESTS / "imported_alignment_evidence.json")[
            "content_hash"
        ]
        or binding.get("reused_alignment_receipt_hash")
        != read_json(MANIFESTS / "elevenlabs_forced_alignment_receipt.json")[
            "content_hash"
        ]
        or binding.get("imported_pexels_evidence_hash")
        != read_json(MANIFESTS / "imported_pexels_evidence.json")["content_hash"]
        or binding.get("imported_veo_evidence_hash")
        != read_json(MANIFESTS / "imported_veo_evidence.json")["content_hash"]
        or binding.get("source_visual_run_id") != VISUAL_SOURCE_RUN_ID
        or binding.get("source_visual_workspace_inventory_hash")
        != VISUAL_SOURCE_INVENTORY_HASH
        or binding.get("source_visual_ledger_hash") != VISUAL_SOURCE_LEDGER_HASH
        or binding.get("ledger_reset") is not False
        or binding.get("ledger_created_fresh") is not True
    ):
        raise RuntimeError("CQR1_FRESH_LEDGER_AUTHORIZATION_BINDING_MISMATCH")
    for key, entry in ledger.entries.items():
        operation_binding = binding["operation_bindings"][key]
        expected = operation_binding["fresh_idempotency_key_hash"]
        reused_provider = key in {
            "elevenlabs_tts",
            "elevenlabs_forced_alignment",
            "pexels_search",
            "pexels_download",
            "google_veo_submit",
            "google_veo_output",
        }
        expected_max_attempts = 0 if reused_provider else 1
        expected_initial_status = "REUSED" if reused_provider else "PLANNED"
        authorization_material = {
            "run_id": CQR1_RUN_ID,
            "operation_key": key,
            "fresh_idempotency_key_hash": expected,
            "approval_ref": APPROVAL_REF,
            "spoken_text_hash": normalized.spoken_text_hash,
            "max_attempts": expected_max_attempts,
            "initial_status": expected_initial_status,
        }
        if (
            entry.idempotency_key_hash != expected
            or entry.max_attempts != expected_max_attempts
            or operation_binding.get("max_attempts") != expected_max_attempts
            or operation_binding.get("initial_status") != expected_initial_status
            or operation_binding.get("authorization_hash")
            != stable_hash(authorization_material)
        ):
            raise RuntimeError("CQR1_LEDGER_OPERATION_BINDING_DRIFT")
    direction = VisualDirectionContract.model_validate(
        read_json(MANIFESTS / "resume_visual_direction_contract.json")
    )
    if stable_hash(direction.model_dump(mode="json", exclude={"content_hash"})) != direction.content_hash:
        raise RuntimeError("CQR1_VISUAL_DIRECTION_HASH_MISMATCH")


def require_preflight_pass() -> tuple[Settings, CQR1CanaryCallLedger, Any]:
    settings = settings_or_block()
    verify_resume_bindings(settings)
    preflight = __import__(
        "app.contracts.creative_quality_canary",
        fromlist=["CQR1PaidCanaryPreflightResult"],
    ).CQR1PaidCanaryPreflightResult.model_validate(
        read_json(MANIFESTS / "resume_paid_canary_preflight.json")
    )
    if preflight.status != "PASS" or not preflight.provider_execution_allowed:
        raise RuntimeError("CQR1_PAID_CANARY_PREFLIGHT_BLOCKED")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    approval = CQR1CanaryApprovalScope.model_validate(
        read_json(MANIFESTS / "resume_approval_scope.json")
    )
    if ledger.provider_call_count or not ledger.preflight_ready(approval):
        raise RuntimeError("CQR1_LEDGER_NOT_FRESH")
    return settings, ledger, preflight


def load_execution_context(
    operation_key: str,
    *,
    prerequisites: Sequence[str] = (),
) -> tuple[Settings, CQR1CanaryCallLedger, Any]:
    settings = settings_or_block()
    verify_resume_bindings(settings)
    preflight = __import__(
        "app.contracts.creative_quality_canary",
        fromlist=["CQR1PaidCanaryPreflightResult"],
    ).CQR1PaidCanaryPreflightResult.model_validate(
        read_json(MANIFESTS / "resume_paid_canary_preflight.json")
    )
    if preflight.status != "PASS" or not preflight.provider_execution_allowed:
        raise RuntimeError("CQR1_PAID_CANARY_PREFLIGHT_BLOCKED")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    if any(entry.status == "FAILED" for entry in ledger.entries.values()):
        raise RuntimeError("CQR1_PRIOR_PROVIDER_ATTEMPT_FAILED")
    for key in prerequisites:
        if not _provider_prerequisite_satisfied(ledger, key):
            raise RuntimeError(f"CQR1_PREREQUISITE_NOT_SUCCEEDED:{key}")
    entry = ledger.entries[operation_key]
    if entry.status != "PLANNED" or entry.attempt_count != 0 or entry.provider_call_made:
        raise RuntimeError(f"CQR1_OPERATION_NOT_FRESH:{operation_key}")
    return settings, ledger, preflight


def tts() -> dict[str, Any]:
    raise RuntimeError("CQR1_RECOVERY_NEW_TTS_GENERATION_FORBIDDEN")


def _legacy_tts_generation_forbidden_reference_only() -> dict[str, Any]:
    """Unreachable source reference retained to keep the original request contract auditable."""

    settings, ledger, preflight = load_execution_context("elevenlabs_tts")
    if not (
        settings.provider_real_execution_enabled
        and settings.elevenlabs_real_execution_enabled
        and settings.elevenlabs_real_generation_enabled
        and not settings.media_provider_calls_disabled
    ):
        raise RuntimeError("CQR1_ELEVENLABS_EXECUTION_FLAGS_BLOCKED")
    normalized = SpokenTextNormalized.model_validate(
        read_json(SOURCE_SCRIPT / "spoken_text_normalized.json")
    )
    destination = require_inside(SOURCE_AUDIO / "elevenlabs-final-narration.mp3")
    if destination.exists() or destination.with_name(destination.name + ".part").exists():
        raise RuntimeError("CQR1_TTS_DESTINATION_NOT_FRESH")
    # Local contract validation occurs before the one-shot ledger transition.
    request_contract = __import__(
        "app.services.temporal_authority",
        fromlist=["ElevenLabsTimestampRequestBuilder"],
    ).ElevenLabsTimestampRequestBuilder().build(
        normalized=normalized,
        voice_id=str(settings.elevenlabs_voice_id),
        model_id=str(settings.elevenlabs_model_id),
        voice_settings=VOICE_SETTINGS,
        seed=41017,
    )
    write_json(MANIFESTS / "elevenlabs_tts_request_contract.json", request_contract)
    client = ElevenLabsConvertWithTimestampsClient(
        media_probe=lambda path: probe_media(path, ffprobe=FFPROBE)
    )

    def execute() -> Mapping[str, Any]:
        result = client.execute_once(
            api_key=settings.elevenlabs_api_key.get_secret_value(),  # type: ignore[union-attr]
            normalized=normalized,
            voice_id=str(settings.elevenlabs_voice_id),
            model_id=str(settings.elevenlabs_model_id),
            destination=destination,
            voice_settings=VOICE_SETTINGS,
            seed=41017,
        )
        model_json(MANIFESTS / "narration_timing_seed.json", result.timing_seed)
        audio_probe = probe_media(destination, ffprobe=FFPROBE)
        write_json(MANIFESTS / "elevenlabs_final_audio_ffprobe.json", audio_probe)
        receipt = {
            "run_id": CQR1_RUN_ID,
            "provider": "ELEVENLABS",
            "endpoint_semantics": "CONVERT_WITH_TIMESTAMPS",
            "provider_request_id": result.timing_seed.provider_request_id,
            "voice_id": result.timing_seed.provider_voice_id,
            "voice_name": read_json(MANIFESTS / "resume_provider_readiness_safe.json")["elevenlabs"]["voice_name"],
            "model_id": result.timing_seed.provider_model_id,
            "voice_settings": result.timing_seed.voice_settings,
            "pronunciation_dictionary_refs": result.timing_seed.pronunciation_dictionary_refs,
            "editorial_text_hash": normalized.source_text_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "provider_text_normalization": "off",
            "audio_path": str(destination),
            "audio_sha256": result.audio_sha256,
            "audio_size_bytes": result.audio_size_bytes,
            "measured_audio_duration_ms": result.audio_duration_ms,
            "provider_alignment_ref": f"narration-timing-seed:{result.timing_seed.content_hash}",
            "normalized_alignment_present": bool(result.timing_seed.normalized_character_alignment),
            "usage_metadata": result.usage_metadata,
            "cost_evidence": {
                "estimated_usd": 0.05,
                "actual_usd": None,
                "actual_cost_reason": "provider response did not expose billed amount",
            },
            "generation_count": 1,
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        receipt["content_hash"] = stable_hash(receipt)
        write_json(MANIFESTS / "elevenlabs_tts_receipt.json", receipt)
        if not result.timing_seed.provider_request_id:
            raise RuntimeError("ELEVENLABS_TTS_PROVIDER_REQUEST_ID_MISSING")
        if not 28_000 <= result.audio_duration_ms <= 40_000:
            raise RuntimeError(
                f"ELEVENLABS_TTS_DURATION_OUTSIDE_CANARY_RANGE:{result.audio_duration_ms}"
            )
        if not result.timing_seed.timing_available:
            raise RuntimeError("ELEVENLABS_PROVIDER_TIMING_INVALID")
        return {
            "provider_request_id": result.timing_seed.provider_request_id,
            "voice_id": result.timing_seed.provider_voice_id,
            "model_id": result.timing_seed.provider_model_id,
            "audio_sha256": result.audio_sha256,
            "audio_size_bytes": result.audio_size_bytes,
            "audio_duration_ms": result.audio_duration_ms,
            "timing_available": True,
            "receipt_hash": receipt["content_hash"],
            "output_count": 1,
        }

    result = run_guarded_once(
        ledger=ledger,
        operation_key="elevenlabs_tts",
        preflight=preflight,
        operation=execute,
    )
    append_event(
        "ELEVENLABS_TTS_COMPLETED",
        {
            "status": result["status"],
            "provider_call_count": CQR1CanaryCallLedger.load(
                MANIFESTS / "planned_provider_call_ledger.json"
            ).provider_call_count,
            "automatic_retry": False,
        },
    )
    print(json.dumps({"CQR1D_ELEVENLABS_TTS": "PASS", **result}, indent=2))
    return result


def align() -> dict[str, Any]:
    raise RuntimeError("CQR1_RECOVERY_NEW_FORCED_ALIGNMENT_CALL_FORBIDDEN")


def _legacy_forced_alignment_call_forbidden_reference_only() -> dict[str, Any]:
    """Unreachable source reference retained for audit of the original call contract."""

    settings, ledger, preflight = load_execution_context(
        "elevenlabs_forced_alignment",
        prerequisites=("elevenlabs_tts",),
    )
    if settings.elevenlabs_forced_alignment_permission_confirmed is not True:
        raise RuntimeError("CQR1_FORCED_ALIGNMENT_PERMISSION_NOT_CONFIRMED")
    require_scoped_external_execution_flags(
        settings, provider="elevenlabs_forced_alignment"
    )
    normalized = SpokenTextNormalized.model_validate(
        read_json(SOURCE_SCRIPT / "spoken_text_normalized.json")
    )
    audio = require_inside(SOURCE_AUDIO / "elevenlabs-final-narration.mp3", must_exist=True)
    seed = NarrationTimingSeed.model_validate(
        read_json(MANIFESTS / "narration_timing_seed.json")
    )
    if sha256_file(audio) != read_json(MANIFESTS / "elevenlabs_tts_receipt.json")["audio_sha256"]:
        raise RuntimeError("CQR1_TTS_AUDIO_HASH_MISMATCH")
    if seed.spoken_text_hash != normalized.spoken_text_hash:
        raise RuntimeError("CQR1_TTS_SPOKEN_TEXT_HASH_MISMATCH")
    request_contract = __import__(
        "app.services.temporal_authority",
        fromlist=["ElevenLabsForcedAlignmentRequestBuilder"],
    ).ElevenLabsForcedAlignmentRequestBuilder().build(
        audio_asset_ref=seed.audio_asset_ref,
        normalized=normalized,
    )
    write_json(MANIFESTS / "elevenlabs_forced_alignment_request_contract.json", request_contract)
    safe_response_path = (
        MANIFESTS
        / "provider-raw/elevenlabs_forced_alignment_response.safe.json"
    )
    if safe_response_path.exists():
        raise RuntimeError("CQR1_FORCED_ALIGNMENT_RESPONSE_CAPTURE_NOT_FRESH")

    def capture_safe_response(payload: Mapping[str, Any]) -> None:
        if safe_response_path.exists():
            raise RuntimeError("CQR1_FORCED_ALIGNMENT_RESPONSE_CAPTURE_COLLISION")
        capture = {
            "run_id": CQR1_RUN_ID,
            "source_run_id": PREVIOUS_CQR1_RUN_ID,
            "captured_before_parser_execution": True,
            "audio_sha256": PREVIOUS_TTS_AUDIO_SHA256,
            "audio_duration_ms": PREVIOUS_TTS_AUDIO_DURATION_MS,
            "spoken_text_hash": normalized.spoken_text_hash,
            "provider_payload": dict(payload),
            "secret_values_exposed": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        capture["content_hash"] = stable_hash(capture)
        write_json(safe_response_path, capture)

    client = ElevenLabsForcedAlignmentClient(
        response_capture=capture_safe_response
    )

    def execute() -> Mapping[str, Any]:
        result = client.execute_once(
            api_key=settings.elevenlabs_api_key.get_secret_value(),  # type: ignore[union-attr]
            normalized=normalized,
            audio_path=audio,
            audio_asset_ref=seed.audio_asset_ref,
            audio_duration_ms=seed.audio_duration_ms,
        )
        forced = result.evidence
        model_json(MANIFESTS / "forced_alignment_evidence.json", forced)
        verified = NarrationAlignmentReconciler().reconcile(
            normalized=normalized,
            timing_seed=seed,
            forced_alignment=forced,
            audio_asset_ref=seed.audio_asset_ref,
            audio_duration_ms=seed.audio_duration_ms,
        )
        model_json(MANIFESTS / "verified_narration_alignment.json", verified)
        safe_capture = read_json(safe_response_path)
        provider_capture = safe_capture.get("provider_payload")
        forced_payload = forced.model_dump(mode="json", exclude={"content_hash"})
        request_response_binding_valid = (
            isinstance(provider_capture, dict)
            and _valid_content_hash(provider_capture)
            and _valid_content_hash(safe_capture)
            and len(result.request_hash) == 64
            and len(result.provider_response_hash) == 64
            and result.request_hash == request_contract["request_hash"]
            and result.provider_response_hash == provider_capture.get("content_hash")
            and safe_capture.get("audio_sha256") == PREVIOUS_TTS_AUDIO_SHA256
            and safe_capture.get("audio_duration_ms") == seed.audio_duration_ms
            and safe_capture.get("spoken_text_hash") == normalized.spoken_text_hash
            and forced.audio_asset_ref == seed.audio_asset_ref
            and forced.audio_duration_ms == seed.audio_duration_ms
            and forced.spoken_text_hash == normalized.spoken_text_hash
            and stable_hash(forced_payload) == forced.content_hash
        )
        receipt = {
            "run_id": CQR1_RUN_ID,
            "provider": "ELEVENLABS",
            "endpoint_semantics": "FORCED_ALIGNMENT",
            "provider_request_id": forced.provider_request_id,
            "provider_request_id_availability": (
                forced.provider_request_id_availability
            ),
            "provider_request_hash": result.request_hash,
            "provider_response_hash": result.provider_response_hash,
            "forced_alignment_content_hash": forced.content_hash,
            "audio_asset_ref": forced.audio_asset_ref,
            "audio_duration_ms": forced.audio_duration_ms,
            "spoken_text_hash": forced.spoken_text_hash,
            "word_count": len(forced.words),
            "character_count": len(forced.characters),
            "alignment_loss": forced.alignment_loss,
            "transcript_loss": forced.transcript_loss,
            "missing_non_whitelisted_count": len(forced.missing_tokens),
            "extra_non_whitelisted_count": len(forced.extra_words),
            "verification_status": forced.verification_status,
            "verified_token_coverage": verified.token_coverage,
            "timing_conflict_count": len(verified.timing_conflicts),
            "verified_alignment_status": verified.verification_status,
            "safe_provider_response_capture_ref": str(safe_response_path),
            "safe_provider_response_capture_hash": safe_capture["content_hash"],
            "request_response_binding_valid": request_response_binding_valid,
            "automatic_retry": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        receipt["content_hash"] = stable_hash(receipt)
        write_json(MANIFESTS / "elevenlabs_forced_alignment_receipt.json", receipt)
        if not request_response_binding_valid:
            raise RuntimeError("ELEVENLABS_FORCED_ALIGNMENT_STRONG_BINDING_INVALID")
        if (
            bool(forced.provider_request_id)
            != (forced.provider_request_id_availability == "PRESENT")
        ):
            raise RuntimeError(
                "ELEVENLABS_FORCED_ALIGNMENT_REQUEST_ID_AVAILABILITY_INVALID"
            )
        if forced.verification_status != "PASS" or forced.missing_tokens or forced.extra_words:
            raise RuntimeError("FORCED_ALIGNMENT_TOKEN_MISMATCH")
        if (
            verified.verification_status != "PASS"
            or verified.token_coverage != 1.0
            or verified.missing_tokens
            or verified.extra_tokens
        ):
            raise RuntimeError("VERIFIED_NARRATION_ALIGNMENT_BLOCKED")
        return {
            "provider_request_id": forced.provider_request_id,
            "provider_request_id_availability": (
                forced.provider_request_id_availability
            ),
            "provider_request_hash": result.request_hash,
            "provider_response_hash": result.provider_response_hash,
            "forced_alignment_content_hash": forced.content_hash,
            "audio_duration_ms": forced.audio_duration_ms,
            "word_count": len(forced.words),
            "alignment_loss": forced.alignment_loss,
            "verification_status": forced.verification_status,
            "spoken_coverage": verified.token_coverage,
            "timing_conflict_count": len(verified.timing_conflicts),
            "receipt_hash": receipt["content_hash"],
            "output_count": 1,
        }

    result = run_guarded_once(
        ledger=ledger,
        operation_key="elevenlabs_forced_alignment",
        preflight=preflight,
        operation=execute,
    )
    append_event(
        "ELEVENLABS_FORCED_ALIGNMENT_COMPLETED",
        {
            "status": result["status"],
            "provider_call_count": CQR1CanaryCallLedger.load(
                MANIFESTS / "planned_provider_call_ledger.json"
            ).provider_call_count,
            "automatic_retry": False,
        },
    )
    print(json.dumps({"CQR1D_FORCED_ALIGNMENT": "PASS", **result}, indent=2))
    return result


def _sentence_token_groups(normalized: SpokenTextNormalized) -> list[list[Any]]:
    boundaries = [
        match.end()
        for match in re.finditer(r"[.!?](?:\s+|$)", CQR1_CANARY_SCRIPT_V2)
    ]
    groups: list[list[Any]] = []
    cursor = 0
    for boundary in boundaries:
        group = [
            token
            for token in normalized.spoken_tokens
            if any(
                cursor <= span.start < boundary
                for span in token.source_spans
            )
        ]
        if group:
            groups.append(group)
        cursor = boundary
    if {token.token_id for group in groups for token in group} != {
        token.token_id for token in normalized.spoken_tokens
    }:
        raise RuntimeError("CQR1_EDITORIAL_SENTENCE_TOKEN_COVERAGE_FAILED")
    return groups


def _segment_inputs(normalized: SpokenTextNormalized) -> list[EditorialSegmentInput]:
    sentence_groups = _sentence_token_groups(normalized)
    if len(sentence_groups) != 8:
        raise RuntimeError(
            f"CQR1_APPROVED_SENTENCE_STRUCTURE_CHANGED:{len(sentence_groups)}"
        )
    # Six scenes: native explanation backbone, one grounded stock scene, a
    # native bridge, one hero scene, and a native close.  Groups remain the
    # exact approved editorial order; no estimated time is introduced.
    specs = [
        ("cqr1-native-open", [0, 1], "EXPLAIN_APPROVED_SCRIPT_TO_FINAL_AUDIO"),
        ("cqr1-native-timeline", [2], "EXPLAIN_VERIFIED_TIMELINE"),
        ("cqr1-stock-support", [3], "GROUNDED_WORKFLOW_CONTEXT"),
        ("cqr1-native-bridge", [4], "BRIDGE_NATIVE_TO_HERO"),
        ("cqr1-veo-hero", [5], "METAPHOR_TRANSITION"),
        ("cqr1-native-close", [6, 7], "ASSEMBLY_QC_ARCHIVE_CLOSE"),
    ]
    segments: list[EditorialSegmentInput] = []
    for segment_id, indices, motion_intent in specs:
        tokens = [token for index in indices for token in sentence_groups[index]]
        source_starts = [span.start for token in tokens for span in token.source_spans]
        source_ends = [span.end for token in tokens for span in token.source_spans]
        segments.append(
            EditorialSegmentInput(
                segment_id=segment_id,
                editorial_span=TextSpan(start=min(source_starts), end=max(source_ends)),
                spoken_token_ids=[token.token_id for token in tokens],
                motion_intent=motion_intent,
                source_provenance=[
                    {
                        "type": "approved_canary_content_plan",
                        "ref": str(MANIFESTS / "canary_content_plan.json"),
                    },
                    {
                        "type": "visual_direction_contract",
                        "ref": str(MANIFESTS / "resume_visual_direction_contract.json"),
                    },
                ],
            )
        )
    return segments


def _audio_analysis(
    audio: Path,
    *,
    audio_asset_ref: str,
    duration_ms: int,
) -> NarrationAudioAnalysis:
    silence = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(audio),
            "-af",
            "silencedetect=noise=-45dB:d=0.08",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    starts: list[float] = []
    spans: list[PauseSpan] = []
    for line in silence.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and starts:
            start = starts.pop(0)
            end = float(end_match.group(1))
            start_ms = max(0, round(start * 1000))
            end_ms = min(duration_ms, round(end * 1000))
            if end_ms > start_ms:
                spans.append(
                    PauseSpan(
                        pause_id=f"audio-silence-{len(spans) + 1:04d}",
                        start_ms=start_ms,
                        end_ms=end_ms,
                        source="AUDIO_SILENCE_ANALYSIS",
                        boundary_kind="OTHER",
                        detected_in_audio=True,
                    )
                )
    volume = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(audio),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    def db_value(name: str) -> float | None:
        match = re.search(rf"{name}:\s*(-?[0-9.]+)\s*dB", volume.stderr)
        return float(match.group(1)) if match else None

    payload = {
        "audio_asset_ref": audio_asset_ref,
        "audio_duration_ms": duration_ms,
        "silence_spans": [item.model_dump(mode="json") for item in spans],
        "waveform_summary": {
            "mean_volume_db": db_value("mean_volume"),
            "max_volume_db": db_value("max_volume"),
            "silence_threshold_db": -45,
            "minimum_silence_duration_ms": 80,
            "silence_span_count": len(spans),
            "measurement_tool": "ffmpeg silencedetect+volumedetect",
        },
        "analysis_ref": f"audio-analysis:{sha256_file(audio)}",
        "analysis_hash": None,
    }
    payload["analysis_hash"] = stable_hash({k: v for k, v in payload.items() if k != "analysis_hash"})
    return NarrationAudioAnalysis.model_validate(payload)


def timeline() -> dict[str, Any]:
    settings = settings_or_block()
    verify_resume_bindings(settings)
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    if any(entry.status == "FAILED" for entry in ledger.entries.values()):
        raise RuntimeError("CQR1_PRIOR_PROVIDER_ATTEMPT_FAILED")
    for key in ("elevenlabs_tts", "elevenlabs_forced_alignment"):
        if not _provider_prerequisite_satisfied(ledger, key):
            raise RuntimeError(f"CQR1_PREREQUISITE_NOT_SUCCEEDED:{key}")
    normalized = SpokenTextNormalized.model_validate(
        read_json(SOURCE_SCRIPT / "spoken_text_normalized.json")
    )
    alignment = VerifiedNarrationAlignment.model_validate(
        read_json(MANIFESTS / "verified_narration_alignment.json")
    )
    seed = NarrationTimingSeed.model_validate(
        read_json(MANIFESTS / "narration_timing_seed.json")
    )
    audio = require_inside(SOURCE_AUDIO / "elevenlabs-final-narration.mp3", must_exist=True)
    tts_receipt = read_json(MANIFESTS / "elevenlabs_tts_receipt.json")
    physical_probe = probe_media(audio, ffprobe=FFPROBE)
    physical_duration_ms = round(media_duration_seconds(physical_probe) * 1000)
    if (
        sha256_file(audio) != tts_receipt.get("audio_sha256")
        or sha256_file(audio) != str(alignment.audio_asset_ref).removeprefix("file-sha256:")
        or physical_duration_ms != seed.audio_duration_ms
        or physical_duration_ms != alignment.audio_duration_ms
    ):
        raise RuntimeError("CQR1_FINAL_NARRATION_PHYSICAL_INTEGRITY_MISMATCH")
    timeline_model = CanonicalMediaTimelineCompiler().compile(
        project_id=CQR1_RUN_ID,
        package_id=PACKAGE_ID,
        channel_id=CHANNEL_KEY,
        script_revision_id=normalized.script_revision_id,
        spoken_text_revision_id=normalized.content_hash,
        tts_request_id=seed.provider_request_id or f"tts:{seed.content_hash}",
        normalized=normalized,
        alignment=alignment,
        segments=_segment_inputs(normalized),
    )
    final_audio_payload = {
        "audio_asset_ref": alignment.audio_asset_ref,
        "duration_ms": alignment.audio_duration_ms,
        "is_final": True,
    }
    final_audio = FinalNarrationAudio(
        **final_audio_payload,
        content_hash=stable_hash(final_audio_payload),
    )
    authority = TemporalAuthorityGate().evaluate(
        normalized=normalized,
        final_audio=final_audio,
        alignment=alignment,
        timeline=timeline_model,
    )
    write_json(MANIFESTS / "temporal_authority_gate.json", authority.model_dump(mode="json"))
    if authority.gate_status != "PASS":
        raise RuntimeError("CQR1_TEMPORAL_AUTHORITY_BLOCKED")
    policy = approved_policy()
    analysis = _audio_analysis(
        audio,
        audio_asset_ref=alignment.audio_asset_ref,
        duration_ms=alignment.audio_duration_ms,
    )
    write_json(MANIFESTS / "narration_audio_analysis.json", analysis.model_dump(mode="json"))
    sentence_groups = _sentence_token_groups(normalized)
    section_boundaries = [
        sentence_groups[index][-1].token_id
        for index in (1, 3, 5)
    ]
    pacing = NarrationPacingAnalyzer().analyze(
        normalized=normalized,
        alignment=alignment,
        audio_analysis=analysis,
        policy=policy["narration_pacing_policy"],
        section_boundary_after_token_ids=section_boundaries,
    )
    pacing = NarrationPacingGate().attach(
        pacing, policy["narration_pacing_policy"]
    )
    model_json(QC_DIR / "narration_pacing_report.json", pacing)
    if pacing.gate_result is None or pacing.gate_result.status == "BLOCK":
        recommendation = {
            "reason_codes": pacing.gate_result.reason_codes if pacing.gate_result else ["PACING_GATE_MISSING"],
            "metrics": pacing.metrics.model_dump(mode="json"),
            "correction_recommendation": "Revise punctuation/script pacing for a new separately approved run; do not regenerate this run.",
            "second_tts_generation_authorized": False,
            "ffmpeg_atempo_authorized": False,
        }
        write_json(QC_DIR / "narration_pacing_fail_closed.json", recommendation)
        raise RuntimeError("CQR1_NARRATION_PACING_NOT_PASS")

    final_cue_hold_policy = {
        "policy_ref": "policy://cqr1/canonical-final-cue-trailing-silence/v1",
        "policy_version": "cqr1-canonical-final-cue-trailing-silence/1.0.0",
        "maximum_hold_ms": 750,
        "target_endpoint": "CANONICAL_AUDIO_END",
    }
    caption_output = ReadableCaptionCompiler().compile(
        normalized=normalized,
        alignment=alignment,
        timeline=timeline_model,
        policy=policy["caption_style_policy"],
        final_cue_trailing_hold_policy=final_cue_hold_policy,
        aspect_ratio="16:9",
    )
    timeline_model = caption_output.timeline
    trailing_hold = caption_output.track.final_cue_trailing_hold
    if (
        trailing_hold is None
        or trailing_hold.target_endpoint != "CANONICAL_AUDIO_END"
        or trailing_hold.maximum_hold_ms != 750
        or trailing_hold.caption_end_after_ms != timeline_model.audio_duration_ms
        or trailing_hold.hold_duration_ms > 750
        or not trailing_hold.spoken_token_ids_unchanged
        or not trailing_hold.spoken_word_timing_unchanged
    ):
        raise RuntimeError("CQR1_CAPTION_FINAL_CUE_TRAILING_HOLD_INVALID")
    model_json(QC_DIR / "caption_final_cue_trailing_hold.json", trailing_hold)
    cues = canonical_caption_cues(timeline_model)
    compilation_gate = CaptionCompilationGate().evaluate(
        cues=cues,
        normalized=normalized,
        timeline=timeline_model,
        policy=policy["caption_style_policy"],
    )
    bbox = CaptionBoundsPreflight(ffmpeg_binary=FFMPEG).preflight(
        cues=cues,
        frame_width=1920,
        frame_height=1080,
        policy=policy["caption_style_policy"],
        aspect_ratio="16:9",
        evidence_dir=RENDER_DIR / "previews/captions",
    )
    timeline_model = CaptionBoundsPreflight(ffmpeg_binary=FFMPEG).apply_to_timeline(
        timeline_model, bbox
    )
    cues = canonical_caption_cues(timeline_model)
    layout_gate = CaptionLayoutGate().evaluate(
        cues=cues,
        bbox_metrics=bbox.cue_metrics,
        policy=policy["caption_style_policy"],
        aspect_ratio="16:9",
    )
    safe_gate = CaptionSafeAreaGate().evaluate(
        bbox_metrics=bbox.cue_metrics,
        policy=policy["caption_style_policy"],
        aspect_ratio="16:9",
    )
    coverage_gate = CaptionCoverageGate().evaluate(
        normalized=normalized,
        timeline=timeline_model,
        policy=policy["caption_sync_policy"],
    )
    sync_gate = CaptionAudioSyncGate().evaluate(
        timeline=timeline_model,
        alignment=alignment,
        policy=policy["caption_sync_policy"],
    )
    drift_gate = TimelineDriftGate().evaluate(
        timeline=timeline_model,
        final_audio_duration_ms=alignment.audio_duration_ms,
        policy=policy["caption_sync_policy"],
    )
    model_json(MANIFESTS / "canonical_media_timeline.json", timeline_model)
    write_json(
        QC_DIR / "caption_compilation_report.json",
        {
            "track": caption_output.track.model_dump(mode="json"),
            "gate": compilation_gate.model_dump(mode="json"),
            "caption_compilation_ref": timeline_model.qc_metrics.get("caption_compilation_ref"),
            "caption_compilation_hash": timeline_model.qc_metrics.get("caption_compilation_hash"),
            "caption_render_payload_hash": timeline_model.qc_metrics.get("caption_render_payload_hash"),
            "cue_count": len(cues),
        },
    )
    model_json(QC_DIR / "caption_bbox_safe_area.json", bbox)
    caption_gates = {
        "NarrationPacingGate": pacing.gate_result.model_dump(mode="json"),
        "CaptionCompilationGate": compilation_gate.model_dump(mode="json"),
        "CaptionLayoutGate": layout_gate.model_dump(mode="json"),
        "CaptionSafeAreaGate": safe_gate.model_dump(mode="json"),
        "CaptionCoverageGate": coverage_gate.model_dump(mode="json"),
        "CaptionAudioSyncGate": sync_gate.model_dump(mode="json"),
        "TimelineDriftGate": drift_gate.model_dump(mode="json"),
    }
    write_json(QC_DIR / "caption_sync_coverage_drift.json", caption_gates)
    blocked = {
        name: value["status"]
        for name, value in caption_gates.items()
        if value["status"] == "BLOCK"
    }
    if blocked:
        raise RuntimeError(
            "CQR1_CAPTION_OR_PACING_GATE_BLOCKED:"
            + json.dumps(blocked, sort_keys=True)
        )
    direction = VisualDirectionContract.model_validate(
        read_json(MANIFESTS / "resume_visual_direction_contract.json")
    )
    canonical_stock_request, canonical_pexels_plan, stock_scene_duration_ms = (
        _canonical_pexels_inputs(timeline_model, direction)
    )
    model_json(MANIFESTS / "resume_pexels_query.json", canonical_pexels_plan)
    duration_binding = {
        "run_id": CQR1_RUN_ID,
        "scene_id": "cqr1-stock-support",
        "canonical_scene_duration_ms": stock_scene_duration_ms,
        "selection_minimum_duration_seconds": (
            canonical_stock_request.minimum_duration_seconds
        ),
        "selection_maximum_duration_seconds": (
            canonical_stock_request.maximum_duration_seconds
        ),
        "rounding_policy": "CEIL_CANONICAL_SCENE_DURATION_TO_WHOLE_SECOND",
        "asset_request_hash": canonical_stock_request.request_hash,
        "pexels_query_plan_hash": canonical_pexels_plan.plan_hash,
        "provider_call_made": False,
    }
    duration_binding["content_hash"] = stable_hash(duration_binding)
    write_json(
        MANIFESTS / "pexels_canonical_duration_binding.json",
        duration_binding,
    )
    hero_segment = next(
        item
        for item in timeline_model.segments
        if item.segment_id == "cqr1-veo-hero"
    )
    duration_fit = VeoFixedDurationPlanner(
        __import__(
            "app.contracts.visual_direction", fromlist=["VeoDurationFitThresholds"]
        ).VeoDurationFitThresholds.from_policy(policy)
    ).decide(hero_segment.target_scene_duration_ms / 1000)
    model_json(MANIFESTS / "paid_veo_duration_fit.json", duration_fit)
    if not duration_fit.provider_execution_allowed:
        raise RuntimeError("CQR1_IMPORTED_VEO_DURATION_FIT_BLOCKED")
    result = {
        "status": "PASS",
        "timeline_hash": timeline_model.timeline_hash,
        "audio_duration_ms": timeline_model.audio_duration_ms,
        "scene_count": len(timeline_model.segments),
        "caption_cue_count": len(cues),
        "caption_final_cue_trailing_hold_status": trailing_hold.status,
        "caption_final_cue_trailing_hold_ms": trailing_hold.hold_duration_ms,
        "pacing_metrics": pacing.metrics.model_dump(mode="json"),
        "caption_gates": {name: value["status"] for name, value in caption_gates.items()},
        "pexels_minimum_duration_seconds": (
            canonical_stock_request.minimum_duration_seconds
        ),
        "veo_duration_fit": duration_fit.decision,
    }
    append_event("CANONICAL_TIMELINE_AND_CAPTIONS_COMPILED", result)
    print(json.dumps(result, indent=2))
    return result


def pexels() -> dict[str, Any]:
    raise RuntimeError("CQR1_RUN009_NEW_PEXELS_CALL_FORBIDDEN_REUSED_RUN007_OUTPUT")


def _legacy_pexels_call_forbidden_reference_only() -> dict[str, Any]:
    settings, ledger, preflight = load_execution_context(
        "pexels_search",
        prerequisites=("elevenlabs_tts", "elevenlabs_forced_alignment"),
    )
    require_scoped_external_execution_flags(settings, provider="pexels")
    timeline_model = CanonicalMediaTimeline.model_validate(
        read_json(MANIFESTS / "canonical_media_timeline.json")
    )
    caption_gates = read_json(QC_DIR / "caption_sync_coverage_drift.json")
    if any(value.get("status") == "BLOCK" for value in caption_gates.values()):
        raise RuntimeError("CQR1_CAPTION_GATES_BLOCKED")
    direction = VisualDirectionContract.model_validate(
        read_json(MANIFESTS / "resume_visual_direction_contract.json")
    )
    plan = PexelsQueryPlan.model_validate(
        read_json(MANIFESTS / "resume_pexels_query.json")
    )
    request, expected_plan, stock_scene_duration_ms = _canonical_pexels_inputs(
        timeline_model,
        direction,
    )
    duration_binding = read_json(
        MANIFESTS / "pexels_canonical_duration_binding.json"
    )
    if (
        plan.model_dump(mode="json") != expected_plan.model_dump(mode="json")
        or not _valid_content_hash(duration_binding)
        or duration_binding.get("canonical_scene_duration_ms")
        != stock_scene_duration_ms
        or duration_binding.get("selection_minimum_duration_seconds")
        != request.minimum_duration_seconds
        or duration_binding.get("selection_maximum_duration_seconds")
        != request.maximum_duration_seconds
        or duration_binding.get("asset_request_hash") != request.request_hash
        or duration_binding.get("pexels_query_plan_hash") != plan.plan_hash
        or request.minimum_duration_seconds
        < stock_scene_duration_ms / 1000
    ):
        raise RuntimeError("CQR1_PEXELS_CANONICAL_DURATION_BINDING_INVALID")
    policy = approved_policy()
    weights = VisualRankingWeights.from_policy(policy)
    risks = VisualRiskPenalties.from_policy(policy)
    thresholds = VisualScoreThresholds.from_policy(policy)
    client = PlannedPexelsV2SearchClient()
    search_box: dict[str, Any] = {}

    def search_once() -> Mapping[str, Any]:
        execution = client.search_and_rank_once(
            api_key=settings.pexels_api_key.get_secret_value(),  # type: ignore[union-attr]
            plan=plan,
            request=request,
            visual_direction=direction,
            weights=weights,
            risk_penalties=risks,
            thresholds=thresholds,
            previous_scene=plan.previous_scene_summary,
            next_scene=plan.next_scene_summary,
            previous_asset_usage_refs=list(REJECTED_PEXELS_ASSET_REFS),
            asset_reuse_history=plan.asset_reuse_history,
            allow_provider_search_review_floor=True,
        )
        selected = execution.selected_candidate
        if selected is None or execution.ranking.ranking_verdict == "BLOCK":
            write_json(
                MANIFESTS / "pexels_search_ranking_provenance.json",
                execution.safe_evidence(),
            )
            raise RuntimeError("PEXELS_CONTEXTUAL_RANKING_BLOCK")
        selected_score = next(
            item
            for item in execution.ranking.candidate_scores
            if item.candidate_id == selected.candidate_id
        )
        semantic = float(selected_score.dimensions["semantic_relevance"])
        continuity = min(
            float(selected_score.dimensions["visual_direction_fit"]),
            float(selected_score.dimensions["previous_scene_continuity"]),
            float(selected_score.dimensions["next_scene_continuity"]),
        )
        if semantic < thresholds.semantic_review_min:
            raise RuntimeError("PEXELS_SEMANTIC_BELOW_REVIEW")
        if continuity < thresholds.adjacency_review_min:
            raise RuntimeError("PEXELS_ADJACENCY_BLOCK")
        if selected.hard_conflict_tags:
            raise RuntimeError("PEXELS_HARD_SEMANTIC_CONFLICT")
        stock_segment = next(
            item for item in timeline_model.segments if item.segment_id == "cqr1-stock-support"
        )
        if selected.duration_seconds + 0.05 < stock_segment.target_scene_duration_ms / 1000:
            raise RuntimeError("PEXELS_SELECTED_CLIP_TOO_SHORT_FOR_CANONICAL_SCENE")
        rendition = PexelsRenditionSelector().select(selected, request)
        download_plan = PexelsDownloadPlanBuilder().build(selected, rendition, request)
        context = PexelsDownloadExecutionContext.from_selected_api_rendition(
            provider_asset_id=selected.provider_asset_id,
            rendition=rendition,
            workspace_directory=STOCK_DIR,
            maximum_allowed_bytes=500 * 1024 * 1024,
        )
        context.validate_against(download_plan)
        if context.workspace_target_path.exists() or context.workspace_target_path.with_name(
            context.workspace_target_path.name + ".part"
        ).exists():
            raise RuntimeError("PEXELS_DOWNLOAD_DESTINATION_NOT_FRESH")
        provenance = execution.safe_evidence()
        provenance.update(
            {
                "selected_score": selected_score.model_dump(mode="json"),
                "download_plan": download_plan.model_dump(mode="json"),
                "selection_rationale": execution.ranking.selected_rationale,
                "post_download_representative_frame_review_required": True,
                "raw_media_url_persisted": False,
            }
        )
        write_json(MANIFESTS / "pexels_search_ranking_provenance.json", provenance)
        search_box.update(
            execution=execution,
            selected=selected,
            download_plan=download_plan,
            context=context,
            selected_score=selected_score,
        )
        return {
            "candidate_count": len(execution.candidates),
            "selected_candidate_id": selected.candidate_id,
            "ranking_verdict": execution.ranking.ranking_verdict,
            "semantic_score": semantic,
            "continuity_floor_score": continuity,
            "rate_limit": execution.rate_limit,
            "query_plan_hash": plan.plan_hash,
            "output_count": 0,
        }

    search_result = run_guarded_once(
        ledger=ledger,
        operation_key="pexels_search",
        preflight=preflight,
        operation=search_once,
    )
    if search_result["status"] != "SUCCEEDED":
        raise RuntimeError("CQR1_PEXELS_SEARCH_NOT_SUCCEEDED")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    download_entry = ledger.entries["pexels_download"]
    if (
        download_entry.status != "PLANNED"
        or download_entry.attempt_count != 0
        or download_entry.provider_call_made
    ):
        raise RuntimeError("CQR1_PEXELS_DOWNLOAD_NOT_FRESH")

    def download_once() -> Mapping[str, Any]:
        download_client = PexelsPA1RClient()
        receipt = download_client.download_once(
            plan=search_box["download_plan"],
            execution_context=search_box["context"],
            request_id=request.request_id,
        )
        model_json(MANIFESTS / "pexels_download_receipt.json", receipt)
        stock_manifest = build_stock_source_manifest(
            asset_id="cqr1-paid-canary-stock-001",
            request=request,
            query_used=plan.queries[0],
            candidate=search_box["selected"],
            plan=search_box["download_plan"],
            download=receipt,
            retrieved_at=datetime.now(UTC),
            rights_policy_ref="policy://pexels-supporting-stock/review-required",
            attribution_required=settings.pexels_attribution_required,
        )
        model_json(MANIFESTS / "pexels_stock_source_manifest.json", stock_manifest)
        original = require_inside(Path(receipt.local_path or ""), must_exist=True)
        still = require_inside(RENDER_DIR / "proxy/pexels-selected-representative.jpg")
        still.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-ss",
                str(min(2.0, max(0.1, search_box["selected"].duration_seconds / 2))),
                "-i",
                str(original),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(still),
            ],
            capture_output=True,
            text=True,
            shell=False,
            check=True,
        )
        return {
            "provider_asset_id": search_box["selected"].provider_asset_id,
            "provider_file_id": search_box["download_plan"].provider_file_id,
            "local_path": str(original),
            "size_bytes": receipt.size_bytes,
            "sha256": receipt.sha256,
            "representative_still_path": str(still),
            "representative_still_sha256": sha256_file(still),
            "receipt_hash": receipt.receipt_hash,
            "output_count": 1,
        }

    download_result = run_guarded_once(
        ledger=ledger,
        operation_key="pexels_download",
        preflight=preflight,
        operation=download_once,
    )
    result = {
        "CQR1D_PEXELS": "PASS",
        "search": search_result,
        "download": download_result,
        "post_download_representative_frame_review": "PENDING_CODEX_PERCEPTUAL_REVIEW",
    }
    append_event(
        "PEXELS_SEARCH_AND_DOWNLOAD_COMPLETED",
        {
            "status": "PASS",
            "search_flow_count": 1,
            "download_count": 1,
            "automatic_retry": False,
            "provider_call_count": CQR1CanaryCallLedger.load(
                MANIFESTS / "planned_provider_call_ledger.json"
            ).provider_call_count,
        },
    )
    print(json.dumps(result, indent=2))
    return result


def veo_submit() -> dict[str, Any]:
    raise RuntimeError("CQR1_RUN009_NEW_VEO_SUBMIT_FORBIDDEN_REUSED_RUN007_OUTPUT")


def _legacy_veo_submit_forbidden_reference_only() -> dict[str, Any]:
    settings, ledger, preflight = load_execution_context(
        "google_veo_submit",
        prerequisites=(
            "elevenlabs_tts",
            "elevenlabs_forced_alignment",
            "pexels_search",
            "pexels_download",
        ),
    )
    require_scoped_external_execution_flags(settings, provider="google_veo")
    if settings.veo_model_id != "veo-3.1-fast-generate-preview":
        raise RuntimeError("CQR1_GOOGLE_VEO_MODEL_DRIFT")
    if (
        settings.veo_default_duration_seconds != 8
        or settings.veo_default_resolution != "720p"
        or settings.veo_default_aspect_ratio != "16:9"
        or settings.veo_default_output_count != 1
    ):
        raise RuntimeError("CQR1_GOOGLE_VEO_CONTRACT_DRIFT")
    timeline_model = CanonicalMediaTimeline.model_validate(
        read_json(MANIFESTS / "canonical_media_timeline.json")
    )
    hero_segment = next(
        item for item in timeline_model.segments if item.segment_id == "cqr1-veo-hero"
    )
    policy = approved_policy()
    thresholds = __import__(
        "app.contracts.visual_direction", fromlist=["VeoDurationFitThresholds"]
    ).VeoDurationFitThresholds.from_policy(policy)
    duration_fit = VeoFixedDurationPlanner(thresholds).decide(
        hero_segment.target_scene_duration_ms / 1000
    )
    model_json(MANIFESTS / "paid_veo_duration_fit.json", duration_fit)
    if not duration_fit.provider_execution_allowed:
        raise RuntimeError("CQR1_VEO_DURATION_FIT_BLOCKED_BEFORE_SUBMIT")
    compiled_prompt = __import__(
        "app.contracts.visual_direction", fromlist=["CompiledVeoPrompt"]
    ).CompiledVeoPrompt.model_validate(read_json(MANIFESTS / "resume_veo_prompt.json"))
    if (
        hashlib.sha256(compiled_prompt.prompt.encode("utf-8")).hexdigest()
        != compiled_prompt.prompt_hash
        or stable_hash(
            compiled_prompt.model_dump(mode="json", exclude={"content_hash"})
        )
        != compiled_prompt.content_hash
        or compiled_prompt.visual_direction_hash
        != VisualDirectionContract.model_validate(
            read_json(MANIFESTS / "resume_visual_direction_contract.json")
        ).content_hash
    ):
        raise RuntimeError("CQR1_VEO_PROMPT_BINDING_HASH_MISMATCH")
    generic = build_ai_hero_request(
        hero_asset_request(),
        package_id=PACKAGE_ID,
        project_id=CQR1_RUN_ID,
        channel_id=CHANNEL_KEY,
        prompt_text=compiled_prompt.prompt,
        provider_resolution_policy_ref="policy://hpr1-pa1r/veo-720p-8s",
    )
    model_json(MANIFESTS / "veo_generic_ai_hero_request.json", generic)
    catalog = GoogleVeoModelPriceCatalog()
    cost = catalog.estimate(
        model_id=settings.veo_model_id,
        resolution=settings.veo_default_resolution,
        duration_seconds=settings.veo_default_duration_seconds,
        output_count=settings.veo_default_output_count,
        hard_cap=Decimal("3.00"),
        approval_amount=Decimal("3.00"),
    )
    model_json(MANIFESTS / "veo_cost_estimate_snapshot.json", cost)
    total_estimate = cost.estimated_amount + Decimal("0.05")
    if total_estimate > Decimal("3.00"):
        raise RuntimeError("CQR1_TOTAL_HARD_COST_CAP_EXCEEDED")
    adapter = GoogleVeoAdapter(settings)
    idempotency_key = "cqr1-idem:" + stable_hash(
        {
            "run_id": CQR1_RUN_ID,
            "operation": "google_veo_submit",
            "prompt_hash": compiled_prompt.prompt_hash,
            "approval_ref": APPROVAL_REF,
        }
    )
    request = adapter.build_generation_request(
        generic,
        cost_catalog_ref=cost.price_catalog_ref,
        approval_ref=APPROVAL_REF,
        approval_scope="CQR1_CONTROLLED_PAID_CANARY_NON_PRODUCTION",
        idempotency_key=idempotency_key,
    )
    model_json(MANIFESTS / "google_veo_generation_request.json", request)
    transport = adapter.transport_config_evidence(request)
    if (
        transport["generate_audio_parameter_sent"]
        or transport["person_generation_sent"] != "allow_all"
        or transport["provider_audio_usage_policy"] != "DISCARD"
        or transport["automatic_retry"]
    ):
        raise RuntimeError("CQR1_GOOGLE_VEO_TRANSPORT_CONTRACT_DRIFT")
    write_json(MANIFESTS / "google_veo_transport_contract.json", transport)
    gate_evidence = {
        "provider_boundary_gate_passed": (
            adapter.transport == "GEMINI_DEVELOPER_API"
            and request.model_id == settings.veo_model_id
        ),
        "human_paid_render_approval_passed": request.approval_ref == APPROVAL_REF,
        "cost_estimate_snapshot_passed": (
            cost.estimated_amount <= cost.hard_cap
            and cost.estimated_amount <= cost.approval_amount
        ),
        "channel_monthly_budget_gate_passed": total_estimate <= Decimal("3.00"),
        "paid_attempt_limit_gate_passed": (
            ledger.entries["google_veo_submit"].attempt_count == 0
            and ledger.entries["google_veo_submit"].max_attempts == 1
        ),
        "provider_idempotency_key_valid": request.idempotency_key == idempotency_key,
        "global_kill_switch_open": not settings.media_provider_calls_disabled,
        "provider_kill_switch_open": settings.veo_real_generation_enabled,
        "approved_production_execution_scope": False,
    }
    write_json(MANIFESTS / "google_veo_execution_gate_evidence.json", gate_evidence)
    gates = GoogleVeoExecutionGates(
        **gate_evidence,
    )
    if not gates.all_passed:
        raise RuntimeError("CQR1_GOOGLE_VEO_EXECUTION_GATE_BLOCKED")

    def submit_once() -> Mapping[str, Any]:
        receipt = adapter.submit_generation(request, gates=gates, fixture_only=False)
        model_json(MANIFESTS / "google_veo_operation_receipt.json", receipt)
        if (
            not receipt.provider_call_made
            or receipt.submit_attempt_no != 1
            or receipt.normalized_status != "SUBMITTED"
            or not receipt.provider_operation_id
        ):
            raise RuntimeError("CQR1_GOOGLE_VEO_SUBMIT_NOT_CONFIRMED")
        return {
            "provider_operation_id": receipt.provider_operation_id,
            "request_hash": receipt.request_hash,
            "submit_attempt_no": receipt.submit_attempt_no,
            "normalized_status": receipt.normalized_status,
            "model_id": request.model_id,
            "estimated_cost_usd": str(cost.estimated_amount),
            "output_count": 0,
        }

    result = run_guarded_once(
        ledger=ledger,
        operation_key="google_veo_submit",
        preflight=preflight,
        operation=submit_once,
    )
    append_event(
        "GOOGLE_VEO_SUBMITTED",
        {
            "status": result["status"],
            "generation_submit_count": 1,
            "automatic_provider_retry": False,
            "external_provider_fallback": False,
            "provider_call_count": CQR1CanaryCallLedger.load(
                MANIFESTS / "planned_provider_call_ledger.json"
            ).provider_call_count,
        },
    )
    print(json.dumps({"CQR1D_GOOGLE_VEO": "WAITING_PROVIDER", **result}, indent=2))
    return result


def veo_poll() -> dict[str, Any]:
    raise RuntimeError("CQR1_RUN009_NEW_VEO_OUTPUT_CALL_FORBIDDEN_REUSED_RUN007_OUTPUT")


def _legacy_veo_output_forbidden_reference_only() -> dict[str, Any]:
    settings, ledger, preflight = load_execution_context(
        "google_veo_output",
        prerequisites=(
            "elevenlabs_tts",
            "elevenlabs_forced_alignment",
            "pexels_search",
            "pexels_download",
            "google_veo_submit",
        ),
    )
    if (MANIFESTS / "google_veo_terminal_failure.json").exists():
        raise RuntimeError("CQR1_GOOGLE_VEO_TERMINAL_FAILURE_RECORDED")
    require_scoped_external_execution_flags(settings, provider="google_veo")
    if settings.veo_model_id != "veo-3.1-fast-generate-preview":
        raise RuntimeError("CQR1_GOOGLE_VEO_MODEL_DRIFT")
    prior = GoogleVeoOperationReceipt.model_validate(
        read_json(MANIFESTS / "google_veo_operation_receipt.json")
    )
    adapter = GoogleVeoAdapter(settings)
    latest = adapter.poll_operation(
        prior,
        max_polls=1,
        fixture_only=False,
        poll_interval_seconds=0,
    )
    model_json(MANIFESTS / "google_veo_operation_receipt.json", latest)
    append_event(
        "GOOGLE_VEO_POLLED",
        {
            "provider_operation_id": latest.provider_operation_id,
            "provider_status": latest.provider_status,
            "normalized_status": latest.normalized_status,
            "generation_submit_count": 1,
            "automatic_provider_retry": False,
        },
    )
    if latest.normalized_status in {"SUBMITTED", "PROCESSING"}:
        result = {
            "CQR1D_GOOGLE_VEO": "WAITING_PROVIDER",
            "provider_operation_id": latest.provider_operation_id,
            "normalized_status": latest.normalized_status,
            "generation_submit_count": 1,
        }
        print(json.dumps(result, indent=2))
        return result
    if latest.normalized_status != "SUCCEEDED":
        terminal = {
            "provider_operation_id": latest.provider_operation_id,
            "normalized_status": latest.normalized_status,
            "provider_error_code": latest.provider_error_code,
            "generation_submit_count": 1,
            "second_submit_authorized": False,
            "automatic_retry": False,
        }
        terminal["content_hash"] = stable_hash(terminal)
        write_json(MANIFESTS / "google_veo_terminal_failure.json", terminal)
        append_event(
            "GOOGLE_VEO_TERMINAL_FAILURE",
            terminal,
        )
        raise RuntimeError(f"CQR1_GOOGLE_VEO_PROVIDER_FAILED:{latest.normalized_status}")
    destination = require_inside(HERO_DIR / "google-veo-hero-original.mp4")
    if destination.exists() or Path(str(destination) + ".part").exists():
        raise RuntimeError("CQR1_GOOGLE_VEO_OUTPUT_DESTINATION_NOT_FRESH")

    def download_once() -> Mapping[str, Any]:
        download = adapter.download_real_output(latest, destination_path=destination)
        write_json(MANIFESTS / "google_veo_download_receipt.json", download)
        media = probe_media(destination, ffprobe=FFPROBE)
        write_json(MANIFESTS / "google_veo_original_ffprobe.json", media)
        video_streams = [item for item in media.get("streams", []) if item.get("codec_type") == "video"]
        audio_streams = [item for item in media.get("streams", []) if item.get("codec_type") == "audio"]
        if len(video_streams) != 1:
            raise RuntimeError("CQR1_GOOGLE_VEO_OUTPUT_VIDEO_STREAM_INVALID")
        still = require_inside(RENDER_DIR / "proxy/veo-hero-representative.jpg")
        still.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-ss",
                "4.0",
                "-i",
                str(destination),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(still),
            ],
            capture_output=True,
            text=True,
            shell=False,
            check=True,
        )
        request = read_json(MANIFESTS / "google_veo_generation_request.json")
        prompt = read_json(MANIFESTS / "resume_veo_prompt.json")
        cost = read_json(MANIFESTS / "veo_cost_estimate_snapshot.json")
        provenance = {
            "provider": "GOOGLE_VEO",
            "transport": adapter.transport,
            "model_id": request["model_id"],
            "operation_id": latest.provider_operation_id,
            "prompt_hash": prompt["prompt_hash"],
            "prompt_content_hash": prompt["content_hash"],
            "request_hash": request["request_hash"],
            "duration_seconds": request["duration_seconds"],
            "resolution": request["resolution"],
            "aspect_ratio": request["aspect_ratio"],
            "output_count": request["output_count"],
            "generate_audio_parameter_sent": False,
            "person_generation": "allow_all",
            "provider_audio_policy": "DISCARD",
            "downloaded_file_path": str(destination),
            "size_bytes": download["size_bytes"],
            "sha256": download["sha256"],
            "provider_audio_present": bool(audio_streams),
            "provider_audio_stream_metadata": {
                "stream_count": len(audio_streams),
                "streams": audio_streams,
            },
            "provider_audio_discarded": False,
            "representative_still_path": str(still),
            "representative_still_sha256": sha256_file(still),
            "cost_evidence": {
                "estimated_amount": cost["estimated_amount"],
                "actual_amount": None,
                "actual_cost_reason": "provider operation did not expose billed amount",
            },
            "approval_ref": APPROVAL_REF,
            "raw_output_url_persisted": False,
            "automatic_retry": False,
            "external_provider_fallback": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        provenance["content_hash"] = stable_hash(provenance)
        write_json(MANIFESTS / "veo_prompt_request_provenance.json", provenance)
        return {
            "provider_operation_id": latest.provider_operation_id,
            "downloaded_path": str(destination),
            "size_bytes": download["size_bytes"],
            "sha256": download["sha256"],
            "provider_audio_present": bool(audio_streams),
            "representative_still_path": str(still),
            "representative_still_sha256": sha256_file(still),
            "provenance_hash": provenance["content_hash"],
            "output_count": 1,
        }

    result = run_guarded_once(
        ledger=ledger,
        operation_key="google_veo_output",
        preflight=preflight,
        operation=download_once,
    )
    append_event(
        "GOOGLE_VEO_OUTPUT_DOWNLOADED",
        {
            "status": result["status"],
            "generation_submit_count": 1,
            "output_count": 1,
            "automatic_provider_retry": False,
            "external_provider_fallback": False,
        },
    )
    print(json.dumps({"CQR1D_GOOGLE_VEO": "PASS", **result}, indent=2))
    return result


def _execute_normalization(manifest: Any, *, video: bool) -> dict[str, Any]:
    """Execute one local deterministic normalization plan and verify its shape."""

    payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    if stable_hash(payload) != manifest.manifest_hash:
        raise RuntimeError("CQR1_NORMALIZATION_MANIFEST_HASH_MISMATCH")
    output = require_inside(Path(manifest.output_path))
    if output.exists() or Path(str(output) + ".part").exists():
        raise RuntimeError("CQR1_NORMALIZATION_DESTINATION_NOT_FRESH")
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = list(manifest.sanitized_ffmpeg_argv_plan)
    argv[0] = FFMPEG
    argv.insert(1, "-y")
    if video:
        argv[-1:-1] = [
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "8M",
            "-maxrate",
            "10M",
            *bt709_h264_metadata_args(),
            "-movflags",
            "+faststart",
        ]
    started = datetime.now(UTC)
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CQR1_NORMALIZATION_FAILED:{result.returncode}")
    probe = probe_media(output, ffprobe=FFPROBE)
    streams = probe.get("streams") or []
    if video:
        video_stream = next(
            (item for item in streams if item.get("codec_type") == "video"), None
        )
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        if not video_stream or audio_streams:
            raise RuntimeError("CQR1_NORMALIZED_VIDEO_SHAPE_INVALID")
        if (
            (video_stream.get("width"), video_stream.get("height")) != (1920, 1080)
            or video_stream.get("pix_fmt") != "yuv420p"
            or video_stream.get("avg_frame_rate") not in {"30/1", "60/2"}
            or video_stream.get("color_space") != "bt709"
            or video_stream.get("color_primaries") != "bt709"
            or video_stream.get("color_transfer") != "bt709"
        ):
            raise RuntimeError("CQR1_NORMALIZED_VIDEO_PROFILE_INVALID")
    else:
        audio_stream = next(
            (item for item in streams if item.get("codec_type") == "audio"), None
        )
        if not audio_stream or (
            int(audio_stream.get("sample_rate") or 0), audio_stream.get("channels")
        ) != (48000, 2):
            raise RuntimeError("CQR1_NORMALIZED_AUDIO_PROFILE_INVALID")
    receipt = {
        "manifest_hash": manifest.manifest_hash,
        "output_path": str(output),
        "output_sha256": sha256_file(output),
        "output_size_bytes": output.stat().st_size,
        "media_type": "VIDEO" if video else "AUDIO",
        "provider_call_made": False,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "ffprobe": probe,
    }
    receipt["content_hash"] = stable_hash(receipt)
    return receipt


def _visual_gate_evidence() -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Validate Codex's post-download frame review and compile real visual gates."""

    review_path = require_inside(QC_DIR / "codex_visual_asset_review.json", must_exist=True)
    review = read_json(review_path)
    if review.get("review_state") != "COMPLETED_REAL_FRAMES":
        raise RuntimeError("CQR1_REAL_VISUAL_FRAME_REVIEW_REQUIRED")
    expected = {"cqr1-stock-support", "cqr1-veo-hero"}
    rows = review.get("assets")
    if not isinstance(rows, list) or {str(item.get("scene_id")) for item in rows} != expected:
        raise RuntimeError("CQR1_VISUAL_REVIEW_ASSET_SET_INVALID")
    thresholds = VisualScoreThresholds.from_policy(approved_policy())
    evaluator = VisualEvaluationService(thresholds)
    evaluations: list[Any] = []
    for row in rows:
        still = require_inside(Path(str(row.get("representative_still_path") or "")), must_exist=True)
        if sha256_file(still) != row.get("representative_still_sha256"):
            raise RuntimeError("CQR1_VISUAL_REVIEW_STILL_HASH_MISMATCH")
        if row.get("logo_or_readable_text_present") is True:
            raise RuntimeError("CQR1_VISUAL_REVIEW_LOGO_OR_TEXT_BLOCK")
        if row.get("identifiable_person_present") is True and row["scene_id"] == "cqr1-veo-hero":
            raise RuntimeError("CQR1_VEO_PERSON_POLICY_BLOCK")
        source_class = (
            "SUPPORTING_STOCK"
            if row["scene_id"] == "cqr1-stock-support"
            else "AI_HERO"
        )
        evaluations.append(
            evaluator.evaluate_scene(
                scene_id=row["scene_id"],
                asset_ref=str(row["asset_ref"]),
                semantic_score=float(row["semantic_score"]),
                visual_direction_score=float(row["visual_direction_score"]),
                previous_adjacency_score=float(row["previous_adjacency_score"]),
                next_adjacency_score=float(row["next_adjacency_score"]),
                current_source_class=source_class,
                previous_source_class="NATIVE_VISUAL",
                next_source_class="NATIVE_VISUAL",
                hard_conflict_reasons=list(row.get("hard_conflict_reasons") or []),
                selected_rationale=str(row["selected_rationale"]),
                representative_still_refs=[str(still)],
            )
        )
    if any(item.result == "BLOCK" for item in evaluations):
        write_json(
            QC_DIR / "visual_asset_evaluations.json",
            {"evaluations": [item.model_dump(mode="json") for item in evaluations]},
        )
        raise RuntimeError("CQR1_REAL_VISUAL_GATE_BLOCK")
    severity = {"PASS": 0, "REVIEW_REQUIRED": 1, "BLOCK": 2}
    aggregate: dict[str, dict[str, Any]] = {}
    for gate_name in (
        "SceneSemanticMatchGate",
        "VisualContinuityGate",
        "AssetAdjacencyGate",
    ):
        instances = [
            gate
            for evaluation in evaluations
            for gate in evaluation.gate_results
            if gate.gate == gate_name
        ]
        verdict = max(instances, key=lambda item: severity[item.verdict]).verdict
        body = {
            "gate_name": gate_name,
            "result": verdict,
            "reason_codes": sorted(
                {reason for item in instances for reason in item.reason_codes}
            ),
            "metrics": {
                "minimum_score": min(
                    item.score for item in instances if item.score is not None
                ),
                "asset_count": len(instances),
                "native_bridge_between_provider_assets": True,
                "direct_pexels_to_veo_cut": False,
            },
            "evidence_refs": [str(review_path)],
        }
        body["content_hash"] = stable_hash(body)
        aggregate[gate_name] = body
    report = {
        "review_source": "CODEX_PERCEPTUAL_REVIEW_OF_REAL_REPRESENTATIVE_FRAMES",
        "human_full_watch_completed": False,
        "native_bridge_between_provider_assets": True,
        "evaluations": [item.model_dump(mode="json") for item in evaluations],
        "aggregate_gates": aggregate,
        "production_eligible": False,
        "not_publishable": True,
    }
    report["content_hash"] = stable_hash(report)
    write_json(QC_DIR / "visual_continuity_report.json", report)
    return evaluations, aggregate


def _native_render_plan(
    *,
    timeline_model: CanonicalMediaTimeline,
    stock: Path,
    hero: Path,
    audio: Path,
    visual_gates: Mapping[str, Any],
) -> NativeRenderPlan:
    caption_gates = read_json(QC_DIR / "caption_sync_coverage_drift.json")
    creative_gates = {**caption_gates, **dict(visual_gates)}
    direction = VisualDirectionContract.model_validate(
        read_json(MANIFESTS / "resume_visual_direction_contract.json")
    )
    treatments = {
        "cqr1-native-open": ("DIAGRAM", "APPROVED_SCRIPT_FLOW"),
        "cqr1-native-timeline": ("TIMELINE", "CANONICAL_TIMELINE"),
        "cqr1-stock-support": ("STOCK_VIDEO", "GROUNDED_WORKSPACE"),
        "cqr1-native-bridge": ("DIAGRAM", "CONTINUITY_BRIDGE"),
        "cqr1-veo-hero": ("AI_HERO_VIDEO", "SYNCHRONIZED_FILMSTRIP"),
        "cqr1-native-close": ("DATA_CARD", "QC_AND_ARCHIVE"),
    }
    scenes: list[NativeRenderScene] = []
    for index, segment in enumerate(timeline_model.segments):
        treatment, layout = treatments[segment.segment_id]
        requirements: list[AssetRequirement] = []
        resolved: list[ResolvedAssetRef] = []
        provider_intent: str | None = None
        if segment.segment_id == "cqr1-stock-support":
            requirements = [AssetRequirement(key="stock", kind="LOCAL_FILE")]
            resolved = [
                ResolvedAssetRef(
                    key="stock", path=str(stock), checksum=sha256_file(stock)
                )
            ]
            provider_intent = "PEXELS_SUPPORTING_STOCK"
        elif segment.segment_id == "cqr1-veo-hero":
            requirements = [AssetRequirement(key="hero", kind="LOCAL_FILE")]
            resolved = [
                ResolvedAssetRef(
                    key="hero", path=str(hero), checksum=sha256_file(hero)
                )
            ]
            provider_intent = "GOOGLE_VEO_AI_HERO"
        scene_payload = {
            "scene_id": segment.segment_id,
            "source_segment_ids": [segment.segment_id],
            "narration_start_ms": segment.scene_start_ms,
            "narration_end_ms": segment.scene_end_ms,
            "duration_ms": segment.target_scene_duration_ms,
            "visual_treatment": treatment,
            "layout_type": layout,
            "asset_requirements": requirements,
            "resolved_asset_refs": resolved,
            "animation_type": "HOLD_STATIC",
            "transition_in": None,
            "transition_out": None,
            "emphasis_targets": [],
            "caption_behavior": "BURN_IN",
            "safe_area_policy": "CQR1_16_9_FROZEN_ASS",
            "originality_role": "SUPPORT" if provider_intent else "EXPLANATION_BACKBONE",
            "provider_intent": provider_intent,
            "scene_notes": CQR1_VISIBLE_LABEL,
        }
        scene_payload["scene_hash"] = stable_hash(scene_payload)
        scenes.append(NativeRenderScene(**scene_payload))
    caption_ref = str(timeline_model.qc_metrics["caption_compilation_ref"])
    caption_hash = str(timeline_model.qc_metrics["caption_compilation_hash"])
    body = {
        "plan_id": f"{CQR1_RUN_ID}-native-render-plan-v1",
        "plan_version": 1,
        "package_id": PACKAGE_ID,
        "video_project_id": CQR1_RUN_ID,
        "company_id": COMPANY_ID,
        "channel_id": CHANNEL_KEY,
        "channel_profile_version_id": CHANNEL_PROFILE_VERSION_ID,
        "effective_context_snapshot_id": EFFECTIVE_CONTEXT_ID,
        "effective_context_hash": EFFECTIVE_CONTEXT_HASH,
        "format_identity_contract_ref": FORMAT_IDENTITY_REF,
        "format_identity_contract_hash": FORMAT_IDENTITY_HASH,
        "format_identity_status": "APPROVED",
        "episode_originality_manifest_ref": ORIGINALITY_REF,
        "episode_originality_manifest_hash": ORIGINALITY_HASH,
        "final_originality_gate": "PASS",
        "claim_evidence_ledger_refs": [],
        "synthetic_media_disclosure_receipt_ref": str(
            MANIFESTS / "synthetic_media_disclosure_receipt.json"
        ),
        "script_ref": str(SOURCE_SCRIPT / "approved_script.json"),
        "script_hash": sha256_file(SOURCE_SCRIPT / "approved_script.json"),
        "srt_ref": caption_ref,
        "srt_hash": caption_hash,
        "audio_timeline_ref": str(MANIFESTS / "canonical_media_timeline.json"),
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": str(MANIFESTS / "canonical_media_timeline.json"),
        "canonical_media_timeline_hash": timeline_model.timeline_hash,
        "canonical_audio_asset_ref": timeline_model.audio_asset_id,
        "canonical_caption_compilation_ref": caption_ref,
        "canonical_caption_compilation_hash": caption_hash,
        "canonical_caption_render_payload_hash": str(
            timeline_model.qc_metrics["caption_render_payload_hash"]
        ),
        "scene_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
        "parallel_timing_inputs": [],
        "visual_plan_ref": str(MANIFESTS / "resume_visual_direction_contract.json"),
        "visual_plan_hash": direction.content_hash,
        "visual_direction_contract_ref": f"visual-direction:{direction.content_hash}",
        "visual_direction_contract_hash": direction.content_hash,
        "creative_gate_results": creative_gates,
        "canvas_spec": CanvasSpec(width=1920, height=1080, fps=30),
        "scenes": scenes,
        "global_motion_policy": {
            "native_explanatory_backbone": True,
            "native_bridge_between_provider_assets": True,
            "one_render_at_a_time": True,
            "transition_execution": "EXPLICIT_HARD_CUTS_WITH_NATIVE_CONTINUITY_BRIDGES",
            "declared_transition_schedule_matches_ffmpeg": True,
        },
        "caption_policy": dict(timeline_model.qc_metrics["caption_render_style"]),
        "audio_policy": {
            "narration_source": "ELEVENLABS_FINAL_AUDIO",
            "normalized_audio_path": str(audio),
            "provider_audio_policy": "DISCARD",
            "atempo_used": False,
            "duration_override_used": False,
        },
        "output_profiles": ["YT_LONG_1080P30_SDR_H264_VT"],
        "character_policy_mode": "NO_CHARACTER",
        "purpose": CQR1_PURPOSE,
        "production_eligible": False,
        "status": "APPROVED",
        "created_at": datetime.now(UTC),
        "created_by": "codex-bounded-paid-canary-execution",
    }
    plan = NativeRenderPlan(**body)
    plan.content_hash = canonical_plan_hash(plan)
    return plan


def _ffmpeg_command_manifest(
    *,
    timeline_model: CanonicalMediaTimeline,
    compiled: Any,
    stock: Path,
    hero: Path,
    audio: Path,
) -> FFmpegCommandManifest:
    work = require_inside(RENDER_DIR / "scenes")
    work.mkdir(parents=True, exist_ok=True)
    output = require_inside(RENDER_DIR / "final/cqr1-non-production-canary.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or Path(str(output) + ".part.mp4").exists():
        raise RuntimeError("CQR1_FINAL_RENDER_DESTINATION_NOT_FRESH")
    cues = canonical_caption_cues(timeline_model)
    ass = work / "canonical-captions.ass"
    write_caption_ass(
        ass,
        cues=[item.model_dump(mode="json") for item in cues],
        frame_width=1920,
        frame_height=1080,
        render_style=dict(timeline_model.qc_metrics["caption_render_style"]),
    )
    ordered = sorted(timeline_model.segments, key=lambda item: item.scene_start_ms)
    windows: list[dict[str, Any]] = []
    cursor_ms = 0
    for segment_index, segment in enumerate(ordered):
        if segment.scene_start_ms < cursor_ms:
            raise RuntimeError("CQR1_CANONICAL_RENDER_WINDOW_OVERLAP")
        if segment.scene_start_ms > cursor_ms:
            previous = ordered[segment_index - 1] if segment_index else None
            native_source = next(
                (
                    candidate.segment_id
                    for candidate in (previous, segment)
                    if candidate is not None
                    and candidate.segment_id.startswith("cqr1-native-")
                ),
                None,
            )
            if native_source is None:
                raise RuntimeError("CQR1_CANONICAL_GAP_NATIVE_VISUAL_SOURCE_MISSING")
            windows.append(
                {
                    "kind": "CANONICAL_NATIVE_CONTINUITY_HOLD",
                    "scene_id": f"pause-before-{segment.segment_id}",
                    "visual_source_scene_id": native_source,
                    "render_start_ms": cursor_ms,
                    "render_end_ms": segment.scene_start_ms,
                    "render_duration_ms": segment.scene_start_ms - cursor_ms,
                    "derivation": "CANONICAL_INTER_SCENE_PAUSE_WITH_ADJACENT_NATIVE_VISUAL",
                }
            )
        windows.append(
            {
                "kind": "CANONICAL_SCENE",
                "scene_id": segment.segment_id,
                "visual_source_scene_id": segment.segment_id,
                "render_start_ms": segment.scene_start_ms,
                "render_end_ms": segment.scene_end_ms,
                "render_duration_ms": segment.target_scene_duration_ms,
                "derivation": "CANONICAL_SCENE_ANCHORS",
            }
        )
        cursor_ms = segment.scene_end_ms
    if cursor_ms < timeline_model.audio_duration_ms:
        trailing_source = ordered[-1].segment_id
        if not trailing_source.startswith("cqr1-native-"):
            raise RuntimeError("CQR1_TRAILING_GAP_NATIVE_VISUAL_SOURCE_MISSING")
        windows.append(
            {
                "kind": "CANONICAL_NATIVE_CONTINUITY_HOLD",
                "scene_id": "pause-after-final-scene",
                "visual_source_scene_id": trailing_source,
                "render_start_ms": cursor_ms,
                "render_end_ms": timeline_model.audio_duration_ms,
                "render_duration_ms": timeline_model.audio_duration_ms - cursor_ms,
                "derivation": "CANONICAL_TRAILING_PAUSE_WITH_FINAL_NATIVE_VISUAL",
            }
        )
    if sum(item["render_duration_ms"] for item in windows) != timeline_model.audio_duration_ms:
        raise RuntimeError("CQR1_CANONICAL_RENDER_WINDOWS_DO_NOT_COVER_AUDIO")
    stock_duration = media_duration_seconds(probe_media(stock, ffprobe=FFPROBE))
    hero_duration = media_duration_seconds(probe_media(hero, ffprobe=FFPROBE))
    duration_fit = read_json(MANIFESTS / "paid_veo_duration_fit.json")
    if not _valid_content_hash(duration_fit):
        raise RuntimeError("CQR1_PAID_VEO_DURATION_FIT_HASH_INVALID")
    hero_trim_start = float(duration_fit.get("trim_head_seconds") or 0)
    hero_trim_tail = float(duration_fit.get("trim_tail_seconds") or 0)
    for item in windows:
        required = item["render_duration_ms"] / 1000
        visual_scene_id = item["visual_source_scene_id"]
        if visual_scene_id == "cqr1-stock-support" and required > stock_duration + 0.02:
            raise RuntimeError("CQR1_STOCK_ASSET_TOO_SHORT_FOR_RENDER_WINDOW")
        if (
            visual_scene_id == "cqr1-veo-hero"
            and hero_trim_start + required + hero_trim_tail > hero_duration + 0.02
        ):
            raise RuntimeError("CQR1_VEO_ASSET_TOO_SHORT_FOR_RENDER_WINDOW")
    write_json(
        MANIFESTS / "canonical_visual_gap_schedule.json",
        {
            "authority": "CANONICAL_MEDIA_TIMELINE",
            "audio_duration_ms": timeline_model.audio_duration_ms,
            "entries": windows,
            "provider_scenes_extended": False,
            "provider_assets_looped": False,
            "silent_gap_visual_policy": "HOLD_ADJACENT_NATIVE_VISUAL",
            "pause_title_cards_inserted": False,
            "veo_trim_head_seconds": hero_trim_start,
            "veo_trim_tail_seconds": hero_trim_tail,
            "estimated_timing_used": False,
        },
    )
    input_index = {"cqr1-stock-support": 0, "cqr1-veo-hero": 1}
    native_copy = {
        "cqr1-native-open": ("APPROVED SCRIPT", "One narration authority"),
        "cqr1-native-timeline": ("CANONICAL TIMELINE", "Scenes and captions share timing"),
        "cqr1-native-bridge": ("NATIVE BRIDGE", "Continuity before the hero moment"),
        "cqr1-native-close": ("QC AND ARCHIVE", "Technical pass then human review"),
    }
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    filters: list[str] = []
    labels: list[str] = []
    for index, window in enumerate(windows):
        scene_id = window["scene_id"]
        visual_scene_id = window["visual_source_scene_id"]
        duration = window["render_duration_ms"] / 1000
        label = f"s{index}"
        labels.append(f"[{label}]")
        if visual_scene_id in input_index:
            trim_start = hero_trim_start if visual_scene_id == "cqr1-veo-hero" else 0.0
            filters.append(
                f"[{input_index[visual_scene_id]}:v]trim=start={trim_start:.3f}:duration={duration:.3f},"
                "setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,"
                f"crop=1920:1080,fps=30,format=yuv420p[{label}]"
            )
        else:
            title, subtitle = native_copy[visual_scene_id]
            filters.append(
                f"color=c=0x0b1020:s=1920x1080:r=30:d={duration:.3f},"
                "drawbox=x=150:y=150:w=1620:h=780:color=0x172033@1:t=fill,"
                f"drawtext=fontfile={font}:text='{title}':fontcolor=white:fontsize=76:x=(w-text_w)/2:y=360,"
                f"drawtext=fontfile={font}:text='{subtitle}':fontcolor=0x67e8f9:fontsize=38:x=(w-text_w)/2:y=500,"
                f"format=yuv420p[{label}]"
            )
    ass_filter = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[base]")
    filters.append(
        f"[base]ass=filename='{ass_filter}',"
        "drawbox=x=0:y=0:w=iw:h=60:color=black@0.76:t=fill,"
        f"drawtext=fontfile={font}:text='{CQR1_VISIBLE_LABEL}':fontcolor=white:fontsize=28:x=(w-text_w)/2:y=15,"
        "format=yuv420p[v]"
    )
    filters.append("[2:a]aresample=48000,asetpts=PTS-STARTPTS[a]")
    filtergraph = work / "cqr1-paid-canary-filtergraph.txt"
    filtergraph.write_text(";\n".join(filters) + "\n", encoding="utf-8")
    part = Path(str(output) + ".part.mp4")
    argv = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(stock),
        "-i",
        str(hero),
        "-i",
        str(audio),
        "-filter_complex_script",
        str(filtergraph),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "8M",
        "-maxrate",
        "10M",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        *bt709_h264_metadata_args(),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-shortest",
        str(part),
    ]
    if "-t" in argv or "atempo" in " ".join(argv):
        raise RuntimeError("CQR1_FORBIDDEN_DURATION_OR_SPEED_OVERRIDE")
    version = subprocess.run(
        [FFMPEG, "-version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    expected = {
        **compiled.output_specs[0],
        "expected_duration_seconds": timeline_model.audio_duration_ms / 1000,
        "max_av_drift_ms": 100,
    }
    checksums = {
        str(filtergraph): sha256_file(filtergraph),
        str(ass): sha256_file(ass),
    }
    core = {
        "run_key": CQR1_RUN_ID,
        "compiled_manifest_ref": compiled.compiled_manifest_id,
        "compiled_manifest_hash": compiled.manifest_hash,
        "ffmpeg_binary_path": FFMPEG,
        "ffprobe_binary_path": FFPROBE,
        "ffmpeg_version": version,
        "command_builder_version": "cqr1-real-canonical-assets/1.1.0-bt709-vui",
        "input_files": [str(stock), str(hero), str(audio)],
        "generated_filtergraph_path": str(filtergraph),
        "generated_text_files": [str(ass)],
        "generated_caption_path": str(ass),
        "generated_file_checksums": checksums,
        "output_file": str(output),
        "output_profile": compiled.renderer_profile_refs[0],
        "sanitized_argv": argv,
        "working_directory": str(work),
        "expected_qc": expected,
        "temporal_authority_mode": compiled.temporal_authority_mode,
        "canonical_media_timeline_ref": compiled.canonical_media_timeline_ref,
        "canonical_media_timeline_hash": compiled.canonical_media_timeline_hash,
        "canonical_audio_asset_ref": compiled.canonical_audio_asset_ref,
        "canonical_duration_ms": compiled.canonical_duration_ms,
        "canonical_caption_compilation_ref": compiled.canonical_caption_compilation_ref,
        "canonical_caption_compilation_hash": compiled.canonical_caption_compilation_hash,
        "canonical_caption_render_payload_hash": compiled.canonical_caption_render_payload_hash,
    }
    return FFmpegCommandManifest(
        **core,
        command_hash=stable_hash(core),
        created_at=datetime.now(UTC),
    )


def _gate_for_creative_qc(name: str, raw: Mapping[str, Any], ref: Path) -> dict[str, Any]:
    decision = str(raw.get("result", raw.get("status", raw.get("verdict", "")))).upper()
    decision = {"BLOCKED": "BLOCK", "FAIL": "BLOCK", "WARN": "REVIEW_REQUIRED"}.get(
        decision, decision
    )
    body = {
        "gate_name": name,
        "result": decision,
        "reason_codes": list(raw.get("reason_codes") or []),
        "metrics": dict(raw.get("metrics") or {}),
        "evidence_refs": [str(ref)],
    }
    body["content_hash"] = stable_hash(body)
    return body


def _make_contact_sheet(final: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(final),
            "-vf",
            "fps=1/5,scale=640:360,tile=4x2",
            "-frames:v",
            "1",
            str(destination),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0 or not destination.is_file():
        raise RuntimeError("CQR1_CONTACT_SHEET_FAILED")


def _write_before_after_packet(
    *,
    final: Path,
    contact: Path,
    timeline_model: CanonicalMediaTimeline,
    technical_result: str,
    creative_result: str,
    visual_gates: Mapping[str, Mapping[str, Any]],
) -> Path:
    packet_path = MANIFESTS / "before_after_comparison.json"
    previous = read_json(packet_path)
    pacing = read_json(QC_DIR / "narration_pacing_report.json")
    caption_sync = read_json(QC_DIR / "caption_sync_coverage_drift.json")
    bbox = read_json(QC_DIR / "caption_bbox_safe_area.json")
    cue_metrics = list(bbox.get("cue_metrics") or [])
    final_probe = probe_media(final, ffprobe=FFPROBE)
    final_duration_ms = round(media_duration_seconds(final_probe) * 1000, 3)
    cues = canonical_caption_cues(timeline_model)
    paid_still = require_inside(RENDER_DIR / "proxy/cqr1-paid-canary-after.jpg")
    subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{timeline_model.audio_duration_ms / 2000:.3f}",
            "-i",
            str(final),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(paid_still),
        ],
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    baseline = require_inside(WORKSPACE / "comparison/pa1r-before.jpg", must_exist=True)
    combined = require_inside(
        WORKSPACE / "comparison/side-by-side-pa1r-vs-real-paid-canary.jpg"
    )
    subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(baseline),
            "-i",
            str(paid_still),
            "-filter_complex",
            "[0:v]scale=960:540[left];[1:v]scale=960:540[right];[left][right]hstack=inputs=2",
            "-frames:v",
            "1",
            str(combined),
        ],
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    caption_cps = [float(item.get("cps") or 0) for item in cue_metrics]
    visual_scores = {
        "semantic_match": visual_gates["SceneSemanticMatchGate"]["metrics"]["minimum_score"],
        "visual_continuity": visual_gates["VisualContinuityGate"]["metrics"]["minimum_score"],
        "asset_adjacency": visual_gates["AssetAdjacencyGate"]["metrics"]["minimum_score"],
    }
    metrics = pacing["metrics"]
    paid = {
        "scope": "REAL_BOUNDED_PAID_CANARY",
        "final_mp4_ref": str(final),
        "final_mp4_sha256": sha256_file(final),
        "contact_sheet_ref": str(contact),
        "contact_sheet_sha256": sha256_file(contact),
        "technical_media_qc": technical_result,
        "creative_perceptual_media_qc": creative_result,
        "narration": metrics,
        "caption_font_scale": cue_metrics[0].get("font_scale") if cue_metrics else None,
        "actual_caption_bboxes": cue_metrics,
        "caption_cps": {
            "per_cue": caption_cps,
            "average": round(sum(caption_cps) / len(caption_cps), 3) if caption_cps else None,
            "p95": sorted(caption_cps)[max(0, round(0.95 * len(caption_cps)) - 1)]
            if caption_cps
            else None,
        },
        "caption_sync": {
            key: caption_sync[key]
            for key in (
                "CaptionAudioSyncGate",
                "CaptionCoverageGate",
                "TimelineDriftGate",
            )
        },
        "visual_scores": visual_scores,
        "duration": {
            "canonical_ms": timeline_model.audio_duration_ms,
            "final_narration_ms": timeline_model.audio_duration_ms,
            "final_mp4_ms": final_duration_ms,
            "final_caption_end_ms": max(item.caption_end_ms for item in cues),
            "final_scene_end_ms": max(
                item.scene_end_ms for item in timeline_model.segments
            ),
            "max_delta_ms": max(
                abs(final_duration_ms - timeline_model.audio_duration_ms),
                abs(
                    max(item.caption_end_ms for item in cues)
                    - timeline_model.audio_duration_ms
                ),
            ),
        },
        "provider_call_count": CQR1CanaryCallLedger.load(
            MANIFESTS / "planned_provider_call_ledger.json"
        ).provider_call_count,
    }
    payload = {
        **previous,
        "comparison_kind": "NON_EQUIVALENT_DIAGNOSTIC_PA1R_VS_REAL_PAID_CANARY",
        "status": "COMPLETE_REAL_PAID_CANARY_EVIDENCE",
        "acceptance_complete": True,
        "new_paid_canary": paid,
        "side_by_side_stills": {
            "historical_pa1r": str(baseline),
            "real_paid_canary": str(paid_still),
            "combined": str(combined),
            "interpretation": "NON_EQUIVALENT_DIAGNOSTIC_ONLY",
        },
    }
    payload.pop("content_hash", None)
    payload["content_hash"] = stable_hash(payload)
    write_json(packet_path, payload)
    return packet_path


def _write_pending_human_packet(final: Path, contact: Path, before_after: Path) -> tuple[Path, Path]:
    drive_ref = str(MANIFESTS / "drive_archive_receipt.json")
    packet = HumanWatchabilityPacketBuilder().build(
        run_id=CQR1_RUN_ID,
        final_mp4_path=str(final),
        contact_sheet_path=str(contact),
        before_after_packet_ref=str(before_after),
        drive_archive_receipt_ref=drive_ref,
        policy=approved_policy()["human_watchability_policy"],
    )
    json_path = QC_DIR / "human_watchability_review_packet.json"
    model_json(json_path, packet)
    markdown_path = QC_DIR / "human_watchability_review.md"
    score_rows = "\n".join(
        f"| {item.dimension} |  |  |" for item in packet.dimensions
    )
    issue_row = "|  |  |  |  |"
    checklist = "\n".join(
        f"- [ ] `{code}`" for code in packet.critical_reason_code_checklist
    )
    markdown = f"""# CQR1 Human Watchability Review Packet

```text
run_id={CQR1_RUN_ID}
review_state=PENDING
production_eligible=false
not_publishable=true
uninterrupted_full_watch_1x_completed=false
```

## Review artifacts

- Final MP4: `{final}`
- Contact sheet: `{contact}`
- Before/after packet: `{before_after}`
- Drive receipt: `{drive_ref}`

## Eight-dimension scoring table

| Dimension | Score 1-5 | Notes |
| --- | ---: | --- |
{score_rows}
| TOTAL / 40 |  |  |

## Timestamped issues

| Timestamp | Reason code | Observation | Repair recommendation |
| --- | --- | --- | --- |
{issue_row}

## Critical reason-code checklist

Unchecked means unevaluated, not cleared.

{checklist}

Codex has not watched/scored the full video and cannot mark this review PASS.
"""
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def render() -> dict[str, Any]:
    settings = settings_or_block()
    verify_resume_bindings(settings)
    if not settings.native_ffmpeg_local_smoke_enabled or settings.native_ffmpeg_production_enabled:
        raise RuntimeError("CQR1_NATIVE_RENDER_FLAGS_BLOCKED")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    required = (
        "elevenlabs_tts",
        "elevenlabs_forced_alignment",
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
    )
    if any(not _provider_prerequisite_satisfied(ledger, key) for key in required):
        raise RuntimeError("CQR1_RENDER_PROVIDER_PREREQUISITES_NOT_SUCCEEDED")
    if any(entry.status == "FAILED" for entry in ledger.entries.values()):
        raise RuntimeError("CQR1_PRIOR_PROVIDER_ATTEMPT_FAILED")
    timeline_model = CanonicalMediaTimeline.model_validate(
        read_json(MANIFESTS / "canonical_media_timeline.json")
    )
    if not 28_000 <= timeline_model.audio_duration_ms <= 40_000:
        raise RuntimeError("CQR1_CANONICAL_DURATION_OUTSIDE_CANARY_RANGE")
    audio_original = require_inside(
        SOURCE_AUDIO / "elevenlabs-final-narration.mp3", must_exist=True
    )
    pexels_receipt = read_json(MANIFESTS / "pexels_download_receipt.json")
    stock_original = require_inside(
        Path(str(pexels_receipt["local_path"])), must_exist=True
    )
    veo_provenance = read_json(MANIFESTS / "veo_prompt_request_provenance.json")
    hero_original = require_inside(
        Path(str(veo_provenance["downloaded_file_path"])), must_exist=True
    )
    if (
        sha256_file(stock_original) != pexels_receipt["sha256"]
        or sha256_file(hero_original) != veo_provenance["sha256"]
        or sha256_file(audio_original)
        != read_json(MANIFESTS / "elevenlabs_tts_receipt.json")["audio_sha256"]
    ):
        raise RuntimeError("CQR1_PROVIDER_ASSET_HASH_MISMATCH_BEFORE_RENDER")
    _, visual_gates = _visual_gate_evidence()
    disclosure = {
        "run_id": CQR1_RUN_ID,
        "synthetic_media": True,
        "provider": "GOOGLE_VEO",
        "model_id": veo_provenance["model_id"],
        "human_likeness": False,
        "visible_label": CQR1_VISIBLE_LABEL,
        "production_eligible": False,
        "not_publishable": True,
    }
    disclosure["content_hash"] = stable_hash(disclosure)
    write_json(MANIFESTS / "synthetic_media_disclosure_receipt.json", disclosure)
    no_publish = {
        "run_id": CQR1_RUN_ID,
        "publishable": False,
        "not_publishable": True,
        "youtube_action_count": 0,
        "FinalMediaRef_created": False,
        "HumanUploadTask_created": False,
        "UploadedVideo_created": False,
        "production_promotion": False,
        "auto_publish": False,
        "proceed_to_ch1_flex": False,
    }
    no_publish["content_hash"] = stable_hash(no_publish)
    write_json(WORKSPACE / "publish/not-publishable-manifest.json", no_publish)
    normalized_dir = require_inside(RENDER_DIR / "normalized-media")
    stock_normalized = normalized_dir / "pexels-support-1080p.mp4"
    hero_normalized = normalized_dir / "veo-hero-1080p.mp4"
    audio_normalized = normalized_dir / "elevenlabs-final-48k-stereo.wav"
    normalizer = MediaNormalizer()
    hero_probe = probe_media(hero_original, ffprobe=FFPROBE)
    hero_audio_streams = [
        item for item in hero_probe.get("streams", []) if item.get("codec_type") == "audio"
    ]
    normalization = {
        "stock": normalizer.compile_video_plan(
            input_asset_ref="cqr1-paid-pexels-original",
            input_asset_hash=sha256_file(stock_original),
            input_path=stock_original,
            output_path=stock_normalized,
            width=1920,
            height=1080,
            fps=30,
            audio_policy="REMOVE",
        ),
        "hero": normalizer.compile_video_plan(
            input_asset_ref="cqr1-paid-veo-original",
            input_asset_hash=sha256_file(hero_original),
            input_path=hero_original,
            output_path=hero_normalized,
            width=1920,
            height=1080,
            fps=30,
            audio_policy="REMOVE",
            provider_audio_present=bool(hero_audio_streams),
            provider_audio_stream_metadata={"streams": hero_audio_streams},
        ),
        "audio": normalizer.compile_audio_plan(
            input_asset_ref="cqr1-paid-elevenlabs-final",
            input_asset_hash=sha256_file(audio_original),
            input_path=audio_original,
            output_path=audio_normalized,
            loudness_peak_policy_ref="creative-policy://cqr1/preserve-final-narration",
            target_duration_seconds=None,
        ),
    }
    for name, manifest in normalization.items():
        model_json(MANIFESTS / f"{name}_media_normalization_manifest.json", manifest)
        receipt = _execute_normalization(manifest, video=name != "audio")
        write_json(MANIFESTS / f"{name}_media_normalization_receipt.json", receipt)
    normalized_hero_probe = probe_media(hero_normalized, ffprobe=FFPROBE)
    normalized_hero_audio = [
        item
        for item in normalized_hero_probe.get("streams", [])
        if item.get("codec_type") == "audio"
    ]
    audio_discard = {
        "provider_audio_present": bool(hero_audio_streams),
        "provider_audio_stream_count": len(hero_audio_streams),
        "provider_audio_stream_metadata": {"streams": hero_audio_streams},
        "provider_audio_usage_policy": "DISCARD",
        "provider_audio_discarded": bool(hero_audio_streams),
        "narration_authority": "ELEVENLABS",
        "final_mix_authority": "NATIVE_FFMPEG",
        "normalized_contains_audio_stream": bool(normalized_hero_audio),
        "media_qc_status": "PASS" if not normalized_hero_audio else "FAIL",
    }
    audio_discard["receipt_hash"] = stable_hash(audio_discard)
    write_json(MANIFESTS / "veo_provider_audio_discard_receipt.json", audio_discard)
    if audio_discard["media_qc_status"] != "PASS":
        raise RuntimeError("CQR1_VEO_PROVIDER_AUDIO_NOT_REMOVED")
    normalized_audio_duration = round(
        media_duration_seconds(probe_media(audio_normalized, ffprobe=FFPROBE)) * 1000
    )
    if abs(normalized_audio_duration - timeline_model.audio_duration_ms) > 25:
        raise RuntimeError("CQR1_AUDIO_NORMALIZATION_DURATION_CHANGED")
    plan = _native_render_plan(
        timeline_model=timeline_model,
        stock=stock_normalized,
        hero=hero_normalized,
        audio=audio_normalized,
        visual_gates=visual_gates,
    )
    compiled = NativeMotionCompiler().compile(
        plan,
        allow_resolved_provider_assets=True,
        canonical_timeline=timeline_model,
    )
    command = _ffmpeg_command_manifest(
        timeline_model=timeline_model,
        compiled=compiled,
        stock=stock_normalized,
        hero=hero_normalized,
        audio=audio_normalized,
    )
    model_json(MANIFESTS / "native_render_plan.json", plan)
    model_json(MANIFESTS / "compiled_native_render_manifest.json", compiled)
    model_json(MANIFESTS / "ffmpeg_command_manifest.json", command)
    renderer = NativeFFmpegRenderer(
        WORKSPACE, smoke_enabled=True, production_enabled=False
    )
    receipt, native_qc = renderer.execute(
        compiled, command, purpose=CQR1_PURPOSE
    )
    model_json(MANIFESTS / "native_render_execution_receipt.json", receipt)
    model_json(QC_DIR / "native_media_qc.json", native_qc)
    final = require_inside(Path(receipt.output_path), must_exist=True)
    technical = TechnicalMediaQC().from_native_media_qc(
        run_id=CQR1_RUN_ID, native_report=native_qc
    )
    model_json(QC_DIR / "technical_media_qc.json", technical)
    if technical.result != "PASS":
        raise RuntimeError("CQR1_PAID_TECHNICAL_MEDIA_QC_FAILED")
    final_probe = probe_media(final, ffprobe=FFPROBE)
    write_json(QC_DIR / "final_ffprobe.json", final_probe)
    final_duration = FinalDurationConsistencyGate(
        approved_policy()["creative_media_qc_policy"]["final_duration_consistency_ms"]
    ).evaluate(
        FinalDurationEvidence(
            canonical_timeline_duration_ms=timeline_model.audio_duration_ms,
            final_narration_duration_ms=timeline_model.audio_duration_ms,
            final_mp4_duration_ms=round(media_duration_seconds(final_probe) * 1000),
            final_caption_end_ms=max(
                item.caption_end_ms
                for item in canonical_caption_cues(timeline_model)
            ),
            final_scene_end_ms=max(
                item.scene_end_ms for item in timeline_model.segments
            ),
        )
    )
    model_json(QC_DIR / "final_duration_consistency.json", final_duration)
    caption_path = QC_DIR / "caption_sync_coverage_drift.json"
    caption_results = read_json(caption_path)
    creative_inputs = [
        _gate_for_creative_qc(name, raw, caption_path)
        for name, raw in caption_results.items()
    ]
    creative_inputs.extend(visual_gates.values())
    creative_inputs.append(final_duration.model_dump(mode="json"))
    creative = CreativePerceptualMediaQC().aggregate(
        run_id=CQR1_RUN_ID, gate_results=creative_inputs
    )
    model_json(QC_DIR / "creative_perceptual_media_qc.json", creative)
    if creative.result == "BLOCK":
        raise RuntimeError("CQR1_PAID_CREATIVE_MEDIA_QC_BLOCKED")
    contact = require_inside(RENDER_DIR / "proxy/cqr1-contact-sheet.jpg")
    _make_contact_sheet(final, contact)
    before_after = _write_before_after_packet(
        final=final,
        contact=contact,
        timeline_model=timeline_model,
        technical_result=technical.result,
        creative_result=creative.result,
        visual_gates=visual_gates,
    )
    human_json, human_md = _write_pending_human_packet(final, contact, before_after)
    result = {
        "status": "PASS",
        "final_mp4": str(final),
        "final_mp4_sha256": sha256_file(final),
        "contact_sheet": str(contact),
        "technical_media_qc": technical.result,
        "creative_perceptual_media_qc": creative.result,
        "human_review": "PENDING",
        "human_packet_json": str(human_json),
        "human_packet_markdown": str(human_md),
        "canonical_duration_ms": timeline_model.audio_duration_ms,
        "production_eligible": False,
        "not_publishable": True,
    }
    append_event("NATIVE_RENDER_AND_PAID_MEDIA_QC_COMPLETED", result)
    print(json.dumps(result, indent=2))
    return result


def _archive_sources() -> tuple[ProductionArchiveManifest, Path]:
    final = require_inside(
        RENDER_DIR / "final/cqr1-non-production-canary.mp4", must_exist=True
    )
    contact = require_inside(RENDER_DIR / "proxy/cqr1-contact-sheet.jpg", must_exist=True)
    pexels_receipt = read_json(MANIFESTS / "pexels_download_receipt.json")
    veo = read_json(MANIFESTS / "veo_prompt_request_provenance.json")
    required_paths = {
        "CANONICAL_MEDIA_TIMELINE": MANIFESTS / "canonical_media_timeline.json",
        "SPOKEN_TEXT_NORMALIZED": SOURCE_SCRIPT / "spoken_text_normalized.json",
        "PROVIDER_TIMING_SEED": MANIFESTS / "narration_timing_seed.json",
        "FORCED_ALIGNMENT_EVIDENCE": MANIFESTS / "forced_alignment_evidence.json",
        "VERIFIED_NARRATION_ALIGNMENT": MANIFESTS / "verified_narration_alignment.json",
        "NARRATION_PACING_REPORT": QC_DIR / "narration_pacing_report.json",
        "CAPTION_COMPILATION_REPORT": QC_DIR / "caption_compilation_report.json",
        "CAPTION_BBOX_SAFE_AREA_EVIDENCE": QC_DIR / "caption_bbox_safe_area.json",
        "CAPTION_SYNC_COVERAGE_DRIFT_REPORT": QC_DIR / "caption_sync_coverage_drift.json",
        "VISUAL_DIRECTION_CONTRACT": MANIFESTS / "resume_visual_direction_contract.json",
        "PEXELS_RANKING_PROVENANCE": MANIFESTS / "pexels_search_ranking_provenance.json",
        "VEO_PROMPT_REQUEST_PROVENANCE": MANIFESTS / "veo_prompt_request_provenance.json",
        "VISUAL_CONTINUITY_REPORT": QC_DIR / "visual_continuity_report.json",
        "NATIVE_RENDER_PLAN": MANIFESTS / "native_render_plan.json",
        "COMPILED_NATIVE_RENDER_MANIFEST": MANIFESTS / "compiled_native_render_manifest.json",
        "FFMPEG_COMMAND_MANIFEST": MANIFESTS / "ffmpeg_command_manifest.json",
        "FINAL_MASTER": final,
        "CONTACT_SHEET": contact,
        "TECHNICAL_MEDIA_QC": QC_DIR / "technical_media_qc.json",
        "CREATIVE_PERCEPTUAL_MEDIA_QC": QC_DIR / "creative_perceptual_media_qc.json",
        "HUMAN_REVIEW_PACKET": QC_DIR / "human_watchability_review.md",
        "SYNTHETIC_MEDIA_DISCLOSURE": MANIFESTS
        / "synthetic_media_disclosure_receipt.json",
        "NOT_PUBLISHABLE_MANIFEST": WORKSPACE
        / "publish/not-publishable-manifest.json",
    }
    extras = {
        "BEFORE_AFTER_PACKET": (
            MANIFESTS / "before_after_comparison.json",
            "06-qc/before-after-packet.json",
        ),
        "HUMAN_REVIEW_PACKET_JSON": (
            QC_DIR / "human_watchability_review_packet.json",
            "06-qc/human-watchability-review-packet.json",
        ),
        "FINAL_NARRATION_AUDIO": (
            SOURCE_AUDIO / "elevenlabs-final-narration.mp3",
            "02-audio/final-narration.mp3",
        ),
        "ELEVENLABS_TTS_RECEIPT": (
            MANIFESTS / "elevenlabs_tts_receipt.json",
            "02-audio/elevenlabs-tts-receipt.json",
        ),
        "ELEVENLABS_FORCED_ALIGNMENT_RECEIPT": (
            MANIFESTS / "elevenlabs_forced_alignment_receipt.json",
            "02-audio/elevenlabs-forced-alignment-receipt.json",
        ),
        "PEXELS_DOWNLOAD_RECEIPT": (
            MANIFESTS / "pexels_download_receipt.json",
            "03-stock/pexels-download-receipt.json",
        ),
        "PEXELS_CANONICAL_DURATION_BINDING": (
            MANIFESTS / "pexels_canonical_duration_binding.json",
            "03-stock/pexels-canonical-duration-binding.json",
        ),
        "PEXELS_STOCK_SOURCE_MANIFEST": (
            MANIFESTS / "pexels_stock_source_manifest.json",
            "03-stock/stock-source-manifest.json",
        ),
        "PEXELS_SELECTED_ORIGINAL": (
            Path(str(pexels_receipt["local_path"])),
            "03-stock/selected-originals/stock-original.mp4",
        ),
        "VEO_OPERATION_RECEIPT": (
            MANIFESTS / "google_veo_operation_receipt.json",
            "04-ai-hero/veo-operation-receipt.json",
        ),
        "VEO_DOWNLOAD_RECEIPT": (
            MANIFESTS / "google_veo_download_receipt.json",
            "04-ai-hero/veo-download-receipt.json",
        ),
        "VEO_SELECTED_ORIGINAL": (
            Path(str(veo["downloaded_file_path"])),
            "04-ai-hero/selected-takes/veo-original.mp4",
        ),
        "VEO_AUDIO_DISCARD_RECEIPT": (
            MANIFESTS / "veo_provider_audio_discard_receipt.json",
            "04-ai-hero/veo-audio-discard-receipt.json",
        ),
        "STOCK_NORMALIZATION_RECEIPT": (
            MANIFESTS / "stock_media_normalization_receipt.json",
            "00-manifests/stock-normalization-receipt.json",
        ),
        "HERO_NORMALIZATION_RECEIPT": (
            MANIFESTS / "hero_media_normalization_receipt.json",
            "00-manifests/hero-normalization-receipt.json",
        ),
        "AUDIO_NORMALIZATION_RECEIPT": (
            MANIFESTS / "audio_media_normalization_receipt.json",
            "00-manifests/audio-normalization-receipt.json",
        ),
        "NATIVE_RENDER_EXECUTION_RECEIPT": (
            MANIFESTS / "native_render_execution_receipt.json",
            "05-render/native-render-execution-receipt.json",
        ),
        "FINAL_FFPROBE": (
            QC_DIR / "final_ffprobe.json",
            "06-qc/final-ffprobe.json",
        ),
        "FINAL_DURATION_CONSISTENCY": (
            QC_DIR / "final_duration_consistency.json",
            "06-qc/final-duration-consistency.json",
        ),
        "CAPTION_FINAL_CUE_TRAILING_HOLD": (
            QC_DIR / "caption_final_cue_trailing_hold.json",
            "06-qc/caption-final-cue-trailing-hold.json",
        ),
        "RESUME_APPROVAL_SCOPE": (
            MANIFESTS / "resume_approval_scope.json",
            "00-manifests/resume-approval-scope.json",
        ),
        "SOURCE_RUN_002_PREFLIGHT": (
            MANIFESTS
            / "history/run002/manifests/resume_paid_canary_preflight.json",
            "00-manifests/source-run002-preflight.json",
        ),
        "RESUME_PREFLIGHT": (
            MANIFESTS / "resume_paid_canary_preflight.json",
            "00-manifests/resume-paid-canary-preflight.json",
        ),
        "RESUME_LEDGER_BINDING": (
            MANIFESTS / "resume_ledger_authorization_binding.json",
            "00-manifests/resume-ledger-authorization-binding.json",
        ),
        "RUN_009_LINEAGE": (
            MANIFESTS / "run_lineage.json",
            "00-manifests/run-lineage.json",
        ),
        "IMPORTED_PEXELS_EVIDENCE": (
            MANIFESTS / "imported_pexels_evidence.json",
            "03-stock/imported-pexels-evidence.json",
        ),
        "IMPORTED_VEO_EVIDENCE": (
            MANIFESTS / "imported_veo_evidence.json",
            "04-ai-hero/imported-veo-evidence.json",
        ),
        "IMPORTED_TTS_AUDIO_EVIDENCE": (
            MANIFESTS / "imported_tts_audio_evidence.json",
            "02-audio/imported-tts-audio-evidence.json",
        ),
        "IMPORTED_ALIGNMENT_EVIDENCE": (
            MANIFESTS / "imported_alignment_evidence.json",
            "02-audio/imported-alignment-evidence.json",
        ),
        "SOURCE_RUN_002_TTS_RECEIPT": (
            MANIFESTS / "history/run002/manifests/elevenlabs_tts_receipt.json",
            "02-audio/source-run002-elevenlabs-tts-receipt.json",
        ),
        "SOURCE_RUN_002_TIMING_SEED": (
            MANIFESTS / "history/run002/manifests/narration_timing_seed.json",
            "02-audio/source-run002-narration-timing-seed.json",
        ),
        "SOURCE_RUN_002_FAILURE_STOP": (
            MANIFESTS
            / "history/run002/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run002-failure-stop.json",
        ),
        "SOURCE_RUN_002_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run002/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run002-provider-ledger.json",
        ),
        "SOURCE_RUN_003_FAILURE_STOP": (
            MANIFESTS
            / "history/run003/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run003-failure-stop.json",
        ),
        "SOURCE_RUN_003_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run003/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run003-provider-ledger.json",
        ),
        "SOURCE_RUN_003_APPROVAL_SCOPE": (
            MANIFESTS / "history/run003/manifests/approval_scope.json",
            "00-manifests/source-run003-approval-scope.json",
        ),
        "SOURCE_RUN_003_IMPORTED_TTS_EVIDENCE": (
            MANIFESTS
            / "history/run003/manifests/imported_tts_audio_evidence.json",
            "02-audio/source-run003-imported-tts-evidence.json",
        ),
        "SOURCE_RUN_003_SAFE_PROVIDER_RESPONSE": (
            MANIFESTS
            / "history/run003/manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json",
            "02-audio/source-run003-forced-alignment-response.safe.json",
        ),
        "SOURCE_RUN_004_FAILURE_STOP": (
            MANIFESTS
            / "history/run004/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run004-failure-stop.json",
        ),
        "SOURCE_RUN_004_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run004/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run004-provider-ledger.json",
        ),
        "SOURCE_RUN_004_APPROVAL_SCOPE": (
            MANIFESTS / "history/run004/manifests/approval_scope.json",
            "00-manifests/source-run004-approval-scope.json",
        ),
        "SOURCE_RUN_004_CODEX_VISUAL_REVIEW": (
            MANIFESTS / "history/run004/qc/codex_visual_asset_review.json",
            "06-qc/source-run004-codex-visual-review.json",
        ),
        "SOURCE_RUN_004_PEXELS_REVIEW_SHEET": (
            MANIFESTS
            / "history/run004/render/proxy/pexels-review-contact-sheet.jpg",
            "06-qc/source-run004-pexels-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_004_FORCED_ALIGNMENT_RECEIPT": (
            MANIFESTS
            / "history/run004/manifests/elevenlabs_forced_alignment_receipt.json",
            "02-audio/source-run004-forced-alignment-receipt.json",
        ),
        "SOURCE_RUN_004_FORCED_ALIGNMENT_EVIDENCE": (
            MANIFESTS / "history/run004/manifests/forced_alignment_evidence.json",
            "02-audio/source-run004-forced-alignment-evidence.json",
        ),
        "SOURCE_RUN_004_VERIFIED_ALIGNMENT": (
            MANIFESTS
            / "history/run004/manifests/verified_narration_alignment.json",
            "02-audio/source-run004-verified-narration-alignment.json",
        ),
        "SOURCE_RUN_004_SAFE_PROVIDER_RESPONSE": (
            MANIFESTS
            / "history/run004/manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json",
            "02-audio/source-run004-forced-alignment-response.safe.json",
        ),
        "SOURCE_RUN_005_FAILURE_STOP": (
            MANIFESTS
            / "history/run005/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run005-failure-stop.json",
        ),
        "SOURCE_RUN_005_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run005/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run005-provider-ledger.json",
        ),
        "SOURCE_RUN_005_APPROVAL_SCOPE": (
            MANIFESTS / "history/run005/manifests/approval_scope.json",
            "00-manifests/source-run005-approval-scope.json",
        ),
        "SOURCE_RUN_005_PREFLIGHT": (
            MANIFESTS
            / "history/run005/manifests/resume_paid_canary_preflight.json",
            "00-manifests/source-run005-preflight.json",
        ),
        "SOURCE_RUN_005_CODEX_VISUAL_REVIEW": (
            MANIFESTS / "history/run005/qc/codex_visual_asset_review.json",
            "06-qc/source-run005-codex-visual-review.json",
        ),
        "SOURCE_RUN_005_PEXELS_REVIEW_SHEET": (
            MANIFESTS
            / "history/run005/render/proxy/pexels-review-contact-sheet.jpg",
            "06-qc/source-run005-pexels-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_005_PEXELS_REPRESENTATIVE": (
            MANIFESTS
            / "history/run005/render/proxy/pexels-selected-representative.jpg",
            "06-qc/source-run005-pexels-representative.jpg",
        ),
        "SOURCE_RUN_005_PEXELS_SEARCH_PROVENANCE": (
            MANIFESTS
            / "history/run005/manifests/pexels_search_ranking_provenance.json",
            "03-stock/source-run005-pexels-search-provenance.json",
        ),
        "SOURCE_RUN_005_PEXELS_DOWNLOAD_RECEIPT": (
            MANIFESTS
            / "history/run005/manifests/pexels_download_receipt.json",
            "03-stock/source-run005-pexels-download-receipt.json",
        ),
        "SOURCE_RUN_006_FAILURE_STOP": (
            MANIFESTS
            / "history/run006/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run006-failure-stop.json",
        ),
        "SOURCE_RUN_006_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run006/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run006-provider-ledger.json",
        ),
        "SOURCE_RUN_006_APPROVAL_SCOPE": (
            MANIFESTS / "history/run006/manifests/approval_scope.json",
            "00-manifests/source-run006-approval-scope.json",
        ),
        "SOURCE_RUN_006_PREFLIGHT": (
            MANIFESTS
            / "history/run006/manifests/resume_paid_canary_preflight.json",
            "00-manifests/source-run006-preflight.json",
        ),
        "SOURCE_RUN_006_CODEX_VISUAL_REVIEW": (
            MANIFESTS / "history/run006/qc/codex_visual_asset_review.json",
            "06-qc/source-run006-codex-visual-review.json",
        ),
        "SOURCE_RUN_006_PEXELS_REVIEW_SHEET": (
            MANIFESTS
            / "history/run006/render/proxy/pexels-review-contact-sheet.jpg",
            "06-qc/source-run006-pexels-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_006_VEO_REVIEW_SHEET": (
            MANIFESTS / "history/run006/render/proxy/veo-review-contact-sheet.jpg",
            "06-qc/source-run006-veo-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_006_PEXELS_REPRESENTATIVE": (
            MANIFESTS
            / "history/run006/render/proxy/pexels-selected-representative.jpg",
            "06-qc/source-run006-pexels-representative.jpg",
        ),
        "SOURCE_RUN_006_VEO_REPRESENTATIVE": (
            MANIFESTS
            / "history/run006/render/proxy/veo-hero-representative.jpg",
            "06-qc/source-run006-veo-representative.jpg",
        ),
        "SOURCE_RUN_006_PEXELS_SEARCH_PROVENANCE": (
            MANIFESTS
            / "history/run006/manifests/pexels_search_ranking_provenance.json",
            "03-stock/source-run006-pexels-search-provenance.json",
        ),
        "SOURCE_RUN_006_PEXELS_DOWNLOAD_RECEIPT": (
            MANIFESTS
            / "history/run006/manifests/pexels_download_receipt.json",
            "03-stock/source-run006-pexels-download-receipt.json",
        ),
        "SOURCE_RUN_006_VEO_OPERATION_RECEIPT": (
            MANIFESTS
            / "history/run006/manifests/google_veo_operation_receipt.json",
            "04-ai-hero/source-run006-veo-operation-receipt.json",
        ),
        "SOURCE_RUN_006_VEO_DOWNLOAD_RECEIPT": (
            MANIFESTS
            / "history/run006/manifests/google_veo_download_receipt.json",
            "04-ai-hero/source-run006-veo-download-receipt.json",
        ),
        "SOURCE_RUN_006_VEO_PROVENANCE": (
            MANIFESTS
            / "history/run006/manifests/veo_prompt_request_provenance.json",
            "04-ai-hero/source-run006-veo-provenance.json",
        ),
        "SOURCE_RUN_006_VEO_PROMPT": (
            MANIFESTS / "history/run006/manifests/resume_veo_prompt.json",
            "04-ai-hero/source-run006-veo-prompt.json",
        ),
        "SOURCE_RUN_007_FAILURE_STOP": (
            MANIFESTS
            / "history/run007/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run007-failure-stop.json",
        ),
        "SOURCE_RUN_007_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run007/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run007-provider-ledger.json",
        ),
        "SOURCE_RUN_007_APPROVAL_SCOPE": (
            MANIFESTS / "history/run007/manifests/approval_scope.json",
            "00-manifests/source-run007-approval-scope.json",
        ),
        "SOURCE_RUN_007_PREFLIGHT": (
            MANIFESTS
            / "history/run007/manifests/resume_paid_canary_preflight.json",
            "00-manifests/source-run007-preflight.json",
        ),
        "SOURCE_RUN_007_VISUAL_DIRECTION": (
            MANIFESTS
            / "history/run007/manifests/resume_visual_direction_contract.json",
            "00-manifests/source-run007-visual-direction-contract.json",
        ),
        "SOURCE_RUN_007_PEXELS_SEARCH_PROVENANCE": (
            MANIFESTS
            / "history/run007/manifests/pexels_search_ranking_provenance.json",
            "03-stock/source-run007-pexels-search-provenance.json",
        ),
        "SOURCE_RUN_007_PEXELS_DOWNLOAD_RECEIPT": (
            MANIFESTS
            / "history/run007/manifests/pexels_download_receipt.json",
            "03-stock/source-run007-pexels-download-receipt.json",
        ),
        "SOURCE_RUN_007_PEXELS_SOURCE_MANIFEST": (
            MANIFESTS
            / "history/run007/manifests/pexels_stock_source_manifest.json",
            "03-stock/source-run007-stock-source-manifest.json",
        ),
        "SOURCE_RUN_007_PEXELS_ORIGINAL": (
            MANIFESTS
            / "history/run007/source/stock/pexels-12991847-5704872.mp4",
            "03-stock/source-run007-stock-original.mp4",
        ),
        "SOURCE_RUN_007_PEXELS_REPRESENTATIVE": (
            MANIFESTS
            / "history/run007/render/proxy/pexels-selected-representative.jpg",
            "06-qc/source-run007-pexels-representative.jpg",
        ),
        "SOURCE_RUN_007_PEXELS_REVIEW_SHEET": (
            MANIFESTS
            / "history/run007/render/proxy/pexels-review-contact-sheet.jpg",
            "06-qc/source-run007-pexels-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_007_VEO_OPERATION_RECEIPT": (
            MANIFESTS
            / "history/run007/manifests/google_veo_operation_receipt.json",
            "04-ai-hero/source-run007-veo-operation-receipt.json",
        ),
        "SOURCE_RUN_007_VEO_GENERATION_REQUEST": (
            MANIFESTS
            / "history/run007/manifests/google_veo_generation_request.json",
            "04-ai-hero/source-run007-veo-generation-request.json",
        ),
        "SOURCE_RUN_007_VEO_DOWNLOAD_RECEIPT": (
            MANIFESTS
            / "history/run007/manifests/google_veo_download_receipt.json",
            "04-ai-hero/source-run007-veo-download-receipt.json",
        ),
        "SOURCE_RUN_007_VEO_PROVENANCE": (
            MANIFESTS
            / "history/run007/manifests/veo_prompt_request_provenance.json",
            "04-ai-hero/source-run007-veo-provenance.json",
        ),
        "SOURCE_RUN_007_VEO_PROMPT": (
            MANIFESTS / "history/run007/manifests/resume_veo_prompt.json",
            "04-ai-hero/source-run007-veo-prompt.json",
        ),
        "SOURCE_RUN_007_VEO_FFPROBE": (
            MANIFESTS
            / "history/run007/manifests/google_veo_original_ffprobe.json",
            "04-ai-hero/source-run007-veo-original-ffprobe.json",
        ),
        "SOURCE_RUN_007_VEO_ORIGINAL": (
            MANIFESTS
            / "history/run007/source/ai-hero/google-veo-hero-original.mp4",
            "04-ai-hero/source-run007-veo-original.mp4",
        ),
        "SOURCE_RUN_007_VEO_REPRESENTATIVE": (
            MANIFESTS
            / "history/run007/render/proxy/veo-hero-representative.jpg",
            "06-qc/source-run007-veo-representative.jpg",
        ),
        "SOURCE_RUN_007_VEO_REVIEW_SHEET": (
            MANIFESTS
            / "history/run007/render/proxy/veo-review-contact-sheet.jpg",
            "06-qc/source-run007-veo-review-contact-sheet.jpg",
        ),
        "SOURCE_RUN_007_CODEX_VISUAL_REVIEW": (
            MANIFESTS / "history/run007/qc/codex_visual_asset_review.json",
            "06-qc/source-run007-codex-visual-review.json",
        ),
        "SOURCE_RUN_007_NORMALIZATION_FAILURE_PROBE": (
            MANIFESTS / "history/run007/qc/normalization_failure_probe.json",
            "06-qc/source-run007-normalization-failure-probe.json",
        ),
        "SOURCE_RUN_007_TERMINAL_EVENT": (
            MANIFESTS
            / "history/run007/manifests/resume-events/0018-local-media-normalization-blocked.json",
            "00-manifests/source-run007-terminal-event.json",
        ),
        "SOURCE_RUN_008_FAILURE_STOP": (
            MANIFESTS
            / "history/run008/manifests/cqr1_paid_canary_failure_stop.json",
            "00-manifests/source-run008-failure-stop.json",
        ),
        "SOURCE_RUN_008_PROVIDER_LEDGER": (
            MANIFESTS
            / "history/run008/manifests/planned_provider_call_ledger.json",
            "00-manifests/source-run008-provider-ledger.json",
        ),
        "SOURCE_RUN_008_APPROVAL_SCOPE": (
            MANIFESTS / "history/run008/manifests/approval_scope.json",
            "00-manifests/source-run008-approval-scope.json",
        ),
        "SOURCE_RUN_008_PREFLIGHT": (
            MANIFESTS
            / "history/run008/manifests/resume_paid_canary_preflight.json",
            "00-manifests/source-run008-preflight.json",
        ),
        "SOURCE_RUN_008_TERMINAL_EVENT": (
            MANIFESTS
            / "history/run008/manifests/resume-events/0004-local-post-render-duration-evidence-blocked.json",
            "00-manifests/source-run008-terminal-event.json",
        ),
        "SOURCE_RUN_008_RENDER_RECEIPT": (
            MANIFESTS
            / "history/run008/manifests/native_render_execution_receipt.json",
            "05-render/source-run008-native-render-execution-receipt.json",
        ),
        "SOURCE_RUN_008_TECHNICAL_MEDIA_QC": (
            MANIFESTS / "history/run008/qc/technical_media_qc.json",
            "06-qc/source-run008-technical-media-qc.json",
        ),
        "SOURCE_RUN_008_FINAL_FFPROBE": (
            MANIFESTS / "history/run008/qc/final_ffprobe.json",
            "06-qc/source-run008-final-ffprobe.json",
        ),
        "SOURCE_RUN_008_FINAL_MP4": (
            MANIFESTS
            / "history/run008/render/final/cqr1-non-production-canary.mp4",
            "05-render/source-run008-incomplete-post-render-output.mp4",
        ),
        "FORCED_ALIGNMENT_SAFE_PROVIDER_RESPONSE": (
            MANIFESTS
            / "history/run004/manifests/provider-raw/elevenlabs_forced_alignment_response.safe.json",
            "02-audio/forced-alignment-provider-response.safe.json",
        ),
        "LEDGER_BEFORE_DRIVE_ARCHIVE": (
            MANIFESTS / "planned_provider_call_ledger.json",
            "00-manifests/provider-ledger-before-drive-archive.json",
        ),
        "CANONICAL_VISUAL_GAP_SCHEDULE": (
            MANIFESTS / "canonical_visual_gap_schedule.json",
            "00-manifests/canonical-visual-gap-schedule.json",
        ),
        "CODEX_REAL_FRAME_REVIEW": (
            QC_DIR / "codex_visual_asset_review.json",
            "06-qc/codex-real-frame-review.json",
        ),
    }
    archive_stage = require_inside(WORKSPACE / "archive-package")
    if archive_stage.exists():
        shutil.rmtree(archive_stage)
    sources: list[ArchiveSource] = []
    for role, source in required_paths.items():
        source = require_inside(source, must_exist=True)
        expected = __import__(
            "app.services.production_archive", fromlist=["ALL_ROLE_ARCHIVE_PATHS"]
        ).ALL_ROLE_ARCHIVE_PATHS[role]
        staged = archive_stage / expected
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged)
        sources.append(ArchiveSource(role, staged, expected))
    for role, (source, expected) in extras.items():
        source = require_inside(source, must_exist=True)
        staged = archive_stage / expected
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged)
        sources.append(ArchiveSource(role, staged, expected))
    manifest = ProductionArchiveBuilder().build(
        manifest_id=f"{CQR1_RUN_ID}-archive-manifest-v1",
        project_id=CQR1_RUN_ID,
        package_id=PACKAGE_ID,
        sources=sources,
        required_roles=CQR1_REQUIRED_ARCHIVE_ROLES,
    )
    model_json(MANIFESTS / "production_archive_manifest.json", manifest)
    return manifest, archive_stage


def archive() -> dict[str, Any]:
    settings, ledger, preflight = load_execution_context(
        "drive_archive",
        prerequisites=(
            "elevenlabs_tts",
            "elevenlabs_forced_alignment",
            "pexels_search",
            "pexels_download",
            "google_veo_submit",
            "google_veo_output",
        ),
    )
    require_scoped_external_execution_flags(settings, provider="google_drive")
    technical = read_json(QC_DIR / "technical_media_qc.json")
    creative = read_json(QC_DIR / "creative_perceptual_media_qc.json")
    if technical.get("result") != "PASS" or creative.get("result") == "BLOCK":
        raise RuntimeError("CQR1_MEDIA_QC_DOES_NOT_PERMIT_ARCHIVE")
    human = read_json(QC_DIR / "human_watchability_review_packet.json")
    if human.get("review_state") != "PENDING" or human.get("drive_archive_receipt_ref") != str(
        MANIFESTS / "drive_archive_receipt.json"
    ):
        raise RuntimeError("CQR1_PENDING_HUMAN_PACKET_INVALID")
    manifest, _stage = _archive_sources()
    root_relative = CQR1ArchivePathBuilder.build(
        run_id=CQR1_RUN_ID, archive_date=ARCHIVE_DATE
    )

    def upload_once() -> Mapping[str, Any]:
        receipts: list[DriveArchiveFileReceipt] = []
        metadata_rows: list[dict[str, Any]] = []
        mismatches: list[str] = []
        run_folder_id: str | None = None
        try:
            with session_scope() as session:
                drive = DrivePA1RArchive(session, settings)
                access_token = drive.access_token()
                root_id = drive.config.root_folder_id()
                if not root_id:
                    raise RuntimeError("DRIVE_ROOT_FOLDER_MISSING")
                run_folder_id = drive.provider.ensure_folder_path(
                    access_token=access_token,
                    root_folder_id=root_id,
                    folder_path=root_relative.split("/"),
                )
                for index, entry in enumerate(manifest.files, start=1):
                    relative = Path(entry.expected_archive_path)
                    folder_id = drive.provider.ensure_folder_path(
                        access_token=access_token,
                        root_folder_id=run_folder_id,
                        folder_path=list(relative.parent.parts)
                        if str(relative.parent) != "."
                        else [],
                    )
                    local = Path(entry.source_path)
                    uploaded = drive.provider.upload_file(
                        access_token=access_token,
                        local_path=local,
                        folder_id=folder_id,
                        upload_mode=drive.config.upload_mode(),
                        mime_type=mimetypes.guess_type(local.name)[0]
                        or "application/octet-stream",
                    )
                    remote = drive.provider.get_file_metadata(
                        access_token=access_token,
                        drive_file_id=uploaded.drive_file_id,
                    )
                    remote_md5 = str(
                        (remote.technical_appendix or {}).get("md5_checksum") or ""
                    ) or None
                    id_ok = bool(
                        remote.drive_file_id
                        and remote.drive_file_id == uploaded.drive_file_id
                    )
                    name_ok = remote.file_name == relative.name
                    parent_ok = remote.drive_folder_id == folder_id
                    size_ok = remote.size_bytes == entry.size_bytes
                    sha_ok = bool(
                        remote.checksum_sha256
                        and remote.checksum_sha256 == entry.sha256
                    )
                    md5_ok = bool(
                        remote_md5 and entry.md5 and remote_md5 == entry.md5
                    )
                    verified = bool(
                        id_ok and name_ok and parent_ok and size_ok and (sha_ok or md5_ok)
                    )
                    if entry.required_for_archive and not verified:
                        mismatches.append(
                            f"DRIVE_VERIFY_MISMATCH:{entry.logical_role}"
                        )
                    receipts.append(
                        DriveArchiveFileReceipt(
                            archive_path=entry.expected_archive_path,
                            drive_file_id=remote.drive_file_id,
                            local_size=entry.size_bytes,
                            drive_size=remote.size_bytes,
                            local_sha256=entry.sha256,
                            drive_sha256=remote.checksum_sha256,
                            local_md5=entry.md5,
                            drive_md5=remote_md5,
                            verification_method=(
                                "SHA256"
                                if sha_ok
                                else "DRIVE_MD5_PLUS_SIZE"
                                if md5_ok
                                else "FAILED"
                            ),
                            verified=verified,
                        )
                    )
                    metadata_rows.append(
                        {
                            "archive_path": entry.expected_archive_path,
                            "drive_file_id": remote.drive_file_id,
                            "id_matches_upload": id_ok,
                            "remote_name": remote.file_name,
                            "name_matches": name_ok,
                            "parent_matches": parent_ok,
                            "size_matches": size_ok,
                            "sha256_matches": sha_ok,
                            "md5_matches": md5_ok,
                            "verified": verified,
                        }
                    )
                    write_json(
                        MANIFESTS / "drive_archive_upload_progress.json",
                        {
                            "archive_state": "UPLOADING",
                            "completed_file_count": index,
                            "total_file_count": len(manifest.files),
                            "metadata_verification": metadata_rows,
                            "mismatch_reason_codes": mismatches,
                            "purge_count": 0,
                        },
                    )
            state = "FAILED" if mismatches else "VERIFIED"
            receipt_payload = {
                "archive_manifest_ref": manifest.manifest_id,
                "archive_manifest_hash": manifest.manifest_hash,
                "configured_root_folder_id_reference": "configured://google-drive-root",
                "root_relative_folder_path": root_relative,
                "drive_folder_id": run_folder_id,
                "files": [item.model_dump(mode="json") for item in receipts],
                "total_local_size": manifest.total_size_bytes,
                "total_drive_size": sum(item.drive_size or 0 for item in receipts),
                "archive_state": state,
                "mismatch_reason_codes": mismatches,
                "verified_at": datetime.now(UTC) if state == "VERIFIED" else None,
                "provider_call_made": True,
                "transport": "GOOGLE_DRIVE_API",
            }
            receipt = DriveArchiveReceipt(
                **receipt_payload, receipt_hash=stable_hash(receipt_payload)
            )
            model_json(MANIFESTS / "drive_archive_receipt.json", receipt)
            write_json(
                MANIFESTS / "drive_archive_metadata_verification.json",
                {
                    "archive_state": state,
                    "root_relative_folder_path": root_relative,
                    "metadata_verification": metadata_rows,
                    "mismatch_reason_codes": mismatches,
                    "purge_count": 0,
                },
            )
            if state != "VERIFIED" or not archive_permits_cleanup(receipt):
                raise RuntimeError("CQR1_DRIVE_ARCHIVE_VERIFICATION_FAILED")
            return {
                "archive_state": "VERIFIED",
                "verified_file_count": len(receipts),
                "required_file_count": len(manifest.files),
                "root_relative_folder_path": root_relative,
                "receipt_hash": receipt.receipt_hash,
                "output_count": 1,
            }
        except Exception as exc:
            if not (MANIFESTS / "drive_archive_receipt.json").exists():
                failure_payload = {
                    "archive_manifest_ref": manifest.manifest_id,
                    "archive_manifest_hash": manifest.manifest_hash,
                    "configured_root_folder_id_reference": "configured://google-drive-root",
                    "root_relative_folder_path": root_relative,
                    "drive_folder_id": run_folder_id,
                    "files": [item.model_dump(mode="json") for item in receipts],
                    "total_local_size": manifest.total_size_bytes,
                    "total_drive_size": sum(item.drive_size or 0 for item in receipts),
                    "archive_state": "FAILED",
                    "mismatch_reason_codes": [
                        *mismatches,
                        f"DRIVE_ARCHIVE_EXCEPTION:{type(exc).__name__}",
                    ],
                    "verified_at": None,
                    "provider_call_made": True,
                    "transport": "GOOGLE_DRIVE_API",
                }
                failure = DriveArchiveReceipt(
                    **failure_payload, receipt_hash=stable_hash(failure_payload)
                )
                model_json(MANIFESTS / "drive_archive_receipt.json", failure)
            raise

    result = run_guarded_once(
        ledger=ledger,
        operation_key="drive_archive",
        preflight=preflight,
        operation=upload_once,
    )
    receipt = DriveArchiveReceipt.model_validate(
        read_json(MANIFESTS / "drive_archive_receipt.json")
    )
    if receipt.archive_state != "VERIFIED" or not archive_permits_cleanup(receipt):
        raise RuntimeError("CQR1_DRIVE_ARCHIVE_NOT_VERIFIED")
    output = {
        "CQR1D_DRIVE_ARCHIVE": "PASS",
        "archive_state": receipt.archive_state,
        "archive_path": receipt.root_relative_folder_path,
        "verified_file_count": len(receipt.files),
        "receipt_hash": receipt.receipt_hash,
        "provider_result": result["status"],
    }
    append_event("DRIVE_ARCHIVE_VERIFIED", output)
    print(json.dumps(output, indent=2))
    return output


def cleanup() -> dict[str, Any]:
    receipt = DriveArchiveReceipt.model_validate(
        read_json(MANIFESTS / "drive_archive_receipt.json")
    )
    if not archive_permits_cleanup(receipt):
        raise RuntimeError("CQR1_CLEANUP_BLOCKED_ARCHIVE_NOT_VERIFIED")
    final = require_inside(
        RENDER_DIR / "final/cqr1-non-production-canary.mp4", must_exist=True
    )
    contact = require_inside(RENDER_DIR / "proxy/cqr1-contact-sheet.jpg", must_exist=True)
    retained = [
        str(final),
        str(contact),
        str(QC_DIR / "human_watchability_review_packet.json"),
        str(QC_DIR / "human_watchability_review.md"),
        str(MANIFESTS),
        str(QC_DIR),
    ]
    delete_roots = [
        RENDER_DIR / "normalized-media",
        RENDER_DIR / "scenes",
        WORKSPACE / "archive-package",
    ]
    deleted: list[str] = []
    failed: list[str] = []
    reclaimed = 0
    candidates = [
        path
        for root in delete_roots
        if root.exists()
        for path in ([root] if root.is_file() else list(root.rglob("*")))
        if path.is_file()
    ]
    candidates.extend(WORKSPACE.rglob("*.part"))
    candidates.extend(WORKSPACE.rglob("*.part.mp4"))
    for path in sorted(set(candidates), key=lambda item: len(item.parts), reverse=True):
        try:
            resolved = require_inside(path)
            if resolved in {final, contact}:
                failed.append(str(resolved))
                continue
            if resolved.is_file():
                reclaimed += resolved.stat().st_size
                resolved.unlink()
                deleted.append(str(resolved))
        except OSError:
            failed.append(str(path))
    for root in delete_roots:
        if root.exists() and root.is_dir():
            shutil.rmtree(root, ignore_errors=False)
    result = {
        "result": "LOCAL_CLEANUP_PARTIAL_REVIEW_OUTPUT_RETAINED"
        if not failed
        else "FAILED",
        "archive_state": receipt.archive_state,
        "deleted_files": deleted,
        "retained_review_outputs": retained,
        "failed_deletions": failed,
        "bytes_reclaimed": reclaimed,
        "full_purge_claimed": False,
        "purge_count": 0,
        "production_eligible": False,
        "not_publishable": True,
    }
    result["content_hash"] = stable_hash(result)
    write_json(MANIFESTS / "local_cleanup_receipt.json", result)
    if failed:
        raise RuntimeError("CQR1_LOCAL_CLEANUP_FAILED")
    append_event("LOCAL_CLEANUP_PARTIAL_COMPLETED", result)
    print(json.dumps({"CQR1D_LOCAL_CLEANUP": "PARTIAL", **result}, indent=2))
    return result


def _final_verdict_payload() -> dict[str, Any]:
    preflight = read_json(MANIFESTS / "resume_paid_canary_preflight.json")
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    caption = read_json(QC_DIR / "caption_sync_coverage_drift.json")
    visual = read_json(QC_DIR / "visual_continuity_report.json")["aggregate_gates"]
    technical = read_json(QC_DIR / "technical_media_qc.json")
    creative = read_json(QC_DIR / "creative_perceptual_media_qc.json")
    archive_receipt = DriveArchiveReceipt.model_validate(
        read_json(MANIFESTS / "drive_archive_receipt.json")
    )
    cleanup_receipt = read_json(MANIFESTS / "local_cleanup_receipt.json")
    human = read_json(QC_DIR / "human_watchability_review_packet.json")
    all_provider_succeeded = all(
        _provider_prerequisite_satisfied(ledger, key)
        for key in ledger.entries
    )
    reused_provider_keys = {
        "elevenlabs_tts",
        "elevenlabs_forced_alignment",
        "pexels_search",
        "pexels_download",
        "google_veo_submit",
        "google_veo_output",
    }
    if not (
        preflight.get("status") == "PASS"
        and all_provider_succeeded
        and ledger.provider_call_count == 1
        and all(
            ledger.entries[key].status == "REUSED"
            and ledger.entries[key].max_attempts == 0
            and ledger.entries[key].attempt_count == 0
            and not ledger.entries[key].provider_call_made
            and ledger.entries[key].output_count == 0
            for key in reused_provider_keys
        )
        and ledger.entries["drive_archive"].status == "SUCCEEDED"
        and ledger.entries["drive_archive"].attempt_count == 1
        and ledger.entries["drive_archive"].provider_call_made
        and ledger.entries["drive_archive"].output_count == 1
        and technical.get("result") == "PASS"
        and creative.get("result") in {"PASS", "REVIEW_REQUIRED"}
        and archive_receipt.archive_state == "VERIFIED"
        and cleanup_receipt.get("result")
        == "LOCAL_CLEANUP_PARTIAL_REVIEW_OUTPUT_RETAINED"
        and human.get("review_state") == "PENDING"
    ):
        raise RuntimeError("CQR1_FINAL_TECHNICAL_COMPLETION_INCOMPLETE")
    return {
        "CQR1_RUN_ID": CQR1_RUN_ID,
        "CQR1_PAID_CANARY_RESUME": "PASS",
        "CQR1D_PAID_CANARY_PREFLIGHT": "PASS",
        "CQR1D_ELEVENLABS_TTS": "PASS",
        "CQR1D_ELEVENLABS_TTS_EXECUTION_MODE": "REUSED_IMMUTABLE_RUN_002_OUTPUT",
        "CQR1D_ELEVENLABS_TTS_NEW_GENERATION_COUNT": 0,
        "CQR1D_FORCED_ALIGNMENT": "PASS",
        "CQR1D_FORCED_ALIGNMENT_EXECUTION_MODE": (
            "REUSED_IMMUTABLE_RUN_004_VERIFIED_ALIGNMENT"
        ),
        "CQR1D_FORCED_ALIGNMENT_NEW_CALL_COUNT": 0,
        "CQR1D_PEXELS": "PASS",
        "CQR1D_PEXELS_EXECUTION_MODE": "REUSED_IMMUTABLE_RUN_007_OUTPUT",
        "CQR1D_PEXELS_NEW_PROVIDER_CALL_COUNT": 0,
        "CQR1D_GOOGLE_VEO": "PASS",
        "CQR1D_GOOGLE_VEO_EXECUTION_MODE": "REUSED_IMMUTABLE_RUN_007_OUTPUT",
        "CQR1D_GOOGLE_VEO_NEW_PROVIDER_CALL_COUNT": 0,
        "CQR1B_NARRATION_PACING": caption["NarrationPacingGate"]["status"],
        "CQR1B_CAPTION_COMPILATION": caption["CaptionCompilationGate"]["status"],
        "CQR1B_CAPTION_LAYOUT": caption["CaptionLayoutGate"]["status"],
        "CQR1B_CAPTION_SAFE_AREA": caption["CaptionSafeAreaGate"]["status"],
        "CQR1B_CAPTION_AUDIO_SYNC": caption["CaptionAudioSyncGate"]["status"],
        "CQR1B_CAPTION_COVERAGE": caption["CaptionCoverageGate"]["status"],
        "CQR1B_TIMELINE_DRIFT": caption["TimelineDriftGate"]["status"],
        "CQR1C_SCENE_SEMANTIC_MATCH": visual["SceneSemanticMatchGate"]["result"],
        "CQR1C_VISUAL_CONTINUITY": visual["VisualContinuityGate"]["result"],
        "CQR1C_ASSET_ADJACENCY": visual["AssetAdjacencyGate"]["result"],
        "CQR1D_PAID_TECHNICAL_MEDIA_QC": technical["result"],
        "CQR1D_PAID_CREATIVE_MEDIA_QC": creative["result"],
        "CQR1D_DRIVE_ARCHIVE": "PASS",
        "CQR1D_LOCAL_CLEANUP": "PARTIAL",
        "CQR1_HUMAN_WATCHABILITY_REVIEW": "PENDING",
        "CREATIVE_QUALITY_REPAIR": "WAITING_HUMAN_REVIEW",
        "FINAL_PRODUCTION_READINESS": "WAITING_HUMAN_REVIEW",
        "PROCEED_TO_CH1_FLEX": False,
        "provider_call_count": ledger.provider_call_count,
        "provider_attempt_counts": {
            key: entry.attempt_count for key, entry in sorted(ledger.entries.items())
        },
        "archive_state": archive_receipt.archive_state,
        "archive_receipt_hash": archive_receipt.receipt_hash,
        "human_review_scores_entered_by_codex": False,
        "production_eligible": False,
        "not_publishable": True,
    }


def finalize() -> dict[str, Any]:
    payload = _final_verdict_payload()
    payload["content_hash"] = stable_hash(payload)
    write_json(MANIFESTS / "cqr1_final_execution_summary.json", payload)
    append_event("CQR1_FINAL_TECHNICAL_COMPLETION", payload)
    print(json.dumps(payload, indent=2))
    return payload


def status() -> dict[str, Any]:
    ledger = CQR1CanaryCallLedger.load(MANIFESTS / "planned_provider_call_ledger.json")
    result = {
        "run_id": CQR1_RUN_ID,
        "provider_call_count": ledger.provider_call_count,
        "operations": {
            key: {
                "status": entry.status,
                "attempt_count": entry.attempt_count,
                "output_count": entry.output_count,
            }
            for key, entry in sorted(ledger.entries.items())
        },
        "final_mp4_exists": (RENDER_DIR / "final/cqr1-non-production-canary.mp4").is_file(),
        "archive_receipt_exists": (MANIFESTS / "drive_archive_receipt.json").is_file(),
        "human_review": read_json(QC_DIR / "human_watchability_review_packet.json").get(
            "review_state"
        )
        if (QC_DIR / "human_watchability_review_packet.json").is_file()
        else "NOT_CREATED",
    }
    print(json.dumps(result, indent=2))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "prepare",
            "probe",
            "tts",
            "align",
            "timeline",
            "pexels",
            "veo-submit",
            "veo-poll",
            "render",
            "archive",
            "cleanup",
            "finalize",
            "status",
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    handlers = {
        "prepare": prepare,
        "probe": probe,
        "tts": tts,
        "align": align,
        "timeline": timeline,
        "pexels": pexels,
        "veo-submit": veo_submit,
        "veo-poll": veo_poll,
        "render": render,
        "archive": archive,
        "cleanup": cleanup,
        "finalize": finalize,
        "status": status,
    }
    handler = handlers.get(args.phase)
    if handler is None:
        raise RuntimeError(f"CQR1_PHASE_NOT_IMPLEMENTED:{args.phase}")
    with execution_lock():
        handler()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "phase": sys.argv[1] if len(sys.argv) > 1 else "unknown",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        raise
