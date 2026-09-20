from ..config import Settings


def build_client(settings: Settings):
    if settings.catalog_mode == "creators":
        from .creators import CreatorsClient
        if not (settings.amazon_credential_id and settings.amazon_credential_secret):
            raise RuntimeError("CATALOG_MODE=creators exige AMAZON_CREDENTIAL_ID e AMAZON_CREDENTIAL_SECRET")
        return CreatorsClient(settings)
    from .mock import MockClient
    return MockClient(settings, jitter=0.03)
