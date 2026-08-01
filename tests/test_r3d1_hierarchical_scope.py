from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_r3d1_does_not_add_provider_media_or_upload_calls() -> None:
    source = (ROOT / "app/services/r3d1.py").read_text()
    forbidden = [
        "GoogleDriveUploadService",
        "MediaOffloadJobService",
        "LLMRouterService",
        "ProviderReadinessService",
        "RealSmokeOrchestratorService",
        "YouTube",
        ".upload(",
        ".publish(",
    ]
    assert [token for token in forbidden if token in source] == []


def test_r3d1_does_not_add_vector_rag_or_memory_retrieval() -> None:
    source = (ROOT / "app/services/r3d1.py").read_text().lower()
    forbidden = [
        "resourceresolverservice",
        "contextpacksnapshot",
        "vector",
        "embedding",
        "rag",
    ]
    assert [token for token in forbidden if token in source] == []
