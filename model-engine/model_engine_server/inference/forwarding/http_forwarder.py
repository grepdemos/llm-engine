import argparse
import asyncio
import os
import signal
from functools import lru_cache
from typing import Any, Dict, Optional

import orjson
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI
from model_engine_server.common.concurrency_limiter import MultiprocessingConcurrencyLimiter
from model_engine_server.common.dtos.tasks import EndpointPredictV1Request
from model_engine_server.core.loggers import logger_name, make_logger
from model_engine_server.inference.forwarding.forwarding import (
    Forwarder,
    LoadForwarder,
    LoadStreamingForwarder,
    StreamingForwarder,
    load_named_config,
)
from sse_starlette import EventSourceResponse

logger = make_logger(logger_name())


def get_config():
    overrides = os.getenv("CONFIG_OVERRIDES")
    config_overrides = None
    if overrides is not None:
        config_overrides = overrides.split(";")
    return load_named_config(
        os.getenv("CONFIG_FILE"),
        config_overrides,
    )


def get_forwarder_loader(destination_path: Optional[str] = None):
    config = get_config()["sync"]
    if "extra_routes" in config:
        del config["extra_routes"]
    if destination_path:
        config["predict_route"] = destination_path
    forwarder_loader = LoadForwarder(**config)
    return forwarder_loader


def get_streaming_forwarder_loader(destination_path: Optional[str] = None):
    config = get_config()["stream"]
    if "extra_routes" in config:
        del config["extra_routes"]
    if destination_path:
        config["predict_route"] = destination_path
    streaming_forwarder_loader = LoadStreamingForwarder(**config)
    return streaming_forwarder_loader


@lru_cache()
def get_concurrency_limiter():
    config = get_config()
    concurrency = int(config.get("max_concurrency", 100))
    return MultiprocessingConcurrencyLimiter(
        concurrency=concurrency, fail_on_concurrency_limit=True
    )


@lru_cache()
def load_forwarder(destination_path: Optional[str] = None):
    return get_forwarder_loader(destination_path).load(None, None)


@lru_cache()
def load_streaming_forwarder(destination_path: Optional[str] = None):
    return get_streaming_forwarder_loader(destination_path).load(None, None)


def predict(
    request: EndpointPredictV1Request,
    background_tasks: BackgroundTasks,
    forwarder=Depends(load_forwarder),
    limiter=Depends(get_concurrency_limiter),
):
    with limiter:
        try:
            response = forwarder(request.model_dump())
            background_tasks.add_task(
                forwarder.post_inference_hooks_handler.handle, request, response
            )
            return response
        except Exception:
            logger.error(f"Failed to decode payload from: {request}")
            raise


async def stream(
    request: EndpointPredictV1Request,
    forwarder=Depends(load_streaming_forwarder),
    limiter=Depends(get_concurrency_limiter),
):
    with limiter:
        try:
            payload = request.model_dump()
        except Exception:
            logger.error(f"Failed to decode payload from: {request}")
            raise
        else:
            logger.debug(f"Received request: {payload}")

        responses = forwarder(payload)

        async def event_generator():
            for response in responses:
                yield {"data": orjson.dumps(response).decode("utf-8")}

        return EventSourceResponse(event_generator())


async def serve_http(app: FastAPI, **uvicorn_kwargs: Any):  # pragma: no cover
    logger.info("Available routes are:")
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)

        if methods is None or path is None:
            continue

        logger.info("Route: %s, Methods: %s", path, ", ".join(methods))

    config = uvicorn.Config(app, **uvicorn_kwargs)
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()

    server_task = loop.create_task(server.serve())

    def signal_handler() -> None:
        # prevents the uvicorn signal handler to exit early
        server_task.cancel()

    async def dummy_shutdown() -> None:
        pass

    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        await server_task
        return dummy_shutdown()
    except asyncio.CancelledError:
        logger.info("Gracefully stopping http server")
        return server.shutdown()


async def run_server(args, **uvicorn_kwargs) -> None:  # pragma: no cover
    app = await init_app()
    shutdown_task = await serve_http(
        app,
        host=args.host,
        port=args.port,
        **uvicorn_kwargs,
    )

    await shutdown_task


async def init_app():
    app = FastAPI()

    def healthcheck():
        return "OK"

    def add_extra_routes(app: FastAPI):
        """Read extra_routes from config and dynamically add routes to app"""
        config = get_config()
        sync_forwarders: Dict[str, Forwarder] = dict()
        stream_forwarders: Dict[str, StreamingForwarder] = dict()
        for route in config.get("sync", {}).get("extra_routes", []):
            sync_forwarders[route] = load_forwarder(route)
        for route in config.get("stream", {}).get("extra_routes", []):
            stream_forwarders[route] = load_streaming_forwarder(route)

        all_routes = set(list(sync_forwarders.keys()) + list(stream_forwarders.keys()))

        for route in all_routes:
            # This route is a catch-all for any requests that don't match the /predict or /stream routes
            # It will treat the request as a streaming request if the "stream" body parameter is set to true
            # NOTE: it is important for this to be defined AFTER the /predict and /stream endpoints
            # because FastAPI will match the first route that matches the request path
            async def predict_or_stream(
                request: EndpointPredictV1Request,
                background_tasks: BackgroundTasks,
                sync_forwarder=Depends(lambda: sync_forwarders.get(route)),
                stream_forwarder=Depends(lambda: stream_forwarders.get(route)),
                limiter=Depends(get_concurrency_limiter),
            ):
                if not request.args:
                    raise Exception("Request has no args")
                if request.args.root.get("stream", False) and stream_forwarder:
                    return await stream(request, stream_forwarder, limiter)
                elif request.args.root.get("stream") is not True and sync_forwarder:
                    return predict(request, background_tasks, sync_forwarder, limiter)
                else:
                    raise Exception("No forwarder configured for this route")

            logger.info(f"Adding route {route}")
            app.add_api_route(
                path=route,
                endpoint=predict_or_stream,
                methods=["POST"],
            )

    app.add_api_route(path="/healthz", endpoint=healthcheck, methods=["GET"])
    app.add_api_route(path="/readyz", endpoint=healthcheck, methods=["GET"])
    app.add_api_route(path="/predict", endpoint=predict, methods=["POST"])
    app.add_api_route(path="/stream", endpoint=stream, methods=["POST"])

    add_extra_routes(app)
    return app


def entrypoint():  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--num-workers", type=int, required=True)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--set", type=str, action="append")
    parser.add_argument("--graceful-timeout", type=int, default=600)

    args, extra_args = parser.parse_known_args()

    os.environ["CONFIG_FILE"] = args.config
    if args.set is not None:
        os.environ["CONFIG_OVERRIDES"] = ";".join(args.set)

    asyncio.run(
        run_server(
            args,
            timeout_keep_alive=2,
            timeout_graceful_shutdown=args.graceful_timeout,
            workers=args.num_workers,
            *extra_args,
        )
    )


if __name__ == "__main__":
    entrypoint()
