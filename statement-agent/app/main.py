"""FastAPI shell owned by workstream 6; dependency import is function-local."""


def create_app():
    from fastapi import FastAPI  # type: ignore[import-not-found]

    app = FastAPI(title="SaveSage Statement Agent")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
