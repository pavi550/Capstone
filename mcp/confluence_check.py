"""Read-only connection check for a configured Confluence Cloud account."""

import httpx
from config import ConfigurationError, confluence_settings, settings


def check():
    root, email, token = confluence_settings(settings())
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                f"{root}/api/v2/spaces", auth=(email, token), params={"limit": 1},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            if not isinstance(response.json()["results"], list):
                raise TypeError
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ConfigurationError(
            f"Confluence returned HTTP {status}. Check the site URL, API token and space permissions."
        ) from None
    except (httpx.RequestError, KeyError, TypeError, ValueError):
        raise ConfigurationError(
            "Could not verify Confluence. Check the site URL and network connection."
        ) from None
    print("Confluence read-only API check passed.")
