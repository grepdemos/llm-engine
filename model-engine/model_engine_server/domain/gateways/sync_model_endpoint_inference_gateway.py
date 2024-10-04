from abc import ABC, abstractmethod

from model_engine_server.common.dtos.tasks import (
    SyncEndpointPredictV1Request,
    SyncEndpointPredictV1Response,
)


class SyncModelEndpointInferenceGateway(ABC):
    """
    Base class for synchronous inference endpoints.
    Note that this is distinct from the ModelEndpoint class, which is a domain entity object that
    corresponds to CRUD operations on Endpoints. This class hierarchy is where the actual inference
    requests get sent to.
    """

    @abstractmethod
    async def predict(
        self, topic: str, predict_request: SyncEndpointPredictV1Request, manually_resolve_dns: bool
    ) -> SyncEndpointPredictV1Response:
        """
        Runs a prediction request and returns a response.

        Raises:
            TooManyRequestsException: If the upstream HTTP service raised 429 errors.
            UpstreamServiceError: If the upstream HTTP service raised an error.
        """
