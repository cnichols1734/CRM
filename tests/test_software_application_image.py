"""Pin SoftwareApplication.image on the five public schema pages."""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    ("/", "templates/landing.html"),
    ("/free-real-estate-crm", "templates/free_real_estate_crm.html"),
    ("/follow-up-boss-alternative", "templates/follow_up_boss_alternative.html"),
    ("/wise-agent-alternative", "templates/wise_agent_alternative.html"),
    ("/kvcore-alternative", "templates/kvcore_alternative.html"),
)
IMAGE_KEY = '"image": "{{ app_base_url }}/static/images/og-share.png"'


def _app_base_url(app):
    return (app.config.get("APP_BASE_URL") or "https://agentflow.origentechnolog.com").rstrip("/")


def _json_ld_graph(html):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)
    assert match is not None
    data = json.loads(match.group(1))
    assert data.get("@context") == "https://schema.org"
    return data["@graph"]


def _software_node(graph):
    software = [node for node in graph if node.get("@type") == "SoftwareApplication"]
    assert len(software) == 1
    return software[0]


def test_software_application_image_on_five_public_pages(app, client):
    base = _app_base_url(app)
    expected = f"{base}/static/images/og-share.png"

    for path, template in PAGES:
        source = (ROOT / template).read_text()
        assert IMAGE_KEY in source, template
        source_graph = _json_ld_graph(
            source.replace("{{ app_base_url }}", "https://schema-pin.test")
        )
        source_software = _software_node(source_graph)
        assert source_software["image"] == "https://schema-pin.test/static/images/og-share.png"
        for node in source_graph:
            if node.get("@type") != "SoftwareApplication":
                assert "image" not in node, (template, node.get("@type"))

        html = client.get(path).get_data(as_text=True)
        graph = _json_ld_graph(html)
        software = _software_node(graph)
        assert software["image"] == expected, path
        assert "/static/images/og-share.png" in software["image"]
        assert software["image"].startswith("http")
        for node in graph:
            if node.get("@type") != "SoftwareApplication":
                assert "image" not in node, (path, node.get("@type"))
