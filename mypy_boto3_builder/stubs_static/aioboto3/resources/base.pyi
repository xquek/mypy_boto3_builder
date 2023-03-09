import logging
from typing import Any, Optional, Type, TypeVar

from boto3.resources.base import ServiceResource

_R = TypeVar("_R", bound="AIOBoto3ServiceResource")

logger: logging.Logger

class AIOBoto3ServiceResource(ServiceResource):
    async def __aenter__(self: _R) -> _R: ...
    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        tb: Any,
    ) -> None: ...
    def close(self) -> None: ...
