from fastapi import FastAPI


def secret(name: str) -> str:
    with open(f"/run/secrets/{name}", encoding="utf-8") as f:
        return f.read().strip()


api_key = secret("api_key")
app = FastAPI()


@app.post("/v1/actions/greet")
def greet(payload: dict):
    arguments = payload.get("arguments", {})
    name = arguments.get("name", "world")
    return {"result": f"hello {name}"}


@app.get("/health")
def health():
    return {"ok": True, "secret_loaded": bool(api_key)}
