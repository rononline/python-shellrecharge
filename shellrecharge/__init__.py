"""The shellrecharge API code."""

import logging
from asyncio import CancelledError, TimeoutError
from typing import Optional

import pydantic
from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientError
from aiohttp_retry import ExponentialRetry, RetryClient
from pydantic import ValidationError
from yarl import URL

from .models import Location
from .user import User


class Api:
    """Class to make API requests."""

    OAUTH_URL = "https://api.shell.com/v1/oauth/token"
    OCTOPUS_BASE = "https://api.shell.com/ds-retail-shellmotorist-shellapp-mobgen/octopus/v3/locations/ev"
    API_KEY = "h5mgWWYfITR3Fn886IX1J4YP6dcsFQ2Q"
    CLIENT_AUTH = "Basic QWprVG01cDNKSFBDTm5VU1NqdUdxRU9QMlhHZzVYYW86Z0k1Q2RpSG9jRHl2S0Q3Zw=="

    def __init__(self, websession: ClientSession):
        """Initialize the session."""
        self.websession = websession
        self.logger = logging.getLogger("shellrecharge")

    async def _get_oauth_token(self) -> str:
        """Fetch OAuth token used by Shell octopus API."""
        headers = {
            "Accept": "application/json",
            "Authorization": self.CLIENT_AUTH,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HomeAssistant shellrecharge custom integration",
        }
        data = "grant_type=client_credentials"

        retry_client = RetryClient(
            client_session=self.websession,
            retry_options=ExponentialRetry(attempts=3, start_timeout=5),
        )

        async with retry_client.post(self.OAUTH_URL, headers=headers, data=data) as response:
            response.raise_for_status()
            result = await response.json()
            token = result.get("access_token")
            if not token:
                raise TokenError("No access_token in OAuth response")
            return token

    async def location_by_id(self, location_id: str) -> Location | None:
        """Perform API request."""
        location = None

        if "*" in location_id:
            location_uid = location_id
        else:
            location_uid = f"NL*LMS*{location_id}"

        token = await self._get_oauth_token()

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "x-octopus-market": "nl-NL",
            "Accept-Language": "nl-NL,nl;q=0.9",
            "User-Agent": "HomeAssistant shellrecharge custom integration",
        }

        url = URL(f"{self.OCTOPUS_BASE}/{location_uid}/details").with_query(
            {
                "apikey": self.API_KEY,
                "pricing": "legacy-to-360",
                "allTariffs": "true",
                "units": "metric",
                "providerIds": "Shell_B2C",
            }
        )

        retry_client = RetryClient(
            client_session=self.websession,
            retry_options=ExponentialRetry(attempts=3, start_timeout=5),
        )

        try:
            async with retry_client.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if result and result.get("data"):
                        item = result["data"][0]

                        # tijdelijke mapping naar oud Location-model
                        mapped = {
                            "id": item.get("externalId") or item.get("uid"),
                            "name": item.get("name") or item.get("address", {}).get("streetAndNumber"),
                            "lat": item.get("coordinates", {}).get("latitude"),
                            "lng": item.get("coordinates", {}).get("longitude"),
                            "address": item.get("address", {}).get("streetAndNumber"),
                            "city": item.get("address", {}).get("city"),
                            "postcode": item.get("address", {}).get("postalCode"),
                            "country_code": item.get("address", {}).get("country"),
                            "country": item.get("address", {}).get("country"),
                            "ev_charging": {
                                "charging_points": len(item.get("evses", [])),
                                "connector_data": [
                                    {
                                        "type": connector.get("connectorType"),
                                        "max_power": {
                                            "source": connector.get("electricalProperties", {}).get("maxElectricPower"),
                                            "parsedValue": connector.get("electricalProperties", {}).get("maxElectricPower"),
                                        },
                                        "status": evse.get("status"),
                                        "available": 1 if evse.get("status") == "Available" else 0,
                                        "total": 1,
                                        "pricing": connector.get("pricing", []),
                                    }
                                    for evse in item.get("evses", [])
                                    for connector in evse.get("connectors", [])
                                ],
                                "operator_name": [item.get("operatorName")] if item.get("operatorName") else [],
                                "evse_ids": [evse.get("evseId") for evse in item.get("evses", []) if evse.get("evseId")],
                            },
                        }

                        if pydantic.version.VERSION.startswith("1"):
                            location = Location.parse_obj(mapped)
                        else:
                            location = Location.model_validate(mapped)
                    else:
                        raise LocationEmptyError()
                elif response.status == 429:
                    raise RateLimitHitError("Rate limit of API has been hit")
                else:
                    body = await response.text()
                    self.logger.error("HTTPError %s occurred while requesting %s - %s", response.status, url, body)
        except ValidationError as err:
            raise LocationValidationError(err)
        except (ClientError, TimeoutError, CancelledError) as err:
            raise err

        return location

    async def get_user(self, email: str, pwd: str, api_key: Optional[str] = None) -> User:
        user = User(email, pwd, self.websession, api_key)
        if not api_key:
            await user.authenticate()
        return user


class LocationEmptyError(Exception):
    """Raised when returned Location API data is empty."""


class LocationValidationError(Exception):
    """Raised when returned Location API data is in the wrong format."""


class RateLimitHitError(Exception):
    """Raised when the rate limit of the API has been hit."""


class TokenError(Exception):
    """Raised when OAuth token retrieval fails."""
