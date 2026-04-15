"""Shell Recharge API."""

from asyncio import CancelledError, TimeoutError
import logging

from aiohttp import ClientError, ClientSession
from aiohttp_retry import ExponentialRetry, RetryClient
from pydantic import ValidationError
import pydantic
from yarl import URL

from .error import LocationEmptyError, LocationValidationError, RateLimitHitError
from .models.location import Location

_LOGGER = logging.getLogger(__name__)


class Api:
    """API class."""

    def __init__(self, websession: ClientSession, logger: logging.Logger = _LOGGER) -> None:
        """Initialize API class."""
        self.websession = websession
        self.logger = logger

    async def location_by_id(self, location_id: str) -> Location | None:
        """Perform API request."""
        location = None
        url = URL(
            "https://shellretaillocator.geoapp.me/api/v2/on_street_charger_locations/{}".format(
                location_id
            )
        ).with_query(
            {
                "locale": "nl_NL",
                "format": "json",
                "driving_distances": "false",
            }
        )
        retry_client = RetryClient(
            client_session=self.websession,
            retry_options=ExponentialRetry(attempts=3, start_timeout=5),
        )
        try:
            async with retry_client.get(url) as response:
                if response.status == 200:
                    result = await response.json()
                    if result:
                        if pydantic.version.VERSION.startswith("1"):
                            location = Location.parse_obj(result)
                        else:
                            location = Location.model_validate(result)
                    else:
                        raise LocationEmptyError()
                elif response.status == 429:
                    raise RateLimitHitError("Rate limit of API has been hit")
                else:
                    self.logger.exception(
                        "HTTPError %s occurred while requesting %s", response.status, url
                    )
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

    pass


class LocationValidationError(Exception):
    """Raised when returned Location API data is in the wrong format."""

    pass


class RateLimitHitError(Exception):
    """Raised when the rate limit of the API has been hit."""

    pass
