import pytest


@pytest.fixture(autouse=True)
def pin_vlm_provider(settings):
    """
    The VLM tests mock the Gemini transport specifically (`_get_client` ->
    `models.generate_content`), so pin the provider rather than inheriting
    whatever VLM_PROVIDER a developer happens to have in their local .env.
    """
    settings.VLM_PROVIDER = "gemini"
