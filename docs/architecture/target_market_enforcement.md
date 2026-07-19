# Target Market Enforcement

VCOS không điều khiển quốc gia phân phối organic. Production model là:

`Channel market truth → market-aware idea/research/content/package → exact destination → manual publish → actual geo measurement`.

`TargetMarketProfileDraft` luôn là agent/operator proposal. Chỉ exact human approval mới tạo `TargetMarketProfile`; GEO1/GEO2 không active profile. `TargetMarketDigest` là projection bounded cho prompt/gate. Hash của profile, digest và component evidence được bind vào `MarketAlignmentDossier`.

Strict order: editorial slot → niche topic → idea market preflight → topic market gate → admission → effective context → research jurisdiction → script → voice → visual → thumbnail → metadata → dossier. Niche PASS không thay market PASS.

Project mới có policy market v3 sẽ freeze profile ref/version/hash, digest ref/hash, market, locale, narration locale và timezone trong `VideoProject.audience_delivery_summary`. Resume không lookup latest. Historical projects thiếu freeze vẫn readable nhưng không được claim market-ready.

Channel-level draft/profile/destination metadata dùng JSON versioned hiện hữu; artifact-level evidence dùng `ArtifactVersion`. Không cần migration cho GEO1.
