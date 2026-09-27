import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("CRM_HOST", "127.0.0.1"),
        port=int(os.environ.get("CRM_PORT", "5000")),
        debug=os.environ.get("CRM_DEBUG") == "1",
    )
