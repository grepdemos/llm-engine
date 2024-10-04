"""Model Endpoint routes for the hosted model inference service.
TODO:
List model endpoint history: GET model-endpoints/<endpoint id>/history
Read model endpoint creation logs: GET model-endpoints/<endpoint id>/creation-logs
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from model_engine_server.api.dependencies import (
    ExternalInterfaces,
    get_external_interfaces,
    get_external_interfaces_read_only,
    verify_authentication,
)
from model_engine_server.common.dtos.model_endpoints import (
    CreateModelEndpointV1Request,
    CreateModelEndpointV1Response,
    DeleteModelEndpointV1Response,
    GetModelEndpointV1Response,
    ListModelEndpointsV1Response,
    ModelEndpointOrderBy,
    UpdateModelEndpointV1Request,
    UpdateModelEndpointV1Response,
)
from model_engine_server.core.auth.authentication_repository import User
from model_engine_server.core.loggers import logger_name, make_logger
from model_engine_server.domain.exceptions import (
    EndpointDeleteFailedException,
    EndpointInfraStateNotFound,
    EndpointLabelsException,
    EndpointResourceInvalidRequestException,
    ExistingEndpointOperationInProgressException,
    ObjectAlreadyExistsException,
    ObjectHasInvalidValueException,
    ObjectNotAuthorizedException,
    ObjectNotFoundException,
    PostInferenceHooksException,
)
from model_engine_server.domain.use_cases.model_endpoint_use_cases import (
    CreateModelEndpointV1UseCase,
    DeleteModelEndpointByIdV1UseCase,
    GetModelEndpointByIdV1UseCase,
    ListModelEndpointsV1UseCase,
    UpdateModelEndpointByIdV1UseCase,
)

model_endpoint_router_v1 = APIRouter(prefix="/v1")
logger = make_logger(logger_name())


@model_endpoint_router_v1.post("/model-endpoints", response_model=CreateModelEndpointV1Response)
async def create_model_endpoint(
    request: CreateModelEndpointV1Request,
    auth: User = Depends(verify_authentication),
    external_interfaces: ExternalInterfaces = Depends(get_external_interfaces),
) -> CreateModelEndpointV1Response:
    """
    Creates a Model for the current user.
    """
    logger.info(f"POST /model-endpoints with {request} for {auth}")
    try:
        use_case = CreateModelEndpointV1UseCase(
            model_bundle_repository=external_interfaces.model_bundle_repository,
            model_endpoint_service=external_interfaces.model_endpoint_service,
        )
        return await use_case.execute(user=auth, request=request)
    except ObjectAlreadyExistsException as exc:
        raise HTTPException(
            status_code=400,
            detail="The specified model endpoint already exists.",
        ) from exc
    except (
        EndpointLabelsException,
        ObjectHasInvalidValueException,
        EndpointResourceInvalidRequestException,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except (ObjectNotFoundException, ObjectNotAuthorizedException) as exc:
        raise HTTPException(
            status_code=404,
            detail="The specified model bundle could not be found.",
        ) from exc


@model_endpoint_router_v1.get("/model-endpoints", response_model=ListModelEndpointsV1Response)
async def list_model_endpoints(
    name: Optional[str] = Query(default=None),
    order_by: Optional[ModelEndpointOrderBy] = Query(default=None),
    auth: User = Depends(verify_authentication),
    external_interfaces: ExternalInterfaces = Depends(get_external_interfaces_read_only),
) -> ListModelEndpointsV1Response:
    """
    Lists the Models owned by the current owner.
    """
    logger.info(f"GET /model-endpoints?name={name}&order_by={order_by} for {auth}")
    use_case = ListModelEndpointsV1UseCase(
        model_endpoint_service=external_interfaces.model_endpoint_service,
    )
    return await use_case.execute(user=auth, name=name, order_by=order_by)


@model_endpoint_router_v1.get(
    "/model-endpoints/{model_endpoint_id}", response_model=GetModelEndpointV1Response
)
async def get_model_endpoint(
    model_endpoint_id: str,
    auth: User = Depends(verify_authentication),
    external_interfaces: ExternalInterfaces = Depends(get_external_interfaces_read_only),
) -> GetModelEndpointV1Response:
    """
    Describe the Model endpoint with given ID.
    """
    logger.info(f"GET /model-endpoints/{model_endpoint_id} for {auth}")
    try:
        use_case = GetModelEndpointByIdV1UseCase(
            model_endpoint_service=external_interfaces.model_endpoint_service
        )
        return await use_case.execute(user=auth, model_endpoint_id=model_endpoint_id)
    except (ObjectNotFoundException, ObjectNotAuthorizedException) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Model Endpoint {model_endpoint_id}  was not found.",
        ) from exc


@model_endpoint_router_v1.put(
    "/model-endpoints/{model_endpoint_id}", response_model=UpdateModelEndpointV1Response
)
async def update_model_endpoint(
    model_endpoint_id: str,
    request: UpdateModelEndpointV1Request,
    auth: User = Depends(verify_authentication),
    external_interfaces: ExternalInterfaces = Depends(get_external_interfaces),
) -> UpdateModelEndpointV1Response:
    """
    Updates the Model endpoint.
    """
    logger.info(f"PUT /model-endpoints/{model_endpoint_id} with {request} for {auth}")
    try:
        use_case = UpdateModelEndpointByIdV1UseCase(
            model_bundle_repository=external_interfaces.model_bundle_repository,
            model_endpoint_service=external_interfaces.model_endpoint_service,
        )
        return await use_case.execute(
            user=auth, model_endpoint_id=model_endpoint_id, request=request
        )
    except (
        EndpointLabelsException,
        ObjectHasInvalidValueException,
        EndpointResourceInvalidRequestException,
        PostInferenceHooksException,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except (ObjectNotFoundException, ObjectNotAuthorizedException) as exc:
        raise HTTPException(
            status_code=404,
            detail="The specified model endpoint or model bundle was not found.",
        ) from exc
    except ExistingEndpointOperationInProgressException as exc:
        raise HTTPException(
            status_code=409,
            detail="Existing operation on endpoint in progress, try again later.",
        ) from exc
    except EndpointInfraStateNotFound as exc:
        raise HTTPException(
            status_code=500,
            detail="Endpoint infra state not found, try again later.",
        ) from exc


@model_endpoint_router_v1.delete(
    "/model-endpoints/{model_endpoint_id}", response_model=DeleteModelEndpointV1Response
)
async def delete_model_endpoint(
    model_endpoint_id: str,
    auth: User = Depends(verify_authentication),
    external_interfaces: ExternalInterfaces = Depends(get_external_interfaces),
) -> DeleteModelEndpointV1Response:
    """
    Lists the Models owned by the current owner.
    """
    logger.info(f"DELETE /model-endpoints/{model_endpoint_id} for {auth}")
    try:
        use_case = DeleteModelEndpointByIdV1UseCase(
            model_endpoint_service=external_interfaces.model_endpoint_service,
        )
        return await use_case.execute(user=auth, model_endpoint_id=model_endpoint_id)
    except (ObjectNotFoundException, ObjectNotAuthorizedException) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Model Endpoint {model_endpoint_id}  was not found.",
        ) from exc
    except ExistingEndpointOperationInProgressException as exc:
        raise HTTPException(
            status_code=409,
            detail="Existing operation on endpoint in progress, try again later.",
        ) from exc
    except EndpointDeleteFailedException as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail="deletion of endpoint failed, compute resources still exist.",
        ) from exc
