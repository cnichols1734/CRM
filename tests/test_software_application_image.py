"""Pin SoftwareApplication.image on the five public schema pages."""
import json
import re


PAGES = ('/', '/free-real-estate-crm', '/follow-up-boss-alternative',
         '/wise-agent-alternative', '/kvcore-alternative')


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

    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        graph = _json_ld_graph(html)
        software = _software_node(graph)
        assert software["image"] == expected, path
        for node in graph:
            if node.get("@type") != "SoftwareApplication":
                assert "image" not in node, (path, node.get("@type"))
