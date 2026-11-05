"""Private Python Worker called only from the USDJPY MCP via a service binding."""
from workers import WorkerEntrypoint, Response
from analysis_core import analyze_payload

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method != "POST":
            return Response.json({"error":"METHOD_NOT_ALLOWED"}, status=405)
        try:
            payload = await request.json()
            result = analyze_payload(payload)
            return Response.json(result)
        except Exception as ex:
            return Response.json({
                "error": "PYTHON_ANALYSIS_FAILED",
                "detail": str(ex)[:400]
            },status=422)
