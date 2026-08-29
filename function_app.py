"""Azure Functions v2 entry point."""

import azure.functions as func

from backend.azure_api.function_app import create_data_api


fastapi_app = create_data_api()
app = func.FunctionApp(
    http_auth_level=func.AuthLevel.ANONYMOUS,
)


@app.function_name(name="http_app_func")
@app.route(
    route="{*route}",
    methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    ],
)
async def http_app_func(
    req: func.HttpRequest,
    context: func.Context,
) -> func.HttpResponse:
    return await func.AsgiMiddleware(fastapi_app).handle_async(req, context)
