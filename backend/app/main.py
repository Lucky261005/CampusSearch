from fastapi import FastAPI

app = FastAPI(title="CampusSearch API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
